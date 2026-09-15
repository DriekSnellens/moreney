"""Shared research economics — same fees/costs for every family."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.venue_fees import venue_taker_fee
from bot.opportunity.waterfall import expected_waterfall
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    NOTIONAL_EUR_DEFAULT,
    SLIPPAGE_BPS_DEFAULT,
)

_BPS = Decimal("10000")
_ZERO = Decimal("0")


def shared_cost_assumptions() -> dict[str, Any]:
    return {
        "fee_model": "retail_taker_roundtrip",
        "venues_referenced": ["binance", "bitvavo", "okx"],
        "adverse_bps": ADVERSE_BPS_DEFAULT,
        "latency_penalty_bps": LATENCY_PENALTY_BPS,
        "slippage_bps": SLIPPAGE_BPS_DEFAULT,
        "notional_eur": NOTIONAL_EUR_DEFAULT,
        "no_queue_fills": True,
        "no_optimistic_fees": True,
        "source": "bot.core.venue_fees + expected_waterfall",
    }


def round_trip_fee_rate(venue_a: str, venue_b: str | None = None) -> Decimal:
    """Conservative: taker entry + taker exit (possibly cross-venue)."""
    a = venue_taker_fee(venue_a)
    b = venue_taker_fee(venue_b or venue_a)
    return a + b


def net_waterfall_from_edge(
    *,
    gross_edge_fraction: float,
    venue: str,
    venue_exit: str | None = None,
    notional_eur: float = NOTIONAL_EUR_DEFAULT,
    adverse_bps: float = ADVERSE_BPS_DEFAULT,
    slippage_bps: float = SLIPPAGE_BPS_DEFAULT,
    latency_bps: float = LATENCY_PENALTY_BPS,
) -> dict[str, Any]:
    """Convert predictive fractional edge into EUR waterfall via shared costs."""
    notional = Decimal(str(notional_eur))
    gross = notional * Decimal(str(gross_edge_fraction))
    fee_rate = round_trip_fee_rate(venue, venue_exit)
    fees = notional * fee_rate
    slip = notional * (Decimal(str(slippage_bps)) / _BPS)
    adverse = notional * (Decimal(str(adverse_bps)) / _BPS)
    latency = notional * (Decimal(str(latency_bps)) / _BPS)
    # Split fees evenly for waterfall display
    half = fees / Decimal("2")
    wf = expected_waterfall(
        gross=gross,
        buy_fee=half,
        sell_fee=fees - half,
        slippage=slip,
        extra_adverse=_ZERO,
        execution_buffer=adverse + latency,
        funding=_ZERO,
        transfer_fx=_ZERO,
        inventory_relief=_ZERO,
    )
    expected_net = float(gross - fees - slip - adverse - latency)
    return {
        "GROSS_PREDICTIVE_EDGE": float(gross),
        "FEES": float(fees),
        "SPREAD_CROSSING": 0.0,  # signal families may fold into gross
        "SLIPPAGE": float(slip),
        "ADVERSE": float(adverse),
        "LATENCY": float(latency),
        "UNCERTAINTY": 0.0,
        "OTHER_COST": 0.0,
        "EXPECTED_NET": expected_net,
        "waterfall_model": wf.as_dict(),
        "fee_rate_roundtrip": float(fee_rate),
        "notional_eur": float(notional),
        "assumptions": shared_cost_assumptions(),
    }


def execution_replay_net(
    *,
    expected_net: float,
    fill_rate: float = 0.55,
    adverse_extra_bps: float = 4.0,
    notional_eur: float = NOTIONAL_EUR_DEFAULT,
) -> dict[str, Any]:
    """Conservative replay: partial fill rate + extra adverse (no queue fills)."""
    extra = notional_eur * (adverse_extra_bps / 10000.0)
    # Realized ≈ fill_rate * (expected_net - extra_adverse_per_trade)
    per_trade = expected_net - extra
    realized = fill_rate * per_trade
    return {
        "fill_rate": fill_rate,
        "adverse_extra_bps": adverse_extra_bps,
        "per_fill_net": per_trade,
        "EXECUTION_NET": realized,
        "no_queue_fills": True,
        "trade_through_baseline": True,
    }
