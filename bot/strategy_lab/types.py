"""Shared types for the Strategy Research Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


_ZERO = Decimal("0")


class LabStatus(StrEnum):
    RESEARCH = "RESEARCH"
    WARMUP = "WARMUP"
    DEVELOPMENT = "DEVELOPMENT"
    FROZEN = "FROZEN"
    OOS = "OOS"
    PAPER = "PAPER"
    FAILED = "FAILED"
    PROMISING = "PROMISING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DecisionAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    SKIP = "SKIP"
    CONTROL = "CONTROL"


@dataclass(frozen=True, slots=True)
class MarketEventView:
    """Causal market snapshot visible at decision time (no future)."""

    event_id: str
    ts_ns: int
    venue: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    bid_levels: tuple[tuple[Decimal, Decimal], ...]  # (price, qty)
    ask_levels: tuple[tuple[Decimal, Decimal], ...]
    sequence: int | None = None
    exchange_ts_ns: int | None = None
    received_ts_ns: int | None = None
    mid: Decimal | None = None
    funding_rate: Decimal | None = None

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class CycleSnapshot:
    """All venues/symbols visible at one research clock tick."""

    cycle_id: str
    ts_ns: int
    books: tuple[MarketEventView, ...]
    label: str = "DEVELOPMENT"  # DEVELOPMENT | OOS | FULL


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Common NET decomposition — one source of truth shape."""

    gross_edge_eur: Decimal = _ZERO
    fees_eur: Decimal = _ZERO
    slippage_eur: Decimal = _ZERO
    adverse_latency_eur: Decimal = _ZERO
    funding_eur: Decimal = _ZERO
    hedge_other_eur: Decimal = _ZERO
    net_eur: Decimal = _ZERO
    conservative_net_eur: Decimal = _ZERO

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """One strategy decision on one cycle (causal: decide before outcome)."""

    strategy_id: str
    strategy_version: str
    cycle_id: str
    ts_ns: int
    symbol: str
    venue: str
    route: str
    action: DecisionAction
    reject_reason: str | None
    expected_edge_eur: Decimal
    costs: CostBreakdown
    capital_required_eur: Decimal
    estimated_capital_lock_ms: float
    uncertainty: Decimal
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def conservative_net_eur(self) -> Decimal:
        return self.costs.conservative_net_eur

    @property
    def net_eur_per_capital_second(self) -> Decimal:
        if self.capital_required_eur <= 0:
            return _ZERO
        lock_s = max(Decimal(str(self.estimated_capital_lock_ms)) / Decimal("1000"), Decimal("0.001"))
        return self.costs.conservative_net_eur / (self.capital_required_eur * lock_s)


@dataclass
class StrategyOutcome:
    """Observed outcome after predeclared horizon (never feeds back into decide)."""

    decision_key: str
    realized_net_eur: Decimal
    realized_gross_eur: Decimal
    realized_fees_eur: Decimal
    realized_slippage_eur: Decimal
    realized_adverse_eur: Decimal
    filled: bool
    independent_event_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scorecard:
    """Standardized strategy scorecard."""

    strategy_id: str
    strategy_version: str
    status: str
    phase: str  # DEVELOPMENT | OOS | FULL
    opportunities: int = 0
    accepted: int = 0
    rejected: int = 0
    completed: int = 0
    winning: int = 0
    losing: int = 0
    win_rate: float = 0.0
    gross_pnl_eur: Decimal = _ZERO
    fees_eur: Decimal = _ZERO
    slippage_eur: Decimal = _ZERO
    adverse_eur: Decimal = _ZERO
    net_pnl_eur: Decimal = _ZERO
    net_eur_per_fill: Decimal = _ZERO
    net_bps: Decimal = _ZERO
    expected_net_eur: Decimal = _ZERO
    realized_net_eur: Decimal = _ZERO
    ev_capture: float | None = None
    capital_used_eur: Decimal = _ZERO
    average_capital_lock_ms: float = 0.0
    capital_velocity: Decimal = _ZERO  # net_eur_per_capital_second aggregate
    max_drawdown_eur: Decimal = _ZERO
    worst_loss_eur: Decimal = _ZERO
    p95_loss_eur: Decimal = _ZERO
    opportunity_frequency: float = 0.0
    rejection_rate: float = 0.0
    participation_rate: float = 0.0
    baseline_opportunities: int = 0
    independent_events: int = 0
    oos_net_eur: Decimal | None = None
    oos_net_per_capital_second: Decimal | None = None
    oos_drawdown_eur: Decimal | None = None
    verdict: str = "RESEARCH"
    sharpe_like: float | None = None
    notes: list[str] = field(default_factory=list)
    waterfall: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            else:
                out[k] = v
        return out
