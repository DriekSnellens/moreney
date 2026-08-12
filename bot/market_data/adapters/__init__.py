"""Public market-data adapters (WebSocket parsers). No private APIs."""

from bot.market_data.adapters.base import PublicMarketDataAdapter
from bot.market_data.adapters.binance import BinancePublicAdapter
from bot.market_data.adapters.bitvavo import BitvavoPublicAdapter
from bot.market_data.adapters.coinbase import CoinbasePublicAdapter
from bot.market_data.adapters.kraken import KrakenPublicAdapter

__all__ = [
    "BinancePublicAdapter",
    "BitvavoPublicAdapter",
    "CoinbasePublicAdapter",
    "KrakenPublicAdapter",
    "PublicMarketDataAdapter",
]
