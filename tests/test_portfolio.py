"""Tests for portfolio service."""

from decimal import Decimal

import pytest

from bot.core.enums import OpportunitySide
from bot.core.models import Position
from bot.portfolio.manager import InMemoryPortfolioService


@pytest.mark.asyncio
async def test_portfolio_snapshot_defaults() -> None:
    svc = InMemoryPortfolioService(equity_usd=Decimal("5000"))
    snap = await svc.get_snapshot()
    assert snap.equity_usd == Decimal("5000")
    assert snap.open_position_count == 0
    assert snap.balances[0].asset == "USD"


@pytest.mark.asyncio
async def test_portfolio_positions_and_pnl() -> None:
    svc = InMemoryPortfolioService()
    svc.set_daily_pnl(Decimal("-25"))
    svc.set_positions(
        [
            Position(
                symbol="BTCUSDT",
                quantity=Decimal("0.5"),
                average_entry_price=Decimal("100"),
                side=OpportunitySide.BUY,
            )
        ]
    )
    snap = await svc.get_snapshot()
    assert snap.daily_realized_pnl_usd == Decimal("-25")
    assert snap.open_position_count == 1
