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
        # Last raw payload per key — skip JSON decode when Redis returns identical bytes.
        self._last_raw: dict[str, str] = {}
        # Polling efficiency counters (process-local).
        self.poll_stats: dict[str, int] = {
            "keys_seen": 0,
            "keys_unchanged": 0,
            "keys_changed": 0,
            "keys_missing": 0,
            "hydrates": 0,
        }

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

    async def set_funding_rates(self, rates: dict[str, str]) -> None:
        key = self._key("funding", "rates")
        await self._set(key, json.dumps(rates))

    async def get_funding_rates(self) -> dict[str, str]:
        raw = await self._get(self._key("funding", "rates"))
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    async def set_equity_quotes(self, quotes: dict[str, dict[str, str]]) -> None:
        key = self._key("equity", "quotes")
        await self._set(key, json.dumps(quotes))

    async def get_equity_quotes(self) -> dict[str, dict[str, str]]:
        raw = await self._get(self._key("equity", "quotes"))
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for sym, payload in data.items():
            if isinstance(payload, dict):
                out[str(sym)] = {str(k): str(v) for k, v in payload.items()}
        return out

    async def fetch_hydrate_raw(
        self,
        *,
        exchanges: list[str],
        symbols: list[str],
    ) -> dict[str, str | None]:
        """One Redis round-trip (pipeline) for all hydrate keys.

        Returns mapping key → raw string (or None). Keys use the same ``md:``
        layout as individual get_* helpers. Callers should treat identical
        consecutive payloads as unchanged to skip JSON decoding.
        """
        keys: list[str] = []
        for exchange in exchanges:
            keys.append(self._key("health", exchange.lower()))
            for symbol in symbols:
                sym = symbol.upper()
                keys.append(self._key("book", exchange.lower(), sym))
                keys.append(self._key("tick", exchange.lower(), sym))
        keys.append(self._key("funding", "rates"))
        keys.append(self._key("equity", "quotes"))

        values = await self._mget(keys)
        return dict(zip(keys, values, strict=True))

    def consume_changed_raw(self, key: str, raw: str | None) -> str | None:
        """Return raw only when it differs from the last seen payload for key.

        Identical Redis payloads (common between 100ms polls) return None so the
        caller skips JSON decode / book rebuild while retaining the prior local
        state — freshness semantics unchanged because the publisher value is
        still the latest snapshot.
        """
        self.poll_stats["keys_seen"] += 1
        if raw is None:
            self.poll_stats["keys_missing"] += 1
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        prev = self._last_raw.get(key)
        if prev is not None and prev == raw:
            self.poll_stats["keys_unchanged"] += 1
            return None
        self._last_raw[key] = raw
        self.poll_stats["keys_changed"] += 1
        return raw

    def mark_hydrate(self) -> None:
        self.poll_stats["hydrates"] += 1

    def polling_efficiency(self) -> dict[str, Any]:
        seen = max(1, self.poll_stats["keys_seen"])
        changed = self.poll_stats["keys_changed"]
        unchanged = self.poll_stats["keys_unchanged"]
        return {
            **self.poll_stats,
            "unchanged_ratio": round(unchanged / seen, 4),
            "changed_ratio": round(changed / seen, 4),
            "useful_key_updates": changed,
            "total_key_observations": self.poll_stats["keys_seen"],
        }

    async def pipeline_set(self, items: list[tuple[str, str]]) -> None:
        """Batch SET+EXPIRE for publisher hot path (one RTT when Redis present)."""
        for key, value in items:
            self._memory[key] = value
        if self._redis is None or not items:
            return
        try:
            pipe = self._redis.pipeline(transaction=False)
            for key, value in items:
                pipe.set(key, value, ex=self._ttl)
            await pipe.execute()
        except Exception as exc:
            logger.debug("REDIS_CACHE_PIPELINE_SET_FAILED error=%s", exc)
            for key, value in items:
                await self._set(key, value)

    def book_key(self, exchange: str, symbol: str) -> str:
        return self._key("book", exchange.lower(), symbol.upper())

    def tick_key(self, exchange: str, symbol: str) -> str:
        return self._key("tick", exchange.lower(), symbol.upper())

    def health_key(self, exchange: str) -> str:
        return self._key("health", exchange.lower())

    def funding_key(self) -> str:
        return self._key("funding", "rates")

    def equity_key(self) -> str:
        return self._key("equity", "quotes")

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
                    if isinstance(value, bytes):
                        value = value.decode("utf-8")
                    return value
            except Exception as exc:
                logger.debug("REDIS_CACHE_GET_FAILED key=%s error=%s", key, exc)
        return self._memory.get(key)

    async def _mget(self, keys: list[str]) -> list[str | None]:
        if not keys:
            return []
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline(transaction=False)
                for key in keys:
                    pipe.get(key)
                raws = await pipe.execute()
                out: list[str | None] = []
                for raw in raws:
                    if raw is None:
                        out.append(None)
                    elif isinstance(raw, bytes):
                        out.append(raw.decode("utf-8"))
                    else:
                        out.append(str(raw))
                return out
            except Exception as exc:
                logger.debug("REDIS_CACHE_PIPELINE_GET_FAILED error=%s", exc)
        return [self._memory.get(k) for k in keys]
