"""Factory for exchange clients based on Settings.exchange_name."""

from __future__ import annotations

from bot.core.config import Settings
from bot.core.exceptions import ConfigurationError
from bot.core.interfaces import ExchangeClient
from bot.exchanges.binance import BinanceExchange
from bot.exchanges.bitvavo import BitvavoExchange
from bot.exchanges.coinbase import CoinbaseExchange
from bot.exchanges.kraken import KrakenExchange
from bot.exchanges.okx import OkxExchange
from bot.exchanges.stub import StubExchangeClient


def create_exchange_client(
    settings: Settings,
    *,
    enable_trading: bool = False,
) -> ExchangeClient:
    """Create an exchange adapter.

    Live order placement stays disabled unless ``enable_trading=True``.
    """
    name = (settings.exchange_name or "stub").strip().lower()
    if name in {"stub", "paper", "mock"}:
        return StubExchangeClient(settings)
    if name == "binance":
        return BinanceExchange(settings, enable_trading=enable_trading)
    if name == "kraken":
        return KrakenExchange(settings, enable_trading=enable_trading)
    if name in {"coinbase", "coinbasepro", "coinbaseadvanced"}:
        return CoinbaseExchange(settings, enable_trading=enable_trading)
    if name == "bitvavo":
        return BitvavoExchange(settings, enable_trading=enable_trading)
    if name == "okx":
        return OkxExchange(settings, enable_trading=enable_trading)
    raise ConfigurationError(f"Unsupported exchange_name: {settings.exchange_name}")
