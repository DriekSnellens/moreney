"""Causal walk-forward for lead-lag — no future leakage."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from bot.core.exchange_types import OrderBookLevel
from bot.opportunity.lead_lag.economics import build_shadow_opportunity
from bot.opportunity.lead_lag.horizons import HORIZON_MS_GRID, LATENCY_MS_GRID
from bot.opportunity.lead_lag.models_a_d import fair_gap_bps, make_models, model_state_key
from bot.opportunity.lead_lag.shadow import shadow_admit
from bot.opportunity.lead_lag.types import LeadLagObservation, LeadLagOutcome, LeadLagSignal

_ZERO = Decimal("0")


@dataclass
class PendingOutcome:
    signal: LeadLagSignal
    available_at_ms: float
    decision_idx: int
    admitted: bool
    opportunity_net: Decimal


@dataclass
class WalkForwardResult:
    decisions: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    pair_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_version: str = ""
    horizon_ms: int = 0
    n_predictions: int = 0
    n_admitted: int = 0
    n_outcomes: int = 0
    hit_rate: float | None = None
    mean_follower_response: float | None = None
    shadow_net_sum: Decimal = _ZERO

    def summary(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "horizon_ms": self.horizon_ms,
            "n_predictions": self.n_predictions,
            "n_admitted": self.n_admitted,
            "n_outcomes": self.n_outcomes,
            "hit_rate": self.hit_rate,
            "mean_follower_response_bps": self.mean_follower_response,
            "shadow_net_sum": str(self.shadow_net_sum),
            "label": "CAUSAL_REPLAY",
        }


def _mid(bid: Decimal, ask: Decimal) -> Decimal:
    return (bid + ask) / Decimal("2")


def follower_future_move_bps(
    observations: Sequence[LeadLagObservation],
    *,
    start_idx: int,
    horizon_ms: int,
) -> Decimal | None:
    """Return follower mid move from decision to first obs at/after t+horizon.

    Outcome is unavailable before the horizon — returns None if not yet reached.
    """
    base = observations[start_idx]
    target = base.timestamp_ms + horizon_ms
    base_mid = base.follower_mid
    if base_mid <= 0:
        return None
    for j in range(start_idx + 1, len(observations)):
        if observations[j].timestamp_ms < target:
            continue
        # Same pair/symbol required
        o = observations[j]
        if (
            o.symbol != base.symbol
            or o.leader_venue != base.leader_venue
            or o.follower_venue != base.follower_venue
        ):
            continue
        return (o.follower_mid - base_mid) / base_mid * Decimal("10000")
    return None


def walk_forward_lead_lag(
    observations: Sequence[LeadLagObservation],
    *,
    model_version: str,
    horizon_ms: int,
    latency_ms: float = 0.0,
    quantity: Decimal = Decimal("1"),
    follower_books: dict[int, tuple[list[OrderBookLevel], list[OrderBookLevel]]] | None = None,
    leader_books: dict[int, tuple[list[OrderBookLevel], list[OrderBookLevel]]] | None = None,
    min_leader_move_bps: float = 5.0,
) -> WalkForwardResult:
    """Strict causal order:

    release_due(t) → update from known outcomes → predict → shadow decide
    → immutable record → wait → observe → then allow into training.
    """
    models = make_models()
    if model_version not in models:
        raise ValueError(f"unknown model_version={model_version}")
    # Per-pair model states live inside make_models() single instance —
    # we key updates by pair via one shared model (pair-agnostic residual)
    # and also track pair-specific deques via observe on that model.
    model = models[model_version]
    pending: list[PendingOutcome] = []
    result = WalkForwardResult(model_version=model_version, horizon_ms=horizon_ms)
    responses: list[float] = []
    hits = 0

    for i, obs in enumerate(observations):
        now = obs.timestamp_ms

        # 1) Release due outcomes (only those whose horizon fully elapsed)
        still_pending: list[PendingOutcome] = []
        for p in pending:
            if p.available_at_ms > now:
                still_pending.append(p)
                continue
            move = follower_future_move_bps(
                observations, start_idx=p.decision_idx, horizon_ms=horizon_ms
            )
            if move is None:
                # Horizon elapsed in clock but path missing — do not train
                continue
            pred = float(p.signal.predicted_follower_move_bps)
            correct = (pred >= 0 and float(move) >= 0) or (pred < 0 and float(move) < 0)
            if correct:
                hits += 1
            responses.append(float(move))
            result.n_outcomes += 1
            result.shadow_net_sum += p.opportunity_net if p.admitted else _ZERO

            # 2) Only now update training state
            model.state.observe_outcome(
                leader_return_bps=float(p.signal.leader_return_bps),
                follower_response_bps=float(move),
                fair_gap_bps=fair_gap_bps(observations[p.decision_idx]),
            )
            outcome = LeadLagOutcome(
                decision_timestamp_ms=p.signal.decision_timestamp_ms,
                symbol=p.signal.symbol,
                leader_venue=p.signal.leader_venue,
                follower_venue=p.signal.follower_venue,
                horizon_ms=horizon_ms,
                follower_move_bps={str(horizon_ms): move},
                realized_directional_correct=correct,
                hypothetical_net_eur=p.opportunity_net if p.admitted else _ZERO,
                conservative_threshold_passed=p.admitted,
                signal_correct=correct,
                available_at_ms=p.available_at_ms,
                label="CAUSAL_REPLAY",
            )
            result.outcomes.append(outcome.as_dict())
        pending = still_pending

        # Skip tiny leader moves
        if abs(float(obs.leader_return_bps)) < min_leader_move_bps:
            continue

        # 3) Predict using information available at t only
        signal = model.predict(obs, horizon_ms=horizon_ms)
        result.n_predictions += 1

        # 4) Shadow economics (optional books; synthetic empty → not executable)
        fb = (follower_books or {}).get(i)
        lb = (leader_books or {}).get(i)
        if fb and lb:
            opp = build_shadow_opportunity(
                signal,
                follower_bids=fb[0],
                follower_asks=fb[1],
                leader_bids=lb[0],
                leader_asks=lb[1],
                quantity=quantity,
                latency_ms=latency_ms,
            )
            adm = shadow_admit(opp)
            admitted = bool(adm["accept"])
            net = opp.conservative_net_eur if admitted else _ZERO
        else:
            admitted = False
            net = _ZERO
            opp = None
            adm = {
                "accept": False,
                "first_gate": "no_depth_books",
                "state": "NOT_EXECUTABLE",
                "alters_execution": False,
            }

        if admitted:
            result.n_admitted += 1

        decision = {
            "idx": i,
            "timestamp_ms": obs.timestamp_ms,
            "signal": signal.as_dict(),
            "admitted": admitted,
            "shadow": adm,
            "opportunity": opp.as_dict() if opp else None,
            "label": "CAUSAL_REPLAY",
        }
        result.decisions.append(decision)

        # 5) Schedule immutable pending outcome
        pending.append(
            PendingOutcome(
                signal=signal,
                available_at_ms=obs.timestamp_ms + horizon_ms,
                decision_idx=i,
                admitted=admitted,
                opportunity_net=net,
            )
        )

    if result.n_outcomes:
        result.hit_rate = hits / result.n_outcomes
        result.mean_follower_response = sum(responses) / len(responses)
    return result


def run_latency_sensitivity(
    observations: Sequence[LeadLagObservation],
    *,
    model_version: str,
    horizon_ms: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Report all predeclared latency scenarios — do not select the best."""
    out: dict[str, Any] = {}
    for lat in LATENCY_MS_GRID:
        wf = walk_forward_lead_lag(
            observations,
            model_version=model_version,
            horizon_ms=horizon_ms,
            latency_ms=float(lat),
            **kwargs,
        )
        out[str(lat)] = wf.summary()
    return out
