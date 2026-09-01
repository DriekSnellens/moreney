"""Core data models for execution realism simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

_ZERO = Decimal("0")


class FillStatus(StrEnum):
    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SignalOutcome(StrEnum):
    SURVIVES_REALISTIC_EXECUTION = "SURVIVES_REALISTIC_EXECUTION"
    PARTIAL_PROFIT = "PARTIAL_PROFIT"
    NO_FILL = "NO_FILL"
    LATENCY_KILLED = "LATENCY_KILLED"
    HEDGE_KILLED = "HEDGE_KILLED"
    DEPTH_KILLED = "DEPTH_KILLED"
    ADVERSE_KILLED = "ADVERSE_KILLED"
    TIMESTAMP_UNCERTAIN = "TIMESTAMP_UNCERTAIN"


class Verdict(StrEnum):
    DATA_NOT_READY = "DATA_NOT_READY"
    RUNNING = "RUNNING"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_FRAGILE = "EXECUTION_FRAGILE"
    PROMISING_EXECUTION = "PROMISING_EXECUTION"
    ROBUST_EXECUTION_CANDIDATE = "ROBUST_EXECUTION_CANDIDATE"


@dataclass(frozen=True, slots=True)
class ExecutionTimeline:
    """Explicit causal timeline for one signal's execution attempt."""

    signal_id: str
    strategy_id: str
    symbol: str
    route: str

    observed_at_ns: int
    decision_at_ns: int

    order_send_at_ns: int
    order_arrival_at_ns: int

    first_possible_fill_at_ns: int
    fill_at_ns: int | None  # None = no fill

    cancel_send_at_ns: int | None
    cancel_effective_at_ns: int | None

    hedge_decision_at_ns: int | None
    hedge_arrival_at_ns: int | None
    hedge_fill_at_ns: int | None

    timeline_quality_flags: tuple[str, ...] = ()

    def is_causal(self) -> bool:
        if self.decision_at_ns < self.observed_at_ns:
            return False
        if self.order_send_at_ns < self.decision_at_ns:
            return False
        if self.order_arrival_at_ns < self.order_send_at_ns:
            return False
        if self.first_possible_fill_at_ns < self.order_arrival_at_ns:
            return False
        if self.fill_at_ns is not None and self.fill_at_ns < self.first_possible_fill_at_ns:
            return False
        if self.cancel_effective_at_ns is not None and self.cancel_send_at_ns is not None:
            if self.cancel_effective_at_ns < self.cancel_send_at_ns:
                return False
        if self.hedge_arrival_at_ns is not None and self.hedge_decision_at_ns is not None:
            if self.hedge_arrival_at_ns < self.hedge_decision_at_ns:
                return False
        return True


@dataclass(frozen=True, slots=True)
class FillResult:
    """Outcome of a single fill attempt under a specific model."""

    fill_model: str
    status: FillStatus
    requested_notional: Decimal
    filled_notional: Decimal
    remaining_notional: Decimal
    fill_price: Decimal | None
    market_mid_at_fill: Decimal | None
    slippage_bps: Decimal
    available_depth: Decimal | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HedgeResult:
    """Outcome of the hedge leg."""

    hedge_scenario: str
    hedge_delay_ms: float
    hedge_price: Decimal | None
    market_mid_at_hedge: Decimal | None
    hedge_slippage_bps: Decimal
    hedge_adverse_bps: Decimal
    hedge_cost_eur: Decimal
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class ExecutionWaterfall:
    """Full per-signal execution economics. Waterfall identity must hold exactly."""

    signal_id: str
    scenario_id: str

    signal_expected_net: Decimal = _ZERO
    requested_notional: Decimal = _ZERO
    filled_notional: Decimal = _ZERO

    gross_spread: Decimal = _ZERO
    maker_fees: Decimal = _ZERO
    taker_fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    latency_cost: Decimal = _ZERO
    queue_cost: Decimal = _ZERO
    partial_fill_cost: Decimal = _ZERO
    adverse_selection: Decimal = _ZERO
    hedge_cost: Decimal = _ZERO
    residual_inventory_cost: Decimal = _ZERO

    execution_net: Decimal = _ZERO

    fill_status: FillStatus = FillStatus.NO_FILL
    outcome: SignalOutcome = SignalOutcome.NO_FILL
    timeline: ExecutionTimeline | None = None
    fill_result: FillResult | None = None
    hedge_result: HedgeResult | None = None

    def waterfall_residual(self) -> Decimal:
        if self.fill_status == FillStatus.NO_FILL:
            return _ZERO
        computed = (
            self.gross_spread
            - self.maker_fees
            - self.taker_fees
            - self.slippage
            - self.latency_cost
            - self.queue_cost
            - self.partial_fill_cost
            - self.adverse_selection
            - self.hedge_cost
            - self.residual_inventory_cost
        )
        return computed - self.execution_net

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "scenario_id": self.scenario_id,
            "fill_status": self.fill_status.value,
            "outcome": self.outcome.value,
            "requested_notional": str(self.requested_notional),
            "filled_notional": str(self.filled_notional),
            "gross_spread": str(self.gross_spread),
            "maker_fees": str(self.maker_fees),
            "taker_fees": str(self.taker_fees),
            "slippage": str(self.slippage),
            "latency_cost": str(self.latency_cost),
            "queue_cost": str(self.queue_cost),
            "partial_fill_cost": str(self.partial_fill_cost),
            "adverse_selection": str(self.adverse_selection),
            "hedge_cost": str(self.hedge_cost),
            "residual_inventory_cost": str(self.residual_inventory_cost),
            "execution_net": str(self.execution_net),
            "waterfall_residual": str(self.waterfall_residual()),
        }


@dataclass(slots=True)
class ScenarioResult:
    """Aggregate result of one scenario configuration across all signals."""

    scenario_id: str
    fill_model: str
    latency_scenario: str
    hedge_scenario: str
    cancel_scenario: str

    n_signals: int = 0
    n_fills: int = 0
    n_partial: int = 0
    n_no_fill: int = 0
    n_cancelled: int = 0

    fill_rate: float = 0.0
    partial_fill_rate: float = 0.0

    execution_net_eur: Decimal = _ZERO
    execution_net_per_signal: Decimal | None = None
    execution_net_per_fill: Decimal | None = None
    canonical_replay_net_eur: Decimal = _ZERO
    delta_eur: Decimal = _ZERO

    positive_windows: int = 0
    negative_windows: int = 0
    median_window_net: Decimal | None = None

    outcome_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "fill_model": self.fill_model,
            "latency_scenario": self.latency_scenario,
            "hedge_scenario": self.hedge_scenario,
            "cancel_scenario": self.cancel_scenario,
            "n_signals": self.n_signals,
            "n_fills": self.n_fills,
            "n_partial": self.n_partial,
            "n_no_fill": self.n_no_fill,
            "n_cancelled": self.n_cancelled,
            "fill_rate": self.fill_rate,
            "partial_fill_rate": self.partial_fill_rate,
            "execution_net_eur": str(self.execution_net_eur),
            "execution_net_per_signal": (
                None if self.execution_net_per_signal is None else str(self.execution_net_per_signal)
            ),
            "execution_net_per_fill": (
                None if self.execution_net_per_fill is None else str(self.execution_net_per_fill)
            ),
            "canonical_replay_net_eur": str(self.canonical_replay_net_eur),
            "delta_eur": str(self.delta_eur),
            "positive_windows": self.positive_windows,
            "negative_windows": self.negative_windows,
            "median_window_net": (
                None if self.median_window_net is None else str(self.median_window_net)
            ),
            "outcome_counts": dict(self.outcome_counts),
        }
