"""Market data ingestion and normalization.

This layer fetches and normalizes market state for strategies. It may use
exchange adapters, but strategies never call exchanges directly.
Public WebSocket / REST market data only — no private trading APIs.
"""

from bot.market_data.cache import MarketDataCache
from bot.market_data.local_order_book import LocalOrderBook
from bot.market_data.models import (
    ConnectionState,
    ExchangeHealth,
    MarketDataEvent,
    MarketTick,
    OrderBook,
    OrderBookLevel,
    OrderBookUpdate,
)
from bot.market_data.provider import (
    ExchangeMarketDataProvider,
    RealtimeMarketDataProvider,
    StaticMarketDataProvider,
)
from bot.market_data.recorder import MarketDataRecorder
from bot.market_data.service import MarketDataService
from bot.market_data.websocket_manager import WebSocketManager

__all__ = [
    "ConnectionState",
    "ExchangeHealth",
    "ExchangeMarketDataProvider",
    "LocalOrderBook",
    "MarketDataCache",
    "MarketDataEvent",
    "MarketDataRecorder",
    "MarketDataService",
    "MarketTick",
    "OrderBook",
    "OrderBookLevel",
    "OrderBookUpdate",
    "RealtimeMarketDataProvider",
    "StaticMarketDataProvider",
    "WebSocketManager",
]
