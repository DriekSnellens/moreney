"""Fixtures for risk-layer unit tests."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.models import (
    MarketSnapshot,
    PortfolioSnapshot,
    ProfitabilityResult,
    TradeOpportunity,
)
from bot.risk.events import InMemoryRiskEventStore
from bot.risk.kill_switch import KillSwitch
from bot.risk.models import RiskContext
from bot.risk.risk_engine import RiskEngine


@pytest.fixture
def risk_settings() -> Settings:
    return Settings(
        app_env="development",
        execution_mode="paper",
        risk_max_position_usd=1000.0,
        risk_max_daily_loss_usd=200.0,
        risk_max_open_positions=5,
        risk_min_net_profit_usd=1.0,
        max_position_percent=10.0,
        max_total_exposure_percent=50.0,
        max_daily_loss_percent=3.0,
        max_drawdown_percent=5.0,
        max_simultaneous_positions=5,
        risk_allow_partial_sizing=False,
        max_trades_per_minute=30,
        max_slippage_percent=0.10,
        max_market_data_age_ms=1000.0,
        max_execution_latency_ms=2000.0,
        max_abnormal_price_move_percent=5.0,
        min_liquidity_base=0.01,
        risk_consecutive_failure_limit=3,
        risk_require_manual_recovery=True,
    )


@pytest.fixture
def event_store() -> InMemoryRiskEventStore:
    return InMemoryRiskEventStore()


@pytest.fixture
def kill_switch(risk_settings: Settings, event_store: InMemoryRiskEventStore) -> KillSwitch:
    return KillSwitch(risk_settings, on_event=event_store.record)


@pytest.fixture
def risk_engine(risk_settings: Settings, kill_switch: KillSwitch) -> RiskEngine:
    return RiskEngine(risk_settings, kill_switch=kill_switch)


@pytest.fixture
def healthy_context() -> RiskContext:
    return RiskContext(
        exchange_healthy=True,
        market_data_age_ms=50.0,
        estimated_slippage_pct=Decimal("0.01"),
        execution_latency_ms=20.0,
        liquidity_base=Decimal("10"),
        reference_price=Decimal("100"),
        current_price=Decimal("100.5"),
    )


@pytest.fixture
def portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        daily_realized_pnl_usd=Decimal("0"),
        open_position_count=0,
        positions=[],
    )


@pytest.fixture
def opportunity() -> TradeOpportunity:
    return TradeOpportunity(
        strategy_name="test",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        expected_exit_price=Decimal("110"),
        market=MarketSnapshot(
            symbol="BTCEUR",
            bid=Decimal("99.9"),
            ask=Decimal("100.1"),
            last=Decimal("100"),
        ),
    )


def make_profit(
    opportunity: TradeOpportunity,
    *,
    net: Decimal = Decimal("10"),
    allowed: bool = True,
) -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=opportunity.id,
        gross_profit_usd=net + Decimal("5"),
        buy_fee_usd=Decimal("0.5"),
        sell_fee_usd=Decimal("0.5"),
        fees_usd=Decimal("1"),
        slippage_usd=Decimal("1"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("1"),
        net_profit_usd=net,
        net_return=net / Decimal("100"),
        is_profitable=allowed,
        trade_allowed=allowed,
    )
