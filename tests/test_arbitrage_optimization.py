"""Tests for cross-exchange arbitrage strategy optimizations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.models import PortfolioSnapshot, Position, TradeOpportunity
from bot.portfolio.accounting import _infer_base
from bot.risk.position_limits import PositionLimitCalculator
from bot.strategies.arbitrage import CrossExchangeArbitrageStrategy, top_of_book_snapshot
from tests.test_arbitrage import _arb_settings, _cheap_asks, _rich_bids


def test_infer_base_recognizes_usdt_suffix() -> None:
    assert _infer_base("BTCUSDT", "EUR") == "BTC"
    assert _infer_base("BTCEUR", "EUR") == "BTC"


def test_arb_exposure_uses_max_notional_not_sum() -> None:
    settings = Settings(
        app_env="development",
        execution_mode="paper",
        max_position_percent=10.0,
        max_total_exposure_percent=50.0,
        risk_max_position_usd=100_000.0,
    )
    calc = PositionLimitCalculator(settings)
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("1000"),
        positions=[
            Position(
                symbol="BTCEUR",
                quantity=Decimal("0.004"),
                average_entry_price=Decimal("55000"),
                side=OpportunitySide.BUY,
            ),
            Position(
                symbol="BTCUSDT",
                quantity=Decimal("0.004"),
                average_entry_price=Decimal("63000"),
                side=OpportunitySide.BUY,
            ),
        ],
        open_position_count=2,
    )
    opp = TradeOpportunity(
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.0003"),
        entry_price=Decimal("55000"),
        metadata={"buy_exchange": "binance", "sell_exchange": "kraken"},
    )
    result = calc.evaluate(opp, portfolio)
    # Gross sum would be ~460 EUR (>50% cap). Max-notional model leaves room.
    assert "MAX_TOTAL_EXPOSURE" not in result.breached_codes
    assert result.remaining_exposure_capacity > Decimal("0")


@pytest.mark.asyncio
async def test_dynamic_sizing_scales_with_equity() -> None:
    settings = _arb_settings(
        arbitrage_max_quantity=1.0,
        arbitrage_position_pct=10.0,
        arbitrage_min_profit_eur=0.0,
        arbitrage_min_profit_pct=0.0,
        profitability_fee_rate=0.0,
        profitability_maker_fee_rate=0.0,
        profitability_taker_fee_rate=0.0,
        profitability_slippage_bps=0.0,
        profitability_execution_buffer_bps=0.0,
        profitability_min_net_profit_usd=0.0,
        profitability_min_net_return=0.0,
    )
    strategy_small = CrossExchangeArbitrageStrategy(settings)
    strategy_large = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=_cheap_asks()),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]
    small = await strategy_small.evaluate_markets(snapshots, equity=Decimal("200"))
    large = await strategy_large.evaluate_markets(snapshots, equity=Decimal("5000"))
    assert small and large
    assert large[0].quantity > small[0].quantity


@pytest.mark.asyncio
async def test_cooldown_blocks_immediate_reemit() -> None:
    settings = _arb_settings(
        arbitrage_opportunity_cooldown_ms=60_000.0,
        arbitrage_max_emits_per_cycle=5,
        arbitrage_min_profit_eur=0.0,
        arbitrage_min_profit_pct=0.0,
        profitability_fee_rate=0.0,
        profitability_maker_fee_rate=0.0,
        profitability_taker_fee_rate=0.0,
        profitability_slippage_bps=0.0,
        profitability_execution_buffer_bps=0.0,
        profitability_min_net_profit_usd=0.0,
        profitability_min_net_return=0.0,
    )
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=_cheap_asks()),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]
    first = await strategy.evaluate_markets(snapshots, equity=Decimal("1000"))
    second = await strategy.evaluate_markets(snapshots, equity=Decimal("1000"))
    assert first
    assert second == []
