"""Central batch processor: profitability → EV → risk → portfolio → rank."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.core.enums import AssetClass, MarketRegime, OpportunityDecisionAction, RiskDecisionStatus
from bot.core.interfaces import ProfitabilityEngine, RiskEngine
from bot.core.models import PortfolioSnapshot, ProfitabilityResult, TradeOpportunity
from bot.markets.calendar import MarketCalendarService
from bot.markets.registry import InstrumentRegistry
from bot.opportunity.decision_log import OpportunityDecisionLogger
from bot.opportunity.ev_engine import ExpectedValueEngine
from bot.opportunity.models import ScoredOpportunity
from bot.opportunity.portfolio_gate import PortfolioExposureGate
from bot.opportunity.ranker import OpportunityRanker
from bot.opportunity.transfer_cost import CrossExchangeTransferCost
from bot.regime.detector import RegimeDetector

_ZERO = Decimal("0")


class GlobalOpportunityEngine:
    """Evaluate, score, rank, and filter opportunities across markets."""

    def __init__(
        self,
        settings: Settings,
        *,
        profitability: ProfitabilityEngine,
        risk: RiskEngine,
        registry: InstrumentRegistry | None = None,
        calendar: MarketCalendarService | None = None,
        regime: RegimeDetector | None = None,
        decision_log: OpportunityDecisionLogger | None = None,
        markout_win_rate: float | None = None,
    ) -> None:
        self._settings = settings
        self._profitability = profitability
        self._risk = risk
        self._registry = registry or InstrumentRegistry(settings)
        self._calendar = calendar or MarketCalendarService()
        self._regime = regime or RegimeDetector()
        self._ev = ExpectedValueEngine(settings, markout_win_rate=markout_win_rate)
        self._transfer = CrossExchangeTransferCost(settings)
        self._ranker = OpportunityRanker(settings)
        self._portfolio_gate = PortfolioExposureGate(settings)
        self._decisions = decision_log or OpportunityDecisionLogger()
        self._global_regime = MarketRegime.NORMAL

    @property
    def decision_log(self) -> OpportunityDecisionLogger:
        return self._decisions

    @property
    def portfolio_gate(self) -> PortfolioExposureGate:
        return self._portfolio_gate

    @property
    def global_regime(self) -> MarketRegime:
        return self._global_regime

    async def evaluate_batch(
        self,
        opportunities: list[TradeOpportunity],
        portfolio: PortfolioSnapshot,
        *,
        venue_snapshots: list[Any] | None = None,
        enrich_risk: Any = None,
        fee_rates: Any = None,
    ) -> tuple[list[ScoredOpportunity], list[ScoredOpportunity]]:
        """Returns (ranked_approved, all_scored)."""
        self._portfolio_gate.sync_from_portfolio(portfolio)
        if venue_snapshots:
            snaps = [s for s in venue_snapshots if getattr(s, "symbol", None)]
            regimes = self._regime.update(snaps)
            self._global_regime = self._regime.global_regime(regimes)
        else:
            regimes = {}

        scored: list[ScoredOpportunity] = []
        approved: list[ScoredOpportunity] = []

        for opportunity in opportunities:
            inst = self._registry.by_symbol(opportunity.symbol)
            asset_class = inst.asset_class if inst else AssetClass.CRYPTO_SPOT
            if inst and not self._calendar.is_tradeable(inst):
                self._decisions.log(
                    ScoredOpportunity(
                        opportunity=opportunity,
                        profitability=_empty_profitability(opportunity),
                        asset_class=asset_class,
                    ),
                    action=OpportunityDecisionAction.REJECT,
                    reason="Market closed for instrument session",
                    stage="calendar",
                )
                continue

            sym_regime = regimes.get(opportunity.symbol.upper(), self._global_regime)
            weight = self._regime.strategy_weight(opportunity.strategy_name, sym_regime)
            if weight <= 0:
                self._decisions.log(
                    ScoredOpportunity(
                        opportunity=opportunity,
                        profitability=_empty_profitability(opportunity),
                        regime=sym_regime,
                        regime_weight=weight,
                    ),
                    action=OpportunityDecisionAction.REJECT,
                    reason=f"Regime {sym_regime.value} disables strategy",
                    stage="regime",
                )
                continue

            buy_rate, sell_rate = _resolve_fee_rates(opportunity, fee_rates)
            profitability = await self._profitability.evaluate(
                opportunity,
                buy_fee_rate=buy_rate,
                sell_fee_rate=sell_rate,
            )

            transfer_cost = self._transfer.estimate(opportunity)
            ev_data = self._ev.enrich(
                opportunity,
                profitability,
                regime_weight=weight,
                transfer_cost=transfer_cost,
            )

            liq = _liquidity_score(opportunity, venue_snapshots)
            exec_q = _execution_quality(opportunity, profitability)

            item = ScoredOpportunity(
                opportunity=opportunity,
                profitability=profitability,
                asset_class=asset_class,
                instrument_id=inst.instrument_id if inst else "",
                correlation_group=inst.correlation_group if inst else "general",
                expected_value=Decimal(str(ev_data["expected_value"])),
                probability_profit=float(ev_data["probability_profit"]),
                expected_loss=Decimal(str(ev_data["expected_loss"])),
                risk_reward=Decimal(str(ev_data["risk_reward"])),
                liquidity_score=liq,
                regime=sym_regime,
                regime_weight=weight,
                transfer_cost=transfer_cost,
                capital_required=opportunity.quantity * opportunity.entry_price,
                execution_quality=exec_q,
                opportunity_decay_ms=int(
                    getattr(self._settings, "opportunity_decay_ms", 5000) or 5000
                ),
            )
            item.score = OpportunityRanker.compute_score(item)

            if not profitability.trade_allowed:
                self._decisions.log(
                    item,
                    action=OpportunityDecisionAction.REJECT,
                    reason="; ".join(
                        (profitability.estimate.disallow_reasons if profitability.estimate else [])
                        or ["NOT_PROFITABLE"]
                    ),
                    stage="profitability",
                )
                scored.append(item)
                continue

            min_ev = Decimal(str(getattr(self._settings, "opportunity_min_expected_value", 0)))
            if item.expected_value < min_ev:
                self._decisions.log(
                    item,
                    action=OpportunityDecisionAction.REJECT,
                    reason=f"EV {item.expected_value} below min {min_ev}",
                    stage="ev",
                )
                scored.append(item)
                continue

            enriched, risk_ctx = enrich_risk(opportunity, venue_snapshots) if enrich_risk else (
                opportunity,
                None,
            )
            if risk_ctx is not None:
                slip_pct = _slippage_pct_from_profitability(profitability, enriched)
                risk_ctx = risk_ctx.model_copy(update={"estimated_slippage_pct": slip_pct})

            decision = await self._risk.evaluate(
                enriched,
                profitability,
                portfolio,
                context=risk_ctx,
            )
            item.risk_decision = decision
            item.opportunity = enriched

            if not decision.approved:
                self._decisions.log(
                    item,
                    action=OpportunityDecisionAction.REJECT,
                    reason=decision.rejection_reason or "; ".join(decision.reasons),
                    stage="risk",
                )
                scored.append(item)
                continue

            ok, msg, _code = self._portfolio_gate.check(item, portfolio)
            if not ok:
                self._decisions.log(
                    item,
                    action=OpportunityDecisionAction.REJECT,
                    reason=msg,
                    stage="portfolio",
                    portfolio_exposure=self._portfolio_gate.snapshot(),
                )
                scored.append(item)
                continue

            self._decisions.log(
                item,
                action=OpportunityDecisionAction.TAKE,
                reason="Approved",
                stage="final",
                portfolio_exposure=self._portfolio_gate.snapshot(),
            )
            scored.append(item)
            approved.append(item)

        ranked = self._ranker.rank(approved)
        max_exec = int(getattr(self._settings, "opportunity_max_executions_per_cycle", 3) or 3)
        return ranked[:max_exec], scored


def _empty_profitability(opportunity: TradeOpportunity) -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=opportunity.id,
        gross_profit_usd=_ZERO,
        fees_usd=_ZERO,
        slippage_usd=_ZERO,
        funding_usd=_ZERO,
        execution_buffer_usd=_ZERO,
        net_profit_usd=_ZERO,
        is_profitable=False,
        trade_allowed=False,
    )


def _resolve_fee_rates(
    opportunity: TradeOpportunity,
    fee_rates: Any,
) -> tuple[Decimal | None, Decimal | None]:
    if fee_rates is None:
        meta = opportunity.metadata or {}
        buy = meta.get("buy_maker_fee_rate") or meta.get("buy_taker_fee_rate")
        sell = meta.get("sell_maker_fee_rate") or meta.get("sell_taker_fee_rate")
        return (
            Decimal(str(buy)) if buy is not None else None,
            Decimal(str(sell)) if sell is not None else None,
        )
    return fee_rates


def _liquidity_score(opportunity: TradeOpportunity, venue_snapshots: list[Any] | None) -> float:
    meta = opportunity.metadata or {}
    liq = meta.get("liquidity_base")
    if liq is not None:
        try:
            base = float(liq)
            qty = float(opportunity.quantity)
            if qty <= 0:
                return 1.0
            return min(1.0, max(0.1, base / qty))
        except (TypeError, ValueError):
            pass
    if venue_snapshots and opportunity.market and opportunity.market.order_book:
        book = opportunity.market.order_book
        depth = sum(float(l.amount) for l in (book.bids or [])[:3])
        qty = float(opportunity.quantity)
        if qty > 0:
            return min(1.0, max(0.1, depth / qty))
    return 0.8


def _execution_quality(opportunity: TradeOpportunity, profitability: ProfitabilityResult) -> float:
    if not profitability.trade_allowed:
        return 0.0
    meta = opportunity.metadata or {}
    latency = float(meta.get("latency_ms") or meta.get("book_age_ms") or 0)
    if latency > 1000:
        return 0.5
    if latency > 500:
        return 0.7
    if meta.get("post_only"):
        return 0.95
    return 0.85


def _slippage_pct_from_profitability(
    profitability: ProfitabilityResult,
    opportunity: TradeOpportunity,
) -> Decimal:
    notional = opportunity.quantity * opportunity.entry_price
    if notional <= 0:
        return Decimal("0")
    return profitability.slippage_usd / notional * Decimal("100")
