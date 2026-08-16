"""Binance public market-data WebSocket adapter (spot depth / bookTicker)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class BinancePublicAdapter(PublicMarketDataAdapter):
    name = "binance"
    ws_url = "wss://stream.binance.com:9443/stream"
    # Partial top-of-book snapshots every 100ms — far cheaper than full @depth diffs.
    depth_levels = 20

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-", "").replace("_", "").replace("/", "")

    def to_exchange_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol).lower()

    def build_subscribe_messages(self) -> list[str]:
        streams: list[str] = []
        depth = self.depth_levels
        for symbol in self.symbols:
            s = self.to_exchange_symbol(symbol)
            streams.append(f"{s}@depth{depth}@100ms")
            streams.append(f"{s}@bookTicker")
        payload = {"method": "SUBSCRIBE", "params": streams, "id": 1}
        return [json.dumps(payload)]

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        # Combined stream wrapper: {"stream": "...", "data": {...}}
        payload: dict[str, Any] = data.get("data") if "data" in data else data  # type: ignore[assignment]
        if not isinstance(payload, dict):
            return []
        if payload.get("result") is None and "id" in payload and "e" not in payload:
            return []  # subscribe ack
        event_type = payload.get("e")
        if event_type == "depthUpdate":
            return [self._parse_depth(payload)]
        if event_type == "bookTicker" or (
            "b" in payload and "a" in payload and "u" in payload and "e" not in payload
        ):
            return [self._parse_book_ticker(payload)]
        # Partial book snapshot style (@depth5/10/20@100ms)
        if "lastUpdateId" in payload and "bids" in payload and "asks" in payload:
            symbol = self.normalize_symbol(str(payload.get("s") or self.symbols[0]))
            # Combined partial-depth streams omit "s"; recover from stream name when present.
            if not payload.get("s") and isinstance(data.get("stream"), str):
                stream = data["stream"]
                symbol = self.normalize_symbol(stream.split("@", 1)[0])
            return [
                self.book_event(
                    symbol=symbol,
                    bids=self.levels_from_pairs(payload.get("bids") or []),
                    asks=self.levels_from_pairs(payload.get("asks") or []),
                    is_snapshot=True,
                    sequence=int(payload["lastUpdateId"]),
                )
            ]
        return []

    def _parse_depth(self, payload: dict[str, Any]) -> MarketDataEvent:
        symbol = self.normalize_symbol(str(payload.get("s", "")))
        return self.book_event(
            symbol=symbol,
            bids=self.levels_from_pairs(payload.get("b") or []),
            asks=self.levels_from_pairs(payload.get("a") or []),
            is_snapshot=False,
            sequence=int(payload.get("u") or 0),
            prev_sequence=int(payload["U"]) - 1 if payload.get("U") is not None else None,
            timestamp=datetime.fromtimestamp(int(payload.get("E", 0)) / 1000, tz=UTC)
            if payload.get("E")
            else None,
            exchange_ts_available=bool(payload.get("E")),
            timestamp_quality="MEDIUM" if payload.get("E") else "UNSUPPORTED",
        )

    def _parse_book_ticker(self, payload: dict[str, Any]) -> MarketDataEvent:
        symbol = self.normalize_symbol(str(payload.get("s", "")))
        tick = MarketTick(
            exchange=self.name,
            symbol=symbol,
            bid=dec(payload.get("b")),
            ask=dec(payload.get("a")),
            bid_size=dec(payload.get("B")),
            ask_size=dec(payload.get("A")),
            sequence=int(payload["u"]) if payload.get("u") is not None else None,
        )
        return MarketDataEvent(
            exchange=self.name,
            symbol=symbol,
            event_type="tick",
            timestamp=tick.timestamp,
            received_at=tick.received_at,
            sequence=tick.sequence,
            tick=tick,
        )
