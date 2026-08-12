"""Tests for strategies."""

from decimal import Decimal

import pytest

from bot.core.enums import OpportunitySide
from bot.core.models import MarketSnapshot
from bot.strategies.stub import StubStrategy


@pytest.mark.asyncio
async def test_stub_strategy_emits_opportunity_on_wide_spread(
    market_snapshot: MarketSnapshot,
) -> None:
    strategy = StubStrategy(min_spread=Decimal("0.05"), quantity=Decimal("2"))
    opps = await strategy.evaluate(market_snapshot)
    assert len(opps) == 1
    assert opps[0].side == OpportunitySide.BUY
    assert opps[0].quantity == Decimal("2")
    assert opps[0].strategy_name == "stub_spread"
    assert opps[0].market is not None


@pytest.mark.asyncio
async def test_stub_strategy_emits_nothing_on_tight_spread() -> None:
    strategy = StubStrategy(min_spread=Decimal("1.00"))
    snap = MarketSnapshot(
        symbol="BTCUSDT",
        bid=Decimal("100"),
        ask=Decimal("100.01"),
        last=Decimal("100"),
    )
    assert await strategy.evaluate(snap) == []
