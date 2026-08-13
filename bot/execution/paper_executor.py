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
import time
from decimal import Decimal
from uuid import UUID

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide, OrderStatus, OrderType
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import ExecutionResult, OrderRequest
from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.execution.base import BaseExecutor
from bot.execution.fill_tracker import FillTracker
from bot.execution.order_manager import OrderManager
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import infer_base_asset, infer_quote_asset

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
        post_only = bool((order_request.metadata or {}).get("post_only"))
        if self._latency_ms > 0 and not post_only:
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

        if post_only:
            return self._rest_post_only(
                order,
                order_request,
                order_book,
                reserve_asset,
                reserve_amount,
            )

        # Limit price check for limit buys/sells against book/top
        if order.order_type == OrderType.LIMIT and order.requested_price is not None:
            if not self._limit_is_marketable(order, order_book):
                if (order.metadata or {}).get("arb_leg"):
                    self._portfolio.release_reservation(reserve_asset, reserve_amount)
                    return self._reject(
                        order,
                        order_request,
                        reason="NOT_MARKETABLE",
                        message="Arb leg missed — book moved before the order landed",
                    )
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
        self._apply_venue_ledger(order, filled_qty, vwap, fee)

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
            self._unlock_venue(order, remaining)
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

    def match_resting(
        self, books: dict[str, dict[str, OrderBook]]
    ) -> list[ExecutionResult]:
        """Fill post-only quotes that were traded through or aged at the touch."""
        now_ms = int(time.time() * 1000)
        rest_ms = float(getattr(self._settings, "paper_maker_rest_ms", 0) or 0)
        raw_queue = getattr(self._settings, "paper_maker_queue_fill_pct", 0)
        fill_pct = Decimal(str(0 if raw_queue is None else raw_queue))
        filled: list[ExecutionResult] = []
        for order in self._orders.open_orders():
            if not (order.metadata or {}).get("post_only"):
                continue
            venue = str((order.metadata or {}).get("venue") or "")
            book = (books.get(venue) or {}).get(order.symbol)
            if book is None:
                continue
            qty = self._maker_fill_quantity(order, book, now_ms, rest_ms, fill_pct)
            if qty <= 0:
                continue
            result = self._fill_resting(order, qty)
            if result is not None:
                filled.append(result)
        return filled

    def _rest_post_only(
        self,
        order: Order,
        request: OrderRequest,
        book: OrderBook | None,
        reserve_asset: str,
        reserve_amount: Decimal,
    ) -> ExecutionResult:
        if self._would_take(order, book):
            self._portfolio.release_reservation(reserve_asset, reserve_amount)
            return self._reject(
                order,
                request,
                reason="WOULD_TAKE",
                message="Post-only order would take liquidity",
            )
        if not self._lock_venue_for_rest(order, reserve_asset, reserve_amount):
            self._portfolio.release_reservation(reserve_asset, reserve_amount)
            return self._reject(
                order,
                request,
                reason="INSUFFICIENT_BALANCE",
                message=f"Insufficient venue {reserve_asset} to rest post-only order",
            )
        meta = order.metadata
        meta["placed_ms"] = str(int(time.time() * 1000))
        meta["reserved_asset"] = reserve_asset
        meta["reserved_amount"] = str(reserve_amount)
        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=request.opportunity_id,
            status=OrderStatus.OPEN,
            filled_quantity=_ZERO,
            average_price=None,
            fees_usd=_ZERO,
            message="Post-only resting (maker)",
            metadata={
                "executor": self.name,
                "exchange": "paper",
                "post_only": True,
                "real_exchange_order": False,
            },
        )
        self.history.append(result)
        logger.info(
            "PAPER_POST_ONLY_REST order_id=%s side=%s price=%s qty=%s venue=%s "
            "real_exchange_order=false",
            order.id,
            order.side.value,
            order.requested_price,
            order.requested_quantity,
            (order.metadata or {}).get("venue"),
        )
        return result

    def _would_take(self, order: Order, book: OrderBook | None) -> bool:
        if book is None or order.requested_price is None:
            return False
        if order.side == OrderSide.BUY:
            if not book.asks:
                return False
            return order.requested_price >= book.asks[0].price
        if not book.bids:
            return False
        return order.requested_price <= book.bids[0].price

    def _maker_fill_quantity(
        self,
        order: Order,
        book: OrderBook,
        now_ms: int,
        rest_ms: float,
        fill_pct: Decimal,
    ) -> Decimal:
        limit = order.requested_price
        remaining = order.remaining_quantity
        if limit is None or remaining <= 0:
            return _ZERO
        placed_ms = int(float((order.metadata or {}).get("placed_ms") or now_ms))
        aged = now_ms - placed_ms
        through_raw = getattr(self._settings, "paper_maker_trade_through_fill_pct", 1)
        through_pct = Decimal(str(1 if through_raw is None else through_raw))

        if order.side == OrderSide.BUY:
            if not book.bids:
                return _ZERO
            best = book.bids[0].price
            displayed = book.bids[0].amount
            # Live-conservative: only fill when the market trades through our bid.
            if best < limit:
                if through_pct <= 0:
                    return _ZERO
                take = remaining if through_pct >= 1 else remaining * through_pct
                return min(remaining, take)
            if best > limit:
                return _ZERO
        else:
            if not book.asks:
                return _ZERO
            best = book.asks[0].price
            displayed = book.asks[0].amount
            if best > limit:
                if through_pct <= 0:
                    return _ZERO
                take = remaining if through_pct >= 1 else remaining * through_pct
                return min(remaining, take)
            if best < limit:
                return _ZERO

        # At-touch queue fills are optional and off by default (too optimistic live).
        if fill_pct <= 0 or aged < rest_ms:
            return _ZERO
        queued = displayed * fill_pct
        if queued <= 0:
            return _ZERO
        return min(remaining, queued)

    async def close_one_leg(
        self,
        *,
        opportunity_id: UUID,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        venue: str,
        order_book: OrderBook | None,
        strategy: str = "maker_inventory",
        reason: str = "one_leg_exit",
    ) -> ExecutionResult | None:
        """Close leftover inventory after a one-sided maker fill (live-equivalent)."""
        if quantity <= 0:
            return None
        adverse_bps = Decimal(
            str(getattr(self._settings, "paper_maker_one_leg_adverse_bps", 0) or 0)
        )
        from bot.core.enums import OpportunitySide
        from bot.core.models import OrderRequest

        if side == OrderSide.BUY:
            # Need to buy back after an unhedged sell fill.
            if not order_book or not order_book.asks:
                return None
            px = order_book.asks[0].price * (
                Decimal("1") + adverse_bps / Decimal("10000")
            )
            opp_side = OpportunitySide.BUY
        else:
            if not order_book or not order_book.bids:
                return None
            px = order_book.bids[0].price * (
                Decimal("1") - adverse_bps / Decimal("10000")
            )
            if px <= 0:
                return None
            opp_side = OpportunitySide.SELL

        request = OrderRequest(
            opportunity_id=opportunity_id,
            symbol=symbol,
            side=opp_side,
            quantity=quantity,
            limit_price=px,
            metadata={
                "venue": venue,
                "arb_leg": True,
                "fee_role": "taker",
                "one_leg_exit": True,
                "exit_reason": reason,
                "strategy": strategy,
                "real_exchange_order": False,
            },
        )
        return await self.execute(
            request,
            order_book=order_book,
            strategy=strategy,
            order_type=OrderType.LIMIT,
        )

    def _fill_resting(self, order: Order, filled_qty: Decimal) -> ExecutionResult | None:
        vwap = order.requested_price
        if vwap is None or filled_qty <= 0:
            return None
        fee_rate = self._fee_rate_for(order)
        fee = filled_qty * vwap * fee_rate
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            price=vwap,
            fee=fee,
            fee_asset=self._quote,
            slippage=_ZERO,
            exchange=str((order.metadata or {}).get("venue") or "paper"),
            metadata={
                "post_only": True,
                "fee_role": "maker",
                "fee_rate": str(fee_rate),
            },
        )
        self._orders.attach_fill(order.id, fill)
        self._fills.apply(order, fill)
        self._apply_venue_ledger(
            order, filled_qty, vwap, fee, inventory_locked=True
        )
        self._portfolio.set_mark_price(order.symbol, vwap)
        result = ExecutionResult(
            order_id=order.id,
            opportunity_id=order.opportunity_id or order.id,
            status=order.status,
            filled_quantity=filled_qty,
            average_price=vwap,
            fees_usd=fee,
            message=f"Paper maker {order.status.value}",
            metadata={
                "executor": self.name,
                "exchange": "paper",
                "post_only": True,
                "fee": str(fee),
                "real_exchange_order": False,
                "fill_id": str(fill.id),
            },
        )
        self.history.append(result)
        logger.info(
            "PAPER_MAKER_FILL order_id=%s status=%s qty=%s price=%s fee=%s "
            "real_exchange_order=false",
            order.id,
            order.status.value,
            filled_qty,
            vwap,
            fee,
        )
        return result

    def _lock_venue_for_rest(
        self, order: Order, reserve_asset: str, reserve_amount: Decimal
    ) -> bool:
        ledger = getattr(self._portfolio, "venue_ledger", None)
        venue = str((order.metadata or {}).get("venue") or "")
        if ledger is None or not venue:
            return True
        return ledger.lock(venue, reserve_asset, reserve_amount)

    def _unlock_venue(self, order: Order, remaining_qty: Decimal) -> None:
        ledger = getattr(self._portfolio, "venue_ledger", None)
        venue = str((order.metadata or {}).get("venue") or "")
        if ledger is None or not venue or remaining_qty <= 0:
            return
        if order.side == OrderSide.BUY:
            price = order.requested_price or order.average_fill_price or _ZERO
            amount = remaining_qty * price * (Decimal("1") + self._fee_rate_for(order))
            ledger.unlock(venue, self._quote_asset_for(order), amount)
        else:
            base = self._portfolio.base_asset_for(order.symbol)
            ledger.unlock(venue, base, remaining_qty)

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


    def _quote_asset_for(self, order: Order) -> str:
        return infer_quote_asset(order.symbol, self._quote)

    def _reservation_needed(
        self, order: Order
    ) -> tuple[bool, str, Decimal]:
        if order.side == OrderSide.BUY:
            quote = self._quote_asset_for(order)
            price = order.requested_price
            if price is None or price <= 0:
                # Market buy: estimate with mark or require price
                mark = self._portfolio.state.mark_prices.get(order.symbol)
                price = mark or Decimal("0")
            if price <= 0:
                # Allow open without hard reserve when price unknown; reject later
                return True, quote, _ZERO
            amount = order.requested_quantity * price * (Decimal("1") + self._fee_rate_for(order))
            ok = self._portfolio.available(quote) >= amount
            ok = ok and self._venue_can_buy(order, amount)
            return ok, quote, amount if ok else amount
        base = self._portfolio.base_asset_for(order.symbol)
        amount = order.requested_quantity
        ok = self._portfolio.available(base) >= amount
        ok = ok and self._venue_can_sell(order, base, amount)
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
            if (order.metadata or {}).get("arb_leg"):
                return None
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
        meta = order.metadata or {}
        role = str(meta.get("fee_role") or "").lower()
        if meta.get("post_only") or role == "maker":
            return venue_maker_fee(venue, fallback=self._fee_rate)
        return venue_taker_fee(venue, fallback=self._fee_rate)

    def _venue_can_buy(self, order: Order, quote_needed: Decimal) -> bool:
        ledger = getattr(self._portfolio, "venue_ledger", None)
        venue = str((order.metadata or {}).get("venue") or "")
        if ledger is None or not venue:
            return True
        return ledger.available(venue, self._quote_asset_for(order)) >= quote_needed

    def _venue_can_sell(self, order: Order, base: str, quantity: Decimal) -> bool:
        ledger = getattr(self._portfolio, "venue_ledger", None)
        venue = str((order.metadata or {}).get("venue") or "")
        if ledger is None or not venue:
            return True
        return ledger.can_sell(venue, base, quantity)

    def _apply_venue_ledger(
        self,
        order: Order,
        filled_qty: Decimal,
        vwap: Decimal,
        fee: Decimal,
        *,
        inventory_locked: bool = False,
    ) -> None:
        ledger = getattr(self._portfolio, "venue_ledger", None)
        venue = str((order.metadata or {}).get("venue") or "")
        if ledger is None or not venue:
            return

        quote = self._quote_asset_for(order)
        base = infer_base_asset(order.symbol, quote)
        if inventory_locked:
            # Resting post-only already deducted the reserved asset.
            if order.side == OrderSide.BUY:
                ledger.credit(venue, base, filled_qty)
            else:
                ledger.credit(venue, quote, filled_qty * vwap - fee)
            return
        if order.side == OrderSide.BUY:
            ledger.apply_buy(
                venue,
                base=base,
                quantity=filled_qty,
                quote_spent=filled_qty * vwap + fee,
                quote_asset=quote,
            )
        else:
            ledger.apply_sell(
                venue,
                base=base,
                quantity=filled_qty,
                quote_received=filled_qty * vwap - fee,
                quote_asset=quote,
            )

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
