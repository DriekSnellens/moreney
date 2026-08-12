"""Shared Redis market-data publisher/consumer wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.market_data.cache import MarketDataCache
from bot.market_data.models import ExchangeHealth, MarketTick
from bot.market_data.service import MarketDataService


def _settings(**kwargs: object) -> Settings:
    base = {
        "market_data_mode": "shared",
        "market_data_exchanges": "binance",
        "market_data_symbols": "BTCEUR",
        "max_market_data_age_ms": 5000.0,
        "market_data_redis_poll_ms": 50.0,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_shared_consumer_hydrates_from_cache() -> None:
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

    # Bypass redis_client check by attaching a dummy truthy client marker via memory-only
    # hydrate path (direct call) — shared start requires redis_client, hydrate does not.
    service = MarketDataService(_settings(), cache=cache, start_websockets=False)
    await service.hydrate_from_redis()

    snaps = service.snapshots_for_arbitrage("BTCEUR")
    assert len(snaps) == 1
    assert snaps[0].bid == Decimal("100")
    local = service.get_local_book("binance", "BTCEUR")
    assert local is not None and local.synchronized
    health = service.get_exchange_health("binance")
    assert health.synchronized is True
    assert health.message_rate_per_sec == 12.5


@pytest.mark.asyncio
async def test_shared_start_requires_redis() -> None:
    service = MarketDataService(_settings(), cache=MarketDataCache(), start_websockets=False)
    with pytest.raises(RuntimeError, match="Redis-backed"):
        await service.start_shared_consumer()
