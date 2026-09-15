"""Normalized scored opportunity for cross-market ranking."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from bot.core.enums import AssetClass, MarketRegime, OpportunityDecisionAction
from bot.core.models import ProfitabilityResult, RiskDecision, TradeOpportunity


class ScoredOpportunity(BaseModel):
    """Comparable opportunity after profitability and EV enrichment."""

    opportunity: TradeOpportunity
    profitability: ProfitabilityResult
    risk_decision: RiskDecision | None = None
    asset_class: AssetClass = AssetClass.CRYPTO_SPOT
    instrument_id: str = ""
    correlation_group: str = "general"
    expected_value: Decimal = Decimal("0")
    probability_profit: float = 0.5
    expected_loss: Decimal = Decimal("0")
    risk_reward: Decimal = Decimal("0")
    liquidity_score: float = 1.0
    regime: MarketRegime = MarketRegime.NORMAL
    regime_weight: Decimal = Decimal("1")
    transfer_cost: Decimal = Decimal("0")
    capital_required: Decimal = Decimal("0")
    execution_quality: float = 1.0
    opportunity_decay_ms: int = 5000
    score: Decimal = Decimal("0")
    rank: int = 0
    calibrated_expected_value: Decimal = Decimal("0")
    expected_net_eur: Decimal = Decimal("0")
    expected_net_bps: Decimal = Decimal("0")
    expected_net_eur_per_capital_second: Decimal = Decimal("0")
    expected_capital_time: Decimal = Decimal("0")
    expected_fee_eur: Decimal = Decimal("0")
    expected_slippage_eur: Decimal = Decimal("0")
    expected_adverse_selection_eur: Decimal = Decimal("0")
    inventory_relief_eur: Decimal = Decimal("0")
    first_limiting_gate: str = ""
    all_gates: list[str] = Field(default_factory=list)

    @property
    def opportunity_id(self) -> UUID:
        return self.opportunity.id


class OpportunityDecision(BaseModel):
    """Structured decision record for observability."""

    opportunity_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: OpportunityDecisionAction
    reason: str = ""
    strategy: str = ""
    symbol: str = ""
    asset_class: AssetClass = AssetClass.CRYPTO_SPOT
    direction: str = ""
    entry_price: Decimal = Decimal("0")
    expected_exit: Decimal | None = None
    expected_gross_return: Decimal = Decimal("0")
    estimated_fees: Decimal = Decimal("0")
    estimated_slippage: Decimal = Decimal("0")
    expected_net_return: Decimal = Decimal("0")
    probability_profit: float = 0.0
    expected_value: Decimal = Decimal("0")
    risk_reward: Decimal = Decimal("0")
    liquidity_score: float = 0.0
    regime: MarketRegime = MarketRegime.NORMAL
    correlation_group: str = ""
    portfolio_exposure: dict[str, Any] = Field(default_factory=dict)
    score: Decimal = Decimal("0")
    stage: str = ""
    calibrated_expected_value: Decimal = Decimal("0")
    expected_net_eur: Decimal = Decimal("0")
    expected_net_eur_per_capital_second: Decimal = Decimal("0")
    first_limiting_gate: str = ""
    all_gates: list[str] = Field(default_factory=list)
    buy_exchange: str = ""
    sell_exchange: str = ""
    theoretical_net: Decimal = Decimal("0")

    @classmethod
    def from_scored(
        cls,
        scored: ScoredOpportunity,
        *,
        action: OpportunityDecisionAction,
        reason: str,
        stage: str,
        portfolio_exposure: dict[str, Any] | None = None,
    ) -> OpportunityDecision:
        opp = scored.opportunity
        prof = scored.profitability
        notional = opp.quantity * opp.entry_price
        gross_ret = prof.gross_profit_usd / notional if notional > 0 else Decimal("0")
        net_ret = prof.net_return
        meta = opp.metadata or {}
        return cls(
            opportunity_id=opp.id,
            action=action,
            reason=reason,
            strategy=opp.strategy_name,
            symbol=opp.symbol,
            asset_class=scored.asset_class,
            direction=opp.side.value,
            entry_price=opp.entry_price,
            expected_exit=opp.expected_exit_price,
            expected_gross_return=gross_ret,
            estimated_fees=prof.fees_usd,
            estimated_slippage=prof.slippage_usd,
            expected_net_return=net_ret,
            probability_profit=scored.probability_profit,
            expected_value=scored.expected_value,
            risk_reward=scored.risk_reward,
            liquidity_score=scored.liquidity_score,
            regime=scored.regime,
            correlation_group=scored.correlation_group,
            portfolio_exposure=portfolio_exposure or {},
            score=scored.score,
            stage=stage,
            calibrated_expected_value=scored.calibrated_expected_value,
            expected_net_eur=scored.expected_net_eur or prof.net_profit_usd,
            expected_net_eur_per_capital_second=scored.expected_net_eur_per_capital_second,
            first_limiting_gate=scored.first_limiting_gate or stage,
            all_gates=list(scored.all_gates or ([stage] if stage else [])),
            buy_exchange=str(meta.get("buy_exchange") or ""),
            sell_exchange=str(meta.get("sell_exchange") or ""),
            theoretical_net=prof.net_profit_usd,
        )
