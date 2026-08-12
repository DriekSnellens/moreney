"""Exchange adapters.

All credentials are loaded from environment / Settings. This package must never
expose withdrawal or transfer-out functionality. Strategy modules must not import
from here for signal generation.
"""

from bot.exchanges.base import BaseExchangeClient
from bot.exchanges.binance import BinanceExchange
from bot.exchanges.bitvavo import BitvavoExchange
from bot.exchanges.ccxt_adapter import CcxtExchangeAdapter
from bot.exchanges.coinbase import CoinbaseExchange
from bot.exchanges.factory import create_exchange_client
from bot.exchanges.kraken import KrakenExchange
from bot.exchanges.stub import StubExchangeClient

__all__ = [
    "BaseExchangeClient",
    "BinanceExchange",
    "BitvavoExchange",
    "CcxtExchangeAdapter",
    "CoinbaseExchange",
    "KrakenExchange",
    "StubExchangeClient",
    "create_exchange_client",
]
