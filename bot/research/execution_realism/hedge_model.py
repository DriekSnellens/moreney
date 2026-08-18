"""Hedge leg realism: model independent execution of the exit/hedge trade."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from bot.research.execution_realism.config import HEDGE_DELAY_MS, NOTIONAL_EUR
from bot.research.execution_realism.models import HedgeResult
from bot.research.tournament.tape_index import SeriesPoint

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def simulate_hedge(
    *,
    hedge_scenario: str,
    fill_at_ns: int,
    filled_notional: Decimal,
    entry_mid: Decimal,
    side: str,
    points: Sequence[SeriesPoint],
    venue_processing_ms: float = 5.0,
) -> HedgeResult:
    """Simulate the hedge leg using the causal market snapshot at hedge arrival time."""
    delay_ms = HEDGE_DELAY_MS.get(hedge_scenario, 50.0)
    hedge_arrival_ns = int(fill_at_ns + int(delay_ms * 1_000_000) + int(venue_processing_ms * 1_000_000))

    hedge_point = None
    for p in points:
        if p.ts_ns >= hedge_arrival_ns:
            hedge_point = p
            break

    if hedge_point is None:
        # No data at hedge time — pessimistic: assume adverse movement
        adverse_bps = Decimal("10")
        cost = filled_notional * adverse_bps / _BPS
        return HedgeResult(
            hedge_scenario=hedge_scenario,
            hedge_delay_ms=delay_ms,
            hedge_price=None,
            market_mid_at_hedge=None,
            hedge_slippage_bps=_ZERO,
            hedge_adverse_bps=adverse_bps,
            hedge_cost_eur=cost,
            notes=("No market data at hedge arrival time; pessimistic adverse assumed.",),
        )

    hedge_mid = Decimal(str(hedge_point.mid))
    if entry_mid <= 0 or hedge_mid <= 0:
        return HedgeResult(
            hedge_scenario=hedge_scenario,
            hedge_delay_ms=delay_ms,
            hedge_price=hedge_mid,
            market_mid_at_hedge=hedge_mid,
            hedge_slippage_bps=_ZERO,
            hedge_adverse_bps=_ZERO,
            hedge_cost_eur=_ZERO,
        )

    # Adverse = movement against us between fill and hedge
    mid_change = hedge_mid - entry_mid
    if side == "BUY":
        adverse_eur = -mid_change / entry_mid * filled_notional
    else:
        adverse_eur = mid_change / entry_mid * filled_notional

    adverse_bps_val = abs(adverse_eur) / filled_notional * _BPS if filled_notional > 0 else _ZERO
    # Hedge slippage from spread at hedge point
    spread_at_hedge = Decimal(str(hedge_point.ask - hedge_point.bid))
    slip_bps = (spread_at_hedge / hedge_mid * _BPS / Decimal("2")) if hedge_mid > 0 else _ZERO

    cost = max(_ZERO, adverse_eur) + filled_notional * slip_bps / _BPS

    return HedgeResult(
        hedge_scenario=hedge_scenario,
        hedge_delay_ms=delay_ms,
        hedge_price=hedge_mid,
        market_mid_at_hedge=hedge_mid,
        hedge_slippage_bps=slip_bps,
        hedge_adverse_bps=adverse_bps_val if adverse_eur > 0 else _ZERO,
        hedge_cost_eur=max(_ZERO, cost),
    )
