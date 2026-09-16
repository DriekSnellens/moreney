"""Mergeable streaming accumulators for execution realism.

Hot-path research state: primitive counts + Decimal sums. No Pydantic.
Does not retain ExecutionWaterfall objects.

Drawdown is sequential. Additive merge of two window accumulators updates
sums/counts exactly; global max drawdown is reconstructed by the reducer
from compact per-signal execution_net lists stored on disk, not by taking
min(window_max_drawdowns).
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Any, Iterable, Mapping, Sequence

from bot.research.accounting.protocol import WATERFALL_TOLERANCE
from bot.research.execution_realism.models import ExecutionWaterfall, FillStatus

_ZERO = Decimal("0")
# Default Decimal precision is 28 significant digits. Grouped window sums
# vs a single pass would otherwise disagree at the last ULP. Research
# accumulators use a wider context so additive merges stay exact.
_ACC_PREC = 80


@contextmanager
def _exact():
    with localcontext() as ctx:
        ctx.prec = _ACC_PREC
        yield


def _d(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return _ZERO
    return Decimal(str(value))


def _add_counts(dst: dict[str, int], src: Mapping[str, int]) -> None:
    for key, val in src.items():
        dst[key] = dst.get(key, 0) + int(val)


@dataclass(slots=True)
class ExecutionAccumulator:
    """Incremental sufficient statistics for one scenario (one window or global)."""

    signal_count: int = 0
    fill_count: int = 0
    partial_count: int = 0
    no_fill_count: int = 0
    cancelled_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    accounting_failures: int = 0

    gross_sum: Decimal = _ZERO
    maker_fee_sum: Decimal = _ZERO
    taker_fee_sum: Decimal = _ZERO
    fee_sum: Decimal = _ZERO
    slippage_sum: Decimal = _ZERO
    latency_sum: Decimal = _ZERO
    queue_sum: Decimal = _ZERO
    partial_fill_cost_sum: Decimal = _ZERO
    adverse_sum: Decimal = _ZERO
    hedge_sum: Decimal = _ZERO
    inventory_sum: Decimal = _ZERO
    canonical_replay_net_sum: Decimal = _ZERO
    expected_net_sum: Decimal = _ZERO
    execution_net_sum: Decimal = _ZERO

    running_equity: Decimal = _ZERO
    peak_equity: Decimal = _ZERO
    max_drawdown: Decimal = _ZERO

    outcome_counts: dict[str, int] = field(default_factory=dict)
    route_counts: dict[str, int] = field(default_factory=dict)
    symbol_counts: dict[str, int] = field(default_factory=dict)

    def observe(
        self,
        wf: ExecutionWaterfall,
        *,
        canonical_net: Decimal | None = None,
    ) -> None:
        """Ingest one ephemeral waterfall and drop all references to it after return."""
        with _exact():
            self.signal_count += 1
            status = wf.fill_status
            if status in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL):
                self.fill_count += 1
            if status == FillStatus.PARTIAL_FILL:
                self.partial_count += 1
            elif status == FillStatus.NO_FILL:
                self.no_fill_count += 1
            elif status == FillStatus.CANCELLED:
                self.cancelled_count += 1

            self.gross_sum += wf.gross_spread
            self.maker_fee_sum += wf.maker_fees
            self.taker_fee_sum += wf.taker_fees
            self.fee_sum += wf.maker_fees + wf.taker_fees
            self.slippage_sum += wf.slippage
            self.latency_sum += wf.latency_cost
            self.queue_sum += wf.queue_cost
            self.partial_fill_cost_sum += wf.partial_fill_cost
            self.adverse_sum += wf.adverse_selection
            self.hedge_sum += wf.hedge_cost
            self.inventory_sum += wf.residual_inventory_cost
            self.execution_net_sum += wf.execution_net
            self.expected_net_sum += wf.signal_expected_net
            if canonical_net is not None:
                self.canonical_replay_net_sum += canonical_net

            if wf.execution_net > 0:
                self.positive_count += 1
            elif wf.execution_net < 0:
                self.negative_count += 1

            outcome = wf.outcome.value if hasattr(wf.outcome, "value") else str(wf.outcome)
            self.outcome_counts[outcome] = self.outcome_counts.get(outcome, 0) + 1
            if wf.timeline is not None:
                self.symbol_counts[wf.timeline.symbol] = self.symbol_counts.get(wf.timeline.symbol, 0) + 1
                self.route_counts[wf.timeline.route] = self.route_counts.get(wf.timeline.route, 0) + 1

            residual = wf.waterfall_residual()
            if abs(residual) > WATERFALL_TOLERANCE:
                self.accounting_failures += 1

            self.observe_drawdown(wf.execution_net)

    def observe_drawdown(self, pnl: Decimal) -> None:
        """Streaming max drawdown. Equivalent to a full equity-curve scan."""
        with _exact():
            self.running_equity += pnl
            if self.running_equity > self.peak_equity:
                self.peak_equity = self.running_equity
            drawdown = self.running_equity - self.peak_equity
            if drawdown < self.max_drawdown:
                self.max_drawdown = drawdown

    def add_sums_from(self, other: ExecutionAccumulator) -> None:
        """Merge additive statistics. Does not merge sequential drawdown state."""
        with _exact():
            self.signal_count += other.signal_count
            self.fill_count += other.fill_count
            self.partial_count += other.partial_count
            self.no_fill_count += other.no_fill_count
            self.cancelled_count += other.cancelled_count
            self.positive_count += other.positive_count
            self.negative_count += other.negative_count
            self.accounting_failures += other.accounting_failures
            self.gross_sum += other.gross_sum
            self.maker_fee_sum += other.maker_fee_sum
            self.taker_fee_sum += other.taker_fee_sum
            self.fee_sum += other.fee_sum
            self.slippage_sum += other.slippage_sum
            self.latency_sum += other.latency_sum
            self.queue_sum += other.queue_sum
            self.partial_fill_cost_sum += other.partial_fill_cost_sum
            self.adverse_sum += other.adverse_sum
            self.hedge_sum += other.hedge_sum
            self.inventory_sum += other.inventory_sum
            self.canonical_replay_net_sum += other.canonical_replay_net_sum
            self.expected_net_sum += other.expected_net_sum
            self.execution_net_sum += other.execution_net_sum
            _add_counts(self.outcome_counts, other.outcome_counts)
            _add_counts(self.route_counts, other.route_counts)
            _add_counts(self.symbol_counts, other.symbol_counts)

    def merge(self, other: ExecutionAccumulator) -> ExecutionAccumulator:
        """Return a new accumulator with additive stats. Drawdown is NOT combined.

        Sequential drawdown must be reconstructed by observing pnl in order
        (see observe_drawdown / reducer). The returned object copies this
        instance's drawdown state unchanged.
        """
        out = self.copy()
        out.add_sums_from(other)
        return out

    def copy(self) -> ExecutionAccumulator:
        return ExecutionAccumulator(
            signal_count=self.signal_count,
            fill_count=self.fill_count,
            partial_count=self.partial_count,
            no_fill_count=self.no_fill_count,
            cancelled_count=self.cancelled_count,
            positive_count=self.positive_count,
            negative_count=self.negative_count,
            accounting_failures=self.accounting_failures,
            gross_sum=self.gross_sum,
            maker_fee_sum=self.maker_fee_sum,
            taker_fee_sum=self.taker_fee_sum,
            fee_sum=self.fee_sum,
            slippage_sum=self.slippage_sum,
            latency_sum=self.latency_sum,
            queue_sum=self.queue_sum,
            partial_fill_cost_sum=self.partial_fill_cost_sum,
            adverse_sum=self.adverse_sum,
            hedge_sum=self.hedge_sum,
            inventory_sum=self.inventory_sum,
            canonical_replay_net_sum=self.canonical_replay_net_sum,
            expected_net_sum=self.expected_net_sum,
            execution_net_sum=self.execution_net_sum,
            running_equity=self.running_equity,
            peak_equity=self.peak_equity,
            max_drawdown=self.max_drawdown,
            outcome_counts=dict(self.outcome_counts),
            route_counts=dict(self.route_counts),
            symbol_counts=dict(self.symbol_counts),
        )

    @property
    def accounting_identity_status(self) -> str:
        return "PASS" if self.accounting_failures == 0 else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_count": self.signal_count,
            "fill_count": self.fill_count,
            "partial_count": self.partial_count,
            "no_fill_count": self.no_fill_count,
            "cancelled_count": self.cancelled_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "accounting_failures": self.accounting_failures,
            "accounting_identity_status": self.accounting_identity_status,
            "gross_sum": str(self.gross_sum),
            "maker_fee_sum": str(self.maker_fee_sum),
            "taker_fee_sum": str(self.taker_fee_sum),
            "fee_sum": str(self.fee_sum),
            "slippage_sum": str(self.slippage_sum),
            "latency_sum": str(self.latency_sum),
            "queue_sum": str(self.queue_sum),
            "partial_fill_cost_sum": str(self.partial_fill_cost_sum),
            "adverse_sum": str(self.adverse_sum),
            "hedge_sum": str(self.hedge_sum),
            "inventory_sum": str(self.inventory_sum),
            "canonical_replay_net_sum": str(self.canonical_replay_net_sum),
            "expected_net_sum": str(self.expected_net_sum),
            "execution_net_sum": str(self.execution_net_sum),
            "running_equity": str(self.running_equity),
            "peak_equity": str(self.peak_equity),
            "max_drawdown": str(self.max_drawdown),
            "outcome_counts": dict(self.outcome_counts),
            "route_counts": dict(self.route_counts),
            "symbol_counts": dict(self.symbol_counts),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionAccumulator:
        return cls(
            signal_count=int(payload.get("signal_count") or 0),
            fill_count=int(payload.get("fill_count") or 0),
            partial_count=int(payload.get("partial_count") or 0),
            no_fill_count=int(payload.get("no_fill_count") or 0),
            cancelled_count=int(payload.get("cancelled_count") or 0),
            positive_count=int(payload.get("positive_count") or 0),
            negative_count=int(payload.get("negative_count") or 0),
            accounting_failures=int(payload.get("accounting_failures") or 0),
            gross_sum=_d(payload.get("gross_sum")),
            maker_fee_sum=_d(payload.get("maker_fee_sum")),
            taker_fee_sum=_d(payload.get("taker_fee_sum")),
            fee_sum=_d(payload.get("fee_sum")),
            slippage_sum=_d(payload.get("slippage_sum")),
            latency_sum=_d(payload.get("latency_sum")),
            queue_sum=_d(payload.get("queue_sum")),
            partial_fill_cost_sum=_d(payload.get("partial_fill_cost_sum")),
            adverse_sum=_d(payload.get("adverse_sum")),
            hedge_sum=_d(payload.get("hedge_sum")),
            inventory_sum=_d(payload.get("inventory_sum")),
            canonical_replay_net_sum=_d(payload.get("canonical_replay_net_sum")),
            expected_net_sum=_d(payload.get("expected_net_sum")),
            execution_net_sum=_d(payload.get("execution_net_sum")),
            running_equity=_d(payload.get("running_equity")),
            peak_equity=_d(payload.get("peak_equity")),
            max_drawdown=_d(payload.get("max_drawdown")),
            outcome_counts=dict(payload.get("outcome_counts") or {}),
            route_counts=dict(payload.get("route_counts") or {}),
            symbol_counts=dict(payload.get("symbol_counts") or {}),
        )

    def sums_fingerprint(self) -> str:
        """Deterministic hash of exact additive statistics (not running drawdown path)."""
        payload = {
            "signal_count": self.signal_count,
            "fill_count": self.fill_count,
            "partial_count": self.partial_count,
            "no_fill_count": self.no_fill_count,
            "cancelled_count": self.cancelled_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "accounting_failures": self.accounting_failures,
            "gross_sum": str(self.gross_sum),
            "fee_sum": str(self.fee_sum),
            "slippage_sum": str(self.slippage_sum),
            "adverse_sum": str(self.adverse_sum),
            "inventory_sum": str(self.inventory_sum),
            "canonical_replay_net_sum": str(self.canonical_replay_net_sum),
            "expected_net_sum": str(self.expected_net_sum),
            "execution_net_sum": str(self.execution_net_sum),
            "max_drawdown": str(self.max_drawdown),
            "outcome_counts": dict(sorted(self.outcome_counts.items())),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def equity_curve_max_drawdown(pnls: Sequence[Decimal]) -> Decimal:
    """Legacy full-curve max drawdown. Starting equity is 0 (counts as a peak)."""
    with _exact():
        equity: list[Decimal] = []
        running = _ZERO
        for pnl in pnls:
            running += pnl
            equity.append(running)
        if not equity:
            return _ZERO
        peak = _ZERO
        max_dd = _ZERO
        for eq in equity:
            if eq > peak:
                peak = eq
            dd = eq - peak
            if dd < max_dd:
                max_dd = dd
        return max_dd


def streaming_max_drawdown(pnls: Iterable[Decimal]) -> Decimal:
    acc = ExecutionAccumulator()
    for pnl in pnls:
        acc.observe_drawdown(pnl)
    return acc.max_drawdown


def accumulators_equal(a: ExecutionAccumulator, b: ExecutionAccumulator) -> bool:
    return (
        a.signal_count == b.signal_count
        and a.fill_count == b.fill_count
        and a.partial_count == b.partial_count
        and a.no_fill_count == b.no_fill_count
        and a.gross_sum == b.gross_sum
        and a.fee_sum == b.fee_sum
        and a.slippage_sum == b.slippage_sum
        and a.adverse_sum == b.adverse_sum
        and a.inventory_sum == b.inventory_sum
        and a.canonical_replay_net_sum == b.canonical_replay_net_sum
        and a.expected_net_sum == b.expected_net_sum
        and a.execution_net_sum == b.execution_net_sum
        and a.max_drawdown == b.max_drawdown
        and a.outcome_counts == b.outcome_counts
    )
