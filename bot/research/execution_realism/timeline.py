"""Construct causal execution timelines from signal events + latency scenarios."""

from __future__ import annotations

from typing import Any

from bot.research.execution_realism.config import LATENCY_SCENARIOS
from bot.research.execution_realism.models import ExecutionTimeline


def _ms_to_ns(ms: float) -> int:
    return int(ms * 1_000_000)


def build_timeline(
    *,
    signal_id: str,
    strategy_id: str,
    symbol: str,
    route: str,
    observed_at_ns: int,
    latency_scenario: str,
    has_fill: bool = True,
    fill_at_ns: int | None = None,
    cancel: bool = False,
    hedge: bool = True,
    hedge_delay_ms: float = 0.0,
    exchange_ts_available: bool = False,
) -> ExecutionTimeline:
    lat = LATENCY_SCENARIOS.get(latency_scenario) or LATENCY_SCENARIOS["NORMAL"]
    obs = int(observed_at_ns)
    decision = obs + _ms_to_ns(lat["observation_delay_ms"]) + _ms_to_ns(lat["decision_delay_ms"])
    order_send = decision + _ms_to_ns(lat["order_transmission_ms"])
    order_arrival = order_send + _ms_to_ns(lat["venue_processing_ms"])
    first_fill = order_arrival

    actual_fill = None
    if has_fill and fill_at_ns is not None:
        actual_fill = max(int(fill_at_ns), first_fill)
    elif has_fill:
        actual_fill = first_fill

    cancel_send = None
    cancel_eff = None
    if cancel:
        cancel_send = order_send + _ms_to_ns(lat["cancel_latency_ms"])
        cancel_eff = cancel_send + _ms_to_ns(lat["venue_processing_ms"])

    hedge_dec = None
    hedge_arr = None
    hedge_fill = None
    if hedge and actual_fill is not None:
        hedge_dec = actual_fill
        hedge_arr = hedge_dec + _ms_to_ns(hedge_delay_ms)
        hedge_fill = hedge_arr + _ms_to_ns(lat["venue_processing_ms"])

    flags: list[str] = []
    if not exchange_ts_available:
        flags.append("NO_EXCHANGE_TS")
        flags.append("ARRIVAL_ESTIMATED_FROM_NETWORK")

    return ExecutionTimeline(
        signal_id=signal_id,
        strategy_id=strategy_id,
        symbol=symbol,
        route=route,
        observed_at_ns=obs,
        decision_at_ns=decision,
        order_send_at_ns=order_send,
        order_arrival_at_ns=order_arrival,
        first_possible_fill_at_ns=first_fill,
        fill_at_ns=actual_fill,
        cancel_send_at_ns=cancel_send,
        cancel_effective_at_ns=cancel_eff,
        hedge_decision_at_ns=hedge_dec,
        hedge_arrival_at_ns=hedge_arr,
        hedge_fill_at_ns=hedge_fill,
        timeline_quality_flags=tuple(flags),
    )
