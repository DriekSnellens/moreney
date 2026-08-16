"""Domain models for lead-lag research (dataclasses — avoid hot-path Pydantic)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LeadLagObservation:
    """Phase A observation at a synchronized sample time."""

    timestamp_ms: float
    local_received_ms: float
    symbol: str
    leader_venue: str
    follower_venue: str
    leader_bid: Decimal
    leader_ask: Decimal
    follower_bid: Decimal
    follower_ask: Decimal
    leader_return_bps: Decimal
    follower_return_bps: Decimal
    spread_leader_bps: Decimal
    spread_follower_bps: Decimal
    leader_book_age_ms: float
    follower_book_age_ms: float
    leader_depth: Decimal
    follower_depth: Decimal
    regime: str = "unknown"
    volatility: Decimal = _ZERO
    feature_version: str = "ll_obs_v1"
    data_quality: str = "UNSUPPORTED"
    event_ts_source: str = "unknown"
    receive_ts_source: str = "local"
    notes: tuple[str, ...] = ()

    @property
    def leader_mid(self) -> Decimal:
        return (self.leader_bid + self.leader_ask) / Decimal("2")

    @property
    def follower_mid(self) -> Decimal:
        return (self.follower_bid + self.follower_ask) / Decimal("2")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        d["notes"] = list(self.notes)
        return d


@dataclass(frozen=True, slots=True)
class LeadLagSignal:
    """Causal prediction produced at decision time (no future labels)."""

    decision_timestamp_ms: float
    symbol: str
    leader_venue: str
    follower_venue: str
    horizon_ms: int
    predicted_follower_move_bps: Decimal
    uncertainty_bps: Decimal
    signal_strength: Decimal
    model_version: str
    evidence_sample_count: int
    leader_return_bps: Decimal = _ZERO
    feature_version: str = "ll_sig_v1"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


@dataclass(frozen=True, slots=True)
class HedgeLeg:
    venue: str
    symbol: str
    side: str
    executable_price: Decimal
    quantity: Decimal
    depth_available: Decimal
    fees_eur: Decimal
    slippage_eur: Decimal
    delay_ms: float
    feasible: bool
    failure_state: str = ""

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        return d


@dataclass(frozen=True, slots=True)
class LeadLagOpportunity:
    """Shadow executable opportunity — never production by default."""

    signal: LeadLagSignal
    entry_side: str  # buy/sell on follower
    entry_venue: str
    executable_entry_price: Decimal
    executable_quantity: Decimal
    hedge: HedgeLeg | None
    gross_predicted_edge_eur: Decimal
    fees_eur: Decimal
    slippage_eur: Decimal
    latency_haircut_eur: Decimal
    hedge_haircut_eur: Decimal
    other_costs_eur: Decimal
    expected_net_eur: Decimal
    conservative_net_eur: Decimal
    capital_required_eur: Decimal
    estimated_capital_lock_ms: float
    hedge_mode: str = "FULLY_HEDGED"
    state: str = "OBSERVED"
    first_gate: str = ""
    latency_scenario_ms: float = 0.0
    observational: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal.as_dict(),
            "entry_side": self.entry_side,
            "entry_venue": self.entry_venue,
            "executable_entry_price": str(self.executable_entry_price),
            "executable_quantity": str(self.executable_quantity),
            "hedge": self.hedge.as_dict() if self.hedge else None,
            "gross_predicted_edge_eur": str(self.gross_predicted_edge_eur),
            "fees_eur": str(self.fees_eur),
            "slippage_eur": str(self.slippage_eur),
            "latency_haircut_eur": str(self.latency_haircut_eur),
            "hedge_haircut_eur": str(self.hedge_haircut_eur),
            "other_costs_eur": str(self.other_costs_eur),
            "expected_net_eur": str(self.expected_net_eur),
            "conservative_net_eur": str(self.conservative_net_eur),
            "capital_required_eur": str(self.capital_required_eur),
            "estimated_capital_lock_ms": self.estimated_capital_lock_ms,
            "hedge_mode": self.hedge_mode,
            "state": self.state,
            "first_gate": self.first_gate,
            "latency_scenario_ms": self.latency_scenario_ms,
            "observational": self.observational,
        }


@dataclass(frozen=True, slots=True)
class LeadLagOutcome:
    """Future evaluation after a frozen decision (reveal only after horizon)."""

    decision_timestamp_ms: float
    symbol: str
    leader_venue: str
    follower_venue: str
    horizon_ms: int
    follower_move_bps: dict[str, Decimal] = field(default_factory=dict)
    realized_directional_correct: bool | None = None
    hypothetical_net_eur: Decimal | None = None
    conservative_threshold_passed: bool | None = None
    signal_correct: bool | None = None
    available_at_ms: float = 0.0
    label: str = "SHADOW_COUNTERFACTUAL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_timestamp_ms": self.decision_timestamp_ms,
            "symbol": self.symbol,
            "leader_venue": self.leader_venue,
            "follower_venue": self.follower_venue,
            "horizon_ms": self.horizon_ms,
            "follower_move_bps": {k: str(v) for k, v in self.follower_move_bps.items()},
            "realized_directional_correct": self.realized_directional_correct,
            "hypothetical_net_eur": (
                str(self.hypothetical_net_eur) if self.hypothetical_net_eur is not None else None
            ),
            "conservative_threshold_passed": self.conservative_threshold_passed,
            "signal_correct": self.signal_correct,
            "available_at_ms": self.available_at_ms,
            "label": self.label,
        }
