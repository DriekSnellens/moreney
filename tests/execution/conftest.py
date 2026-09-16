"""Fixtures for paper execution tests."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.execution.executor import ExecutionService
from bot.portfolio.portfolio import PaperPortfolio


@pytest.fixture
def exec_settings() -> Settings:
    return Settings(
        execution_mode="paper",
        paper_starting_eur=200.0,
        paper_quote_asset="EUR",
        paper_fee_rate=0.001,
        paper_slippage_mode="order_book",
        paper_fixed_slippage_pct=0.05,
        paper_partial_fills_on_thin_book=True,
        paper_reject_on_insufficient_liquidity=False,
        paper_simulated_latency_ms=0.0,
        paper_venue_inventory=False,
        paper_second_leg_adverse_bps=0.0,
        paper_maker_enabled=False,
    )


@pytest.fixture
def portfolio(exec_settings: Settings) -> PaperPortfolio:
    return PaperPortfolio(exec_settings, starting_eur=Decimal("200"))


@pytest.fixture
def execution(exec_settings: Settings, portfolio: PaperPortfolio) -> ExecutionService:
    return ExecutionService(exec_settings, portfolio=portfolio)


def make_book(
    *,
    ask_price: str = "100",
    ask_qty: str = "5",
    bid_price: str = "99",
    bid_qty: str = "5",
) -> OrderBook:
    return OrderBook(
        symbol="BTCEUR",
        asks=[OrderBookLevel(price=Decimal(ask_price), amount=Decimal(ask_qty))],
        bids=[OrderBookLevel(price=Decimal(bid_price), amount=Decimal(bid_qty))],
    )
