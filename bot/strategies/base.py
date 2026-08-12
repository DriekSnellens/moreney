"""Abstract strategy base. No exchange imports allowed in this package."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from bot.core.models import MarketSnapshot, TradeOpportunity


class BaseStrategy(ABC):
    """Strategy contract: market data → list[TradeOpportunity].

    Strategies must never call exchange APIs or executors. They only emit
    ``TradeOpportunity`` objects for downstream profitability / risk / execution.
    """

    name: str

    def __init__(self, name: str | None = None) -> None:
        if name is not None:
            self.name = name

    @abstractmethod
    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        """Produce zero or more opportunities from a single normalized snapshot."""

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
    ) -> list[TradeOpportunity]:
        """Evaluate one or more venue snapshots.

        Default behaviour evaluates each snapshot independently. Multi-venue
        strategies (e.g. cross-exchange arbitrage) should override this.
        """
        opportunities: list[TradeOpportunity] = []
        for snapshot in snapshots:
            opportunities.extend(await self.evaluate(snapshot))
        return opportunities
