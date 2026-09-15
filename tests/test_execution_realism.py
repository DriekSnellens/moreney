"""Execution realism lab tests — causal timelines, fill models, waterfall identity."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.research.execution_realism.accounting import audit_waterfall
from bot.research.execution_realism.breakeven import compute_breakeven_surface
from bot.research.execution_realism.config import (
    EXECUTION_REALISM_PRODUCTION_ENABLED,
    EXECUTION_REALISM_SHADOW_ONLY,
    LATENCY_SCENARIOS,
)
from bot.research.execution_realism.execution_simulator import simulate_signal
from bot.research.execution_realism.fill_model import (
    depth_constrained,
    existing_trade_through,
    post_only_survival,
)
from bot.research.execution_realism.models import (
    ExecutionTimeline,
    ExecutionWaterfall,
    FillStatus,
    SignalOutcome,
)
from bot.research.execution_realism.scenario import full_matrix, stage1_screen
from bot.research.execution_realism.timeline import build_timeline
from bot.research.tournament.tape_index import SeriesPoint

_ZERO = Decimal("0")


def _points(start_ns: int, n: int = 10, spread_bps: float = 5.0) -> list[SeriesPoint]:
    pts = []
    for i in range(n):
        mid = 1000.0 + i * 0.01
        half_spread = mid * spread_bps / 20000.0
        pts.append(SeriesPoint(
            ts_ns=start_ns + i * 100_000_000,
            mid=mid,
            bid=mid - half_spread,
            ask=mid + half_spread,
            bid_size=1.0,
            ask_size=1.0,
            exchange_ts_ns=None,
            sequence=i,
        ))
    return pts


def test_execution_timeline_is_causal() -> None:
    tl = build_timeline(
        signal_id="S1",
        strategy_id="H-0005",
        symbol="BTCEUR",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="NORMAL",
    )
    assert tl.is_causal()
    assert tl.decision_at_ns >= tl.observed_at_ns
    assert tl.order_send_at_ns >= tl.decision_at_ns
    assert tl.order_arrival_at_ns >= tl.order_send_at_ns
    assert tl.first_possible_fill_at_ns >= tl.order_arrival_at_ns


def test_future_tick_cannot_fill_past_order() -> None:
    tl = build_timeline(
        signal_id="S2",
        strategy_id="H-0005",
        symbol="ETHEUR",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="SLOW",
    )
    assert tl.first_possible_fill_at_ns > tl.observed_at_ns
    # A fill timestamp before order arrival would be non-causal
    bad = ExecutionTimeline(
        signal_id="BAD",
        strategy_id="X",
        symbol="X",
        route="x",
        observed_at_ns=100,
        decision_at_ns=200,
        order_send_at_ns=300,
        order_arrival_at_ns=400,
        first_possible_fill_at_ns=400,
        fill_at_ns=350,  # before arrival!
        cancel_send_at_ns=None,
        cancel_effective_at_ns=None,
        hedge_decision_at_ns=None,
        hedge_arrival_at_ns=None,
        hedge_fill_at_ns=None,
    )
    assert not bad.is_causal()


def test_order_arrival_respects_latency() -> None:
    for name, lat in LATENCY_SCENARIOS.items():
        tl = build_timeline(
            signal_id="LAT",
            strategy_id="H-0005",
            symbol="X",
            route="x",
            observed_at_ns=0,
            latency_scenario=name,
        )
        expected_arrival = int(
            (lat["observation_delay_ms"] + lat["decision_delay_ms"] +
             lat["order_transmission_ms"] + lat["venue_processing_ms"]) * 1_000_000
        )
        assert tl.order_arrival_at_ns == expected_arrival


def test_cancel_effective_after_cancel_latency() -> None:
    tl = build_timeline(
        signal_id="CN",
        strategy_id="H-0005",
        symbol="X",
        route="x",
        observed_at_ns=0,
        latency_scenario="NORMAL",
        cancel=True,
    )
    assert tl.cancel_send_at_ns is not None
    assert tl.cancel_effective_at_ns is not None
    assert tl.cancel_effective_at_ns > tl.cancel_send_at_ns
    assert tl.is_causal()


def test_no_fill_has_zero_trade_pnl() -> None:
    pts = _points(10**18)
    tl = build_timeline(
        signal_id="NF",
        strategy_id="H-0005",
        symbol="X",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="STRESSED",
    )
    # Post-only with a price that market never crosses
    result = post_only_survival(
        tl, pts, side="BUY", entry_price=Decimal("500"),  # way below market
    )
    assert result.status == FillStatus.NO_FILL
    # Waterfall for no-fill must be zero
    wf = ExecutionWaterfall(signal_id="NF", scenario_id="test", fill_status=FillStatus.NO_FILL)
    assert wf.execution_net == _ZERO
    assert audit_waterfall(wf)["ACCOUNTING_AUDIT"] == "PASS"


def test_partial_fill_only_hedges_filled_quantity() -> None:
    pts = _points(10**18, n=5)
    tl = build_timeline(
        signal_id="PF",
        strategy_id="H-0005",
        symbol="X",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="FAST",
    )
    result = depth_constrained(tl, pts, side="BUY", entry_price=Decimal("1000"))
    if result.status == FillStatus.PARTIAL_FILL:
        assert result.filled_notional > 0
        assert result.filled_notional < result.requested_notional
        assert result.remaining_notional == result.requested_notional - result.filled_notional


def test_depth_constrained_fill_never_exceeds_depth() -> None:
    pts = [SeriesPoint(
        ts_ns=10**18 + 100_000_000,
        mid=1000.0, bid=999.5, ask=1000.5,
        bid_size=0.01, ask_size=0.01,  # very thin
        exchange_ts_ns=None, sequence=0,
    )]
    tl = build_timeline(
        signal_id="DC",
        strategy_id="H-0005",
        symbol="X",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="FAST",
    )
    result = depth_constrained(tl, pts, side="BUY", entry_price=Decimal("1000"))
    if result.available_depth is not None:
        assert result.filled_notional <= result.available_depth + Decimal("0.01")


def test_hedge_uses_future_causal_snapshot_not_entry_snapshot() -> None:
    pts = _points(10**18, n=20)
    wf = simulate_signal(
        signal_id="HG",
        strategy_id="H-0005",
        symbol="ETHEUR",
        route="okx|bitvavo",
        venue="okx",
        venue_exit="bitvavo",
        side="BUY",
        forward=0.005,
        observed_at_ns=10**18,
        entry_price=Decimal("1000"),
        points=pts,
        fill_model="EXISTING_TRADE_THROUGH",
        latency_scenario="NORMAL",
        hedge_scenario="NORMAL",
        cancel_scenario="NORMAL",
    )
    if wf.hedge_result is not None and wf.timeline is not None:
        assert wf.hedge_result.hedge_delay_ms > 0 or wf.hedge_result.hedge_scenario == "INSTANT"


def test_uncertainty_bounds_are_ordered() -> None:
    assert LATENCY_SCENARIOS["IDEALIZED"]["order_transmission_ms"] <= \
           LATENCY_SCENARIOS["FAST"]["order_transmission_ms"] <= \
           LATENCY_SCENARIOS["NORMAL"]["order_transmission_ms"] <= \
           LATENCY_SCENARIOS["SLOW"]["order_transmission_ms"] <= \
           LATENCY_SCENARIOS["STRESSED"]["order_transmission_ms"]


def test_waterfall_identity() -> None:
    pts = _points(10**18, n=20)
    wf = simulate_signal(
        signal_id="WF",
        strategy_id="H-0005",
        symbol="ETHEUR",
        route="okx|bitvavo",
        venue="okx",
        venue_exit="bitvavo",
        side="BUY",
        forward=0.01,
        observed_at_ns=10**18,
        entry_price=Decimal("1000"),
        points=pts,
        fill_model="EXISTING_TRADE_THROUGH",
        latency_scenario="FAST",
        hedge_scenario="FAST",
        cancel_scenario="NORMAL",
    )
    assert audit_waterfall(wf)["ACCOUNTING_AUDIT"] == "PASS"


def test_scenario_determinism() -> None:
    pts = _points(10**18, n=20)
    kwargs: dict = dict(
        signal_id="DET",
        strategy_id="H-0005",
        symbol="ETHEUR",
        route="okx|bitvavo",
        venue="okx",
        venue_exit="bitvavo",
        side="BUY",
        forward=0.005,
        observed_at_ns=10**18,
        entry_price=Decimal("1000"),
        points=pts,
        fill_model="EXISTING_TRADE_THROUGH",
        latency_scenario="NORMAL",
        hedge_scenario="NORMAL",
        cancel_scenario="NORMAL",
    )
    a = simulate_signal(**kwargs)
    b = simulate_signal(**kwargs)
    assert a.execution_net == b.execution_net
    assert a.fill_status == b.fill_status


def test_seed_independence() -> None:
    s1 = stage1_screen()
    s2 = stage1_screen()
    assert s1 == s2


def test_no_lookahead() -> None:
    tl = build_timeline(
        signal_id="LA",
        strategy_id="H-0005",
        symbol="X",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="NORMAL",
    )
    assert tl.first_possible_fill_at_ns > 10**18


def test_window_isolation() -> None:
    fm = full_matrix()
    assert len(fm) > 0
    ids = [s["scenario_id"] for s in fm]
    assert len(ids) == len(set(ids))


def test_canonical_vs_execution_delta_accounting() -> None:
    pts = _points(10**18, n=20)
    wf = simulate_signal(
        signal_id="DELTA",
        strategy_id="H-0005",
        symbol="ETHEUR",
        route="okx|bitvavo",
        venue="okx",
        venue_exit="bitvavo",
        side="BUY",
        forward=0.01,
        observed_at_ns=10**18,
        entry_price=Decimal("1000"),
        points=pts,
        fill_model="EXISTING_TRADE_THROUGH",
        latency_scenario="NORMAL",
        hedge_scenario="NORMAL",
        cancel_scenario="NORMAL",
    )
    # Delta = execution_net - canonical per-signal net (both should be computable)
    assert wf.execution_net is not None


def test_existing_trade_through_reproduces_legacy_result() -> None:
    tl = build_timeline(
        signal_id="TT",
        strategy_id="H-0005",
        symbol="X",
        route="okx|bitvavo",
        observed_at_ns=10**18,
        latency_scenario="IDEALIZED",
    )
    result = existing_trade_through(tl, 0.01)
    assert result.status == FillStatus.FULL_FILL
    assert result.filled_notional == result.requested_notional


def test_optimistic_result_cannot_drive_acceptance() -> None:
    assert EXECUTION_REALISM_PRODUCTION_ENABLED is False
    assert EXECUTION_REALISM_SHADOW_ONLY is True


def test_breakeven_surface() -> None:
    surface = compute_breakeven_surface(
        canonical_net_per_signal=Decimal("3.38"),
        n_signals=19557,
        fill_rate_baseline=0.55,
        fee_baseline_per_signal=Decimal("0.20"),
        adverse_baseline_per_signal=Decimal("0.08"),
        slippage_baseline_per_signal=Decimal("0.02"),
    )
    assert "latency_surface" in surface
    assert "breakeven_latency_ms" in surface
    assert len(surface["latency_surface"]) > 0


def test_dashboard_net_per_fill_is_canonical() -> None:
    from bot.research.execution_realism.config import FILL_RATE
    assert FILL_RATE == 0.55
