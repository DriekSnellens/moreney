"""Legacy risk tests — DefaultRiskEngine is an alias of RiskEngine."""

from decimal import Decimal

import pytest

from bot.core.enums import OpportunitySide, RiskDecisionStatus
from bot.core.models import PortfolioSnapshot, Position, ProfitabilityResult, TradeOpportunity
from bot.risk.engine import DefaultRiskEngine
from bot.risk.models import RiskContext


def _profit(
    opportunity: TradeOpportunity,
    *,
    net: Decimal,
    profitable: bool,
) -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=opportunity.id,
        gross_profit_usd=net + Decimal("5"),
        buy_fee_usd=Decimal("0.5"),
        sell_fee_usd=Decimal("0.5"),
        fees_usd=Decimal("1"),
        slippage_usd=Decimal("1"),
        funding_usd=Decimal("1"),
        execution_buffer_usd=Decimal("1"),
        net_profit_usd=net,
        net_return=net / Decimal("100") if net != 0 else Decimal("0"),
        is_profitable=profitable,
        trade_allowed=profitable,
    )


def _ctx() -> RiskContext:
    return RiskContext(
        exchange_healthy=True,
        market_data_age_ms=10.0,
        estimated_slippage_pct=Decimal("0.01"),
        execution_latency_ms=5.0,
        liquidity_base=Decimal("100"),
        reference_price=Decimal("100"),
        current_price=Decimal("100"),
    )


@pytest.mark.asyncio
async def test_risk_approves_valid_trade(
    settings, opportunity: TradeOpportunity, portfolio
) -> None:
    engine = DefaultRiskEngine(settings)
    snap = await portfolio.get_snapshot()
    decision = await engine.evaluate(
        opportunity,
        _profit(opportunity, net=Decimal("10"), profitable=True),
        snap,
        context=_ctx(),
    )
    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.approved is True


@pytest.mark.asyncio
async def test_risk_partial_sizing_when_enabled(settings, opportunity: TradeOpportunity) -> None:
    partial_settings = settings.model_copy(update={"risk_allow_partial_sizing": True})
    engine = DefaultRiskEngine(partial_settings)
    huge = opportunity.model_copy(update={"quantity": Decimal("100"), "entry_price": Decimal("100")})
    decision = await engine.evaluate(
        huge,
        _profit(huge, net=Decimal("50"), profitable=True),
        PortfolioSnapshot(equity_usd=Decimal("10000"), peak_equity_usd=Decimal("10000")),
        context=_ctx(),
    )
    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.max_allowed_quantity is not None
    assert decision.max_allowed_quantity < huge.quantity


@pytest.mark.asyncio
async def test_risk_rejects_oversized_position(settings, opportunity: TradeOpportunity) -> None:
    engine = DefaultRiskEngine(settings)
    huge = opportunity.model_copy(update={"quantity": Decimal("100"), "entry_price": Decimal("100")})
    decision = await engine.evaluate(
        huge,
        _profit(huge, net=Decimal("50"), profitable=True),
        PortfolioSnapshot(equity_usd=Decimal("10000"), peak_equity_usd=Decimal("10000")),
        context=_ctx(),
    )
    assert decision.status == RiskDecisionStatus.REJECTED
    assert any("notional" in r.lower() for r in decision.reasons)


@pytest.mark.asyncio
async def test_risk_rejects_when_max_positions_reached(
    settings, opportunity: TradeOpportunity
) -> None:
    engine = DefaultRiskEngine(settings)
    positions = [
        Position(
            symbol=f"S{i}",
            quantity=Decimal("1"),
            average_entry_price=Decimal("10"),
            side=OpportunitySide.BUY,
        )
        for i in range(settings.risk_max_open_positions)
    ]
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=len(positions),
    )
    decision = await engine.evaluate(
        opportunity,
        _profit(opportunity, net=Decimal("10"), profitable=True),
        portfolio,
        context=_ctx(),
    )
    assert decision.status == RiskDecisionStatus.REJECTED
    assert any("open positions" in r.lower() for r in decision.reasons)


@pytest.mark.asyncio
async def test_risk_rejects_unprofitable(
    settings, opportunity: TradeOpportunity
) -> None:
    engine = DefaultRiskEngine(settings)
    decision = await engine.evaluate(
        opportunity,
        _profit(opportunity, net=Decimal("-1"), profitable=False),
        PortfolioSnapshot(equity_usd=Decimal("10000"), peak_equity_usd=Decimal("10000")),
        context=_ctx(),
    )
    assert decision.status == RiskDecisionStatus.REJECTED
