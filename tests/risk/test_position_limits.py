"""Position limit calculator unit tests."""

from decimal import Decimal

from bot.core.enums import OpportunitySide
from bot.core.models import PortfolioSnapshot, Position, TradeOpportunity
from bot.risk.position_limits import PositionLimitCalculator


def test_max_notional_by_percent(risk_settings) -> None:
    calc = PositionLimitCalculator(risk_settings)
    assert calc.max_notional_by_percent(Decimal("10000")) == Decimal("1000")


def test_exposure_capacity(risk_settings) -> None:
    calc = PositionLimitCalculator(risk_settings)
    positions = [
        Position(
            symbol="ETH",
            quantity=Decimal("10"),
            average_entry_price=Decimal("200"),
            side=OpportunitySide.BUY,
        )
    ]
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=1,
    )
    # Current exposure 2000, max 50% = 5000, remaining 3000
    opp = TradeOpportunity(
        strategy_name="t",
        symbol="BTC",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    result = calc.evaluate(opp, portfolio)
    assert result.remaining_exposure_capacity == Decimal("3000")
    assert result.breached_codes == []


def test_total_exposure_breach(risk_settings) -> None:
    calc = PositionLimitCalculator(risk_settings)
    positions = [
        Position(
            symbol="ETH",
            quantity=Decimal("40"),
            average_entry_price=Decimal("100"),
            side=OpportunitySide.BUY,
        )
    ]
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("10000"),
        positions=positions,
        open_position_count=1,
    )
    # Exposure 4000, max 5000, remaining 1000; request 2000 → breach
    opp = TradeOpportunity(
        strategy_name="t",
        symbol="BTC",
        side=OpportunitySide.BUY,
        quantity=Decimal("20"),
        entry_price=Decimal("100"),
    )
    result = calc.evaluate(opp, portfolio)
    assert "MAX_TOTAL_EXPOSURE" in result.breached_codes
