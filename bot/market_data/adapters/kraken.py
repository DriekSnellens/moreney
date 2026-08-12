"""Kraken public market-data WebSocket adapter (v2 book channel)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.core.exchange_types import OrderBookLevel
from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class KrakenPublicAdapter(PublicMarketDataAdapter):
    name = "kraken"
    ws_url = "wss://ws.kraken.com/v2"

    _SYMBOL_MAP = {
        "BTCEUR": "BTC/EUR",
        "BTCUSDT": "BTC/USDT",
        "XBTEUR": "BTC/EUR",
    }

    def normalize_symbol(self, symbol: str) -> str:
        raw = symbol.upper().replace("-", "").replace("_", "").replace("/", "")
        if raw in {"XBTEUR", "XXBTZEUR"}:
            return "BTCEUR"
        if raw in {"XBTUSDT", "XXBTUSDT"}:
            return "BTCUSDT"
        return raw

    def to_exchange_symbol(self, symbol: str) -> str:
        internal = self.normalize_symbol(symbol)
        return self._SYMBOL_MAP.get(internal, f"{internal[:3]}/{internal[3:]}")

    def build_subscribe_messages(self) -> list[str]:
        symbols = [self.to_exchange_symbol(s) for s in self.symbols]
        payload = {
            "method": "subscribe",
            "params": {"channel": "book", "symbol": symbols, "depth": 10},
        }
        return [json.dumps(payload)]

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        channel = data.get("channel")
        msg_type = data.get("type")
        if channel == "heartbeat" or data.get("method") == "pong":
            return [
                MarketDataEvent(
                    exchange=self.name,
                    symbol=self.symbols[0] if self.symbols else "",
                    event_type="heartbeat",
                    message="heartbeat",
                )
            ]
        if channel != "book":
            return []
        payload = data.get("data")
        if not isinstance(payload, list) or not payload:
            return []
        events: list[MarketDataEvent] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            events.append(self._parse_book_item(item, is_snapshot=(msg_type == "snapshot")))
        return events

    def _parse_book_item(self, item: dict[str, Any], *, is_snapshot: bool) -> MarketDataEvent:
        symbol = self.normalize_symbol(str(item.get("symbol", "")))
        bids = [
            OrderBookLevel(price=dec(level.get("price")), amount=dec(level.get("qty")))
            for level in item.get("bids") or []
            if isinstance(level, dict)
        ]
        asks = [
            OrderBookLevel(price=dec(level.get("price")), amount=dec(level.get("qty")))
            for level in item.get("asks") or []
            if isinstance(level, dict)
        ]
        # Kraken checksums are integrity hashes, not monotonic sequences — never use
        # them for gap detection or LocalOrderBook sequencing.
        ts = datetime.now(UTC)
        if item.get("timestamp"):
            try:
                ts = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        event = self.book_event(
            symbol=symbol,
            bids=bids,
            asks=asks,
            is_snapshot=is_snapshot,
            sequence=None,
            timestamp=ts,
        )
        # Also emit top-of-book tick when levels present
        if bids and asks:
            tick = MarketTick(
                exchange=self.name,
                symbol=symbol,
                bid=bids[0].price,
                ask=asks[0].price,
                bid_size=bids[0].amount,
                ask_size=asks[0].amount,
                timestamp=ts,
                sequence=None,
            )
            event.tick = tick
        return event
