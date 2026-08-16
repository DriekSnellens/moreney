"""OKX public market-data WebSocket adapter (spot books5 — free, no API key)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.core.exchange_types import OrderBookLevel
from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class OkxPublicAdapter(PublicMarketDataAdapter):
    """Public OKX spot order books. No credentials / no private channels."""

    name = "okx"
    ws_url = "wss://ws.okx.com:8443/ws/v5/public"

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-", "").replace("_", "").replace("/", "")

    def to_exchange_symbol(self, symbol: str) -> str:
        internal = self.normalize_symbol(symbol)
        if internal.endswith("USDT") and len(internal) > 4:
            return f"{internal[:-4]}-USDT"
        if internal.endswith("EUR") and len(internal) > 3:
            return f"{internal[:-3]}-EUR"
        if internal.endswith("USD") and len(internal) > 3:
            return f"{internal[:-3]}-USD"
        return internal

    def build_subscribe_messages(self) -> list[str]:
        args = [
            {"channel": "books5", "instId": self.to_exchange_symbol(symbol)}
            for symbol in self.symbols
        ]
        return [json.dumps({"op": "subscribe", "args": args})]

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        if data.get("event") in {"subscribe", "unsubscribe", "error"}:
            return []
        if data.get("event") == "channel-conn-count":
            return [
                MarketDataEvent(
                    exchange=self.name,
                    symbol=self.symbols[0] if self.symbols else "",
                    event_type="heartbeat",
                    message="channel-conn-count",
                )
            ]
        arg = data.get("arg") or {}
        channel = arg.get("channel") if isinstance(arg, dict) else None
        if channel != "books5":
            return []
        payload = data.get("data")
        if not isinstance(payload, list) or not payload:
            return []
        inst = str(arg.get("instId") or "")
        symbol = self.normalize_symbol(inst)
        action = str(data.get("action") or "snapshot").lower()
        is_snapshot = action != "update"
        events: list[MarketDataEvent] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            events.append(self._parse_book(item, symbol=symbol, is_snapshot=is_snapshot))
        return events

    def _parse_book(
        self,
        item: dict[str, Any],
        *,
        symbol: str,
        is_snapshot: bool,
    ) -> MarketDataEvent:
        bids = [
            OrderBookLevel(price=dec(level[0]), amount=dec(level[1]))
            for level in item.get("bids") or []
            if isinstance(level, list) and len(level) >= 2
        ]
        asks = [
            OrderBookLevel(price=dec(level[0]), amount=dec(level[1]))
            for level in item.get("asks") or []
            if isinstance(level, list) and len(level) >= 2
        ]
        ts_ms = item.get("ts")
        timestamp = (
            datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
            if ts_ms is not None
            else datetime.now(UTC)
        )
        event = self.book_event(
            symbol=symbol,
            bids=bids,
            asks=asks,
            is_snapshot=is_snapshot,
            sequence=int(item["seqId"]) if item.get("seqId") is not None else None,
            timestamp=timestamp if ts_ms is not None else None,
            exchange_ts_available=ts_ms is not None,
            timestamp_quality="MEDIUM" if ts_ms is not None else "UNSUPPORTED",
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
