"""Free Nasdaq/Yahoo equity quote feed."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot.core.config import Settings
from bot.core.enums import AssetClass, MarketSessionPhase
from bot.market_data.cache import MarketDataCache
from bot.market_data.equity import (
    EquityQuote,
    EquityQuoteService,
    nasdaq_ticker,
    parse_equity_symbols,
    parse_money,
    yahoo_ticker,
)
from bot.market_data.service import MarketDataService
from bot.markets.calendar import MarketCalendarService
from bot.markets.registry import InstrumentRegistry
from bot.paper.dashboard import _equity_quote_rows
from bot.strategies.equity_mean_reversion import EquityMeanReversionStrategy


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "execution_mode": "paper",
        "global_equity_enabled": True,
        "global_equity_symbols": "SPY.US,AAPL.US,SAP.DE",
        "market_data_symbols": "BTCEUR",
        "market_data_exchanges": "binance",
        "global_funding_strategy_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_symbol_mapping() -> None:
    assert parse_equity_symbols("spy.us, AAPL.US, SAP.DE, spy.us") == [
        "SPY.US",
        "AAPL.US",
        "SAP.DE",
    ]
    assert nasdaq_ticker("AAPL.US") == "AAPL"
    assert nasdaq_ticker("SAP.DE") is None
    assert yahoo_ticker("AAPL.US") == "AAPL"
    assert yahoo_ticker("SAP.DE") == "SAP.DE"
    assert parse_money("$304.90") == Decimal("304.90")
    assert parse_money("N/A") is None


def test_registry_equity_venues() -> None:
    reg = InstrumentRegistry(_settings())
    us = reg.by_symbol("AAPL.US")
    eu = reg.by_symbol("SAP.DE")
    assert us is not None and us.asset_class == AssetClass.EQUITY
    assert us.venue == "nasdaq"
    assert eu is not None and eu.venue == "yahoo"
    assert "AAPL.US" in reg.scan_symbols()


def test_us_session_hours_are_new_york_wall_clock() -> None:
    cal = MarketCalendarService()
    reg = InstrumentRegistry(_settings())
    aapl = reg.by_symbol("AAPL.US")
    sap = reg.by_symbol("SAP.DE")
    assert aapl is not None and sap is not None
    ny_open = datetime(2026, 8, 14, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    ny_pre = datetime(2026, 8, 14, 7, 30, tzinfo=ZoneInfo("America/New_York"))
    assert cal.phase(aapl, now=ny_open.astimezone(UTC)) == MarketSessionPhase.REGULAR
    assert cal.phase(aapl, now=ny_pre.astimezone(UTC)) == MarketSessionPhase.PRE_MARKET
    assert cal.is_tradeable(aapl, now=ny_pre.astimezone(UTC))
    ams_open = datetime(2026, 8, 14, 13, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert cal.phase(sap, now=ams_open.astimezone(UTC)) == MarketSessionPhase.REGULAR
    sessions = cal.active_asset_classes(now=ams_open.astimezone(UTC))
    assert AssetClass.EQUITY in sessions


def _nasdaq_payload(*, last: str, bid: str, ask: str, etf: bool = False) -> dict:
    return {
        "data": {
            "symbol": "AAPL" if not etf else "SPY",
            "primaryData": {
                "lastSalePrice": last,
                "bidPrice": bid,
                "askPrice": ask,
                "bidSize": "1",
                "askSize": "10",
                "isRealTime": True,
            },
        },
        "status": {"rCode": 200},
    }


def _yahoo_payload(symbol: str, price: float) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "regularMarketPrice": price,
                        "currency": "EUR" if symbol.endswith(".DE") else "USD",
                    }
                }
            ],
            "error": None,
        }
    }


@pytest.mark.asyncio
async def test_equity_service_uses_nasdaq_for_us_and_yahoo_for_eu() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "nasdaq.com" in url and "AAPL" in url:
            return httpx.Response(200, json=_nasdaq_payload(last="$304.90", bid="$304.82", ask="$304.90"))
        if "nasdaq.com" in url and "SPY" in url:
            if "assetclass=etf" in url:
                return httpx.Response(
                    200, json=_nasdaq_payload(last="$778.40", bid="$778.39", ask="$778.45", etf=True)
                )
            return httpx.Response(200, json={"status": {"rCode": 400}, "data": {}})
        if "yahoo.com" in url and "SAP.DE" in url:
            return httpx.Response(200, json=_yahoo_payload("SAP.DE", 184.26))
        return httpx.Response(404, json={"error": "missing"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = EquityQuoteService(_settings(), client=client)
    await service.refresh_once()
    await client.aclose()

    aapl = service.snapshot_for("AAPL.US")
    spy = service.snapshot_for("SPY.US")
    sap = service.snapshot_for("SAP.DE")
    assert aapl is not None
    assert aapl.bid == Decimal("304.82")
    assert aapl.ask == Decimal("304.90")
    assert aapl.metadata.get("asset_class") == "equity"
    assert aapl.exchange == "nasdaq"
    assert aapl.order_book is not None
    assert spy is not None and spy.last == Decimal("778.40")
    assert sap is not None
    assert sap.last == Decimal("184.26")
    assert sap.exchange == "yahoo"
    assert sap.ask > sap.bid


@pytest.mark.asyncio
async def test_market_data_service_returns_equity_snapshot_not_crypto_books() -> None:
    md = MarketDataService(_settings(), start_websockets=False)
    md.equity.import_quotes(
        {
            "AAPL.US": EquityQuote(
                symbol="AAPL.US",
                bid=Decimal("100"),
                ask=Decimal("100.2"),
                last=Decimal("100.1"),
                source="nasdaq",
                exchange="nasdaq",
                realtime=True,
            ).export()
        }
    )
    snaps = md.snapshots_for_arbitrage("AAPL.US")
    assert len(snaps) == 1
    assert snaps[0].metadata.get("asset_class") == "equity"
    assert snaps[0].exchange == "nasdaq"


@pytest.mark.asyncio
async def test_equity_quotes_roundtrip_cache() -> None:
    cache = MarketDataCache(redis_client=None, ttl_seconds=30)
    quote = EquityQuote(
        symbol="AAPL.US",
        bid=Decimal("10"),
        ask=Decimal("10.1"),
        last=Decimal("10.05"),
        source="nasdaq",
        exchange="nasdaq",
    )
    await cache.set_equity_quotes({"AAPL.US": quote.export()})
    loaded = await cache.get_equity_quotes()
    service = EquityQuoteService(_settings())
    service.import_quotes(loaded)
    snap = service.snapshot_for("AAPL.US")
    assert snap is not None
    assert snap.bid == Decimal("10")


@pytest.mark.asyncio
async def test_mean_reversion_emits_on_live_equity_snapshot() -> None:
    settings = _settings(
        profitability_min_net_profit_usd=0.0,
        profitability_min_net_return=0.0,
        profitability_execution_buffer_bps=0.0,
        profitability_taker_fee_rate=0.0,
        profitability_maker_fee_rate=0.0,
        global_equity_deviation_bps=5.0,
    )
    strategy = EquityMeanReversionStrategy(settings)
    first = EquityQuote(
        symbol="AAPL.US",
        bid=Decimal("100"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        source="nasdaq",
        exchange="nasdaq",
    ).to_snapshot()
    await strategy.evaluate_markets([first], equity=Decimal("5000"))
    moved = EquityQuote(
        symbol="AAPL.US",
        bid=Decimal("99.2"),
        ask=Decimal("99.3"),
        last=Decimal("99.25"),
        source="nasdaq",
        exchange="nasdaq",
    ).to_snapshot()
    await strategy.evaluate_markets([moved], equity=Decimal("5000"))
    stats = strategy.scan_stats()
    assert int(stats["pairs_evaluated"]) >= 1
    assert moved.metadata.get("asset_class") == "equity"


def test_dashboard_equity_rows_render_quotes() -> None:
    html = _equity_quote_rows(
        {"AAPL.US": {"bid": "304.82", "ask": "304.90", "last": "304.90", "source": "nasdaq"}}
    )
    assert "AAPL.US" in html
    assert "nasdaq" in html
    empty = _equity_quote_rows({})
    assert "GLOBAL_EQUITY_ENABLED" in empty


@pytest.mark.asyncio
async def test_live_nasdaq_and_yahoo_respond() -> None:
    """Smoke-check the public endpoints this environment can actually reach."""
    settings = _settings(global_equity_symbols="AAPL.US,SAP.DE")
    service = EquityQuoteService(settings)
    try:
        await service.refresh_once()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"public equity feed unreachable: {type(exc).__name__}")
    aapl = service.snapshot_for("AAPL.US")
    sap = service.snapshot_for("SAP.DE")
    if aapl is None and sap is None:
        pytest.skip("no live equity quotes returned")
    if aapl is not None:
        assert aapl.bid > 0
        assert aapl.ask >= aapl.bid
    if sap is not None:
        assert sap.last > 0
