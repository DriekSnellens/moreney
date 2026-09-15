"""Core execution simulator: signal → timeline → fill → hedge → waterfall."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.research.execution_realism.config import (
    ADVERSE_BPS,
    CANCEL_DELAY_MS,
    HEDGE_DELAY_MS,
    LATENCY_SCENARIOS,
    NOTIONAL_EUR,
    SLIPPAGE_BPS,
)
from bot.research.execution_realism.fill_model import (
    depth_constrained,
    existing_trade_through,
    post_only_survival,
    uncertainty_bounded,
)
from bot.research.execution_realism.hedge_model import simulate_hedge
from bot.research.execution_realism.models import (
    ExecutionTimeline,
    ExecutionWaterfall,
    FillResult,
    FillStatus,
    SignalOutcome,
)
from bot.research.execution_realism.timeline import build_timeline
from bot.research.tournament.tape_index import SeriesPoint

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def simulate_signal(
    *,
    signal_id: str,
    strategy_id: str,
    symbol: str,
    route: str,
    venue: str,
    venue_exit: str | None,
    side: str,
    forward: float,
    observed_at_ns: int,
    entry_price: Decimal,
    points: Sequence[SeriesPoint],
    fill_model: str,
    latency_scenario: str,
    hedge_scenario: str,
    cancel_scenario: str,
    exchange_ts_available: bool = False,
    notional: Decimal = NOTIONAL_EUR,
) -> ExecutionWaterfall:
    """Simulate full execution lifecycle for one signal under one scenario."""
    hedge_delay = HEDGE_DELAY_MS.get(hedge_scenario, 50.0)
    lat = LATENCY_SCENARIOS.get(latency_scenario) or LATENCY_SCENARIOS["NORMAL"]

    timeline = build_timeline(
        signal_id=signal_id,
        strategy_id=strategy_id,
        symbol=symbol,
        route=route,
        observed_at_ns=observed_at_ns,
        latency_scenario=latency_scenario,
        has_fill=True,
        hedge=venue_exit is not None,
        hedge_delay_ms=hedge_delay,
        exchange_ts_available=exchange_ts_available,
    )

    assert timeline.is_causal(), f"Non-causal timeline for {signal_id}"

    # Run fill model
    if fill_model == "EXISTING_TRADE_THROUGH":
        fill = existing_trade_through(timeline, forward, notional=notional)
    elif fill_model == "POST_ONLY_SURVIVAL":
        fill = post_only_survival(
            timeline, points, side=side, entry_price=entry_price, notional=notional
        )
    elif fill_model == "DEPTH_CONSTRAINED":
        fill = depth_constrained(
            timeline, points, side=side, entry_price=entry_price, notional=notional
        )
    elif fill_model == "UNCERTAINTY_BOUNDED":
        fill = uncertainty_bounded(
            timeline, points, side=side, entry_price=entry_price, notional=notional,
            exchange_ts_available=exchange_ts_available,
        )
    else:
        raise ValueError(f"Unknown fill_model: {fill_model}")

    wf = ExecutionWaterfall(
        signal_id=signal_id,
        scenario_id=f"{fill_model}|{latency_scenario}|{hedge_scenario}|{cancel_scenario}",
        requested_notional=notional,
        filled_notional=fill.filled_notional,
        fill_status=fill.status,
        timeline=timeline,
        fill_result=fill,
    )

    if fill.status == FillStatus.NO_FILL:
        wf.outcome = SignalOutcome.NO_FILL
        wf.execution_net = _ZERO
        return wf

    filled = fill.filled_notional
    fwd = Decimal(str(forward))
    gross = filled * fwd
    wf.gross_spread = gross

    # Fees: maker on entry, taker on hedge (conservative)
    maker_rate = Decimal(str(venue_maker_fee(venue)))
    taker_rate = Decimal(str(venue_taker_fee(venue_exit or venue)))
    wf.maker_fees = filled * maker_rate
    wf.taker_fees = filled * taker_rate

    # Slippage from fill model
    wf.slippage = filled * fill.slippage_bps / _BPS

    # Latency cost: proportional to total latency
    total_lat_ms = sum(lat.values())
    wf.latency_cost = filled * Decimal(str(total_lat_ms * 0.01)) / _BPS

    # Adverse selection (model constant from canonical)
    wf.adverse_selection = filled * Decimal(str(ADVERSE_BPS)) / _BPS

    # Partial fill cost
    if fill.status == FillStatus.PARTIAL_FILL:
        wf.partial_fill_cost = fill.remaining_notional * Decimal(str(SLIPPAGE_BPS)) / _BPS
        wf.outcome = SignalOutcome.PARTIAL_PROFIT
    else:
        wf.partial_fill_cost = _ZERO

    # Hedge
    hedge_result = None
    if venue_exit is not None and timeline.fill_at_ns is not None:
        entry_mid = entry_price if entry_price > 0 else Decimal("1")
        hedge_result = simulate_hedge(
            hedge_scenario=hedge_scenario,
            fill_at_ns=timeline.fill_at_ns,
            filled_notional=filled,
            entry_mid=entry_mid,
            side=side,
            points=points,
            venue_processing_ms=lat["venue_processing_ms"],
        )
        wf.hedge_cost = hedge_result.hedge_cost_eur
        wf.hedge_result = hedge_result

    # Residual inventory cost for partial fills
    if fill.status == FillStatus.PARTIAL_FILL:
        wf.residual_inventory_cost = fill.remaining_notional * Decimal(str(ADVERSE_BPS)) / _BPS

    # Execution net
    wf.execution_net = (
        wf.gross_spread
        - wf.maker_fees
        - wf.taker_fees
        - wf.slippage
        - wf.latency_cost
        - wf.queue_cost
        - wf.partial_fill_cost
        - wf.adverse_selection
        - wf.hedge_cost
        - wf.residual_inventory_cost
    )

    # Classify outcome
    if wf.execution_net <= 0:
        if wf.hedge_cost > abs(wf.execution_net) * Decimal("0.5"):
            wf.outcome = SignalOutcome.HEDGE_KILLED
        elif wf.latency_cost > abs(wf.execution_net) * Decimal("0.5"):
            wf.outcome = SignalOutcome.LATENCY_KILLED
        elif wf.adverse_selection > abs(wf.execution_net) * Decimal("0.5"):
            wf.outcome = SignalOutcome.ADVERSE_KILLED
        elif fill.status == FillStatus.PARTIAL_FILL:
            wf.outcome = SignalOutcome.DEPTH_KILLED
        elif "TIMESTAMP_UNCERTAIN" in (fill.notes or ()):
            wf.outcome = SignalOutcome.TIMESTAMP_UNCERTAIN
        else:
            wf.outcome = SignalOutcome.LATENCY_KILLED
    elif fill.status == FillStatus.PARTIAL_FILL:
        wf.outcome = SignalOutcome.PARTIAL_PROFIT
    else:
        wf.outcome = SignalOutcome.SURVIVES_REALISTIC_EXECUTION

    if not exchange_ts_available and wf.outcome == SignalOutcome.SURVIVES_REALISTIC_EXECUTION:
        wf.outcome = SignalOutcome.TIMESTAMP_UNCERTAIN

    return wf
