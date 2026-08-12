"""Strategy layer.

Strategies consume MarketSnapshot data and emit TradeOpportunity objects.
They must never import or call exchange clients / APIs.
"""

from bot.strategies.arbitrage import CrossExchangeArbitrageStrategy, walk_book
from bot.strategies.base import BaseStrategy
from bot.strategies.stub import StubStrategy

__all__ = [
    "BaseStrategy",
    "CrossExchangeArbitrageStrategy",
    "StubStrategy",
    "walk_book",
]
