"""Stub exchange client for scaffolding and tests (no real network calls)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderStatus
from bot.core.models import (
    Balance,
    ExecutionResult,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
)
from bot.exchanges.base import BaseExchangeClient
from bot.exchanges.models import (
    ExchangeOrder,
    HealthCheckResult,
    OrderBook,
    OrderBookLevel,
    TradingFee,
)
from bot.exchanges.symbols import to_internal_symbol


class StubExchangeClient(BaseExchangeClient):
    """Deterministic stub used for tests and paper scaffolding."""

    name = "stub"

    def __init__(
        self,
        settings: Settings,
        *,
        default_bid: Decimal = Decimal("100"),
        default_ask: Decimal = Decimal("100.1"),
        enable_trading: bool = True,
    ) -> None:
        super().__init__(settings, enable_trading=enable_trading)
        self._default_bid = default_bid
        self._default_ask = default_ask
        self.placed_orders: list[OrderRequest] = []
        self.cancelled_orders: list[tuple[str, str]] = []
        self._orders: dict[str, ExchangeOrder] = {}

    async def fetch_ticker(self, symbol: str) -> MarketSnapshot:
        mid = (self._default_bid + self._default_ask) / Decimal("2")
        return MarketSnapshot(
            symbol=to_internal_symbol(symbol),
            bid=self._default_bid,
            ask=self._default_ask,
            last=mid,
            funding_rate=Decimal(str(self._settings.profitability_funding_rate)),
        )

    async def fetch_order_book(self, symbol: str, *, limit: int | None = None) -> OrderBook:
        depth = limit or 5
        bids = [
            OrderBookLevel(
                price=self._default_bid - Decimal(i) * Decimal("0.1"),
                amount=Decimal("1"),
            )
            for i in range(depth)
        ]
        asks = [
            OrderBookLevel(
                price=self._default_ask + Decimal(i) * Decimal("0.1"),
                amount=Decimal("1"),
            )
            for i in range(depth)
        ]
        return OrderBook(symbol=to_internal_symbol(symbol), bids=bids, asks=asks)

    async def fetch_trading_fees(self, symbol: str) -> TradingFee:
        rate = Decimal(str(self._settings.profitability_fee_rate))
        return TradingFee(symbol=to_internal_symbol(symbol), maker=rate, taker=rate)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        orders = [
            order
            for order in self._orders.values()
            if order.status in {OrderStatus.SUBMITTED, OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}
        ]
        if symbol:
            internal = to_internal_symbol(symbol)
            orders = [order for order in orders if order.symbol == internal]
        return orders

    async def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        if order_id in self._orders:
            return self._orders[order_id]
        return ExchangeOrder(
            id=order_id,
            symbol=to_internal_symbol(symbol),
            side=OpportunitySide.BUY,
            status=OrderStatus.SUBMITTED,
            quantity=Decimal("1"),
        )

    async def place_order(self, order: OrderRequest) -> ExecutionResult:
        self.placed_orders.append(order)
        exchange_order_id = str(uuid4())
        exchange_order = ExchangeOrder(
            id=exchange_order_id,
            symbol=to_internal_symbol(order.symbol),
            side=order.side,
            status=OrderStatus.SUBMITTED,
            quantity=order.quantity,
            price=order.limit_price,
            client_order_id=order.client_order_id,
            created_at=datetime.now(UTC),
        )
        self._orders[exchange_order_id] = exchange_order
        return ExecutionResult(
            order_id=order.id,
            opportunity_id=order.opportunity_id,
            status=OrderStatus.SUBMITTED,
            filled_quantity=Decimal("0"),
            message="Stub exchange accepted order (not live-traded)",
            metadata={"exchange": self.name, "exchange_order_id": exchange_order_id},
        )

    async def cancel_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        self.cancelled_orders.append((order_id, symbol))
        existing = self._orders.get(order_id)
        cancelled = ExchangeOrder(
            id=order_id,
            symbol=to_internal_symbol(symbol),
            side=existing.side if existing else OpportunitySide.BUY,
            status=OrderStatus.CANCELLED,
            quantity=existing.quantity if existing else Decimal("0"),
            filled_quantity=existing.filled_quantity if existing else Decimal("0"),
            price=existing.price if existing else None,
        )
        self._orders[order_id] = cancelled
        return cancelled

    async def get_balances(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            balances=[Balance(asset="USD", free=Decimal("10000"), locked=Decimal("0"))],
            positions=[],
            equity_usd=Decimal("10000"),
            daily_realized_pnl_usd=Decimal("0"),
            open_position_count=0,
        )

    async def health_check(self) -> HealthCheckResult:
        return HealthCheckResult(
            exchange=self.name,
            healthy=True,
            authenticated=False,
            latency_ms=0.1,
            message="stub ok",
        )
