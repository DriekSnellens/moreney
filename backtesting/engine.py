"""Simple historical replay engine (scaffolding — not production-grade yet)."""

from dataclasses import dataclass, field
from decimal import Decimal

from bot.core.interfaces import ProfitabilityEngine, RiskEngine, Strategy
from bot.core.models import MarketSnapshot, PortfolioSnapshot, ProfitabilityResult, TradeOpportunity


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""

    opportunities: list[TradeOpportunity] = field(default_factory=list)
    profitability: list[ProfitabilityResult] = field(default_factory=list)
    approved_count: int = 0
    rejected_count: int = 0
    total_expected_net_profit_usd: Decimal = Decimal("0")


class BacktestEngine:
    """Replays snapshots through strategy → profitability → risk (no live orders)."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        profitability: ProfitabilityEngine,
        risk: RiskEngine,
        portfolio: PortfolioSnapshot | None = None,
    ) -> None:
        self._strategy = strategy
        self._profitability = profitability
        self._risk = risk
        self._portfolio = portfolio or PortfolioSnapshot(equity_usd=Decimal("10000"))

    async def run(self, snapshots: list[MarketSnapshot]) -> BacktestResult:
        result = BacktestResult()

        for snapshot in snapshots:
            opportunities = await self._strategy.evaluate(snapshot)
            for opportunity in opportunities:
                result.opportunities.append(opportunity)
                profit = await self._profitability.evaluate(opportunity)
                result.profitability.append(profit)
                decision = await self._risk.evaluate(opportunity, profit, self._portfolio)
                if decision.approved:
                    result.approved_count += 1
                    result.total_expected_net_profit_usd += profit.net_profit_usd
                else:
                    result.rejected_count += 1

        return result
