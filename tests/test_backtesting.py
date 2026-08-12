"""Tests for backtesting scaffolding."""

from decimal import Decimal

import pytest

from bot.core.models import MarketSnapshot
from backtesting.engine import BacktestEngine
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.engine import DefaultRiskEngine
from bot.strategies.stub import StubStrategy


@pytest.mark.asyncio
async def test_backtest_runs_over_snapshots(settings) -> None:
    snapshots = [
        MarketSnapshot(
            symbol="BTCUSDT",
            bid=Decimal("100"),
            ask=Decimal("100.20"),
            last=Decimal("100.10"),
        ),
        MarketSnapshot(
            symbol="BTCUSDT",
            bid=Decimal("100"),
            ask=Decimal("100.01"),
            last=Decimal("100"),
        ),
    ]
    engine = BacktestEngine(
        strategy=StubStrategy(min_spread=Decimal("0.05")),
        profitability=DefaultProfitabilityEngine(settings),
        risk=DefaultRiskEngine(settings),
    )
    result = await engine.run(snapshots)
    assert len(result.opportunities) == 1
    assert result.approved_count + result.rejected_count == 1
