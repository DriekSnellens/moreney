"""RiskEngine approval / rejection scenarios."""

from decimal import Decimal

import pytest

from bot.core.enums import OpportunitySide, RiskDecisionStatus, RiskRejectReason
from bot.core.models import PortfolioSnapshot, Position, TradeOpportunity
from bot.risk.models import RiskContext
from bot.risk.risk_engine import RiskEngine
from tests.risk.conftest import make_profit


@pytest.mark.asyncio
async def test_normal_approved_trade(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is True
    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.rejection_reason is None
    assert decision.position_size_allowed == opportunity.quantity
    assert decision.risk_score >= 0
    assert decision.maximum_loss is not None


@pytest.mark.asyncio
async def test_position_too_large(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    huge = opportunity.model_copy(
        update={"quantity": Decimal("50"), "entry_price": Decimal("100")}
    )
    ctx = healthy_context.model_copy(update={"liquidity_base": Decimal("100")})
    with caplog.at_level("INFO"):
        decision = await risk_engine.evaluate(
            huge,
            make_profit(huge, net=Decimal("50")),
            portfolio,
            context=ctx,
        )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_POSITION_SIZE.value
    assert "RISK_REJECTED" in caplog.text
    assert "MAX_POSITION_SIZE" in caplog.text


@pytest.mark.asyncio
async def test_position_percent_exceeded(
    risk_settings,
    kill_switch,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    # 10% of 10000 = 1000 absolute also 1000 — use smaller absolute to isolate percent.
    settings = risk_settings.model_copy(
        update={"risk_max_position_usd": 5000.0, "max_position_percent": 5.0}
    )
    engine = RiskEngine(settings, kill_switch=kill_switch)
    # 8% of portfolio = 800 > 5% cap = 500
    trade = opportunity.model_copy(
        update={"quantity": Decimal("8"), "entry_price": Decimal("100")}
    )
    decision = await engine.evaluate(
        trade,
        make_profit(trade, net=Decimal("20")),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_POSITION_PERCENT.value


@pytest.mark.asyncio
async def test_daily_loss_exceeded(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
    event_store,
) -> None:
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        daily_realized_pnl_usd=Decimal("-500"),  # > min(3% = 300, abs 200) → 200
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_DAILY_LOSS.value
    assert risk_engine.kill_switch.state.value in {"paused", "emergency_stop"}
    assert any(e.event_type == "kill_switch_activated" for e in event_store.events)


@pytest.mark.asyncio
async def test_drawdown_exceeded(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
) -> None:
    # Peak 10000, equity 9400 → 6% drawdown > 5%
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("9400"),
        peak_equity_usd=Decimal("10000"),
        daily_realized_pnl_usd=Decimal("0"),
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_DRAWDOWN.value


@pytest.mark.asyncio
async def test_stale_market_data(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
) -> None:
    ctx = RiskContext(
        exchange_healthy=True,
        market_data_age_ms=5000.0,
        estimated_slippage_pct=Decimal("0.01"),
        liquidity_base=Decimal("10"),
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=ctx,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.STALE_MARKET_DATA.value


@pytest.mark.asyncio
async def test_excessive_slippage(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    ctx = healthy_context.model_copy(update={"estimated_slippage_pct": Decimal("1.5")})
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=ctx,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.EXCESSIVE_SLIPPAGE.value


@pytest.mark.asyncio
async def test_exchange_unavailable(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    ctx = healthy_context.model_copy(update={"exchange_healthy": False})
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=ctx,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.EXCHANGE_UNHEALTHY.value


@pytest.mark.asyncio
async def test_too_many_open_positions(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
) -> None:
    positions = [
        Position(
            symbol=f"S{i}",
            quantity=Decimal("1"),
            average_entry_price=Decimal("10"),
            side=OpportunitySide.BUY,
        )
        for i in range(5)
    ]
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=5,
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_SIMULTANEOUS_POSITIONS.value


@pytest.mark.asyncio
async def test_max_positions_allows_add_to_existing_symbol(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
) -> None:
    """At the cap, buying more of an already-open symbol must still pass."""
    positions = [
        Position(
            symbol=opportunity.symbol,
            quantity=Decimal("1"),
            average_entry_price=Decimal("10"),
            side=OpportunitySide.BUY,
        ),
        Position(
            symbol="OTHEREUR",
            quantity=Decimal("1"),
            average_entry_price=Decimal("10"),
            side=OpportunitySide.BUY,
        ),
    ]
    # Cap is 5 in conftest; force count at/above by setting open_position_count high
    # while listing the opportunity symbol as already open.
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=5,
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is True


@pytest.mark.asyncio
async def test_max_positions_blocks_brand_new_symbol(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
) -> None:
    positions = [
        Position(
            symbol=f"S{i}EUR",
            quantity=Decimal("1"),
            average_entry_price=Decimal("10"),
            side=OpportunitySide.BUY,
        )
        for i in range(5)
    ]
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=5,
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_SIMULTANEOUS_POSITIONS.value


@pytest.mark.asyncio
async def test_too_many_trades_per_minute(
    risk_settings,
    kill_switch,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    settings = risk_settings.model_copy(update={"max_trades_per_minute": 2})
    engine = RiskEngine(settings, kill_switch=kill_switch)
    for _ in range(2):
        ok = await engine.evaluate(
            opportunity,
            make_profit(opportunity),
            portfolio,
            context=healthy_context,
        )
        assert ok.approved is True
    decision = await engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.MAX_TRADES_PER_MINUTE.value


@pytest.mark.asyncio
async def test_execution_latency_rejected(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    ctx = healthy_context.model_copy(update={"execution_latency_ms": 10_000.0})
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=ctx,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.EXECUTION_LATENCY.value


@pytest.mark.asyncio
async def test_abnormal_price_movement(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    ctx = healthy_context.model_copy(
        update={
            "reference_price": Decimal("100"),
            "current_price": Decimal("120"),
        }
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=ctx,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.ABNORMAL_PRICE_MOVEMENT.value


@pytest.mark.asyncio
async def test_leverage_forbidden(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    leveraged = opportunity.model_copy(update={"metadata": {"leverage": 3}})
    decision = await risk_engine.evaluate(
        leveraged,
        make_profit(leveraged),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.LEVERAGE_FORBIDDEN.value


@pytest.mark.asyncio
async def test_risk_decision_structured_fields(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert hasattr(decision, "approved")
    assert hasattr(decision, "rejection_reason")
    assert hasattr(decision, "risk_score")
    assert hasattr(decision, "position_size_allowed")
    assert hasattr(decision, "maximum_loss")
    assert hasattr(decision, "warnings")
