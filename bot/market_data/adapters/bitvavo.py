"""Bitvavo public market-data WebSocket adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class BitvavoPublicAdapter(PublicMarketDataAdapter):
    name = "bitvavo"
    ws_url = "wss://ws.bitvavo.com/v2"

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-", "").replace("_", "").replace("/", "")

    def to_exchange_symbol(self, symbol: str) -> str:
        internal = self.normalize_symbol(symbol)
        if internal.endswith("EUR") and len(internal) > 3:
            return f"{internal[:-3]}-EUR"
        if internal.endswith("USDT") and len(internal) > 4:
            return f"{internal[:-4]}-USDT"
        return internal

    def build_subscribe_messages(self) -> list[str]:
        messages: list[str] = []
        for symbol in self.symbols:
            market = self.to_exchange_symbol(symbol)
            messages.append(
                json.dumps(
                    {
                        "action": "subscribe",
                        "channels": [{"name": "book", "markets": [market]}],
                    }
                )
            )
            messages.append(
                json.dumps(
                    {
                        "action": "subscribe",
                        "channels": [{"name": "ticker", "markets": [market]}],
                    }
                )
            )
        return messages

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        event = data.get("event")
        if event in {"subscribed", "pong"}:
            return [
                MarketDataEvent(
                    exchange=self.name,
                    symbol=self.symbols[0] if self.symbols else "",
                    event_type="heartbeat",
                    message=str(event),
                )
            ]
        if event == "book":
            return [self._parse_book(data)]
        if event == "ticker":
            return [self._parse_ticker(data)]
        return []

    def _parse_book(self, data: dict[str, Any]) -> MarketDataEvent:
        market = self.normalize_symbol(str(data.get("market") or ""))
        nonce = int(data["nonce"]) if data.get("nonce") is not None else None
        is_snapshot = bool(data.get("bids") and data.get("asks") and data.get("nonce") is not None)
        # Bitvavo sends full sides on snapshot-like messages; deltas otherwise.
        if data.get("bids") is not None and data.get("asks") is not None and len(data.get("bids") or []) > 5:
            is_snapshot = True
        return self.book_event(
            symbol=market,
            bids=self.levels_from_pairs(data.get("bids") or []),
            asks=self.levels_from_pairs(data.get("asks") or []),
            is_snapshot=is_snapshot,
            sequence=nonce,
            timestamp=datetime.now(UTC),
        )

    def _parse_ticker(self, data: dict[str, Any]) -> MarketDataEvent:
        market = self.normalize_symbol(str(data.get("market") or ""))
        tick = MarketTick(
            exchange=self.name,
            symbol=market,
            bid=dec(data.get("bid")),
            ask=dec(data.get("ask")),
            bid_size=dec(data.get("bidSize")) if data.get("bidSize") is not None else None,
            ask_size=dec(data.get("askSize")) if data.get("askSize") is not None else None,
            last=dec(data.get("last")) if data.get("last") is not None else None,
            timestamp=datetime.now(UTC),
        )
        return MarketDataEvent(
            exchange=self.name,
            symbol=market,
            event_type="tick",
            timestamp=tick.timestamp,
            received_at=tick.received_at,
            tick=tick,
        )
