"""Expected slippage including order-book depth and market impact."""

from dataclasses import dataclass
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import TradeOpportunity

_BPS = Decimal("10000")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class SlippageEstimate:
    """Breakdown of expected execution slippage in quote currency (USD)."""

    base_slippage: Decimal
    market_impact: Decimal
    thin_book_penalty: Decimal
    total_slippage: Decimal
    depth_available: Decimal
    depth_consumed_ratio: Decimal
    vwap: Decimal | None
    levels_consumed: int


class SlippageModel:
    """Estimates slippage from configured bps plus order-book market impact.

    When an order book is available, walks the relevant side to compute VWAP
    vs the opportunity's entry/exit reference prices. Insufficient depth adds a
    configurable thin-book penalty. Without a book, only base bps slippage applies.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_slippage_bps = Decimal(str(settings.profitability_slippage_bps))
        self._impact_factor = Decimal(str(settings.profitability_market_impact_factor))
        self._thin_book_penalty_bps = Decimal(str(settings.profitability_thin_book_penalty_bps))

    def estimate(
        self,
        opportunity: TradeOpportunity,
        *,
        exit_price: Decimal,
        order_book: OrderBook | None = None,
    ) -> SlippageEstimate:
        entry_notional = opportunity.quantity * opportunity.entry_price
        exit_notional = opportunity.quantity * exit_price
        round_trip_notional = entry_notional + exit_notional

        # Base expected slippage applied to both legs.
        base = round_trip_notional * (self._base_slippage_bps / _BPS)

        book = order_book or (opportunity.market.order_book if opportunity.market else None)
        if book is None:
            return SlippageEstimate(
                base_slippage=base,
                market_impact=_ZERO,
                thin_book_penalty=_ZERO,
                total_slippage=base,
                depth_available=_ZERO,
                depth_consumed_ratio=_ZERO,
                vwap=None,
                levels_consumed=0,
            )

        entry_impact = self._walk_book_impact(
            quantity=opportunity.quantity,
            reference_price=opportunity.entry_price,
            levels=self._entry_levels(opportunity.side, book),
        )
        exit_impact = self._walk_book_impact(
            quantity=opportunity.quantity,
            reference_price=exit_price,
            levels=self._exit_levels(opportunity.side, book),
        )

        market_impact = (entry_impact.impact + exit_impact.impact) * self._impact_factor
        thin_penalty = (entry_impact.thin_penalty + exit_impact.thin_penalty) * self._impact_factor
        depth_available = entry_impact.depth_available  # entry-side depth as primary signal
        consumed_ratio = entry_impact.consumed_ratio
        levels = entry_impact.levels_consumed + exit_impact.levels_consumed

        total = base + market_impact + thin_penalty
        return SlippageEstimate(
            base_slippage=base,
            market_impact=market_impact,
            thin_book_penalty=thin_penalty,
            total_slippage=total,
            depth_available=depth_available,
            depth_consumed_ratio=consumed_ratio,
            vwap=entry_impact.vwap,
            levels_consumed=levels,
        )

    @staticmethod
    def _entry_levels(side: OpportunitySide, book: OrderBook) -> list[OrderBookLevel]:
        if side in {OpportunitySide.BUY, OpportunitySide.LONG}:
            return book.asks
        return book.bids

    @staticmethod
    def _exit_levels(side: OpportunitySide, book: OrderBook) -> list[OrderBookLevel]:
        if side in {OpportunitySide.BUY, OpportunitySide.LONG}:
            return book.bids
        return book.asks

    def _walk_book_impact(
        self,
        *,
        quantity: Decimal,
        reference_price: Decimal,
        levels: list[OrderBookLevel],
    ) -> "_BookWalkResult":
        remaining = quantity
        notional = _ZERO
        filled = _ZERO
        depth_available = sum((level.amount for level in levels), _ZERO)
        levels_consumed = 0

        for level in levels:
            if remaining <= 0:
                break
            take = min(remaining, level.amount)
            notional += take * level.price
            filled += take
            remaining -= take
            levels_consumed += 1

        if filled <= 0:
            # Empty book: full thin-book penalty on intended notional.
            intended = quantity * reference_price
            penalty = intended * (self._thin_book_penalty_bps / _BPS)
            return _BookWalkResult(
                impact=_ZERO,
                thin_penalty=penalty,
                depth_available=depth_available,
                consumed_ratio=Decimal("1") if quantity > 0 else _ZERO,
                vwap=None,
                levels_consumed=0,
            )

        vwap = notional / filled
        impact_per_unit = abs(vwap - reference_price)
        impact = impact_per_unit * filled

        thin_penalty = _ZERO
        if remaining > 0:
            # Unfilled remainder assumed worse than reference by thin-book penalty.
            remainder_notional = remaining * reference_price
            thin_penalty = remainder_notional * (self._thin_book_penalty_bps / _BPS)
            # Also count unfilled size as adverse impact at last known price if any.
            last_price = levels[levels_consumed - 1].price if levels_consumed else reference_price
            impact += abs(last_price - reference_price) * remaining

        consumed_ratio = (quantity - remaining) / quantity if quantity > 0 else _ZERO
        if remaining > 0:
            consumed_ratio = Decimal("1")

        return _BookWalkResult(
            impact=impact,
            thin_penalty=thin_penalty,
            depth_available=depth_available,
            consumed_ratio=consumed_ratio,
            vwap=vwap,
            levels_consumed=levels_consumed,
        )


@dataclass(frozen=True, slots=True)
class _BookWalkResult:
    impact: Decimal
    thin_penalty: Decimal
    depth_available: Decimal
    consumed_ratio: Decimal
    vwap: Decimal | None
    levels_consumed: int
