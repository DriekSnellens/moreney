"""Stub strategy that emits a simple TradeOpportunity for scaffolding / tests.

Does not call exchange APIs. Uses only the provided MarketSnapshot.
"""

from decimal import Decimal

from bot.core.enums import OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.strategies.base import BaseStrategy


class StubStrategy(BaseStrategy):
    """Emits a buy opportunity when the spread is wider than a threshold."""

    name = "stub_spread"

    def __init__(
        self,
        *,
        min_spread: Decimal = Decimal("0.05"),
        quantity: Decimal = Decimal("1"),
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._min_spread = min_spread
        self._quantity = quantity

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        if snapshot.spread < self._min_spread:
            return []

        return [
            TradeOpportunity(
                strategy_name=self.name,
                symbol=snapshot.symbol,
                side=OpportunitySide.BUY,
                quantity=self._quantity,
                entry_price=snapshot.ask,
                expected_exit_price=snapshot.ask + snapshot.spread,
                confidence=0.5,
                rationale=f"Spread {snapshot.spread} >= min_spread {self._min_spread}",
                market=snapshot,
            )
        ]
