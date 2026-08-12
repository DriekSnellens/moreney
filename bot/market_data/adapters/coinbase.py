"""Coinbase Advanced Trade public market-data WebSocket adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from bot.core.exchange_types import OrderBookLevel
from bot.market_data.adapters.base import PublicMarketDataAdapter, dec
from bot.market_data.models import MarketDataEvent, MarketTick


class CoinbasePublicAdapter(PublicMarketDataAdapter):
    name = "coinbase"
    ws_url = "wss://advanced-trade-ws.coinbase.com"

    def normalize_symbol(self, symbol: str) -> str:
        raw = symbol.upper().replace("_", "").replace("/", "").replace("-", "")
        return raw

    def to_exchange_symbol(self, symbol: str) -> str:
        internal = self.normalize_symbol(symbol)
        if internal.endswith("EUR") and len(internal) > 3:
            return f"{internal[:-3]}-EUR"
        if internal.endswith("USDT") and len(internal) > 4:
            return f"{internal[:-4]}-USDT"
        if internal.endswith("USD") and len(internal) > 3:
            return f"{internal[:-3]}-USD"
        return internal

    def build_subscribe_messages(self) -> list[str]:
        product_ids = [self.to_exchange_symbol(s) for s in self.symbols]
        payload = {
            "type": "subscribe",
            "product_ids": product_ids,
            "channel": "level2",
        }
        return [json.dumps(payload)]

    def parse_message(self, raw: str | bytes) -> list[MarketDataEvent]:
        data = self.loads(raw)
        if not isinstance(data, dict):
            return []
        channel = data.get("channel") or data.get("type")
        if channel in {"subscriptions", "heartbeats"}:
            return [
                MarketDataEvent(
                    exchange=self.name,
                    symbol=self.symbols[0] if self.symbols else "",
                    event_type="heartbeat",
                    message=str(channel),
                )
            ]
        events: list[MarketDataEvent] = []
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            product = self.normalize_symbol(str(event.get("product_id") or ""))
            if etype == "snapshot":
                events.append(self._parse_l2(event, product, is_snapshot=True))
            elif etype == "update":
                events.append(self._parse_l2(event, product, is_snapshot=False))
            elif etype == "ticker" or "best_bid" in event:
                events.append(self._parse_ticker(event, product))
        # Legacy level2 message shape
        if data.get("type") == "l2update":
            product = self.normalize_symbol(str(data.get("product_id") or ""))
            events.append(self._parse_legacy_l2(data, product))
        if data.get("type") == "snapshot" and "bids" in data:
            product = self.normalize_symbol(str(data.get("product_id") or ""))
            events.append(
                self.book_event(
                    symbol=product,
                    bids=self.levels_from_pairs(data.get("bids") or []),
                    asks=self.levels_from_pairs(data.get("asks") or []),
                    is_snapshot=True,
                )
            )
        return events

    def _parse_l2(self, event: dict[str, Any], symbol: str, *, is_snapshot: bool) -> MarketDataEvent:
        updates = event.get("updates") or []
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            level = OrderBookLevel(price=dec(upd.get("price_level")), amount=dec(upd.get("new_quantity")))
            side = str(upd.get("side", "")).upper()
            if side == "BID":
                bids.append(level)
            elif side == "OFFER" or side == "ASK":
                asks.append(level)
        return self.book_event(symbol=symbol, bids=bids, asks=asks, is_snapshot=is_snapshot)

    def _parse_legacy_l2(self, data: dict[str, Any], symbol: str) -> MarketDataEvent:
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        for change in data.get("changes") or []:
            if len(change) < 3:
                continue
            side, price, size = change[0], change[1], change[2]
            level = OrderBookLevel(price=dec(price), amount=dec(size))
            if str(side).lower() == "buy":
                bids.append(level)
            else:
                asks.append(level)
        return self.book_event(symbol=symbol, bids=bids, asks=asks, is_snapshot=False)

    def _parse_ticker(self, event: dict[str, Any], symbol: str) -> MarketDataEvent:
        tick = MarketTick(
            exchange=self.name,
            symbol=symbol,
            bid=dec(event.get("best_bid")),
            ask=dec(event.get("best_ask")),
            bid_size=dec(event.get("best_bid_quantity")) if event.get("best_bid_quantity") else None,
            ask_size=dec(event.get("best_ask_quantity")) if event.get("best_ask_quantity") else None,
            last=dec(event.get("price")) if event.get("price") else None,
            timestamp=datetime.now(UTC),
        )
        return MarketDataEvent(
            exchange=self.name,
            symbol=symbol,
            event_type="tick",
            timestamp=tick.timestamp,
            received_at=tick.received_at,
            tick=tick,
        )
