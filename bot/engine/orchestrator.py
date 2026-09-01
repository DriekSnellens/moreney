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
import time

from bot.core.enums import EntryQualityRecommendation, OpportunitySide, OrderStatus, OrderType, RiskDecisionStatus
from bot.core.exceptions import ExchangeError, RiskRejectedError
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
from bot.opportunity.engine import GlobalOpportunityEngine
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.strategies.entry_quality import (
    EntryQualityAssessment,
    EntryQualityConfig,
    EntryQualityDiagnostics,
    apply_size_multiplier,
    config_from_settings,
    evaluate_entry_quality,
)
from bot.strategies.opportunity_economics import (
    EconomicDiagnostics,
    assess_opportunity_economics,
    config_capital_efficiency_from_settings,
    config_venue_economics_from_settings,
    select_best_buy_opportunities,
)


@dataclass
class EntryQualityContext:
    """Live entry quality + capital economics hook."""

    config: EntryQualityConfig
    capital_config: Any  # CapitalEfficiencyConfig
    venue_config: Any  # VenueEconomicsConfig
    diagnostics: EntryQualityDiagnostics
    economic_diagnostics: EconomicDiagnostics
    marks_for: Any | None = None  # callable[[str], list[Decimal]]


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
    entry_quality_rejected: list[tuple[TradeOpportunity, EntryQualityAssessment]] = field(
        default_factory=list
    )
    portfolio_equity: Decimal | None = None
    opportunity_ranking: dict[str, Any] | None = None


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
        opportunity_engine: Any | None = None,
        entry_quality: EntryQualityContext | None = None,
    ) -> None:
        self._market_data = market_data
        self._strategy = strategy
        self._profitability = profitability
        self._risk = risk
        self._portfolio = portfolio
        self._executor = executor
        self._opportunity_engine = opportunity_engine
        self._entry_quality = entry_quality
        self._latency: Any = None

    def attach_latency_tracker(self, tracker: Any | None) -> None:
        self._latency = tracker
        if self._opportunity_engine is not None and hasattr(
            self._opportunity_engine, "attach_latency_tracker"
        ):
            self._opportunity_engine.attach_latency_tracker(tracker)

    @property
    def paper_portfolio(self) -> PaperPortfolio | None:
        if isinstance(self._portfolio, PaperPortfolio):
            return self._portfolio
        if isinstance(self._executor, (PaperExecutor, ExecutionService)):
            return self._executor.portfolio  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _is_buy_opportunity(opportunity: TradeOpportunity) -> bool:
        side = opportunity.side
        val = str(side.value if hasattr(side, "value") else side).lower()
        return val in {"buy", "long"}

    @staticmethod
    def _skip_entry_quality(opportunity: TradeOpportunity) -> bool:
        meta = opportunity.metadata or {}
        return bool(
            meta.get("dust_top_up")
            or meta.get("ladder_leg")
            or meta.get("trail_take_profit")
            or meta.get("winner_add")
        )

    def _apply_entry_quality(
        self,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
    ) -> tuple[TradeOpportunity | None, EntryQualityAssessment | None]:
        ctx = self._entry_quality
        if ctx is None or not ctx.config.enabled:
            return opportunity, None
        if not self._is_buy_opportunity(opportunity) or self._skip_entry_quality(opportunity):
            return opportunity, None
        marks: list[Decimal] = []
        if ctx.marks_for is not None:
            try:
                marks = [Decimal(str(m)) for m in ctx.marks_for(opportunity.symbol)]
            except Exception:  # noqa: BLE001
                marks = []
        economics = assess_opportunity_economics(
            opportunity=opportunity,
            profitability=profitability,
            marks=marks,
            entry_config=ctx.config,
            capital_config=ctx.capital_config,
            venue_config=ctx.venue_config,
        )
        eq = economics.entry_quality
        if eq is not None:
            ctx.diagnostics.record(eq)
        ce = economics.capital_efficiency
        if ce is not None:
            ctx.economic_diagnostics.capital_efficiency_candidates += 1
            if ce.recommendation == EntryQualityRecommendation.REJECT:
                ctx.economic_diagnostics.capital_efficiency_rejected += 1
            elif ce.recommendation == EntryQualityRecommendation.REDUCED_SIZE:
                ctx.economic_diagnostics.capital_efficiency_reduced += 1
            if ce.expected_net_profit_per_hour is not None:
                ctx.economic_diagnostics.record_net_per_hour(
                    ce.expected_net_profit_per_hour
                )
        if economics.venue:
            v = economics.venue.lower()
            if v == "bitvavo":
                ctx.economic_diagnostics.venue_bitvavo_selected += 1
            elif v == "okx":
                ctx.economic_diagnostics.venue_okx_selected += 1
        if economics.recommendation == EntryQualityRecommendation.REJECT:
            return None, eq
        new_qty = apply_size_multiplier(
            opportunity.quantity,
            economics.combined_multiplier,
        )
        if new_qty <= 0:
            return None, eq
        meta = dict(opportunity.metadata or {})
        meta.update(
            {
                "entry_quality_score": str(eq.score if eq else ""),
                "headroom_pct": (
                    str(eq.headroom_pct) if eq and eq.headroom_pct is not None else None
                ),
                "extension_pct": (
                    str(eq.extension_pct) if eq and eq.extension_pct is not None else None
                ),
                "trend_continuity": (
                    str(eq.trend_continuity)
                    if eq and eq.trend_continuity is not None
                    else None
                ),
                "entry_quality_multiplier": str(economics.combined_multiplier),
                "entry_quality_recommendation": economics.recommendation.value,
                "entry_quality_reject_reason": economics.reject_reason,
                "required_move_pct": str(eq.required_move_pct if eq else ""),
                "net_break_even_pct": str(eq.net_break_even_pct if eq else ""),
                "expected_net_profit_per_hour": (
                    str(ce.expected_net_profit_per_hour)
                    if ce and ce.expected_net_profit_per_hour is not None
                    else None
                ),
                "capital_efficiency_per_capital_hour": (
                    str(ce.capital_efficiency_per_capital_hour)
                    if ce and ce.capital_efficiency_per_capital_hour is not None
                    else None
                ),
                "expected_hold_seconds": (
                    str(ce.expected_hold_seconds)
                    if ce and ce.expected_hold_seconds is not None
                    else None
                ),
            }
        )
        if economics.venue and not meta.get("buy_exchange"):
            meta["buy_exchange"] = economics.venue
            meta["venue"] = economics.venue
        update: dict[str, Any] = {"quantity": new_qty, "metadata": meta}
        return opportunity.model_copy(update=update), eq

    async def run_once(self, symbol: str) -> TradeCycleResult:
        """Run a single evaluation cycle for ``symbol``."""
        return await self._run_cycle(symbols=[symbol.upper()])

    async def run_universe(self, symbols: list[str]) -> TradeCycleResult:
        """Evaluate all ``symbols`` in one cycle (EUR maker + USDT fair value)."""
        cleaned = [s.strip().upper().replace("-", "").replace("/", "") for s in symbols if s.strip()]
        return await self._run_cycle(symbols=cleaned or ["BTCEUR"])

    async def _run_cycle(self, *, symbols: list[str]) -> TradeCycleResult:
        primary_symbol = symbols[0]
        result = TradeCycleResult(symbol=primary_symbol)
        venue_snapshots: list[Any] = []
        primary: Any = None
        for symbol in symbols:
            snaps, snap_primary = await self._load_snapshots(symbol)
            if snaps:
                venue_snapshots.extend(snaps)
                if primary is None:
                    primary = snaps[0]
            elif snap_primary is not None:
                venue_snapshots.append(snap_primary)
                if primary is None:
                    primary = snap_primary
        if not venue_snapshots and primary is None:
            return result
        paper = self.paper_portfolio
        if paper is not None:
            for snap in venue_snapshots or ([primary] if primary is not None else []):
                px = getattr(snap, "last", None) or getattr(snap, "bid", None) or getattr(snap, "ask", None)
                if px:
                    paper.maybe_seed_inventory(getattr(snap, "symbol", primary_symbol), px)
                    paper.set_mark_price(getattr(snap, "symbol", primary_symbol), px)
        portfolio = await self._portfolio.get_snapshot()
        portfolio_equity = portfolio.equity_usd
        lat = self._latency
        timing = bool(lat is not None and getattr(lat, "enabled", False))

        t0 = time.perf_counter() if timing else 0.0
        if venue_snapshots:
            evaluate_markets = getattr(self._strategy, "evaluate_markets", None)
            if callable(evaluate_markets):
                kwargs: dict[str, Any] = {"equity": portfolio_equity}
                paper = self.paper_portfolio
                if paper is not None and paper.venue_ledger is not None:
                    kwargs["inventory"] = paper.venue_ledger
                if paper is not None:
                    kwargs["portfolio_state"] = paper.state
                try:
                    opportunities = await evaluate_markets(venue_snapshots, **kwargs)
                except TypeError:
                    try:
                        opportunities = await evaluate_markets(
                            venue_snapshots, equity=portfolio_equity
                        )
                    except TypeError:
                        opportunities = await evaluate_markets(venue_snapshots)
            else:
                opportunities = await self._strategy.evaluate(primary)
        else:
            opportunities = await self._strategy.evaluate(primary)
        if timing:
            lat.record("candidate_creation", time.perf_counter() - t0)
        eq_ctx = self._entry_quality
        if eq_ctx is not None and eq_ctx.venue_config.enabled and opportunities:
            opportunities = select_best_buy_opportunities(
                opportunities, config=eq_ctx.venue_config
            )
        result.opportunities = opportunities
        processed: list[TradeOpportunity] = []

        if self._opportunity_engine is not None and opportunities:
            t0 = time.perf_counter() if timing else 0.0
            ranked, all_scored = await self._opportunity_engine.evaluate_batch(
                opportunities,
                portfolio,
                venue_snapshots=venue_snapshots,
                enrich_risk=self._enrich_for_risk,
            )
            if timing:
                lat.record("goe_evaluate", time.perf_counter() - t0)
            result.opportunity_ranking = GlobalOpportunityEngine.ranking_summary(
                ranked,
                all_scored,
                input_count=len(opportunities),
            )
            t_exec = 0.0
            ranked = self._interleave_ranked_by_buy_venue(ranked)
            for scored in ranked:
                opportunity = scored.opportunity
                profitability = scored.profitability
                decision = scored.risk_decision
                if decision is None or not decision.approved:
                    continue
                if self._should_skip_maker_quote(opportunity):
                    continue
                eq_orig = opportunity
                opportunity, eq_assessment = self._apply_entry_quality(
                    opportunity, profitability
                )
                if opportunity is None:
                    if eq_assessment is not None:
                        result.entry_quality_rejected.append((eq_orig, eq_assessment))
                    continue
                processed.append(opportunity)
                result.profitability.append(profitability)
                result.risk_decisions.append(decision)
                paper = self.paper_portfolio
                if paper is not None and opportunity.market is not None:
                    paper.set_mark_price(opportunity.symbol, opportunity.market.last)
                buy_book = self._resolve_execution_book(opportunity, venue_snapshots, primary)
                te = time.perf_counter() if timing else 0.0
                execution = await self._execute_opportunity(
                    opportunity,
                    decision,
                    snapshot_book=primary,
                    order_book=buy_book,
                    venue_snapshots=venue_snapshots,
                    cycle_result=result,
                )
                if timing:
                    t_exec += time.perf_counter() - te
                result.executions.append(execution)
                self._collect_order_fill(result, execution)
                gate = getattr(self._opportunity_engine, "portfolio_gate", None)
                if gate is not None:
                    notional = opportunity.quantity * opportunity.entry_price
                    if decision.max_allowed_quantity and decision.max_allowed_quantity < opportunity.quantity:
                        notional = decision.max_allowed_quantity * opportunity.entry_price
                    gate.record_fill(scored, notional)
                portfolio = await self._portfolio.get_snapshot()
            if timing:
                lat.record("executor", t_exec)
            result.opportunities = processed
            if self.paper_portfolio is not None:
                result.portfolio_equity = self.paper_portfolio.state.total_equity
            return result

        for opportunity in opportunities:
            if self._should_skip_maker_quote(opportunity):
                continue
            # Keep mark prices fresh for unrealized PnL when using paper portfolio.
            paper = self.paper_portfolio
            if paper is not None and opportunity.market is not None:
                paper.set_mark_price(opportunity.symbol, opportunity.market.last)

            profitability = await self._profitability.evaluate(
                opportunity,
                buy_fee_rate=_fee_rate(
                    opportunity.metadata,
                    "buy_maker_fee_rate" if self._is_maker_quote(opportunity) else "buy_taker_fee_rate",
                ),
                sell_fee_rate=_fee_rate(
                    opportunity.metadata,
                    "sell_maker_fee_rate" if self._is_maker_quote(opportunity) else "sell_taker_fee_rate",
                ),
            )
            result.profitability.append(profitability)

            eq_orig = opportunity
            opportunity, eq_assessment = self._apply_entry_quality(
                opportunity, profitability
            )
            if opportunity is None:
                if eq_assessment is not None:
                    result.entry_quality_rejected.append((eq_orig, eq_assessment))
                continue

            processed.append(opportunity)

            opportunity, risk_context = self._enrich_for_risk(opportunity, venue_snapshots)
            slip_pct = (
                profitability.slippage_usd
                / (opportunity.quantity * opportunity.entry_price)
                * Decimal("100")
                if opportunity.quantity * opportunity.entry_price > 0
                else Decimal("0")
            )
            if risk_context is not None:
                risk_context = risk_context.model_copy(
                    update={"estimated_slippage_pct": slip_pct}
                )
            decision = await self._evaluate_risk(
                opportunity, profitability, portfolio, risk_context
            )
            result.risk_decisions.append(decision)

            if not decision.approved:
                result.rejected.append((opportunity, decision))
                continue

            buy_book = self._resolve_execution_book(opportunity, venue_snapshots, primary)
            execution = await self._execute_opportunity(
                opportunity,
                decision,
                snapshot_book=primary,
                order_book=buy_book,
                venue_snapshots=venue_snapshots,
                cycle_result=result,
            )
            result.executions.append(execution)
            self._collect_order_fill(result, execution)

            # Refresh portfolio snapshot after fills for subsequent risk checks.
            portfolio = await self._portfolio.get_snapshot()

        result.opportunities = processed
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
        getter = getattr(self._market_data, "get_snapshot", None)
        if not callable(getter):
            return [], None
        try:
            primary = await getter(symbol)
        except ExchangeError:
            return [], None
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
        *,
        exchange_key: str = "buy_exchange",
        symbol_override: str | None = None,
    ) -> Any:
        """Resolve synchronized order book for a venue (buy or sell leg)."""
        venue = opportunity.metadata.get(exchange_key)
        symbol = str(symbol_override or opportunity.symbol).upper()
        if venue:
            getter = getattr(self._market_data, "get_order_book", None)
            if callable(getter):
                book = getter(str(venue), symbol)
                if book is not None:
                    return book
            for snap in venue_snapshots:
                if (
                    getattr(snap, "exchange", None) == venue
                    and str(getattr(snap, "symbol", "")).upper() == symbol
                    and snap.order_book
                ):
                    return snap.order_book
        if exchange_key == "buy_exchange":
            if opportunity.market is not None and opportunity.market.order_book is not None:
                return opportunity.market.order_book
            return getattr(primary, "order_book", None)
        return None

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
        return await self._execute_opportunity(
            opportunity,
            decision,
            snapshot_book=None,
            order_book=book,
            venue_snapshots=[],
        )

    async def _execute_opportunity(
        self,
        opportunity: TradeOpportunity,
        decision: RiskDecision,
        *,
        snapshot_book: Any = None,
        order_book: Any = None,
        venue_snapshots: list[Any] | None = None,
        cycle_result: TradeCycleResult | None = None,
    ) -> ExecutionResult:
        qty = decision.max_allowed_quantity or decision.position_size_allowed or opportunity.quantity
        if self._is_maker_quote(opportunity):
            return await self._execute_maker_quote(
                opportunity,
                qty,
                snapshot_book=snapshot_book,
                order_book=order_book,
                venue_snapshots=venue_snapshots or [],
                cycle_result=cycle_result,
            )
        is_arb = opportunity.strategy_name == "cross_exchange_arbitrage"
        buy_order = OrderRequest(
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            side=opportunity.side,
            quantity=qty,
            limit_price=opportunity.entry_price,
            metadata={
                "strategy": opportunity.strategy_name,
                "real_exchange_order": False,
                "leg": "buy",
                "arb_leg": is_arb,
                "venue": str(opportunity.metadata.get("buy_exchange") or ""),
            },
        )
        book = order_book
        if book is None and opportunity.market is not None:
            book = opportunity.market.order_book
        if book is None and snapshot_book is not None:
            book = getattr(snapshot_book, "order_book", None)

        if isinstance(self._executor, (PaperExecutor, ExecutionService)):
            buy_result = await self._executor.execute(
                buy_order,
                order_book=book,
                strategy=opportunity.strategy_name,
                order_type=OrderType.LIMIT,
            )
        else:
            buy_result = await self._executor.execute(buy_order)

        if self._should_execute_arb_sell_leg(opportunity, buy_result):
            sell_result = await self._execute_arb_sell_leg(
                opportunity,
                buy_result,
                venue_snapshots=venue_snapshots or [],
                snapshot_book=snapshot_book,
            )
            buy_result.metadata["sell_leg"] = {
                "order_id": str(sell_result.order_id),
                "status": sell_result.status.value,
                "filled_quantity": str(sell_result.filled_quantity),
            }
            if cycle_result is not None:
                cycle_result.executions.append(sell_result)
                self._collect_order_fill(cycle_result, sell_result)

        return buy_result

    @staticmethod
    def _should_execute_arb_sell_leg(
        opportunity: TradeOpportunity,
        buy_result: ExecutionResult,
    ) -> bool:
        if opportunity.strategy_name != "cross_exchange_arbitrage":
            return False
        if buy_result.filled_quantity <= 0:
            return False
        if buy_result.status not in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}:
            return False
        return bool(opportunity.metadata.get("sell_exchange"))

    @staticmethod
    def _is_maker_quote(opportunity: TradeOpportunity) -> bool:
        if opportunity.strategy_name in {
            "maker_inventory",
            "triangle_bridge",
            "desk_composite",
            "global_composite",
        }:
            return True
        return bool((opportunity.metadata or {}).get("post_only"))

    def _interleave_ranked_by_buy_venue(self, ranked: list[Any]) -> list[Any]:
        """Round-robin approved opportunities by buy venue so OKX is not starved."""
        if len(ranked) <= 1:
            return ranked
        by_venue: dict[str, list[Any]] = {}
        for item in ranked:
            opp = getattr(item, "opportunity", None)
            meta = getattr(opp, "metadata", None) or {}
            venue = str(
                meta.get("buy_exchange") or meta.get("venue") or ""
            ).strip().lower() or "_"
            by_venue.setdefault(venue, []).append(item)
        if len(by_venue) <= 1:
            return ranked
        venues = sorted(by_venue.keys())
        out: list[Any] = []
        idx = 0
        while True:
            added = False
            for venue in venues:
                bucket = by_venue[venue]
                if idx < len(bucket):
                    out.append(bucket[idx])
                    added = True
            if not added:
                break
            idx += 1
        return out or ranked

    def _should_skip_maker_quote(self, opportunity: TradeOpportunity) -> bool:
        if not self._is_maker_quote(opportunity):
            return False
        if self._maker_slot_taken(opportunity):
            return True
        max_open = self._max_open_quotes_per_venue()
        meta = opportunity.metadata or {}
        buy = str(meta.get("buy_exchange") or "").strip().lower()
        sell = str(meta.get("sell_exchange") or "").strip().lower()
        sell_only = bool(meta.get("sell_only"))
        buy_only = bool(meta.get("buy_only"))
        if sell_only and sell:
            return self._open_maker_quote_count_for(sell) >= max_open
        if buy_only and buy:
            return self._open_maker_quote_count_for(buy) >= max_open
        if buy and self._open_maker_quote_count_for(buy) >= max_open:
            return True
        if sell and self._open_maker_quote_count_for(sell) >= max_open:
            return True
        return False

    def _max_open_quotes_per_venue(self) -> int:
        max_open = 4
        settings = self._executor_settings()
        if settings is not None:
            max_open = int(getattr(settings, "paper_maker_max_open_quotes", 4) or 4)
        return max_open

    def _executor_settings(self) -> Any:
        return getattr(self._executor, "_settings", None) or getattr(
            self._portfolio, "_settings", None
        )

    def _open_maker_orders(self) -> list[Order]:
        manager = getattr(self._executor, "order_manager", None)
        if manager is None or not hasattr(manager, "open_orders"):
            return []
        return [
            o
            for o in manager.open_orders()
            if (o.metadata or {}).get("post_only")
        ]

    def _open_maker_quote_count(self) -> int:
        return len({o.opportunity_id for o in self._open_maker_orders() if o.opportunity_id})

    def _open_maker_quote_count_for(self, venue: str) -> int:
        key = venue.strip().lower()
        if not key:
            return self._open_maker_quote_count()
        return sum(
            1
            for o in self._open_maker_orders()
            if str((o.metadata or {}).get("venue") or "").strip().lower() == key
        )

    def _maker_slot_taken(self, opportunity: TradeOpportunity) -> bool:
        buy = str((opportunity.metadata or {}).get("buy_exchange") or "")
        sell = str((opportunity.metadata or {}).get("sell_exchange") or "")
        venues = {buy, sell} - {""}
        symbol = opportunity.symbol
        for order in self._open_maker_orders():
            if order.symbol != symbol:
                continue
            venue = str((order.metadata or {}).get("venue") or "")
            if venue in venues:
                return True
        return False

    def _live_execute_venues(self) -> set[str] | None:
        venues = getattr(self._executor, "_execute_venues", None)
        if not venues:
            return None
        return {str(v).strip().lower() for v in venues if str(v).strip()}

    def _apply_live_venue_leg_policy(
        self,
        *,
        buy_venue: str,
        sell_venue: str,
        sell_only: bool,
        buy_only: bool,
    ) -> tuple[bool, bool]:
        """When only some venues are live, prefer executable legs only."""
        live = self._live_execute_venues()
        if live is None:
            return sell_only, buy_only
        buy_live = buy_venue.strip().lower() in live if buy_venue else False
        sell_live = sell_venue.strip().lower() in live if sell_venue else False
        if buy_live and sell_live:
            return sell_only, buy_only
        if sell_live and not buy_live:
            return True, False
        if buy_live and not sell_live:
            return False, True
        return sell_only, buy_only

    async def _execute_maker_quote(
        self,
        opportunity: TradeOpportunity,
        qty: Decimal,
        *,
        snapshot_book: Any,
        order_book: Any,
        venue_snapshots: list[Any],
        cycle_result: TradeCycleResult | None,
    ) -> ExecutionResult:
        buy_venue = str(opportunity.metadata.get("buy_exchange") or "")
        sell_venue = str(opportunity.metadata.get("sell_exchange") or "")
        buy_symbol = str(
            opportunity.metadata.get("buy_symbol") or opportunity.symbol
        ).upper()
        sell_symbol = str(
            opportunity.metadata.get("sell_symbol") or opportunity.symbol
        ).upper()
        # Triangle quotes use native leg prices; EUR-normalized entry/exit for PnL gate.
        buy_limit = opportunity.entry_price
        sell_limit = opportunity.expected_exit_price
        if opportunity.metadata.get("triangle"):
            raw_buy = opportunity.metadata.get("buy_vwap")
            raw_sell = opportunity.metadata.get("sell_vwap")
            if raw_buy is not None:
                buy_limit = Decimal(str(raw_buy))
            if raw_sell is not None:
                sell_limit = Decimal(str(raw_sell))
        buy_order = OrderRequest(
            opportunity_id=opportunity.id,
            symbol=buy_symbol,
            side=OpportunitySide.BUY,
            quantity=qty,
            limit_price=buy_limit,
            metadata={
                "strategy": opportunity.strategy_name,
                "real_exchange_order": False,
                "leg": "buy",
                "post_only": True,
                "fee_role": "maker",
                "venue": buy_venue,
                "triangle": bool(opportunity.metadata.get("triangle")),
                "hybrid_hedge": bool(opportunity.metadata.get("hybrid_hedge")),
            },
        )
        sell_price = sell_limit
        if sell_price is None:
            raw = opportunity.metadata.get("sell_vwap")
            sell_price = Decimal(str(raw)) if raw is not None else opportunity.entry_price
        sell_order = OrderRequest(
            opportunity_id=opportunity.id,
            symbol=sell_symbol,
            side=OpportunitySide.SELL,
            quantity=qty,
            limit_price=sell_price,
            metadata={
                "strategy": opportunity.strategy_name,
                "real_exchange_order": False,
                "leg": "sell",
                "post_only": True,
                "fee_role": "maker",
                "venue": sell_venue,
                "triangle": bool(opportunity.metadata.get("triangle")),
                "hybrid_hedge": bool(opportunity.metadata.get("hybrid_hedge")),
            },
        )
        buy_book = order_book
        if buy_book is None or buy_symbol != opportunity.symbol:
            buy_book = self._resolve_execution_book(
                opportunity,
                venue_snapshots,
                snapshot_book,
                exchange_key="buy_exchange",
                symbol_override=buy_symbol,
            )
        sell_book = self._resolve_execution_book(
            opportunity,
            venue_snapshots,
            snapshot_book,
            exchange_key="sell_exchange",
            symbol_override=sell_symbol,
        )
        sell_only = bool((opportunity.metadata or {}).get("sell_only"))
        buy_only = bool((opportunity.metadata or {}).get("buy_only"))
        sell_only, buy_only = self._apply_live_venue_leg_policy(
            buy_venue=buy_venue,
            sell_venue=sell_venue,
            sell_only=sell_only,
            buy_only=buy_only,
        )
        if sell_only:
            # Inventory overweight / dump guard: recycle ALT→EUR only.
            sell_result = await self._execute_limit(
                sell_order, sell_book, opportunity.strategy_name
            )
            sell_result.metadata["sell_only"] = True
            if cycle_result is not None:
                cycle_result.executions.append(sell_result)
                self._collect_order_fill(cycle_result, sell_result)
            return sell_result
        if buy_only:
            settings = self._executor_settings()
            allow_buy_only = True
            if settings is not None:
                allow_buy_only = bool(
                    getattr(settings, "paper_maker_allow_buy_only", True)
                )
            if not allow_buy_only:
                return ExecutionResult(
                    order_id=buy_order.id,
                    opportunity_id=opportunity.id,
                    status=OrderStatus.REJECTED,
                    filled_quantity=Decimal("0"),
                    average_price=None,
                    message="buy_only disabled (winst-mode)",
                    metadata={"buy_only_disabled": True},
                )
            buy_result = await self._execute_limit(
                buy_order, buy_book, opportunity.strategy_name
            )
            buy_result.metadata["buy_only"] = True
            if cycle_result is not None:
                cycle_result.executions.append(buy_result)
                self._collect_order_fill(cycle_result, buy_result)
            return buy_result
        # Two-sided: only post the sell leg when inventory can cover it.
        try:
            from bot.portfolio.venue_ledger import infer_base_asset

            base = infer_base_asset(sell_symbol)
            free_base = Decimal("0")
            if self._portfolio is not None:
                ledger = getattr(self._portfolio, "venue_ledger", None)
                if ledger is not None and sell_venue:
                    free_base = Decimal(str(ledger.available(sell_venue, base) or 0))
                if free_base <= 0:
                    free_base = Decimal(str(self._portfolio.available(base) or 0))
            min_notional = Decimal("5")
            settings = self._executor_settings()
            allow_buy_only = True
            if settings is not None:
                min_notional = Decimal(
                    str(getattr(settings, "paper_maker_min_notional_eur", 5) or 5)
                )
                allow_buy_only = bool(
                    getattr(settings, "paper_maker_allow_buy_only", True)
                )
            if free_base <= 0 or (free_base * sell_price) < min_notional:
                if not allow_buy_only:
                    return ExecutionResult(
                        order_id=buy_order.id,
                        opportunity_id=opportunity.id,
                        status=OrderStatus.REJECTED,
                        filled_quantity=Decimal("0"),
                        average_price=None,
                        message="insufficient base for profitable two-sided quote",
                        metadata={"sell_leg_skipped": "insufficient_base"},
                    )
                buy_result = await self._execute_limit(
                    buy_order, buy_book, opportunity.strategy_name
                )
                buy_result.metadata["buy_only"] = True
                buy_result.metadata["sell_leg_skipped"] = "insufficient_base"
                if cycle_result is not None:
                    cycle_result.executions.append(buy_result)
                    self._collect_order_fill(cycle_result, buy_result)
                return buy_result
            sell_qty = min(qty, free_base)
            if sell_qty < qty:
                sell_order = sell_order.model_copy(update={"quantity": sell_qty})
        except Exception:  # noqa: BLE001
            pass
        buy_result = await self._execute_limit(buy_order, buy_book, opportunity.strategy_name)
        sell_result = await self._execute_limit(sell_order, sell_book, opportunity.strategy_name)
        buy_result.metadata["sell_leg"] = {
            "order_id": str(sell_result.order_id),
            "status": sell_result.status.value,
            "filled_quantity": str(sell_result.filled_quantity),
        }
        if cycle_result is not None:
            cycle_result.executions.append(sell_result)
            self._collect_order_fill(cycle_result, sell_result)
        return buy_result

    async def _execute_limit(
        self,
        request: OrderRequest,
        book: Any,
        strategy: str,
    ) -> ExecutionResult:
        if isinstance(self._executor, (PaperExecutor, ExecutionService)):
            return await self._executor.execute(
                request,
                order_book=book,
                strategy=strategy,
                order_type=OrderType.LIMIT,
            )
        return await self._executor.execute(request)

    async def _execute_arb_sell_leg(
        self,
        opportunity: TradeOpportunity,
        buy_result: ExecutionResult,
        *,
        venue_snapshots: list[Any],
        snapshot_book: Any = None,
    ) -> ExecutionResult:
        """Simulate the sell leg on the sell venue using a post-latency book."""
        sell_exchange = str(opportunity.metadata.get("sell_exchange") or "")
        sell_price = opportunity.expected_exit_price
        if sell_price is None:
            raw = opportunity.metadata.get("sell_vwap")
            sell_price = Decimal(str(raw)) if raw is not None else None

        sell_order = OrderRequest(
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            side=OpportunitySide.SELL,
            quantity=buy_result.filled_quantity,
            limit_price=sell_price,
            metadata={
                "strategy": opportunity.strategy_name,
                "real_exchange_order": False,
                "leg": "sell",
                "arb_leg": True,
                "venue": sell_exchange,
                "buy_order_id": str(buy_result.order_id),
            },
        )
        sell_book = self._resolve_execution_book(
            opportunity,
            venue_snapshots,
            snapshot_book,
            exchange_key="sell_exchange",
        )
        sell_book = _apply_second_leg_adverse(sell_book, self._second_leg_adverse_bps())
        return await self._execute_limit(
            sell_order, sell_book, opportunity.strategy_name
        )

    def _second_leg_adverse_bps(self) -> Decimal:
        settings = getattr(self._executor, "_settings", None) or getattr(
            self._portfolio, "_settings", None
        )
        if settings is None:
            return Decimal("0")
        return Decimal(str(getattr(settings, "paper_second_leg_adverse_bps", 0) or 0))

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


def _fee_rate(metadata: dict[str, Any] | None, key: str) -> Decimal | None:
    if not metadata or key not in metadata:
        return None
    try:
        return Decimal(str(metadata[key]))
    except Exception:
        return None


def _apply_second_leg_adverse(book: Any, bps: Decimal) -> Any:
    """Worsen sell-side bids to model the book moving during order latency."""
    if book is None or bps <= 0:
        return book
    frac = Decimal("1") - (bps / Decimal("10000"))
    if frac <= 0:
        return book
    bids = getattr(book, "bids", None)
    if not bids:
        return book
    shifted = []
    for level in bids:
        shifted.append(level.model_copy(update={"price": level.price * frac}))
    return book.model_copy(update={"bids": shifted})
