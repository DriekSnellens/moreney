"""Order lifecycle manager for paper (and future live) execution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from bot.core.enums import OrderStatus
from bot.portfolio.models import Fill, Order


class OrderManager:
    """In-memory order registry with status transitions."""

    def __init__(self) -> None:
        self._orders: dict[UUID, Order] = {}

    def add(self, order: Order) -> Order:
        self._orders[order.id] = order
        return order

    def get(self, order_id: UUID) -> Order | None:
        return self._orders.get(order_id)

    def list_orders(self, *, symbol: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if symbol:
            sym = symbol.upper()
            orders = [o for o in orders if o.symbol == sym]
        return orders

    def open_orders(self, *, symbol: str | None = None) -> list[Order]:
        """Non-terminal orders that still have remaining quantity."""
        live = {
            OrderStatus.PENDING,
            OrderStatus.OPEN,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }
        orders = [
            o
            for o in self._orders.values()
            if o.status in live and o.remaining_quantity > 0
        ]
        if symbol:
            sym = symbol.upper()
            orders = [o for o in orders if o.symbol == sym]
        return orders

    def set_status(
        self,
        order_id: UUID,
        status: OrderStatus,
        *,
        rejection_reason: str | None = None,
    ) -> Order:
        order = self._require(order_id)
        order.status = status
        order.updated_at = datetime.now(UTC)
        if rejection_reason is not None:
            order.rejection_reason = rejection_reason
        return order

    def attach_fill(self, order_id: UUID, fill: Fill) -> Order:
        order = self._require(order_id)
        order.fills.append(fill)
        order.filled_quantity += fill.quantity
        order.fee += fill.fee
        order.slippage += fill.slippage
        # VWAP average fill price
        notional = sum((f.quantity * f.price for f in order.fills), start=Decimal("0"))
        if order.filled_quantity > 0:
            order.average_fill_price = notional / order.filled_quantity
        if order.filled_quantity >= order.requested_quantity:
            order.status = OrderStatus.FILLED
        elif order.filled_quantity > 0:
            order.status = OrderStatus.PARTIALLY_FILLED
        order.updated_at = datetime.now(UTC)
        return order

    def cancel(self, order_id: UUID, *, reason: str = "cancelled") -> Order:
        order = self._require(order_id)
        if order.is_terminal and order.status != OrderStatus.PARTIALLY_FILLED:
            if order.status in {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.FAILED}:
                return order
        if order.status == OrderStatus.FILLED:
            return order
        order.status = OrderStatus.CANCELLED
        order.rejection_reason = reason
        order.updated_at = datetime.now(UTC)
        return order

    def _require(self, order_id: UUID) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order {order_id}")
        return order
