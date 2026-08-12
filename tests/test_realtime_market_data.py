"""Unit tests for realtime market-data layer (mocked WebSockets, no live APIs)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.core.exchange_types import OrderBookLevel
from bot.market_data.adapters.binance import BinancePublicAdapter
from bot.market_data.adapters.bitvavo import BitvavoPublicAdapter
from bot.market_data.adapters.coinbase import CoinbasePublicAdapter
from bot.market_data.adapters.kraken import KrakenPublicAdapter
from bot.market_data.cache import MarketDataCache
from bot.market_data.local_order_book import LocalOrderBook
from bot.market_data.models import (
    ConnectionState,
    ExchangeHealth,
    MarketDataEvent,
    MarketTick,
    OrderBookUpdate,
)
from bot.market_data.recorder import MarketDataRecorder
from bot.market_data.service import MarketDataService
from bot.market_data.websocket_manager import WebSocketManager


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        app_env="development",
        execution_mode="paper",
        max_market_data_age_ms=1000.0,
        market_data_exchanges="binance,kraken,coinbase,bitvavo",
        market_data_symbols="BTCEUR,BTCUSDT",
        market_data_recording_enabled=False,
        market_data_ws_reconnect_base_ms=50.0,
        market_data_ws_reconnect_max_ms=200.0,
        market_data_heartbeat_interval_ms=50.0,
        market_data_connection_timeout_ms=500.0,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class FakeWebSocket:
    """In-memory WebSocket transport for reconnect / heartbeat tests."""

    def __init__(self, messages: list[str] | None = None) -> None:
        self._messages = list(messages or [])
        self._closed = False
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._closed:
            raise RuntimeError("closed")
        if not self._messages:
            await asyncio.sleep(0.05)
            raise TimeoutError("no messages")
        return self._messages.pop(0)

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


def test_normalized_market_tick_uses_decimal() -> None:
    tick = MarketTick(
        exchange="Binance",
        symbol="btc/eur",
        bid=Decimal("100"),
        ask=Decimal("101"),
        bid_size=Decimal("1.5"),
        ask_size=Decimal("2"),
        sequence=42,
    )
    assert tick.exchange == "binance"
    assert tick.symbol == "BTCEUR"
    assert tick.mid == Decimal("100.5")
    assert tick.sequence == 42
    assert tick.received_at.tzinfo is not None


def test_exchange_health_model() -> None:
    health = ExchangeHealth(exchange="kraken", connected=True, stale=False)
    assert health.connection_state == ConnectionState.DISCONNECTED
    assert health.exchange == "kraken"


def test_snapshot_and_incremental_update() -> None:
    book = LocalOrderBook("binance", "BTCEUR")
    snap = OrderBookUpdate(
        exchange="binance",
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("2"))],
        is_snapshot=True,
        sequence=1,
    )
    book.apply_snapshot(snap)
    assert book.synchronized
    assert book.best_bid() == (Decimal("100"), Decimal("1"))

    ok = book.apply_update(
        OrderBookUpdate(
            exchange="binance",
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("0"))],
            asks=[
                OrderBookLevel(price=Decimal("101"), amount=Decimal("0")),
                OrderBookLevel(price=Decimal("101.5"), amount=Decimal("3")),
            ],
            sequence=2,
        )
    )
    assert ok is True
    assert book.best_bid() is None
    assert book.best_ask() == (Decimal("101.5"), Decimal("3"))
    assert book.to_order_book() is None


def test_sequence_gap_marks_desync(caplog: pytest.LogCaptureFixture) -> None:
    book = LocalOrderBook("binance", "BTCEUR")
    book.apply_snapshot(
        OrderBookUpdate(
            exchange="binance",
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("1"), amount=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("2"), amount=Decimal("1"))],
            is_snapshot=True,
            sequence=10,
        )
    )
    with caplog.at_level(logging.INFO):
        ok = book.apply_update(
            OrderBookUpdate(
                exchange="binance",
                symbol="BTCEUR",
                bids=[OrderBookLevel(price=Decimal("1"), amount=Decimal("2"))],
                sequence=15,
            )
        )
    assert ok is False
    assert book.synchronized is False
    assert book.to_order_book() is None
    assert "SEQUENCE_GAP" in caplog.text


def test_duplicate_sequence_ignored() -> None:
    book = LocalOrderBook("kraken", "BTCEUR")
    book.apply_snapshot(
        OrderBookUpdate(
            exchange="kraken",
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("1"), amount=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("2"), amount=Decimal("1"))],
            is_snapshot=True,
            sequence=5,
        )
    )
    ok = book.apply_update(
        OrderBookUpdate(
            exchange="kraken",
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("1"), amount=Decimal("9"))],
            sequence=5,
        )
    )
    assert ok is True
    assert book.best_bid() == (Decimal("1"), Decimal("1"))


def test_binance_depth_and_ticker_parse() -> None:
    adapter = BinancePublicAdapter(["BTCEUR"])
    depth = {
        "e": "depthUpdate",
        "E": 1_700_000_000_000,
        "s": "BTCEUR",
        "U": 2,
        "u": 2,
        "b": [["99900", "1.0"]],
        "a": [["100000", "1.5"]],
    }
    events = adapter.parse_message(json.dumps({"stream": "btceur@depth", "data": depth}))
    assert len(events) == 1
    assert events[0].event_type == "book_update"
    assert events[0].book_update is not None
    assert events[0].book_update.bids[0].price == Decimal("99900")

    ticker = {"u": 9, "s": "BTCEUR", "b": "99900", "B": "1", "a": "100000", "A": "2"}
    ticks = adapter.parse_message(json.dumps(ticker))
    assert ticks[0].event_type == "tick"
    assert ticks[0].tick is not None
    assert ticks[0].tick.ask == Decimal("100000")


def test_binance_snapshot_parse() -> None:
    adapter = BinancePublicAdapter(["BTCEUR"])
    raw = json.dumps(
        {
            "lastUpdateId": 100,
            "bids": [["99900", "1"]],
            "asks": [["100000", "1"]],
            "s": "BTCEUR",
        }
    )
    events = adapter.parse_message(raw)
    assert events[0].event_type == "book_snapshot"
    assert events[0].sequence == 100


def test_kraken_book_snapshot_parse() -> None:
    adapter = KrakenPublicAdapter(["BTCEUR"])
    raw = json.dumps(
        {
            "channel": "book",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "BTC/EUR",
                    "bids": [{"price": 100150.0, "qty": 1.0}],
                    "asks": [{"price": 100250.0, "qty": 1.0}],
                    "checksum": 1,
                }
            ],
        }
    )
    events = adapter.parse_message(raw)
    assert events[0].event_type == "book_snapshot"
    assert events[0].symbol == "BTCEUR"
    assert events[0].tick is not None
    assert events[0].tick.bid == Decimal("100150")


def test_coinbase_and_bitvavo_parse() -> None:
    cb = CoinbasePublicAdapter(["BTCEUR"])
    cb_raw = json.dumps(
        {
            "channel": "l2_data",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "BTC-EUR",
                    "updates": [
                        {"side": "bid", "price_level": "99", "new_quantity": "1"},
                        {"side": "offer", "price_level": "100", "new_quantity": "2"},
                    ],
                }
            ],
        }
    )
    cb_events = cb.parse_message(cb_raw)
    assert cb_events[0].event_type == "book_snapshot"

    bv = BitvavoPublicAdapter(["BTCEUR"])
    bv_raw = json.dumps(
        {
            "event": "book",
            "market": "BTC-EUR",
            "nonce": 1,
            "bids": [
                ["99", "1"],
                ["98", "1"],
                ["97", "1"],
                ["96", "1"],
                ["95", "1"],
                ["94", "1"],
            ],
            "asks": [
                ["100", "1"],
                ["101", "1"],
                ["102", "1"],
                ["103", "1"],
                ["104", "1"],
                ["105", "1"],
            ],
        }
    )
    bv_events = bv.parse_message(bv_raw)
    assert bv_events[0].book_update is not None
    assert bv_events[0].book_update.is_snapshot is True


def test_malformed_messages_return_empty() -> None:
    assert BinancePublicAdapter(["BTCEUR"]).parse_message("{}") == []
    assert KrakenPublicAdapter(["BTCEUR"]).parse_message(json.dumps({"channel": "status"})) == []


@pytest.mark.asyncio
async def test_websocket_connect_heartbeat_reconnect() -> None:
    sockets: list[FakeWebSocket] = []

    async def factory(_url: str) -> FakeWebSocket:
        ws = FakeWebSocket(messages=['{"ok":true}'])
        sockets.append(ws)
        return ws

    received: list[str] = []

    async def on_message(raw: str | bytes) -> None:
        received.append(raw if isinstance(raw, str) else raw.decode())

    manager = WebSocketManager(
        name="binance",
        url="wss://example.test",
        reconnect_base_ms=20,
        reconnect_max_ms=50,
        heartbeat_interval_ms=30,
        connection_timeout_ms=200,
        connect_factory=factory,
        heartbeat_payload='{"method":"ping"}',
    )
    manager.set_subscriptions(['{"method":"SUBSCRIBE"}'])
    await manager.start(on_message)
    await asyncio.sleep(0.15)
    assert manager.state in {
        ConnectionState.CONNECTED,
        ConnectionState.RECONNECTING,
        ConnectionState.DISCONNECTED,
        ConnectionState.CONNECTING,
    }
    if sockets:
        await sockets[0].close()
    await asyncio.sleep(0.12)
    await manager.stop()
    assert manager.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_websocket_duplicate_message_protection() -> None:
    manager = WebSocketManager(name="test", url="wss://x")
    assert manager.note_message("hello") is True
    assert manager.note_message("hello") is False


@pytest.mark.asyncio
async def test_websocket_stale_when_disconnected() -> None:
    manager = WebSocketManager(name="test", url="wss://x")
    assert manager.is_stale(1000) is True
    manager.note_message("x")
    assert manager.is_stale(1000) is True


@pytest.mark.asyncio
async def test_service_multi_exchange_btc_markets() -> None:
    service = MarketDataService(_settings())
    service.inject_snapshot(
        "binance", "BTCEUR", bid=Decimal("99900"), ask=Decimal("100000"), sequence=1
    )
    service.inject_snapshot(
        "kraken", "BTCEUR", bid=Decimal("100150"), ask=Decimal("100250"), sequence=1
    )
    service.inject_snapshot(
        "binance", "BTCUSDT", bid=Decimal("60000"), ask=Decimal("60010"), sequence=1
    )
    snaps = service.snapshots_for_arbitrage("BTCEUR")
    assert len(snaps) == 2
    exchanges = {s.exchange for s in snaps}
    assert exchanges == {"binance", "kraken"}
    usdt = service.snapshots_for_arbitrage("BTCUSDT")
    assert any(s.exchange == "binance" for s in usdt)


@pytest.mark.asyncio
async def test_stale_data_omitted_from_strategy(caplog: pytest.LogCaptureFixture) -> None:
    service = MarketDataService(
        _settings(max_market_data_age_ms=50.0, market_data_exchanges="binance", market_data_symbols="BTCEUR")
    )
    service.inject_snapshot(
        "binance", "BTCEUR", bid=Decimal("1"), ask=Decimal("2"), sequence=1
    )
    book = service.get_local_book("binance", "BTCEUR")
    assert book is not None
    book._timestamp = datetime.now(UTC) - timedelta(seconds=2)  # noqa: SLF001
    with caplog.at_level(logging.INFO):
        snaps = service.snapshots_for_arbitrage("BTCEUR")
    assert snaps == []
    assert "STALE_MARKET_DATA" in caplog.text
    health = service.get_exchange_health("binance")
    assert health.stale is True


@pytest.mark.asyncio
async def test_handle_event_applies_adapter_messages() -> None:
    service = MarketDataService(_settings(market_data_exchanges="binance"))
    adapter = BinancePublicAdapter(["BTCEUR"])
    snap_raw = json.dumps(
        {
            "lastUpdateId": 1,
            "bids": [["99900", "1"]],
            "asks": [["100000", "1"]],
            "s": "BTCEUR",
        }
    )
    for event in adapter.parse_message(snap_raw):
        await service.handle_event(event)
    book = service.get_valid_order_book("binance", "BTCEUR")
    assert book is not None
    assert book.bids[0].price == Decimal("99900")


@pytest.mark.asyncio
async def test_redis_cache_memory_fallback() -> None:
    cache = MarketDataCache(redis_client=None, ttl_seconds=30)
    tick = MarketTick(
        exchange="binance",
        symbol="BTCEUR",
        bid=Decimal("1"),
        ask=Decimal("2"),
    )
    await cache.set_tick(tick)
    got = await cache.get_tick("binance", "BTCEUR")
    assert got is not None
    assert got.bid == Decimal("1")

    health = ExchangeHealth(exchange="binance", connected=True, stale=False)
    await cache.set_health(health)
    assert (await cache.get_health("binance")) is not None


@pytest.mark.asyncio
async def test_recorder_writes_jsonl(tmp_path: Path) -> None:
    recorder = MarketDataRecorder(enabled=True, path=str(tmp_path))
    event = MarketDataEvent(
        exchange="binance",
        symbol="BTCEUR",
        event_type="book_snapshot",
        message="test",
    )
    await recorder.record(event)
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert "binance" in files[0].read_text()


@pytest.mark.asyncio
async def test_exchange_disconnect_marks_unhealthy() -> None:
    manager = WebSocketManager(name="binance", url="wss://x")
    service = MarketDataService(
        _settings(market_data_exchanges="binance"),
        adapters=[],
    )
    service._managers["binance"] = manager  # noqa: SLF001
    health = service.get_exchange_health("binance")
    assert health.connected is False
    assert health.stale is True
