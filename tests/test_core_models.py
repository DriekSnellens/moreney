"""Tests for core domain models."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from bot.core.enums import OpportunitySide, RiskDecisionStatus
from bot.core.models import (
    Balance,
    MarketSnapshot,
    ProfitEstimate,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)


def test_market_snapshot_mid_and_spread() -> None:
    snap = MarketSnapshot(
        symbol="ethusdt",
        bid=Decimal("10"),
        ask=Decimal("12"),
        last=Decimal("11"),
    )
    assert snap.symbol == "ETHUSDT"
    assert snap.mid == Decimal("11")
    assert snap.spread == Decimal("2")


def test_trade_opportunity_normalizes_symbol() -> None:
    opp = TradeOpportunity(
        strategy_name="s",
        symbol=" btcusdt ",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    assert opp.symbol == "BTCUSDT"


def test_trade_opportunity_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValidationError):
        TradeOpportunity(
            strategy_name="s",
            symbol="BTCUSDT",
            side=OpportunitySide.BUY,
            quantity=Decimal("0"),
            entry_price=Decimal("100"),
        )


def test_risk_decision_approved_property() -> None:
    from uuid import uuid4

    oid = uuid4()
    approved = RiskDecision(opportunity_id=oid, status=RiskDecisionStatus.APPROVED)
    rejected = RiskDecision(opportunity_id=oid, status=RiskDecisionStatus.REJECTED)
    assert approved.approved is True
    assert rejected.approved is False


def test_balance_total() -> None:
    bal = Balance(asset="USD", free=Decimal("8"), locked=Decimal("2"))
    assert bal.total == Decimal("10")


def test_profitability_result_fields() -> None:
    from uuid import uuid4

    estimate = ProfitEstimate(
        gross_profit=Decimal("10"),
        buy_fee=Decimal("0.5"),
        sell_fee=Decimal("0.5"),
        slippage=Decimal("1"),
        funding_cost=Decimal("1"),
        execution_buffer=Decimal("1"),
        net_profit=Decimal("6"),
        net_return=Decimal("0.06"),
        trade_allowed=True,
    )
    result = ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=Decimal("10"),
        buy_fee_usd=Decimal("0.5"),
        sell_fee_usd=Decimal("0.5"),
        fees_usd=Decimal("1"),
        slippage_usd=Decimal("1"),
        funding_usd=Decimal("1"),
        execution_buffer_usd=Decimal("1"),
        net_profit_usd=Decimal("6"),
        net_return=Decimal("0.06"),
        is_profitable=True,
        trade_allowed=True,
        estimate=estimate,
    )
    assert result.is_profitable is True
    assert result.trade_allowed is True
    assert result.net_profit_usd == Decimal("6")
    assert result.estimate is not None
    assert result.estimate.total_fees == Decimal("1")
