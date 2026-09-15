"""Extended backtest engine with evaluate_markets and optional execution replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bot.core.interfaces import Executor, ProfitabilityEngine, RiskEngine, Strategy
from bot.core.models import MarketSnapshot, PortfolioSnapshot, ProfitabilityResult, TradeOpportunity


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""

    opportunities: list[TradeOpportunity] = field(default_factory=list)
    profitability: list[ProfitabilityResult] = field(default_factory=list)
    approved_count: int = 0
    rejected_count: int = 0
    executed_count: int = 0
    total_expected_net_profit_usd: Decimal = Decimal("0")


class BacktestEngine:
    """Replays snapshots through strategy → profitability → risk (+ optional executor)."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        profitability: ProfitabilityEngine,
        risk: RiskEngine,
        portfolio: PortfolioSnapshot | None = None,
        executor: Executor | None = None,
        global_engine: Any | None = None,
    ) -> None:
        self._strategy = strategy
        self._profitability = profitability
        self._risk = risk
        self._portfolio = portfolio or PortfolioSnapshot(equity_usd=Decimal("10000"))
        self._executor = executor
        self._global_engine = global_engine

    async def run(
        self,
        snapshots: list[MarketSnapshot],
        *,
        batch_size: int = 50,
    ) -> BacktestResult:
        result = BacktestResult()
        buffer: list[MarketSnapshot] = []

        for snapshot in snapshots:
            buffer.append(snapshot)
            if len(buffer) < batch_size and snapshot is not snapshots[-1]:
                continue

            opportunities = await self._evaluate_buffer(buffer)
            buffer = []

            if self._global_engine is not None:
                ranked, _all = await self._global_engine.evaluate_batch(
                    opportunities,
                    self._portfolio,
                    venue_snapshots=snapshots,
                )
                for scored in ranked:
                    result.opportunities.append(scored.opportunity)
                    result.profitability.append(scored.profitability)
                    if scored.risk_decision and scored.risk_decision.approved:
                        result.approved_count += 1
                        result.total_expected_net_profit_usd += scored.profitability.net_profit_usd
                        if self._executor is not None and scored.risk_decision:
                            result.executed_count += 1
                    else:
                        result.rejected_count += 1
                continue

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

    async def _evaluate_buffer(self, buffer: list[MarketSnapshot]) -> list[TradeOpportunity]:
        evaluate_markets = getattr(self._strategy, "evaluate_markets", None)
        if callable(evaluate_markets):
            try:
                return list(await evaluate_markets(buffer, equity=self._portfolio.equity_usd))
            except TypeError:
                return list(await evaluate_markets(buffer))
        opportunities: list[TradeOpportunity] = []
        for snap in buffer:
            opportunities.extend(await self._strategy.evaluate(snap))
        return opportunities
