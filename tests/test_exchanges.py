"""Unit tests for the exchange abstraction layer (mocked; no live network)."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from bot.core.enums import OpportunitySide, OrderStatus
from bot.core.exceptions import (
    ExchangeRateLimitError,
    ExchangeTradingDisabledError,
    ExchangeTransientError,
)
from bot.core.models import OrderRequest
from bot.exchanges.base import BaseExchangeClient
from bot.exchanges.binance import BinanceExchange
from bot.exchanges.bitvavo import BitvavoExchange
from bot.exchanges.ccxt_adapter import CcxtExchangeAdapter, sanitize_okx_client_order_id
from bot.exchanges.coinbase import CoinbaseExchange
from bot.exchanges.factory import create_exchange_client
from bot.exchanges.kraken import KrakenExchange
from bot.exchanges.okx import OkxExchange
from bot.exchanges.retry import RetryPolicy, compute_backoff, with_retries
from bot.exchanges.sanitize import redact_mapping, redact_text
from bot.exchanges.stub import StubExchangeClient
from bot.exchanges.symbols import to_ccxt_symbol, to_internal_symbol


class FakeCcxtExchange:
    """Minimal async CCXT-like double."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail_times: dict[str, int] = {}
        self.errors: dict[str, Exception] = {}
        self.ticker = {
            "symbol": "BTC/USDT",
            "bid": 100,
            "ask": 100.2,
            "last": 100.1,
            "baseVolume": 12.5,
            "timestamp": 1_700_000_000_000,
        }
        self.order_book = {
            "bids": [[100.0, 1.5], [99.9, 2.0]],
            "asks": [[100.2, 1.0], [100.3, 3.0]],
            "timestamp": 1_700_000_000_000,
            "nonce": 7,
        }
        self.trading_fee = {"maker": 0.001, "taker": 0.002, "percentage": True}
        self.balance = {
            "free": {"USDT": 1000, "BTC": 0.5},
            "used": {"USDT": 50, "BTC": 0},
            "total": {"USDT": 1050, "BTC": 0.5},
        }
        self.open_orders = [
            {
                "id": "o1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "status": "open",
                "amount": 1,
                "filled": 0,
                "price": 99,
                "timestamp": 1_700_000_000_000,
            }
        ]
        self.order = {
            "id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "status": "open",
            "amount": 1,
            "filled": 0.25,
            "price": 99,
            "average": 99,
            "clientOrderId": "c1",
            "fee": {"cost": 0.01, "currency": "USDT"},
            "timestamp": 1_700_000_000_000,
        }
        self.created_order = {
            "id": "new1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "status": "open",
            "amount": 1,
            "filled": 0,
            "price": 100,
            "timestamp": 1_700_000_000_000,
        }
        self.cancelled_order = {
            "id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "status": "canceled",
            "amount": 1,
            "filled": 0,
            "price": 99,
            "timestamp": 1_700_000_000_000,
        }

    async def _maybe_fail(self, name: str) -> None:
        self.calls.append((name, (), {}))
        remaining = self.fail_times.get(name, 0)
        if remaining > 0:
            self.fail_times[name] = remaining - 1
            raise self.errors.get(name, ExchangeTransientError("transient"))

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        await self._maybe_fail("fetch_ticker")
        payload = dict(self.ticker)
        payload["symbol"] = symbol
        return payload

    async def fetch_order_book(self, symbol: str, limit: int | None = None) -> dict[str, Any]:
        await self._maybe_fail("fetch_order_book")
        book = dict(self.order_book)
        if limit:
            book["bids"] = book["bids"][:limit]
            book["asks"] = book["asks"][:limit]
        return book

    async def fetch_trading_fee(self, symbol: str) -> dict[str, Any]:
        await self._maybe_fail("fetch_trading_fee")
        return dict(self.trading_fee)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        await self._maybe_fail("fetch_open_orders")
        return list(self.open_orders)

    async def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        await self._maybe_fail("fetch_order")
        payload = dict(self.order)
        payload["id"] = order_id
        payload["symbol"] = symbol
        return payload

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._maybe_fail("create_order")
        payload = dict(self.created_order)
        payload.update(
            {
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "type": order_type,
            }
        )
        return payload

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        await self._maybe_fail("cancel_order")
        payload = dict(self.cancelled_order)
        payload["id"] = order_id
        payload["symbol"] = symbol
        return payload

    async def fetch_balance(self) -> dict[str, Any]:
        await self._maybe_fail("fetch_balance")
        return dict(self.balance)

    async def load_markets(self) -> dict[str, Any]:
        await self._maybe_fail("load_markets")
        return {"BTC/USDT": {}}

    async def close(self) -> None:
        self.calls.append(("close", (), {}))


def _order_request() -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCUSDT",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        client_order_id="cid-1",
    )


@pytest.mark.asyncio
async def test_stub_fetch_ticker(settings) -> None:
    client = StubExchangeClient(settings)
    snap = await client.fetch_ticker("btcusdt")
    assert snap.symbol == "BTCUSDT"
    assert snap.bid == Decimal("100")


@pytest.mark.asyncio
async def test_stub_place_order_records_history(settings) -> None:
    client = StubExchangeClient(settings)
    order = _order_request()
    result = await client.place_order(order)
    assert len(client.placed_orders) == 1
    assert result.opportunity_id == order.opportunity_id


@pytest.mark.asyncio
async def test_stub_get_balances(settings) -> None:
    client = StubExchangeClient(settings)
    portfolio = await client.get_balances()
    assert portfolio.equity_usd == Decimal("10000")


@pytest.mark.asyncio
async def test_stub_order_book_fees_open_orders_cancel_health(settings) -> None:
    client = StubExchangeClient(settings)
    book = await client.fetch_order_book("BTC/USDT", limit=2)
    assert len(book.bids) == 2
    fees = await client.fetch_trading_fees("BTCUSDT")
    assert fees.taker == Decimal("0.001")
    placed = await client.place_order(_order_request())
    exchange_order_id = placed.metadata["exchange_order_id"]
    open_orders = await client.fetch_open_orders("BTCUSDT")
    assert any(o.id == exchange_order_id for o in open_orders)
    cancelled = await client.cancel_order(exchange_order_id, "BTCUSDT")
    assert cancelled.status == OrderStatus.CANCELLED
    health = await client.health_check()
    assert health.healthy is True


@pytest.mark.asyncio
async def test_base_exchange_methods_not_implemented(settings) -> None:
    client = BaseExchangeClient(settings)
    with pytest.raises(NotImplementedError):
        await client.fetch_ticker("BTCUSDT")
    with pytest.raises(NotImplementedError):
        await client.fetch_order_book("BTCUSDT")
    with pytest.raises(NotImplementedError):
        await client.health_check()


def test_no_withdraw_on_exchange_types() -> None:
    forbidden = {"withdraw", "withdrawal", "transfer_out", "send_funds", "cash_out"}
    for cls in (
        BaseExchangeClient,
        StubExchangeClient,
        CcxtExchangeAdapter,
        BinanceExchange,
        KrakenExchange,
        CoinbaseExchange,
        BitvavoExchange,
    ):
        methods = {name.lower() for name in dir(cls)}
        assert methods.isdisjoint(forbidden)


def test_symbol_helpers() -> None:
    assert to_internal_symbol("btc/usdt") == "BTCUSDT"
    assert to_ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert to_ccxt_symbol("eth-usd") == "ETH/USD"


def test_redact_secrets() -> None:
    redacted = redact_mapping({"apiKey": "super-secret", "enableRateLimit": True})
    assert redacted["apiKey"] == "***REDACTED***"
    assert redacted["enableRateLimit"] is True
    assert "super-secret" not in redact_text("api_key=super-secret used")


def test_compute_backoff_respects_cap_and_retry_after() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=2.0, jitter=False)
    assert compute_backoff(0, policy) == 1.0
    assert compute_backoff(5, policy) == 2.0
    assert compute_backoff(0, policy, retry_after=1.5) == 1.5


@pytest.mark.asyncio
async def test_with_retries_eventually_succeeds() -> None:
    attempts = {"n": 0}

    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ExchangeTransientError("nope")
        return "ok"

    result = await with_retries(
        flaky,
        policy=RetryPolicy(max_attempts=5, base_delay=0.01, jitter=False),
        operation_name="flaky",
    )
    assert result == "ok"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_with_retries_raises_rate_limit() -> None:
    async def always_limited() -> None:
        raise ExchangeRateLimitError("slow down", retry_after=0.01)

    with pytest.raises(ExchangeRateLimitError):
        await with_retries(
            always_limited,
            policy=RetryPolicy(max_attempts=2, base_delay=0.01, jitter=False),
            operation_name="limited",
        )


@pytest.mark.asyncio
async def test_ccxt_adapter_normalizes_market_data(settings) -> None:
    fake = FakeCcxtExchange()
    client = BinanceExchange(settings, exchange=fake, enable_trading=False)
    ticker = await client.fetch_ticker("btcusdt")
    assert ticker.symbol == "BTCUSDT"
    assert ticker.bid == Decimal("100")
    book = await client.fetch_order_book("BTCUSDT", limit=1)
    assert len(book.asks) == 1
    fees = await client.fetch_trading_fees("BTCUSDT")
    assert fees.maker == Decimal("0.001")
    balances = await client.get_balances()
    assets = {b.asset for b in balances.balances}
    assert "USDT" in assets and "BTC" in assets


@pytest.mark.asyncio
async def test_ccxt_adapter_orders_and_dry_run(settings) -> None:
    fake = FakeCcxtExchange()
    client = CcxtExchangeAdapter(
        settings, ccxt_id="binance", exchange=fake, enable_trading=False
    )
    dry = await client.place_order(_order_request())
    assert dry.metadata["dry_run"] is True
    assert not any(name == "create_order" for name, _, _ in fake.calls)

    live = CcxtExchangeAdapter(
        settings, ccxt_id="binance", exchange=fake, enable_trading=True
    )
    placed = await live.place_order(_order_request())
    assert placed.metadata["dry_run"] is False
    assert placed.metadata["exchange_order_id"] == "new1"
    assert any(name == "create_order" for name, _, _ in fake.calls)

    open_orders = await live.fetch_open_orders("BTCUSDT")
    assert open_orders[0].id == "o1"
    status = await live.fetch_order("o1", "BTCUSDT")
    assert status.filled_quantity == Decimal("0.25")
    cancelled = await live.cancel_order("o1", "BTCUSDT")
    assert cancelled.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_blocked_when_trading_disabled(settings) -> None:
    fake = FakeCcxtExchange()
    client = KrakenExchange(settings, exchange=fake, enable_trading=False)
    with pytest.raises(ExchangeTradingDisabledError):
        await client.cancel_order("o1", "BTCUSD")


@pytest.mark.asyncio
async def test_ccxt_retry_on_transient_ticker(settings) -> None:
    fake = FakeCcxtExchange()
    fake.fail_times["fetch_ticker"] = 2
    fake.errors["fetch_ticker"] = ExchangeTransientError("timeout")
    client = CoinbaseExchange(
        settings,
        exchange=fake,
        enable_trading=False,
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0.01, jitter=False),
    )
    # Inject retryable failures via adapter path: Fake raises ExchangeTransientError
    # before adapter mapping; call adapter method which uses with_retries around _call.
    # Make Fake raise a Network-like error through adapter by patching _call usage:
    ticker = await client.fetch_ticker("BTCUSD")
    assert ticker.last == Decimal("100.1")


@pytest.mark.asyncio
async def test_health_check_success_and_failure(settings) -> None:
    fake = FakeCcxtExchange()
    client = BitvavoExchange(settings, exchange=fake)
    ok = await client.health_check()
    assert ok.healthy is True

    failing = FakeCcxtExchange()

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise ExchangeTransientError("down")

    failing.load_markets = boom  # type: ignore[method-assign]
    client_bad = BitvavoExchange(settings, exchange=failing)
    bad = await client_bad.health_check()
    assert bad.healthy is False
    assert "down" in bad.message


@pytest.mark.asyncio
async def test_rate_limit_error_maps_and_retries(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeCcxtExchange()
    client = BinanceExchange(
        settings,
        exchange=fake,
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.01, jitter=False),
    )

    async def limited(*_a: Any, **_k: Any) -> Any:
        raise ExchangeRateLimitError("429", retry_after=0.01)

    monkeypatch.setattr(client, "_call", limited)
    with pytest.raises(ExchangeRateLimitError):
        await client.fetch_ticker("BTCUSDT")


def test_factory_creates_expected_clients(settings) -> None:
    settings.exchange_name = "stub"
    assert isinstance(create_exchange_client(settings), StubExchangeClient)
    settings.exchange_name = "binance"
    assert isinstance(create_exchange_client(settings), BinanceExchange)
    settings.exchange_name = "kraken"
    assert isinstance(create_exchange_client(settings), KrakenExchange)
    settings.exchange_name = "coinbase"
    assert isinstance(create_exchange_client(settings), CoinbaseExchange)
    settings.exchange_name = "bitvavo"
    assert isinstance(create_exchange_client(settings), BitvavoExchange)
    settings.exchange_name = "okx"
    assert isinstance(create_exchange_client(settings), OkxExchange)


def test_secret_not_logged_on_init(settings, caplog: pytest.LogCaptureFixture) -> None:
    from pydantic import SecretStr

    settings.exchange_api_key = SecretStr("LIVE_KEY_VALUE")
    settings.exchange_api_secret = SecretStr("LIVE_SECRET_VALUE")
    fake = FakeCcxtExchange()
    with caplog.at_level(logging.INFO):
        client = BinanceExchange(settings, exchange=fake, enable_trading=False)
        # Trigger the initialization log path used when constructing a real client.
        owning = BinanceExchange(settings, enable_trading=False)
        # Replace exchange factory internals by assigning after partial init log:
        owning._exchange = fake
        owning._owns_exchange = False
        assert "LIVE_SECRET_VALUE" not in caplog.text
        assert "LIVE_KEY_VALUE" not in caplog.text
        assert client.credential_fingerprint()["api_key_present"] is True
        assert "***REDACTED***" == redact_mapping({"api_secret": "LIVE_SECRET_VALUE"})["api_secret"]


def test_exchange_modules_do_not_import_strategies() -> None:
    import inspect

    import bot.exchanges.binance as binance
    import bot.exchanges.bitvavo as bitvavo
    import bot.exchanges.ccxt_adapter as ccxt_adapter
    import bot.exchanges.coinbase as coinbase
    import bot.exchanges.kraken as kraken

    for module in (binance, kraken, coinbase, bitvavo, ccxt_adapter):
        source = inspect.getsource(module)
        assert "bot.strategies" not in source
        assert "TradeOpportunity" not in source


def test_sanitize_okx_client_order_id_strips_hyphens() -> None:
    out = sanitize_okx_client_order_id("micro-ccb77bc79e1f4fca")
    assert "-" not in out
    assert len(out) <= 32
    assert out[0].isalpha()
    assert out.startswith("micro")
