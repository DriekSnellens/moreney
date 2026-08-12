"""Orchestrates the mandatory trading pipeline.

Pipeline:
  Market Data → Strategy → TradeOpportunity → Profitability → Risk → Paper Executor
                                                                      → FillTracker
                                                                      → Portfolio / Accounting

Risk approval is mandatory. Unapproved opportunities never reach the executor.
Paper execution never places real exchange orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bot.core.enums import OrderStatus, OrderType, RiskDecisionStatus
from bot.core.exceptions import RiskRejectedError
from bot.core.interfaces import (
    Executor,
    MarketDataProvider,
    PortfolioService,
    ProfitabilityEngine,
    RiskEngine,
    Strategy,
)
from bot.core.models import (
    ExecutionResult,
    OrderRequest,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)
from bot.execution.executor import ExecutionService
from bot.execution.paper_executor import PaperExecutor
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio


@dataclass
class TradeCycleResult:
    """Outcome of evaluating one symbol through the full pipeline."""

    symbol: str
    opportunities: list[TradeOpportunity] = field(default_factory=list)
    profitability: list[ProfitabilityResult] = field(default_factory=list)
    risk_decisions: list[RiskDecision] = field(default_factory=list)
    executions: list[ExecutionResult] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rejected: list[tuple[TradeOpportunity, RiskDecision]] = field(default_factory=list)
    portfolio_equity: Decimal | None = None


class TradingEngine:
    """Coordinates layers while preserving architectural boundaries."""

    def __init__(
        self,
        *,
        market_data: MarketDataProvider,
        strategy: Strategy,
        profitability: ProfitabilityEngine,
        risk: RiskEngine,
        portfolio: PortfolioService,
        executor: Executor,
    ) -> None:
        self._market_data = market_data
        self._strategy = strategy
        self._profitability = profitability
        self._risk = risk
        self._portfolio = portfolio
        self._executor = executor

    @property
    def paper_portfolio(self) -> PaperPortfolio | None:
        if isinstance(self._portfolio, PaperPortfolio):
            return self._portfolio
        if isinstance(self._executor, (PaperExecutor, ExecutionService)):
            return self._executor.portfolio  # type: ignore[no-any-return]
        return None

    async def run_once(self, symbol: str) -> TradeCycleResult:
        """Run a single evaluation cycle for ``symbol``."""
        result = TradeCycleResult(symbol=symbol.upper())
        venue_snapshots, primary = await self._load_snapshots(symbol)
        if venue_snapshots:
            evaluate_markets = getattr(self._strategy, "evaluate_markets", None)
            if callable(evaluate_markets):
                opportunities = await evaluate_markets(venue_snapshots)
            else:
                opportunities = await self._strategy.evaluate(primary)
        else:
            opportunities = await self._strategy.evaluate(primary)
        result.opportunities = opportunities

        portfolio = await self._portfolio.get_snapshot()

        for opportunity in opportunities:
            # Keep mark prices fresh for unrealized PnL when using paper portfolio.
            paper = self.paper_portfolio
            if paper is not None and opportunity.market is not None:
                paper.set_mark_price(opportunity.symbol, opportunity.market.last)

            profitability = await self._profitability.evaluate(opportunity)
            result.profitability.append(profitability)

            opportunity, risk_context = self._enrich_for_risk(opportunity, venue_snapshots)
            decision = await self._evaluate_risk(
                opportunity, profitability, portfolio, risk_context
            )
            result.risk_decisions.append(decision)

            if not decision.approved:
                result.rejected.append((opportunity, decision))
                continue

            buy_book = self._resolve_execution_book(opportunity, venue_snapshots, primary)
            execution = await self._execute_opportunity(
                opportunity, decision, snapshot_book=primary, order_book=buy_book
            )
            result.executions.append(execution)
            self._collect_order_fill(result, execution)

            # Refresh portfolio snapshot after fills for subsequent risk checks.
            portfolio = await self._portfolio.get_snapshot()

        if self.paper_portfolio is not None:
            result.portfolio_equity = self.paper_portfolio.state.total_equity
        return result

    async def _load_snapshots(self, symbol: str) -> tuple[list[Any], Any]:
        """Load multi-venue snapshots when available; else a single snapshot."""
        venue_getter = getattr(self._market_data, "get_venue_snapshots", None)
        if callable(venue_getter):
            venues = list(await venue_getter(symbol))
            if venues:
                return venues, venues[0]
        primary = await self._market_data.get_snapshot(symbol)
        return [], primary

    def _enrich_for_risk(
        self,
        opportunity: TradeOpportunity,
        venue_snapshots: list[Any],
    ) -> tuple[TradeOpportunity, Any]:
        """Attach freshness / health for RiskEngine without modifying it."""
        from bot.risk.models import RiskContext

        buy_ex = str(opportunity.metadata.get("buy_exchange") or "")
        age_ms: float | None = None
        healthy = True
        liquidity = None
        service = getattr(self._market_data, "service", None)
        if service is not None and buy_ex:
            ctx = service.build_risk_context(buy_ex, opportunity.symbol)
            age_ms = ctx.market_data_age_ms
            healthy = ctx.exchange_healthy
            liquidity = ctx.liquidity_base
        elif venue_snapshots:
            ages = [
                float(s.latency_ms)
                for s in venue_snapshots
                if getattr(s, "latency_ms", None) is not None
            ]
            age_ms = max(ages) if ages else None

        meta = {
            **opportunity.metadata,
            "exchange_healthy": healthy,
            "market_data_age_ms": age_ms,
        }
        enriched = opportunity.model_copy(update={"metadata": meta})
        risk_ctx = RiskContext(
            exchange_healthy=healthy,
            market_data_age_ms=age_ms,
            estimated_slippage_pct=Decimal(
                str(opportunity.metadata.get("estimated_slippage_pct", "0"))
            ),
            liquidity_base=liquidity,
            reference_price=(
                opportunity.market.mid if opportunity.market is not None else None
            ),
            current_price=(
                opportunity.market.last if opportunity.market is not None else None
            ),
            metadata={"source": "market_data_layer"},
        )
        return enriched, risk_ctx

    async def _evaluate_risk(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        portfolio: Any,
        risk_context: Any,
    ) -> RiskDecision:
        evaluate = self._risk.evaluate
        try:
            return await evaluate(
                opportunity, profitability, portfolio, context=risk_context
            )
        except TypeError:
            return await evaluate(opportunity, profitability, portfolio)

    def _resolve_execution_book(
        self,
        opportunity: TradeOpportunity,
        venue_snapshots: list[Any],
        primary: Any,
    ) -> Any:
        """Prefer buy-venue synchronized book for paper fills (real liquidity)."""
        buy_ex = opportunity.metadata.get("buy_exchange")
        if buy_ex:
            getter = getattr(self._market_data, "get_order_book", None)
            if callable(getter):
                book = getter(str(buy_ex), opportunity.symbol)
                if book is not None:
                    return book
            for snap in venue_snapshots:
                if getattr(snap, "exchange", None) == buy_ex and snap.order_book:
                    return snap.order_book
        if opportunity.market is not None and opportunity.market.order_book is not None:
            return opportunity.market.order_book
        return getattr(primary, "order_book", None)

    async def execute_approved(
        self,
        opportunity: TradeOpportunity,
        decision: RiskDecision,
    ) -> ExecutionResult:
        """Execute a previously approved opportunity (explicit gate)."""
        if decision.status != RiskDecisionStatus.APPROVED:
            raise RiskRejectedError(
                "; ".join(decision.reasons) or "Risk engine rejected trade"
            )
        if decision.opportunity_id != opportunity.id:
            raise RiskRejectedError("Risk decision does not match opportunity")

        book = opportunity.market.order_book if opportunity.market else None
        return await self._execute_opportunity(opportunity, decision, snapshot_book=None, order_book=book)

    async def _execute_opportunity(
        self,
        opportunity: TradeOpportunity,
        decision: RiskDecision,
        *,
        snapshot_book: Any = None,
        order_book: Any = None,
    ) -> ExecutionResult:
        qty = decision.max_allowed_quantity or decision.position_size_allowed or opportunity.quantity
        order = OrderRequest(
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            side=opportunity.side,
            quantity=qty,
            limit_price=opportunity.entry_price,
            metadata={
                "strategy": opportunity.strategy_name,
                "real_exchange_order": False,
            },
        )
        book = order_book
        if book is None and opportunity.market is not None:
            book = opportunity.market.order_book
        if book is None and snapshot_book is not None:
            book = getattr(snapshot_book, "order_book", None)

        if isinstance(self._executor, (PaperExecutor, ExecutionService)):
            return await self._executor.execute(
                order,
                order_book=book,
                strategy=opportunity.strategy_name,
                order_type=OrderType.LIMIT,
            )
        return await self._executor.execute(order)

    def _collect_order_fill(self, result: TradeCycleResult, execution: ExecutionResult) -> None:
        executor = self._executor
        manager = getattr(executor, "order_manager", None)
        tracker = getattr(executor, "fill_tracker", None)
        if manager is not None:
            order = manager.get(execution.order_id)
            if order is not None:
                result.orders.append(order)
        if tracker is not None:
            for fill in tracker.fills:
                if fill.order_id == execution.order_id and fill not in result.fills:
                    result.fills.append(fill)

    @staticmethod
    def notional_usd(opportunity: TradeOpportunity) -> Decimal:
        return opportunity.quantity * opportunity.entry_price
