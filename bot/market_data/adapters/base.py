"""Public WebSocket market-data adapter base (no private APIs / credentials)."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bot.core.exchange_types import OrderBookLevel
from bot.market_data.models import MarketDataEvent, OrderBookUpdate
from bot.market_data.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

EventHandler = Callable[[MarketDataEvent], Awaitable[None]]


def dec(value: object, default: str = "0") -> Decimal:
    try:
        if value is None:
            return Decimal(default)
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


class PublicMarketDataAdapter(ABC):
    """Exchange-specific public feed parser + optional live WebSocket."""

    name: str
    ws_url: str

    def __init__(
        self,
        symbols: Sequence[str],
        *,
        manager: WebSocketManager | None = None,
    ) -> None:
        self.symbols = [self.normalize_symbol(s) for s in symbols]
        self._handler: EventHandler | None = None
        self._manager = manager

    @abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        """Internal symbol form, e.g. BTCEUR."""

    @abstractmethod
    def to_exchange_symbol(self, symbol: str) -> str:
        """Venue-specific wire symbol."""

    @abstractmethod
    def build_subscribe_messages(self) -> list[str]:
        """JSON subscribe frames for configured symbols."""

    @abstractmethod
    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        """Parse a raw public WS message into normalized events."""

    async def start(self, on_event: EventHandler) -> None:
        self._handler = on_event
        if self._manager is None:
            raise RuntimeError(f"{self.name} adapter has no WebSocketManager")
        self._manager.set_subscriptions(self.build_subscribe_messages())
        await self._manager.start(self._on_raw, on_connected=self._on_connected)

    async def stop(self) -> None:
        if self._manager is not None:
            await self._manager.stop()

    async def _on_connected(self) -> None:
        logger.info("WEBSOCKET_CONNECTED exchange=%s symbols=%s", self.name, self.symbols)

    async def _on_raw(self, raw: str | bytes) -> None:
        try:
            events = self.parse_message(raw)
        except Exception as exc:
            logger.info(
                "MALFORMED_MESSAGE exchange=%s error=%s",
                self.name,
                type(exc).__name__,
            )
            return
        if not self._handler:
            return
        for event in events:
            await self._handler(event)

    @staticmethod
    def loads(raw: str | bytes) -> object:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    @staticmethod
    def levels_from_pairs(
        pairs: list[list[object]] | list[tuple[object, object]],
    ) -> list[OrderBookLevel]:
        levels: list[OrderBookLevel] = []
        for pair in pairs:
            if len(pair) < 2:
                continue
            price = dec(pair[0])
            amount = dec(pair[1])
            levels.append(OrderBookLevel(price=price, amount=amount))
        return levels

    def book_event(
        self,
        *,
        symbol: str,
        bids: list[OrderBookLevel],
        asks: list[OrderBookLevel],
        is_snapshot: bool,
        sequence: int | None = None,
        prev_sequence: int | None = None,
        timestamp: datetime | None = None,
    ) -> MarketDataEvent:
        ts = timestamp or datetime.now(UTC)
        update = OrderBookUpdate(
            exchange=self.name,
            symbol=symbol,
            bids=bids,
            asks=asks,
            is_snapshot=is_snapshot,
            timestamp=ts,
            received_at=datetime.now(UTC),
            sequence=sequence,
            prev_sequence=prev_sequence,
        )
        return MarketDataEvent(
            exchange=self.name,
            symbol=symbol,
            event_type="book_snapshot" if is_snapshot else "book_update",
            timestamp=ts,
            received_at=update.received_at,
            sequence=sequence,
            book_update=update,
        )
