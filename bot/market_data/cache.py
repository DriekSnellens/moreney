"""Redis cache for latest market-data state only (not trade history)."""

from __future__ import annotations

import json
import logging
from typing import Any

from bot.core.exchange_types import OrderBook
from bot.market_data.models import ExchangeHealth, MarketTick

logger = logging.getLogger(__name__)


class MarketDataCache:
    """Stores current ticks / books / health with short TTL."""

    def __init__(self, redis_client: Any | None = None, *, ttl_seconds: int = 30) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._memory: dict[str, str] = {}

    @property
    def redis_client(self) -> Any | None:
        return self._redis

    def _key(self, *parts: str) -> str:
        return "md:" + ":".join(parts)

    async def set_tick(self, tick: MarketTick) -> None:
        key = self._key("tick", tick.exchange, tick.symbol)
        await self._set(key, tick.model_dump_json())

    async def get_tick(self, exchange: str, symbol: str) -> MarketTick | None:
        raw = await self._get(self._key("tick", exchange.lower(), symbol.upper()))
        return MarketTick.model_validate_json(raw) if raw else None

    async def set_book(self, exchange: str, book: OrderBook) -> None:
        key = self._key("book", exchange.lower(), book.symbol)
        payload = book.model_dump(mode="json")
        payload["exchange"] = exchange.lower()
        await self._set(key, json.dumps(payload))

    async def get_book(self, exchange: str, symbol: str) -> OrderBook | None:
        raw = await self._get(self._key("book", exchange.lower(), symbol.upper()))
        if not raw:
            return None
        data = json.loads(raw)
        data.pop("exchange", None)
        return OrderBook.model_validate(data)

    async def set_health(self, health: ExchangeHealth) -> None:
        key = self._key("health", health.exchange)
        await self._set(key, health.model_dump_json())

    async def get_health(self, exchange: str) -> ExchangeHealth | None:
        raw = await self._get(self._key("health", exchange.lower()))
        return ExchangeHealth.model_validate_json(raw) if raw else None

    async def _set(self, key: str, value: str) -> None:
        self._memory[key] = value
        if self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=self._ttl)
        except Exception as exc:
            logger.debug("REDIS_CACHE_SET_FAILED key=%s error=%s", key, exc)

    async def _get(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                value = await self._redis.get(key)
                if value is not None:
                    return value
            except Exception as exc:
                logger.debug("REDIS_CACHE_GET_FAILED key=%s error=%s", key, exc)
        return self._memory.get(key)
