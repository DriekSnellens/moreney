"""Causal walk-forward evaluation — no look-ahead, no architecture redesign.

Event order (enforced):

  predict(belief before t)
  → decide(take/reject)
  → if taken: record realized outcome
  → schedule markout observation at t + horizon
  → at later events: release due markouts into beliefs
  → never mutate the belief used for the current decision

Usage:
  .venv/bin/python -m bot.opportunity.causal_walkforward data/paper_25000live.json
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from bot.opportunity.calibration import EvCalibrator

_ZERO = Decimal("0")
_ONE = Decimal("1")

# Frozen experiment configs — do not tune during this task.
CONFIGS: dict[str, dict[str, Any]] = {
    "A_BASELINE": {
        "early_stop": False,
        "conditional_ev": False,
        "label": "BASELINE",
    },
    "B_EARLY_STOP_ONLY": {
        "early_stop": True,
        "conditional_ev": False,
        "label": "EARLY_STOP_ONLY",
    },
    "C_CONDITIONAL_EV_ONLY": {
        "early_stop": False,
        "conditional_ev": True,
        "label": "CONDITIONAL_EV_ONLY",
    },
    "D_CONDITIONAL_EV_PLUS_EARLY_STOP": {
        "early_stop": True,
        "conditional_ev": True,
        "label": "CONDITIONAL_EV_PLUS_EARLY_STOP",
    },
}

# Markout horizon used for causal adverse learning (matches primary gate).
DEFAULT_MARKOUT_DELAY = timedelta(seconds=5)


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _parse_ts(raw: object) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class BeliefSnapshot:
    """Immutable view of beliefs at decision time (copy, never mutated later)."""

    route: str
    n: int
    raw_capture: Decimal | None
    shrunk_capture: Decimal
    early_stop: bool
    route_state: str
    route_reason: str
    predicted_adverse_eur: Decimal
    historical_adverse_n: int
    belief_version: int


@dataclass
class PendingMarkout:
    available_at: datetime
    route: str
    adverse_eur: Decimal
    expected_net: Decimal
    realized_net: Decimal
    key: str
    strategy: str
    opportunity_id: str
    source_event_ts: datetime


@dataclass
class CausalBeliefModel:
    """predict() then later observe() — never the reverse for the same event."""

    calibrator: EvCalibrator = field(default_factory=lambda: EvCalibrator(
        prior_strength=40,
        min_samples=20,
        early_stop_samples=8,
        early_stop_capture=Decimal("-0.25"),
        early_stop_min_loss_eur=Decimal("5"),
    ))
    adverse_by_route: dict[str, list[Decimal]] = field(default_factory=dict)
    pending: list[PendingMarkout] = field(default_factory=list)
    belief_version: int = 0
    markout_delay: timedelta = DEFAULT_MARKOUT_DELAY
    # Refuse loading evaluation-period outcomes as "priors".
    allow_eval_init: bool = False

    def release_due(self, now: datetime) -> list[PendingMarkout]:
        """Incorporate markouts whose horizon has elapsed (strictly <= now)."""
        due = [p for p in self.pending if p.available_at <= now]
        self.pending = [p for p in self.pending if p.available_at > now]
        for item in due:
            self._apply_observation(item)
        if due:
            self.belief_version += 1
        return due

    def predict(self, *, route: str, expected_adverse_buffer: Decimal) -> BeliefSnapshot:
        """Read-only belief for decision. Does not observe."""
        status = self.calibrator.route_state(route)
        hist = list(self.adverse_by_route.get(route) or [])
        predicted_adv = expected_adverse_buffer
        if len(hist) >= 3:
            ordered = sorted(hist)
            predicted_adv = max(expected_adverse_buffer, ordered[len(ordered) // 2])
        return BeliefSnapshot(
            route=route,
            n=int(status["n"]),
            raw_capture=_d(status["raw_capture"]) if status.get("raw_capture") is not None else None,
            shrunk_capture=_d(status["shrunk_capture"]),
            early_stop=bool(status["early_stop"]),
            route_state=str(status["state"]),
            route_reason=str(status["reason"]),
            predicted_adverse_eur=predicted_adv,
            historical_adverse_n=len(hist),
            belief_version=self.belief_version,
        )

    def schedule_observation(
        self,
        *,
        event_ts: datetime,
        route: str,
        adverse_eur: Decimal,
        expected_net: Decimal,
        realized_net: Decimal,
        key: str,
        strategy: str,
        opportunity_id: str,
    ) -> None:
        """Queue learning until markout horizon elapses — not at fill time."""
        self.pending.append(
            PendingMarkout(
                available_at=event_ts + self.markout_delay,
                route=route,
                adverse_eur=adverse_eur,
                expected_net=expected_net,
                realized_net=realized_net,
                key=key,
                strategy=strategy,
                opportunity_id=opportunity_id,
                source_event_ts=event_ts,
            )
        )

    def observe_immediate_roundtrip(
        self,
        *,
        route: str,
        key: str,
        strategy: str,
        expected_net: Decimal,
        realized_net: Decimal,
    ) -> None:
        """Round-trip PnL is known at completion; calibrator may update immediately.

        Markout-conditioned adverse still uses schedule_observation (delayed).
        Capture for early-stop uses completed NET only — available at fill close.
        """
        self.calibrator.observe(
            key=key,
            route=route,
            strategy=strategy,
            expected_net=expected_net,
            realized_net=realized_net,
        )
        self.belief_version += 1

    def _apply_observation(self, item: PendingMarkout) -> None:
        self.adverse_by_route.setdefault(item.route, []).append(item.adverse_eur)

    def import_train_only(
        self,
        train_trades: Iterable[dict[str, Any]],
        *,
        eval_start: datetime | None,
    ) -> None:
        """Initialize from an explicit training window only.

        Raises if any train row is on/after eval_start (full-dataset init guard).
        """
        if eval_start is not None and not self.allow_eval_init:
            for row in train_trades:
                ts = _parse_ts(row.get("timestamp"))
                if ts >= eval_start:
                    raise ValueError(
                        "Refusing evaluation-period outcomes as train init: "
                        f"{ts.isoformat()} >= {eval_start.isoformat()}"
                    )
        for row in sorted(train_trades, key=lambda r: r.get("timestamp") or ""):
            route = f"{row.get('buy_exchange')}->{row.get('sell_exchange')}"
            exp = _d(row.get("expected_net_profit"))
            real = _d(row.get("realized_net_profit"))
            key = (
                f"{row.get('strategy')}|{row.get('symbol')}|{route}|buy"
            )
            self.observe_immediate_roundtrip(
                route=route,
                key=key,
                strategy=str(row.get("strategy") or ""),
                expected_net=exp,
                realized_net=real,
            )
            # Train markouts are treated as already elapsed (train ends before eval).
            self.adverse_by_route.setdefault(route, []).append(
                _d(row.get("realized_adverse"))
            )
            self.belief_version += 1


@dataclass
class DecisionRecord:
    timestamp: datetime
    opportunity_id: str
    route: str
    belief_version: int
    historical_n: int
    raw_capture_before: Decimal | None
    shrunk_capture_before: Decimal
    route_state_before: str
    route_reason_before: str
    predicted_adverse_eur: Decimal
    predicted_net_if_fill: Decimal
    predicted_p_fill: Decimal
    predicted_ev: Decimal
    decision: str  # take | reject
    decision_reason: str
    realized_net: Decimal | None = None
    route_state_after: str | None = None
    markout_available_at: datetime | None = None


def decide(
    *,
    config: dict[str, Any],
    belief: BeliefSnapshot,
    expected_net: Decimal,
    expected_buffer: Decimal,
    p_fill: Decimal = _ONE,
) -> tuple[str, str, Decimal, Decimal]:
    """Return (decision, reason, net_if_fill, ev). Uses only the provided belief."""
    extra = _ZERO
    if config.get("conditional_ev") and belief.historical_adverse_n >= 3:
        extra = max(_ZERO, belief.predicted_adverse_eur - expected_buffer)
    net_if_fill = expected_net - extra
    ev = p_fill * net_if_fill

    if config.get("early_stop") and belief.early_stop:
        return "reject", "early_stop_historical", net_if_fill, ev
    if config.get("conditional_ev") and belief.historical_adverse_n >= 3 and net_if_fill <= 0:
        return "reject", "conditional_ev_non_positive", net_if_fill, ev
    return "take", "approved", net_if_fill, ev


def walk_forward(
    trades: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    model: CausalBeliefModel | None = None,
    data_status: str = "IN_SAMPLE_CAUSAL_REPLAY",
) -> dict[str, Any]:
    """Chronological causal replay. Frozen fill outcomes from the dataset.

    Does not re-simulate fills more easily — only re-applies take/reject gates.
    """
    model = model or CausalBeliefModel()
    events: list[DecisionRecord] = []
    taken_realized: list[Decimal] = []
    taken_expected: list[Decimal] = []
    rejected = 0
    stop_reasons: dict[str, int] = {}

    ordered = sorted(trades, key=lambda t: t.get("timestamp") or "")
    for row in ordered:
        ts = _parse_ts(row.get("timestamp"))
        # 1) Release markouts that are now observable BEFORE predicting.
        model.release_due(ts)

        route = f"{row.get('buy_exchange')}->{row.get('sell_exchange')}"
        exp = _d(row.get("expected_net_profit"))
        real = _d(row.get("realized_net_profit"))
        buffer = _d(row.get("expected_adverse"))
        adverse = _d(row.get("realized_adverse"))
        opp_id = str(row.get("opportunity_id") or f"{route}:{ts.isoformat()}")
        key = f"{row.get('strategy')}|{row.get('symbol')}|{route}|buy"
        strategy = str(row.get("strategy") or "")

        # 2) Predict from history only.
        belief = model.predict(route=route, expected_adverse_buffer=buffer)
        decision, reason, net_if_fill, ev = decide(
            config=config,
            belief=belief,
            expected_net=exp,
            expected_buffer=buffer,
        )

        rec = DecisionRecord(
            timestamp=ts,
            opportunity_id=opp_id,
            route=route,
            belief_version=belief.belief_version,
            historical_n=belief.n,
            raw_capture_before=belief.raw_capture,
            shrunk_capture_before=belief.shrunk_capture,
            route_state_before=belief.route_state,
            route_reason_before=belief.route_reason,
            predicted_adverse_eur=belief.predicted_adverse_eur,
            predicted_net_if_fill=net_if_fill,
            predicted_p_fill=_ONE,
            predicted_ev=ev,
            decision=decision,
            decision_reason=reason,
        )

        if decision == "reject":
            rejected += 1
            stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
            # Rejected: do NOT learn from this trade's outcome (no leakage).
            after = model.calibrator.route_state(route)
            rec.route_state_after = str(after["state"])
            events.append(rec)
            continue

        # 3) Taken: record realized NET (dataset outcome), then observe.
        rec.realized_net = real
        taken_realized.append(real)
        taken_expected.append(exp)

        # Immediate: round-trip capture for early-stop (known at completion).
        model.observe_immediate_roundtrip(
            route=route,
            key=key,
            strategy=strategy,
            expected_net=exp,
            realized_net=real,
        )
        # Delayed: adverse/markout enters conditional-EV memory only after horizon.
        model.schedule_observation(
            event_ts=ts,
            route=route,
            adverse_eur=adverse,
            expected_net=exp,
            realized_net=real,
            key=key,
            strategy=strategy,
            opportunity_id=opp_id,
        )
        rec.markout_available_at = ts + model.markout_delay
        after = model.calibrator.route_state(route)
        rec.route_state_after = str(after["state"])
        events.append(rec)

    # Flush remaining pending markouts after the last event (for completeness
    # of belief state; they cannot affect past decisions).
    if ordered:
        last_ts = _parse_ts(ordered[-1].get("timestamp"))
        model.release_due(last_ts + model.markout_delay + timedelta(seconds=1))

    n_taken = len(taken_realized)
    sum_real = sum(taken_realized, _ZERO)
    sum_exp = sum(taken_expected, _ZERO)
    pred_errs = [
        (e.realized_net - e.predicted_net_if_fill)
        for e in events
        if e.decision == "take" and e.realized_net is not None
    ]
    mean_err = (sum(pred_errs, _ZERO) / Decimal(len(pred_errs))) if pred_errs else None

    return {
        "config": config,
        "data_status": data_status,
        "kind": "causal_walk_forward",
        "opportunities_scanned": len(trades),
        "rejected_opportunities": rejected,
        "executed_opportunities": n_taken,
        "fills": n_taken,
        "completed_round_trips": n_taken,
        "fill_rate": None,
        "total_realized_net": str(sum_real),
        "net_per_completed_trade": str(sum_real / n_taken) if n_taken else "0",
        "sum_expected_net": str(sum_exp),
        "ev_capture": str(sum_real / sum_exp) if abs(sum_exp) > Decimal("0.01") else None,
        "mean_prediction_error": str(mean_err) if mean_err is not None else None,
        "route_stops": sorted(
            {
                e.route
                for e in events
                if e.decision == "reject" and e.decision_reason == "early_stop_historical"
            }
        ),
        "stop_reasons": stop_reasons,
        "events": [_event_dict(e) for e in events],
        "final_route_states": {
            r: model.calibrator.route_state(r)
            for r in sorted({e.route for e in events})
        },
    }


def _event_dict(e: DecisionRecord) -> dict[str, Any]:
    return {
        "timestamp": e.timestamp.isoformat(),
        "opportunity_id": e.opportunity_id,
        "route": e.route,
        "belief_version": e.belief_version,
        "historical_n": e.historical_n,
        "raw_capture_before": str(e.raw_capture_before) if e.raw_capture_before is not None else None,
        "shrunk_capture_before": str(e.shrunk_capture_before),
        "route_state_before": e.route_state_before,
        "route_reason_before": e.route_reason_before,
        "predicted_adverse_eur": str(e.predicted_adverse_eur),
        "predicted_net_if_fill": str(e.predicted_net_if_fill),
        "predicted_p_fill": str(e.predicted_p_fill),
        "predicted_ev": str(e.predicted_ev),
        "decision": e.decision,
        "decision_reason": e.decision_reason,
        "realized_net": str(e.realized_net) if e.realized_net is not None else None,
        "route_state_after": e.route_state_after,
        "markout_available_at": e.markout_available_at.isoformat() if e.markout_available_at else None,
        "prediction_before_observation": (
            e.markout_available_at is None
            or e.timestamp < e.markout_available_at
        ),
    }


def first_early_stop_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Chronological events for the first route that reaches EARLY_STOPPED."""
    events = result.get("events") or []
    target = None
    for e in events:
        if e.get("route_state_after") == "early_stopped":
            target = e.get("route")
            break
    if not target:
        return []
    return [e for e in events if e.get("route") == target]


def split_trades(
    trades: list[dict[str, Any]],
    *,
    train_frac: float = 0.4,
    val_frac: float = 0.3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(trades, key=lambda t: t.get("timestamp") or "")
    n = len(ordered)
    if n < 3:
        return [], ordered, []
    i_train = max(1, int(n * train_frac))
    i_val = max(i_train + 1, int(n * (train_frac + val_frac)))
    return ordered[:i_train], ordered[i_train:i_val], ordered[i_val:]


def run_matrix(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    trades = list((data.get("tracker") or {}).get("trades") or [])
    train, val, test = split_trades(trades)

    results: dict[str, Any] = {}
    for name, cfg in CONFIGS.items():
        # Validation: train-init then walk-forward on val (causal).
        model = CausalBeliefModel()
        eval_start = _parse_ts(val[0]["timestamp"]) if val else None
        if train and eval_start is not None:
            model.import_train_only(train, eval_start=eval_start)
        val_result = walk_forward(
            val,
            config=cfg,
            model=model,
            data_status="VALIDATION",
        )
        # Untouched test: freeze config; re-init from train+val only, then test.
        model_test = CausalBeliefModel()
        test_start = _parse_ts(test[0]["timestamp"]) if test else None
        if test_start is not None:
            model_test.import_train_only(train + val, eval_start=test_start)
        test_result = walk_forward(
            test,
            config=cfg,
            model=model_test,
            data_status="UNTOUCHED_OUT_OF_SAMPLE",
        )
        # Full in-sample causal replay (no train split) for continuity with prior report.
        full = walk_forward(
            trades,
            config=cfg,
            model=CausalBeliefModel(),
            data_status="IN_SAMPLE_CAUSAL_REPLAY",
        )
        results[name] = {
            "validation": _summary(val_result),
            "untouched_oos": _summary(test_result),
            "in_sample_causal_replay": _summary(full),
            "early_stop_trace_in_sample": first_early_stop_trace(full),
            "full_events_in_sample": full.get("events"),
        }

    return {
        "source": str(path),
        "trade_count": len(trades),
        "split": {
            "train": len(train),
            "validation": len(val),
            "untouched_test": len(test),
        },
        "note": (
            "Fill assumptions frozen. No parameter tuning. "
            "Markout adverse enters beliefs only after +5s. "
            "Rejected trades do not update beliefs."
        ),
        "experiments": results,
    }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        k: result.get(k)
        for k in (
            "data_status",
            "opportunities_scanned",
            "rejected_opportunities",
            "executed_opportunities",
            "completed_round_trips",
            "total_realized_net",
            "net_per_completed_trade",
            "ev_capture",
            "mean_prediction_error",
            "route_stops",
            "stop_reasons",
            "final_route_states",
        )
    }


# ---------------------------------------------------------------------------
# Leakage audit (static documentation produced alongside the runner)
# ---------------------------------------------------------------------------

LEAKAGE_AUDIT: list[dict[str, str]] = [
    {
        "source": "bot/opportunity/experiment_runner.py (legacy)",
        "function": "run_experiment",
        "status": "FIXED_BY_REPLACEMENT",
        "why": (
            "Appended realized_adverse for rejected trades and used same-row "
            "adverse without markout delay; superseded by causal_walkforward."
        ),
        "available_at_decision": "N/A — do not use for evidence",
    },
    {
        "source": "bot/opportunity/causal_walkforward.py",
        "function": "walk_forward",
        "status": "SAFE",
        "why": (
            "predict() before decide(); observe only after take; markout "
            "scheduled at t+5s; rejected trades never update beliefs."
        ),
        "available_at_decision": "prior taken trades + released markouts only",
    },
    {
        "source": "bot/opportunity/audit_report.py",
        "function": "toxicity_report / markout import_state",
        "status": "SAFE_IF_LABELED_OBSERVED",
        "why": "Full-period aggregate for reporting only; not used in walk-forward decisions.",
        "available_at_decision": "not used for decisions",
    },
    {
        "source": "bot/paper/runner.py",
        "function": "_observe_calibration / _seed_calibrator",
        "status": "SAFE_LIVE",
        "why": (
            "Observes after completed round-trip; seed rebuilds from past "
            "completed fills only. Live markout waits for horizon in MarkoutTracker."
        ),
        "available_at_decision": "past completed fills + elapsed markouts",
    },
    {
        "source": "bot/paper/markout.py",
        "function": "update / suggested_adverse_bps",
        "status": "SAFE_LIVE",
        "why": "Samples appended only after horizon age; pending until then.",
        "available_at_decision": "markouts with age >= horizon",
    },
    {
        "source": "bot/opportunity/missed.py",
        "function": "update_mids",
        "status": "SAFE_IF_REPORTING_ONLY",
        "why": "Post-decision counterfactual markout; documented as not feeding live ranker.",
        "available_at_decision": "not used for live decisions",
    },
]


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "data/paper_25000live.json")
    report = run_matrix(path)
    report["leakage_audit"] = LEAKAGE_AUDIT
    dest = Path("data/causal_walkforward_report.json")
    dest.write_text(json.dumps(report, indent=2, default=str))
    # Compact stdout summary
    summary = {
        name: {
            "in_sample": exp["in_sample_causal_replay"].get("total_realized_net"),
            "validation": exp["validation"].get("total_realized_net"),
            "oos": exp["untouched_oos"].get("total_realized_net"),
            "in_sample_net_per": exp["in_sample_causal_replay"].get("net_per_completed_trade"),
            "stops": exp["in_sample_causal_replay"].get("route_stops"),
        }
        for name, exp in report["experiments"].items()
    }
    print(json.dumps({"source": str(path), "split": report["split"], "summary": summary}, indent=2))
    print(f"\nWrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
