"""Tests for market data providers."""

from decimal import Decimal

import pytest

from bot.core.exceptions import ExchangeError
from bot.core.models import MarketSnapshot
from bot.exchanges.stub import StubExchangeClient
from bot.market_data.provider import ExchangeMarketDataProvider, StaticMarketDataProvider


@pytest.mark.asyncio
async def test_static_provider_returns_snapshot(market_snapshot: MarketSnapshot) -> None:
    provider = StaticMarketDataProvider({market_snapshot.symbol: market_snapshot})
    got = await provider.get_snapshot("btcusdt")
    assert got.symbol == "BTCUSDT"
    assert got.bid == Decimal("100.00")


@pytest.mark.asyncio
async def test_static_provider_missing_symbol_raises() -> None:
    provider = StaticMarketDataProvider()
    with pytest.raises(ExchangeError, match="No market snapshot"):
        await provider.get_snapshot("ETHUSDT")


@pytest.mark.asyncio
async def test_static_provider_get_snapshots(market_snapshot: MarketSnapshot) -> None:
    provider = StaticMarketDataProvider({market_snapshot.symbol: market_snapshot})
    snaps = await provider.get_snapshots(["BTCUSDT"])
    assert len(snaps) == 1


@pytest.mark.asyncio
async def test_exchange_market_data_provider(settings) -> None:
    client = StubExchangeClient(settings)
    provider = ExchangeMarketDataProvider(client)
    snap = await provider.get_snapshot("BTCUSDT")
    assert snap.ask > snap.bid
