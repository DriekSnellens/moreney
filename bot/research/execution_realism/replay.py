"""Legacy in-memory replay vs streaming replay for one window of signals.

Streaming path: simulate → observe accumulator → record compact net string →
release the ExecutionWaterfall. Never append waterfalls to a list.

Legacy path: retain the full waterfall list. Tests and the small-fixture
benchmark use it; the default engine path does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any, Mapping, Sequence

from bot.research.execution_realism.accumulator import ExecutionAccumulator
from bot.research.execution_realism.config import NOTIONAL_EUR
from bot.research.execution_realism.execution_simulator import simulate_signal
from bot.research.execution_realism.models import ExecutionWaterfall, ScenarioResult
from bot.research.tournament.tape_index import SeriesPoint

_ZERO = Decimal("0")


@dataclass(slots=True)
class WindowSignal:
    """Immutable-enough work item for one parent signal inside one window.

    `points` is a read-only view of the causal tape slice. Fill models must
    not mutate it. Shared across scenarios of the same window.
    """

    signal_id: str
    strategy_id: str
    symbol: str
    route: str
    venue: str
    venue_exit: str | None
    side: str
    forward: float
    observed_at_ns: int
    entry_price: Decimal
    exchange_ts_available: bool
    canonical_net: Decimal
    points: Sequence[SeriesPoint]


def simulate_one(
    sig: WindowSignal,
    scenario: Mapping[str, str],
    *,
    notional: Decimal = NOTIONAL_EUR,
) -> ExecutionWaterfall:
    return simulate_signal(
        signal_id=sig.signal_id,
        strategy_id=sig.strategy_id,
        symbol=sig.symbol,
        route=sig.route,
        venue=sig.venue,
        venue_exit=sig.venue_exit,
        side=sig.side,
        forward=sig.forward,
        observed_at_ns=sig.observed_at_ns,
        entry_price=sig.entry_price,
        points=sig.points,
        fill_model=scenario["fill_model"],
        latency_scenario=scenario["latency_scenario"],
        hedge_scenario=scenario["hedge_scenario"],
        cancel_scenario=scenario["cancel_scenario"],
        exchange_ts_available=sig.exchange_ts_available,
        notional=notional,
    )


def legacy_replay_window(
    signals: Sequence[WindowSignal],
    scenario: Mapping[str, str],
    *,
    notional: Decimal = NOTIONAL_EUR,
) -> list[ExecutionWaterfall]:
    """Retain every waterfall. Fixture / equivalence / benchmark only."""
    return [simulate_one(sig, scenario, notional=notional) for sig in signals]


def streaming_replay_window(
    signals: Sequence[WindowSignal],
    scenario: Mapping[str, str],
    *,
    notional: Decimal = NOTIONAL_EUR,
    accumulator: ExecutionAccumulator | None = None,
) -> tuple[ExecutionAccumulator, list[str]]:
    """Replay one scenario over one window without retaining waterfalls.

    Returns (accumulator, compact execution_net strings for exact drawdown
    reconstruction). The nets list is O(window signals) primitives, not objects.
    """
    acc = accumulator if accumulator is not None else ExecutionAccumulator()
    nets: list[str] = []
    for sig in signals:
        wf = simulate_one(sig, scenario, notional=notional)
        acc.observe(wf, canonical_net=sig.canonical_net)
        nets.append(str(wf.execution_net))
        del wf
    return acc, nets


def accumulator_from_waterfalls(
    waterfalls: Sequence[ExecutionWaterfall],
    signals: Sequence[WindowSignal] | None = None,
) -> ExecutionAccumulator:
    acc = ExecutionAccumulator()
    for i, wf in enumerate(waterfalls):
        canonical = None
        if signals is not None:
            canonical = signals[i].canonical_net
        acc.observe(wf, canonical_net=canonical)
    return acc


def build_scenario_result(
    scenario: Mapping[str, str],
    acc: ExecutionAccumulator,
    *,
    window_execution_nets: Sequence[Decimal],
    parent_canonical_net: Decimal,
) -> ScenarioResult:
    n = acc.signal_count
    fills = acc.fill_count
    exec_net = acc.execution_net_sum
    pos_w = sum(1 for x in window_execution_nets if x > 0)
    neg_w = sum(1 for x in window_execution_nets if x < 0)
    med_w = Decimal(str(median(float(x) for x in window_execution_nets))) if window_execution_nets else None
    return ScenarioResult(
        scenario_id=scenario["scenario_id"],
        fill_model=scenario["fill_model"],
        latency_scenario=scenario["latency_scenario"],
        hedge_scenario=scenario["hedge_scenario"],
        cancel_scenario=scenario["cancel_scenario"],
        n_signals=n,
        n_fills=fills,
        n_partial=acc.partial_count,
        n_no_fill=acc.no_fill_count,
        n_cancelled=acc.cancelled_count,
        fill_rate=fills / n if n else 0.0,
        partial_fill_rate=acc.partial_count / n if n else 0.0,
        execution_net_eur=exec_net,
        execution_net_per_signal=exec_net / Decimal(n) if n else None,
        execution_net_per_fill=exec_net / Decimal(fills) if fills else None,
        canonical_replay_net_eur=parent_canonical_net,
        delta_eur=exec_net - parent_canonical_net,
        positive_windows=pos_w,
        negative_windows=neg_w,
        median_window_net=med_w,
        outcome_counts=dict(acc.outcome_counts),
    )


def scenario_result_dict(
    scenario: Mapping[str, str],
    acc: ExecutionAccumulator,
    *,
    window_execution_nets: Sequence[Decimal],
    parent_canonical_net: Decimal,
) -> dict[str, Any]:
    sr = build_scenario_result(
        scenario,
        acc,
        window_execution_nets=window_execution_nets,
        parent_canonical_net=parent_canonical_net,
    )
    d = sr.to_dict()
    d["max_drawdown"] = str(acc.max_drawdown)
    d["gross_sum"] = str(acc.gross_sum)
    d["fee_sum"] = str(acc.fee_sum)
    d["slippage_sum"] = str(acc.slippage_sum)
    d["adverse_sum"] = str(acc.adverse_sum)
    d["inventory_sum"] = str(acc.inventory_sum)
    d["expected_net_sum"] = str(acc.expected_net_sum)
    d["accounting_identity_status"] = acc.accounting_identity_status
    d["deterministic_fingerprint"] = acc.sums_fingerprint()
    return d
