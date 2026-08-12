"""KillSwitch state machine tests."""

from decimal import Decimal

import pytest

from bot.core.enums import KillSwitchState, RiskRejectReason
from bot.core.models import PortfolioSnapshot, TradeOpportunity
from bot.risk.kill_switch import KillSwitch
from bot.risk.models import RiskContext
from bot.risk.risk_engine import RiskEngine
from tests.risk.conftest import make_profit


@pytest.mark.asyncio
async def test_kill_switch_activation_on_daily_loss(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    healthy_context: RiskContext,
    event_store,
) -> None:
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        peak_equity_usd=Decimal("10000"),
        daily_realized_pnl_usd=Decimal("-250"),
    )
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert not decision.approved
    assert risk_engine.kill_switch.state == KillSwitchState.PAUSED
    assert event_store.events
    assert event_store.events[-1].kill_switch_state == KillSwitchState.PAUSED


@pytest.mark.asyncio
async def test_kill_switch_blocks_new_orders(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    await risk_engine.kill_switch.pause("manual pause", code=RiskRejectReason.KILL_SWITCH)
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert decision.rejection_reason == RiskRejectReason.KILL_SWITCH.value


@pytest.mark.asyncio
async def test_emergency_stop(
    kill_switch: KillSwitch,
    event_store,
) -> None:
    await kill_switch.emergency_stop("circuit broken", code=RiskRejectReason.KILL_SWITCH)
    assert kill_switch.state == KillSwitchState.EMERGENCY_STOP
    assert kill_switch.allows_new_orders is False
    assert event_store.events[-1].event_type == "kill_switch_activated"


@pytest.mark.asyncio
async def test_kill_switch_recovery_denied_without_conditions(
    kill_switch: KillSwitch,
) -> None:
    await kill_switch.pause("test pause")
    recovered = await kill_switch.recover()
    assert recovered is False
    assert kill_switch.state == KillSwitchState.PAUSED


@pytest.mark.asyncio
async def test_kill_switch_recovery_when_conditions_met(
    kill_switch: KillSwitch,
    event_store,
) -> None:
    await kill_switch.pause("test pause")
    kill_switch.update_conditions(
        {
            "daily_loss_ok": True,
            "drawdown_ok": True,
            "market_data_fresh": True,
            "exchange_healthy": True,
            "execution_stable": True,
        }
    )
    recovered = await kill_switch.recover()
    assert recovered is True
    assert kill_switch.state == KillSwitchState.RUNNING
    assert kill_switch.allows_new_orders is True
    assert any(e.event_type == "kill_switch_recovered" for e in event_store.events)


@pytest.mark.asyncio
async def test_consecutive_failures_trigger_emergency_stop(
    kill_switch: KillSwitch,
) -> None:
    await kill_switch.record_execution_failure("timeout")
    await kill_switch.record_execution_failure("timeout")
    assert kill_switch.state != KillSwitchState.EMERGENCY_STOP
    await kill_switch.record_execution_failure("timeout")
    assert kill_switch.state == KillSwitchState.EMERGENCY_STOP


@pytest.mark.asyncio
async def test_warning_state_still_allows_orders(
    kill_switch: KillSwitch,
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    await kill_switch.warn("approaching limits")
    assert kill_switch.state == KillSwitchState.WARNING
    assert kill_switch.allows_new_orders is True
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is True


@pytest.mark.asyncio
async def test_no_auto_resume_on_healthy_context_while_paused(
    risk_engine: RiskEngine,
    opportunity: TradeOpportunity,
    portfolio: PortfolioSnapshot,
    healthy_context: RiskContext,
) -> None:
    await risk_engine.kill_switch.pause("paused for test")
    decision = await risk_engine.evaluate(
        opportunity,
        make_profit(opportunity),
        portfolio,
        context=healthy_context,
    )
    assert decision.approved is False
    assert risk_engine.kill_switch.state == KillSwitchState.PAUSED
