"""Redis hydrate pipeline + unchanged-payload skip."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.market_data.cache import MarketDataCache
from bot.market_data.models import ExchangeHealth, MarketTick
from bot.market_data.service import MarketDataService


class FakeRedis:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store
        self.gets = 0
        self.rtts = 0
        self.sets = 0

    async def get(self, key: str) -> str | None:
        self.gets += 1
        self.rtts += 1
        await asyncio.sleep(0.0005)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets += 1
        self.store[key] = value

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, str, str | None]] = []

    def get(self, key: str) -> "FakePipeline":
        self.ops.append(("get", key, None))
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> "FakePipeline":
        self.ops.append(("set", key, value))
        return self

    async def execute(self) -> list[str | None]:
        self.redis.rtts += 1
        await asyncio.sleep(0.0005)  # one RTT for the whole batch
        out: list[str | None] = []
        for op, key, value in self.ops:
            if op == "get":
                self.redis.gets += 1
                out.append(self.redis.store.get(key))
            else:
                self.redis.sets += 1
                assert value is not None
                self.redis.store[key] = value
                out.append(True)  # type: ignore[arg-type]
        return out


def _settings(**kwargs: object) -> Settings:
    base = {
        "market_data_mode": "shared",
        "market_data_exchanges": "binance,okx",
        "market_data_symbols": "BTCEUR,ETHEUR",
        "max_market_data_age_ms": 5000.0,
        "market_data_redis_poll_ms": 50.0,
        "perf_instrumentation_enabled": True,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


async def _seed(cache: MarketDataCache, exchanges: list[str], symbols: list[str]) -> None:
    for ex in exchanges:
        await cache.set_health(
            ExchangeHealth(
                exchange=ex,
                connected=True,
                stale=False,
                synchronized=True,
                message_rate_per_sec=10.0,
            )
        )
        for sym in symbols:
            book = OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
                asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("1"))],
                timestamp=datetime.now(UTC),
                nonce=1,
                metadata={"exchange": ex, "synchronized": True},
            )
            await cache.set_book(ex, book)
            await cache.set_tick(
                MarketTick(
                    exchange=ex,
                    symbol=sym,
                    bid=Decimal("100"),
                    ask=Decimal("101"),
                    sequence=1,
                )
            )


@pytest.mark.asyncio
async def test_hydrate_uses_single_pipeline_rtt() -> None:
    store: dict[str, str] = {}
    redis = FakeRedis(store)
    cache = MarketDataCache(redis_client=redis, ttl_seconds=30)
    settings = _settings()
    await _seed(cache, ["binance", "okx"], ["BTCEUR", "ETHEUR"])
    cache._memory.clear()
    redis.gets = 0
    redis.rtts = 0
    service = MarketDataService(settings, cache=cache, start_websockets=False)
    await service.hydrate_from_redis()
    # 2 health + 2*2*(book+tick) + funding + equity = 2+8+2 = 12 keys, 1 RTT
    assert redis.rtts == 1
    assert redis.gets == 12
    snaps = service.snapshots_for_arbitrage("BTCEUR")
    assert len(snaps) == 2


@pytest.mark.asyncio
async def test_hydrate_skips_unchanged_raw_second_poll() -> None:
    store: dict[str, str] = {}
    redis = FakeRedis(store)
    cache = MarketDataCache(redis_client=redis, ttl_seconds=30)
    settings = _settings()
    await _seed(cache, ["binance"], ["BTCEUR"])
    cache._memory.clear()
    service = MarketDataService(settings, cache=cache, start_websockets=False)
    await service.hydrate_from_redis()
    local = service.get_local_book("binance", "BTCEUR")
    assert local is not None
    seq1 = local.sequence
    # Second poll: identical payloads → no book rebuild (nonce unchanged).
    await service.hydrate_from_redis()
    local2 = service.get_local_book("binance", "BTCEUR")
    assert local2 is not None
    assert local2.sequence == seq1
    # Still one RTT per hydrate.
    assert redis.rtts == 2


@pytest.mark.asyncio
async def test_shared_consumer_still_hydrates_after_set() -> None:
    """Regression: set must not poison consume_changed_raw skip on first hydrate."""
    cache = MarketDataCache(redis_client=None, ttl_seconds=30)
    book = OrderBook(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("2"))],
        timestamp=datetime.now(UTC),
        nonce=7,
        metadata={"exchange": "binance", "synchronized": True},
    )
    await cache.set_book("binance", book)
    await cache.set_tick(
        MarketTick(
            exchange="binance",
            symbol="BTCEUR",
            bid=Decimal("100"),
            ask=Decimal("101"),
            sequence=7,
        )
    )
    await cache.set_health(
        ExchangeHealth(
            exchange="binance",
            connected=True,
            stale=False,
            synchronized=True,
            message_rate_per_sec=12.5,
        )
    )
    service = MarketDataService(
        _settings(market_data_exchanges="binance", market_data_symbols="BTCEUR"),
        cache=cache,
        start_websockets=False,
    )
    await service.hydrate_from_redis()
    snaps = service.snapshots_for_arbitrage("BTCEUR")
    assert len(snaps) == 1
    assert snaps[0].bid == Decimal("100")
