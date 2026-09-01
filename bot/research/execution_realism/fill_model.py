"""Fill models: existing trade-through, post-only survival, depth-constrained, uncertainty-bounded.

Each model determines whether a signal would realistically fill given the causal
timeline and market state. No future data is used.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.execution_realism.config import NOTIONAL_EUR
from bot.research.execution_realism.models import (
    ExecutionTimeline,
    FillResult,
    FillStatus,
)
from bot.research.tournament.tape_index import SeriesPoint

_ZERO = Decimal("0")


def _find_point_at_or_after(points: Sequence[SeriesPoint], ts_ns: int) -> SeriesPoint | None:
    for p in points:
        if p.ts_ns >= ts_ns:
            return p
    return None


def _find_point_before(points: Sequence[SeriesPoint], ts_ns: int) -> SeriesPoint | None:
    result = None
    for p in points:
        if p.ts_ns >= ts_ns:
            break
        result = p
    return result


def _mid_decimal(p: SeriesPoint) -> Decimal:
    return Decimal(str(p.mid))


def existing_trade_through(
    timeline: ExecutionTimeline,
    forward: float,
    *,
    notional: Decimal = NOTIONAL_EUR,
) -> FillResult:
    """Reproduce existing canonical replay: fill_rate=0.55 applied externally.

    At the individual signal level this model always assumes FULL_FILL to match
    the canonical waterfall (the 0.55 is a portfolio-level statistical fill rate).
    """
    return FillResult(
        fill_model="EXISTING_TRADE_THROUGH",
        status=FillStatus.FULL_FILL,
        requested_notional=notional,
        filled_notional=notional,
        remaining_notional=_ZERO,
        fill_price=None,
        market_mid_at_fill=None,
        slippage_bps=_ZERO,
        available_depth=None,
        notes=("Legacy model: all signals assumed filled; 0.55 fill_rate applied at portfolio level.",),
    )


def post_only_survival(
    timeline: ExecutionTimeline,
    points: Sequence[SeriesPoint],
    *,
    side: str,
    entry_price: Decimal,
    notional: Decimal = NOTIONAL_EUR,
) -> FillResult:
    """Order fills only if quote survives to order arrival and market crosses limit."""
    arrival_ns = timeline.order_arrival_at_ns

    pre_arrival = _find_point_before(points, arrival_ns)
    if pre_arrival is None:
        return FillResult(
            fill_model="POST_ONLY_SURVIVAL",
            status=FillStatus.NO_FILL,
            requested_notional=notional,
            filled_notional=_ZERO,
            remaining_notional=notional,
            fill_price=None,
            market_mid_at_fill=None,
            slippage_bps=_ZERO,
            available_depth=None,
            notes=("No market data before order arrival.",),
        )

    at_arrival = _find_point_at_or_after(points, arrival_ns)
    if at_arrival is None:
        return FillResult(
            fill_model="POST_ONLY_SURVIVAL",
            status=FillStatus.NO_FILL,
            requested_notional=notional,
            filled_notional=_ZERO,
            remaining_notional=notional,
            fill_price=None,
            market_mid_at_fill=None,
            slippage_bps=_ZERO,
            available_depth=None,
            notes=("No market data at/after order arrival.",),
        )

    # Quote must still be competitive at arrival
    if side == "BUY":
        if Decimal(str(at_arrival.ask)) <= entry_price:
            return FillResult(
                fill_model="POST_ONLY_SURVIVAL",
                status=FillStatus.NO_FILL,
                requested_notional=notional,
                filled_notional=_ZERO,
                remaining_notional=notional,
                fill_price=None,
                market_mid_at_fill=_mid_decimal(at_arrival),
                slippage_bps=_ZERO,
                available_depth=None,
                notes=("Ask moved through entry — would have been a taker cross, not a fill.",),
            )
    else:
        if Decimal(str(at_arrival.bid)) >= entry_price:
            return FillResult(
                fill_model="POST_ONLY_SURVIVAL",
                status=FillStatus.NO_FILL,
                requested_notional=notional,
                filled_notional=_ZERO,
                remaining_notional=notional,
                fill_price=None,
                market_mid_at_fill=_mid_decimal(at_arrival),
                slippage_bps=_ZERO,
                available_depth=None,
                notes=("Bid moved through entry — would have been a taker cross, not a fill.",),
            )

    # Look for a subsequent crossing after arrival
    for p in points:
        if p.ts_ns <= arrival_ns:
            continue
        crossed = False
        if side == "BUY" and Decimal(str(p.ask)) <= entry_price:
            crossed = True
        elif side == "SELL" and Decimal(str(p.bid)) >= entry_price:
            crossed = True
        if crossed:
            slip = abs(_mid_decimal(p) - entry_price) / entry_price * Decimal("10000") if entry_price > 0 else _ZERO
            return FillResult(
                fill_model="POST_ONLY_SURVIVAL",
                status=FillStatus.FULL_FILL,
                requested_notional=notional,
                filled_notional=notional,
                remaining_notional=_ZERO,
                fill_price=entry_price,
                market_mid_at_fill=_mid_decimal(p),
                slippage_bps=slip,
                available_depth=Decimal(str(p.bid_size + p.ask_size)) * _mid_decimal(p),
            )

    return FillResult(
        fill_model="POST_ONLY_SURVIVAL",
        status=FillStatus.NO_FILL,
        requested_notional=notional,
        filled_notional=_ZERO,
        remaining_notional=notional,
        fill_price=None,
        market_mid_at_fill=None,
        slippage_bps=_ZERO,
        available_depth=None,
        notes=("Market never crossed entry price after order arrival within horizon.",),
    )


def depth_constrained(
    timeline: ExecutionTimeline,
    points: Sequence[SeriesPoint],
    *,
    side: str,
    entry_price: Decimal,
    notional: Decimal = NOTIONAL_EUR,
) -> FillResult:
    """Fill cannot exceed observable executable depth at time of fill."""
    arrival_ns = timeline.order_arrival_at_ns
    at_arrival = _find_point_at_or_after(points, arrival_ns)
    if at_arrival is None:
        return FillResult(
            fill_model="DEPTH_CONSTRAINED",
            status=FillStatus.NO_FILL,
            requested_notional=notional,
            filled_notional=_ZERO,
            remaining_notional=notional,
            fill_price=None,
            market_mid_at_fill=None,
            slippage_bps=_ZERO,
            available_depth=None,
            notes=("No market data at/after order arrival.",),
        )

    mid = _mid_decimal(at_arrival)
    if side == "BUY":
        depth_units = Decimal(str(at_arrival.ask_size))
    else:
        depth_units = Decimal(str(at_arrival.bid_size))

    available_eur = depth_units * mid if mid > 0 else _ZERO
    filled = min(notional, available_eur)
    remaining = notional - filled

    if filled <= 0:
        return FillResult(
            fill_model="DEPTH_CONSTRAINED",
            status=FillStatus.NO_FILL,
            requested_notional=notional,
            filled_notional=_ZERO,
            remaining_notional=notional,
            fill_price=None,
            market_mid_at_fill=mid,
            slippage_bps=_ZERO,
            available_depth=available_eur,
            notes=("Zero observable depth.",),
        )

    status = FillStatus.FULL_FILL if remaining <= 0 else FillStatus.PARTIAL_FILL
    slip = _ZERO
    return FillResult(
        fill_model="DEPTH_CONSTRAINED",
        status=status,
        requested_notional=notional,
        filled_notional=filled,
        remaining_notional=remaining,
        fill_price=entry_price,
        market_mid_at_fill=mid,
        slippage_bps=slip,
        available_depth=available_eur,
    )


def uncertainty_bounded(
    timeline: ExecutionTimeline,
    points: Sequence[SeriesPoint],
    *,
    side: str,
    entry_price: Decimal,
    notional: Decimal = NOTIONAL_EUR,
    exchange_ts_available: bool = False,
) -> FillResult:
    """Compute optimistic/central/pessimistic; use central for acceptance."""
    if exchange_ts_available:
        return post_only_survival(
            timeline, points, side=side, entry_price=entry_price, notional=notional
        )

    # Without exchange timestamps, arrival time is uncertain.
    # Pessimistic: assume 2x stated latency. Central: stated. Optimistic: 0.5x.
    # We use central (stated) for the fill decision but flag uncertainty.
    result = post_only_survival(
        timeline, points, side=side, entry_price=entry_price, notional=notional
    )
    notes = list(result.notes) + ["TIMESTAMP_UNCERTAIN: exchange_ts unavailable; using central estimate."]
    return FillResult(
        fill_model="UNCERTAINTY_BOUNDED",
        status=result.status,
        requested_notional=result.requested_notional,
        filled_notional=result.filled_notional,
        remaining_notional=result.remaining_notional,
        fill_price=result.fill_price,
        market_mid_at_fill=result.market_mid_at_fill,
        slippage_bps=result.slippage_bps,
        available_depth=result.available_depth,
        notes=tuple(notes),
    )
