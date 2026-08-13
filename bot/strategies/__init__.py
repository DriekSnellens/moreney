"""Strategy layer.

Strategies consume MarketSnapshot data and emit TradeOpportunity objects.
They must never import or call exchange clients / APIs.
"""

from bot.strategies.arbitrage import CrossExchangeArbitrageStrategy, walk_book
from bot.strategies.base import BaseStrategy
from bot.strategies.maker_inventory import MakerInventoryStrategy
from bot.strategies.stub import StubStrategy
from bot.strategies.triangle_bridge import CompositeDeskStrategy, TriangleBridgeStrategy

__all__ = [
    "BaseStrategy",
    "CompositeDeskStrategy",
    "CrossExchangeArbitrageStrategy",
    "MakerInventoryStrategy",
    "StubStrategy",
    "TriangleBridgeStrategy",
    "walk_book",
]
