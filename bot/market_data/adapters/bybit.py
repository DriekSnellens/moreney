"""Bybit public market-data WebSocket adapter (spot orderbook — free, no API key)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.core.exchange_types import OrderBookLevel
from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class BybitPublicAdapter(PublicMarketDataAdapter):
    """Public Bybit spot order books. No credentials / no private channels."""

    name = "bybit"
    ws_url = "wss://stream.bybit.com/v5/public/spot"
    depth = 50

    # Bybit spot is primarily USDT-quoted; skip unsupported EUR pairs.
    _SUPPORTED_SUFFIXES = ("USDT", "USDC", "USD")

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-", "").replace("_", "").replace("/", "")

    def to_exchange_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    def _supported(self, symbol: str) -> bool:
        internal = self.normalize_symbol(symbol)
        return any(internal.endswith(suffix) for suffix in self._SUPPORTED_SUFFIXES)

    def build_subscribe_messages(self) -> list[str]:
        args = [
            f"orderbook.{self.depth}.{self.to_exchange_symbol(symbol)}"
            for symbol in self.symbols
            if self._supported(symbol)
        ]
        if not args:
            return []
        return [json.dumps({"op": "subscribe", "args": args})]

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        if data.get("op") in {"subscribe", "unsubscribe", "pong"}:
            return []
        if data.get("op") == "ping":
            return [
                MarketDataEvent(
                    exchange=self.name,
                    symbol=self.symbols[0] if self.symbols else "",
                    event_type="heartbeat",
                    message="ping",
                )
            ]
        topic = str(data.get("topic") or "")
        if not topic.startswith("orderbook."):
            return []
        payload = data.get("data")
        if not isinstance(payload, dict):
            return []
        parts = topic.split(".")
        symbol = self.normalize_symbol(parts[-1] if parts else "")
        msg_type = str(data.get("type") or "snapshot").lower()
        # Bybit orderbook topics are safest as replace-snapshots for our book
        # model: delta sequencing is easy to desync under load and floods unhealthy.
        is_snapshot = True
        return [self._parse_book(payload, symbol=symbol, is_snapshot=is_snapshot)]

    def _parse_book(
        self,
        item: dict[str, Any],
        *,
        symbol: str,
        is_snapshot: bool,
    ) -> MarketDataEvent:
        bids = [
            OrderBookLevel(price=dec(level[0]), amount=dec(level[1]))
            for level in item.get("b") or []
            if isinstance(level, list) and len(level) >= 2
        ]
        asks = [
            OrderBookLevel(price=dec(level[0]), amount=dec(level[1]))
            for level in item.get("a") or []
            if isinstance(level, list) and len(level) >= 2
        ]
        # Prefer exchange event time when present; fall back to now.
        ts_ms = item.get("ts")
        if ts_ms is None and isinstance(item.get("cts"), (int, str)):
            ts_ms = item.get("cts")
        timestamp = datetime.now(UTC)
        try:
            if ts_ms is not None:
                ts_i = int(ts_ms)
                if ts_i > 10_000_000_000:
                    timestamp = datetime.fromtimestamp(ts_i / 1000, tz=UTC)
        except (TypeError, ValueError, OSError):
            timestamp = datetime.now(UTC)
        event = self.book_event(
            symbol=symbol,
            bids=bids,
            asks=asks,
            is_snapshot=is_snapshot,
            sequence=None,  # avoid gap-desync; snapshots replace the book
            timestamp=timestamp,
        )
        if bids and asks:
            event.tick = MarketTick(
                exchange=self.name,
                symbol=symbol,
                bid=bids[0].price,
                ask=asks[0].price,
                bid_size=bids[0].amount,
                ask_size=asks[0].amount,
                timestamp=timestamp,
                sequence=event.sequence,
            )
        return event
