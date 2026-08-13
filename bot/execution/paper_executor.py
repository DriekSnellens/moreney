"""Paper trading executor — simulates fills from order books.

HARD SAFETY:
* Never accesses exchange credentials
* Never calls private trading APIs
* Never withdraws funds
* Never places real orders
* Completely isolated from LiveExecutor
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from uuid import UUID

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide, OrderStatus, OrderType
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import ExecutionResult, OrderRequest
from bot.core.venue_fees import venue_taker_fee
from bot.execution.base import BaseExecutor
from bot.execution.fill_tracker import FillTracker
from bot.execution.order_manager import OrderManager
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class PaperExecutor(BaseExecutor):
    """Simulated executor implementing the shared Executor interface.

    Uses order-book depth when provided; otherwise fixed-percentage slippage.
    Updates the paper portfolio ONLY via FillTracker (actual fills).
    """

    name = "paper"

    def __init__(
        self,
        settings: Settings,
        *,
        portfolio: PaperPortfolio,
        order_manager: OrderManager | None = None,
        fill_tracker: FillTracker | None = None,
    ) -> None:
        mode = settings.execution_mode
        mode_value = mode.value if hasattr(mode, "value") else str(mode)
        if mode_value == "live":
            raise RuntimeError(
                "PaperExecutor refuses to run while EXECUTION_MODE=live. "
                "Paper trading must remain isolated from live execution."
            )
        self._settings = settings
        self._portfolio = portfolio
        self._orders = order_manager or OrderManager()
        self._fills = fill_tracker or FillTracker(portfolio)
        self._fee_rate = Decimal(str(settings.paper_fee_rate))
        self._max_slippage = Decimal(str(settings.max_slippage_percent)) / _HUNDRED
        self._slippage_mode = settings.paper_slippage_mode
        self._fixed_slippage_pct = Decimal(str(settings.paper_fixed_slippage_pct))
        self._partial_ok = settings.paper_partial_fills_on_thin_book
        self._reject_thin = settings.paper_reject_on_insufficient_liquidity
        self._latency_ms = settings.paper_simulated_latency_ms
        self._quote = settings.paper_quote_asset.upper()
        self.history: list[ExecutionResult] = []

    @property
    def order_manager(self) -> OrderManager:
        return self._orders

    @property
    def fill_tracker(self) -> FillTracker:
        return self._fills

    @property
    def portfolio(self) -> PaperPortfolio:
        return self._portfolio

    async def execute(
        self,
        order_request: OrderRequest,
        *,
        order_book: OrderBook | None = None,
        strategy: str = "",
        order_type: OrderType = OrderType.LIMIT,
    ) -> ExecutionResult:
        """Simulate an order. Never contacts an exchange."""
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        side = _to_order_side(order_request.side)
        order = Order(
            id=order_request.id,
            strategy=strategy,
            symbol=order_request.symbol,
            side=side,
            order_type=order_type,
            requested_quantity=order_request.quantity,
            requested_price=order_request.limit_price,
            status=OrderStatus.PENDING,
            exchange="paper",
            opportunity_id=order_request.opportunity_id,
            client_order_id=order_request.client_order_id,
            metadata={**(order_request.metadata or {}), "executor": self.name},
        )
        self._orders.add(order)

        # Balance reservation
        reserve_ok, reserve_asset, reserve_amount = self._reservation_needed(order)
        if not reserve_ok:
            return self._reject(
                order,
                order_request,
                reason="INSUFFICIENT_BALANCE",
                message=f"Insufficient {reserve_asset} balance for order",
            )
        if reserve_amount > 0:
            if not self._portfolio.reserve(reserve_asset, reserve_amount):
                return self._reject(
                    order,
                    order_request,
                    reason="INSUFFICIENT_BALANCE",
                    message=f"Insufficient available {reserve_asset}",
                )

        self._orders.set_status(order.id, OrderStatus.OPEN)

        # Limit price check for limit buys/sells against book/top
        if order.order_type == OrderType.LIMIT and order.requested_price is not None:
            if not self._limit_is_marketable(order, order_book):
                # Leave resting as OPEN (paper: no matching engine loop) —
                # treat as accepted but unfilled until cancel or later fill.
                result = ExecutionResult(
                    order_id=order.id,
                    opportunity_id=order_request.opportunity_id,
                    status=OrderStatus.OPEN,
                    filled_quantity=_ZERO,
                    average_price=None,
                    fees_usd=_ZERO,
                    message="Limit order resting (not marketable)",
                    metadata={"executor": self.name, "exchange": "paper"},
                )
                self.history.append(result)
                return result

        fill_plan = self._simulate_fill(order, order_book)
        if fill_plan is None:
            self._portfolio.release_reservation(reserve_asset, reserve_amount)
            return self._reject(
                order,
                order_request,
                reason="INSUFFICIENT_LIQUIDITY",
                message="Insufficient simulated order-book liquidity",
            )

        levels, filled_qty, vwap, slippage_cost = fill_plan
        if filled_qty <= 0:
            self._portfolio.release_reservation(reserve_asset, reserve_amount)
            return self._reject(
                order,
                order_request,
                reason="INSUFFICIENT_LIQUIDITY",
                message="No liquidity available to fill",
            )
        if self._is_adverse_slippage(order, vwap):
            self._portfolio.release_reservation(reserve_asset, reserve_amount)
            return self._reject(
                order,
                order_request,
                reason="EXCESSIVE_SLIPPAGE",
                message="Fill price worse than live slippage limit",
            )

        fee_rate = self._fee_rate_for(order)
        fee = filled_qty * vwap * fee_rate
        # Adjust reservation: release unused, keep used portion for accounting.
        self._adjust_reservation_after_fill(
            order, reserve_asset, reserve_amount, filled_qty, vwap, fee
        )

        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            price=vwap,
            fee=fee,
            fee_asset=self._quote,
            slippage=slippage_cost,
            exchange=str((order.metadata or {}).get("venue") or "paper"),
            metadata={
                "levels_consumed": levels,
                "slippage_mode": self._slippage_mode,
                "fee_rate": str(fee_rate),
            },
        )
        self._orders.attach_fill(order.id, fill)
        self._fills.apply(order, fill)

        # Mark price for unrealized PnL
        self._portfolio.set_mark_price(order.symbol, vwap)

        status = order.status
        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=order_request.opportunity_id,
            status=status,
            filled_quantity=filled_qty,
            average_price=vwap,
            fees_usd=fee,
            message=f"Paper {status.value} via {self._slippage_mode} slippage",
            metadata={
                "executor": self.name,
                "exchange": "paper",
                "slippage": str(slippage_cost),
                "fee": str(fee),
                "real_exchange_order": False,
                "fill_id": str(fill.id),
            },
        )
        self.history.append(result)
        logger.info(
            "PAPER_EXECUTION order_id=%s status=%s qty=%s price=%s fee=%s slippage=%s "
            "real_exchange_order=false",
            order.id,
            status.value,
            filled_qty,
            vwap,
            fee,
            slippage_cost,
        )
        return result

    async def cancel(self, order_id: UUID, *, reason: str = "user_cancel") -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order {order_id}")
        if order.status in {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.FAILED}:
            return order
        # Release unused reservation
        remaining = order.remaining_quantity
        if remaining > 0:
            if order.side == OrderSide.BUY:
                price = order.requested_price or order.average_fill_price or _ZERO
                est = remaining * price * (Decimal("1") + self._fee_rate_for(order))
                self._portfolio.release_reservation(self._quote, est)
            else:
                base = self._portfolio.base_asset_for(order.symbol)
                self._portfolio.release_reservation(base, remaining)
        return self._orders.cancel(order_id, reason=reason)

    async def get_order(self, order_id: UUID) -> Order | None:
        return self._orders.get(order_id)

    def _reject(
        self,
        order: Order,
        request: OrderRequest,
        *,
        reason: str,
        message: str,
    ) -> ExecutionResult:
        self._orders.set_status(
            order.id, OrderStatus.REJECTED, rejection_reason=reason
        )
        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=request.opportunity_id,
            status=OrderStatus.REJECTED,
            filled_quantity=_ZERO,
            average_price=None,
            fees_usd=_ZERO,
            message=message,
            metadata={
                "executor": self.name,
                "rejection_reason": reason,
                "real_exchange_order": False,
            },
        )
        self.history.append(result)
        logger.info(
            "PAPER_REJECTED order_id=%s reason=%s message=%s real_exchange_order=false",
            order.id,
            reason,
            message,
        )
        return result

    def _reservation_needed(
        self, order: Order
    ) -> tuple[bool, str, Decimal]:
        if order.side == OrderSide.BUY:
            price = order.requested_price
            if price is None or price <= 0:
                # Market buy: estimate with mark or require price
                mark = self._portfolio.state.mark_prices.get(order.symbol)
                price = mark or Decimal("0")
            if price <= 0:
                # Allow open without hard reserve when price unknown; reject later
                return True, self._quote, _ZERO
            amount = order.requested_quantity * price * (Decimal("1") + self._fee_rate_for(order))
            ok = self._portfolio.available(self._quote) >= amount
            return ok, self._quote, amount if ok else amount
        base = self._portfolio.base_asset_for(order.symbol)
        amount = order.requested_quantity
        ok = self._portfolio.available(base) >= amount
        return ok, base, amount if ok else amount

    def _adjust_reservation_after_fill(
        self,
        order: Order,
        reserve_asset: str,
        reserved: Decimal,
        filled_qty: Decimal,
        vwap: Decimal,
        fee: Decimal,
    ) -> None:
        if reserved <= 0:
            # Ensure funds available for accounting path when we didn't pre-reserve
            if order.side == OrderSide.BUY:
                needed = filled_qty * vwap + fee
                # Move from available to reserved so accounting can consume reserved
                if self._portfolio.available(self._quote) >= needed:
                    self._portfolio.reserve(self._quote, needed)
            else:
                base = self._portfolio.base_asset_for(order.symbol)
                if self._portfolio.available(base) >= filled_qty:
                    self._portfolio.reserve(base, filled_qty)
            return

        if order.side == OrderSide.BUY:
            used = filled_qty * vwap + fee
            unused = reserved - used
            if unused > 0:
                self._portfolio.release_reservation(reserve_asset, unused)
            elif unused < 0:
                # Need a bit more — try reserve extra from available
                extra = -unused
                self._portfolio.reserve(reserve_asset, extra)
        else:
            unused = reserved - filled_qty
            if unused > 0:
                self._portfolio.release_reservation(reserve_asset, unused)

    def _limit_is_marketable(self, order: Order, book: OrderBook | None) -> bool:
        assert order.requested_price is not None
        if book is None:
            return True  # without book, accept limit at requested price
        if order.side == OrderSide.BUY:
            if not book.asks:
                return False
            return order.requested_price >= book.asks[0].price
        if not book.bids:
            return False
        return order.requested_price <= book.bids[0].price

    def _simulate_fill(
        self,
        order: Order,
        book: OrderBook | None,
    ) -> tuple[int, Decimal, Decimal, Decimal] | None:
        """Return (levels, qty, vwap, slippage_cost) or None if rejected."""
        qty = order.requested_quantity
        if self._slippage_mode == "order_book" and book is not None:
            levels = book.asks if order.side == OrderSide.BUY else book.bids
            return self._walk_book(order, levels, qty)

        # Fixed percentage slippage around reference price
        ref = order.requested_price
        if ref is None or ref <= 0:
            if book is not None:
                if order.side == OrderSide.BUY and book.asks:
                    ref = book.asks[0].price
                elif order.side == OrderSide.SELL and book.bids:
                    ref = book.bids[0].price
            if ref is None or ref <= 0:
                return None
        slip_frac = self._fixed_slippage_pct / _HUNDRED
        if order.side == OrderSide.BUY:
            px = ref * (Decimal("1") + slip_frac)
        else:
            px = ref * (Decimal("1") - slip_frac)
        slip_cost = abs(px - ref) * qty
        return 1, qty, px, slip_cost

    def _walk_book(
        self,
        order: Order,
        levels: list[OrderBookLevel],
        quantity: Decimal,
    ) -> tuple[int, Decimal, Decimal, Decimal] | None:
        remaining = quantity
        notional = _ZERO
        filled = _ZERO
        consumed = 0
        ref = order.requested_price
        if ref is None and levels:
            ref = levels[0].price

        for level in levels:
            if remaining <= 0:
                break
            if level.amount <= 0:
                continue
            # Limit orders cannot cross beyond limit
            if order.order_type == OrderType.LIMIT and order.requested_price is not None:
                if order.side == OrderSide.BUY and level.price > order.requested_price:
                    break
                if order.side == OrderSide.SELL and level.price < order.requested_price:
                    break
            take = min(remaining, level.amount)
            notional += take * level.price
            filled += take
            remaining -= take
            consumed += 1

        if filled <= 0:
            return None

        if remaining > 0:
            if self._reject_thin and not self._partial_ok:
                return None
            if not self._partial_ok:
                return None

        vwap = notional / filled
        slip_cost = _ZERO
        if ref is not None and ref > 0:
            slip_cost = abs(vwap - ref) * filled
        return consumed, filled, vwap, slip_cost

    def _fee_rate_for(self, order: Order) -> Decimal:
        venue = str((order.metadata or {}).get("venue") or "")
        return venue_taker_fee(venue, fallback=self._fee_rate)

    def _is_adverse_slippage(self, order: Order, vwap: Decimal) -> bool:
        """Reject order-book fills that would be untradeable live due to price impact."""
        if self._slippage_mode != "order_book":
            return False
        ref = order.requested_price
        if ref is None or ref <= 0 or vwap <= 0 or self._max_slippage <= 0:
            return False
        if order.side == OrderSide.BUY:
            return vwap > ref * (Decimal("1") + self._max_slippage)
        return vwap < ref * (Decimal("1") - self._max_slippage)


def _to_order_side(side: OpportunitySide | OrderSide) -> OrderSide:
    value = side.value if hasattr(side, "value") else str(side)
    if value in {"sell", "short"}:
        return OrderSide.SELL
    return OrderSide.BUY
