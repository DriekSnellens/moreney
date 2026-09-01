"""Identical-payload skip audit — each cached component independently."""

from __future__ import annotations

import asyncio
import json
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
        "market_data_exchanges": "binance,okx",
        "market_data_symbols": "BTCEUR",
        "max_market_data_age_ms": 60_000.0,
        "perf_instrumentation_enabled": False,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _book(*, nonce: int, bid: str = "100", ask: str = "101") -> OrderBook:
    return OrderBook(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal(bid), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal(ask), amount=Decimal("1"))],
        timestamp=datetime.now(UTC),
        nonce=nonce,
        metadata={"exchange": "binance", "synchronized": True},
    )


@pytest.mark.asyncio
async def test_byte_compare_cheaper_than_decode() -> None:
    cache = MarketDataCache()
    book = _book(nonce=1)
    payload = book.model_dump(mode="json")
    payload["exchange"] = "binance"
    raw = json.dumps(payload)
    key = cache.book_key("binance", "BTCEUR")
    assert cache.consume_changed_raw(key, raw) is not None
    import time

    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        assert cache.consume_changed_raw(key, raw) is None
    cmp_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n):
        data = json.loads(raw)
        data.pop("exchange", None)
        OrderBook.model_validate(data)
    decode_s = time.perf_counter() - t0
    assert cmp_s < decode_s


@pytest.mark.asyncio
async def test_book_change_invalidates_only_book_not_tick() -> None:
    cache = MarketDataCache(redis_client=None)
    await cache.set_book("binance", _book(nonce=1, bid="100"))
    await cache.set_tick(
        MarketTick(
            exchange="binance",
            symbol="BTCEUR",
            bid=Decimal("100"),
            ask=Decimal("101"),
            sequence=1,
        )
    )
    service = MarketDataService(
        _settings(market_data_exchanges="binance"),
        cache=cache,
        start_websockets=False,
    )
    await service.hydrate_from_redis()
    tick1 = service._ticks[("binance", "BTCEUR")]  # noqa: SLF001
    # Change only book
    await cache.set_book("binance", _book(nonce=2, bid="99"))
    # Force consume path: clear last_raw for book only by writing new raw via set
    # (set does not update last_raw — hydrate will see change)
    await service.hydrate_from_redis()
    local = service.get_local_book("binance", "BTCEUR")
    assert local is not None and local.sequence == 2
    tick2 = service._ticks[("binance", "BTCEUR")]  # noqa: SLF001
    # Tick payload unchanged → object retained (same sequence)
    assert tick2.sequence == tick1.sequence
    assert tick2.bid == tick1.bid


@pytest.mark.asyncio
async def test_health_change_applies_when_books_unchanged() -> None:
    cache = MarketDataCache()
    await cache.set_book("binance", _book(nonce=1))
    await cache.set_health(
        ExchangeHealth(
            exchange="binance",
            connected=True,
            stale=False,
            synchronized=True,
            message_rate_per_sec=10.0,
        )
    )
    service = MarketDataService(
        _settings(market_data_exchanges="binance"),
        cache=cache,
        start_websockets=False,
    )
    await service.hydrate_from_redis()
    assert service._remote_health["binance"].message_rate_per_sec == 10.0  # noqa: SLF001
    await cache.set_health(
        ExchangeHealth(
            exchange="binance",
            connected=True,
            stale=True,
            synchronized=True,
            message_rate_per_sec=1.0,
        )
    )
    await service.hydrate_from_redis()
    health = service._remote_health["binance"]  # noqa: SLF001
    assert health.stale is True
    assert health.message_rate_per_sec == 1.0
    # Book still at nonce 1
    local = service.get_local_book("binance", "BTCEUR")
    assert local is not None and local.sequence == 1


@pytest.mark.asyncio
async def test_tick_change_independent_of_book() -> None:
    cache = MarketDataCache()
    await cache.set_book("binance", _book(nonce=5))
    await cache.set_tick(
        MarketTick(
            exchange="binance",
            symbol="BTCEUR",
            bid=Decimal("100"),
            ask=Decimal("101"),
            sequence=5,
        )
    )
    service = MarketDataService(
        _settings(market_data_exchanges="binance"),
        cache=cache,
        start_websockets=False,
    )
    await service.hydrate_from_redis()
    await cache.set_tick(
        MarketTick(
            exchange="binance",
            symbol="BTCEUR",
            bid=Decimal("100.5"),
            ask=Decimal("101.5"),
            sequence=6,
        )
    )
    await service.hydrate_from_redis()
    tick = service._ticks[("binance", "BTCEUR")]  # noqa: SLF001
    assert tick.sequence == 6
    assert tick.bid == Decimal("100.5")
    assert service.get_local_book("binance", "BTCEUR").sequence == 5  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_no_mutable_book_shared_across_cycles() -> None:
    """Collecting books twice must not share a mutated list between cycles."""
    from bot.paper.runner import PaperRunner
    from bot.paper.store import PaperTradingStore
    from bot.risk.risk_engine import RiskEngine

    settings = _settings(
        market_data_exchanges="binance,okx",
        market_data_symbols="BTCEUR",
        paper_persist_path="/tmp/moreney_payload_audit.json",
        paper_starting_eur=1000.0,
        paper_maker_enabled=True,
        paper_hmm_enabled=False,
        global_funding_strategy_enabled=False,
    )
    service = MarketDataService(settings, start_websockets=False)
    service.inject_snapshot(
        "binance", "BTCEUR", bid=Decimal("100"), ask=Decimal("101"), sequence=1
    )
    service.inject_snapshot(
        "okx", "BTCEUR", bid=Decimal("100.5"), ask=Decimal("101.5"), sequence=1
    )
    runner = PaperRunner(
        settings,
        market_data=service,
        risk_engine=RiskEngine(settings),
        store=PaperTradingStore(settings),
    )
    books1 = runner._collect_books()  # noqa: SLF001
    books2 = runner._collect_books()  # noqa: SLF001
    # Same content, but nested dicts are fresh containers.
    assert books1 is not books2
    assert books1["binance"] is not books2["binance"]


@pytest.mark.asyncio
async def test_payload_change_cannot_be_missed() -> None:
    cache = MarketDataCache()
    key = cache.book_key("binance", "BTCEUR")
    raw1 = json.dumps(
        {
            **_book(nonce=1).model_dump(mode="json"),
            "exchange": "binance",
        }
    )
    raw2 = json.dumps(
        {
            **_book(nonce=2, bid="90").model_dump(mode="json"),
            "exchange": "binance",
        }
    )
    assert cache.consume_changed_raw(key, raw1) == raw1
    assert cache.consume_changed_raw(key, raw1) is None
    assert cache.consume_changed_raw(key, raw2) == raw2
    assert cache.consume_changed_raw(key, raw2) is None


@pytest.mark.asyncio
async def test_funding_and_equity_independent_cache() -> None:
    cache = MarketDataCache()
    await cache.set_funding_rates({"BTCUSDT": "0.01"})
    await cache.set_equity_quotes({"SPY.US": {"bid": "1", "ask": "2"}})
    service = MarketDataService(
        _settings(market_data_exchanges="binance", global_funding_strategy_enabled=True),
        cache=cache,
        start_websockets=False,
    )
    await service.hydrate_from_redis()
    # Unchanged second hydrate
    await service.hydrate_from_redis()
    poll = cache.polling_efficiency()
    assert poll["keys_unchanged"] > 0
    await cache.set_funding_rates({"BTCUSDT": "0.02"})
    await service.hydrate_from_redis()
    assert cache.poll_stats["keys_changed"] >= 1
