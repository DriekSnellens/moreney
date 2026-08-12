"""Shared pytest fixtures."""

from decimal import Decimal
from pathlib import Path

import pytest

from bot.core.config import Settings, get_settings
from bot.core.enums import OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.execution.paper import PaperExecutor
from bot.market_data.provider import StaticMarketDataProvider
from bot.portfolio.manager import InMemoryPortfolioService
from bot.portfolio.portfolio import PaperPortfolio
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.engine import DefaultRiskEngine
from bot.strategies.stub import StubStrategy


@pytest.fixture(autouse=True)
def _isolate_paper_persist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep paper persistence out of the shared data/ directory during tests."""
    path = tmp_path / "paper_state.json"
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(path))
    monkeypatch.setenv("PAPER_AUTO_START", "false")
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="development",
        execution_mode="paper",
        exchange_name="stub",
        paper_trading_enabled=True,
        paper_auto_start=False,
        paper_starting_eur=200.0,
        risk_max_position_usd=1000.0,
        risk_max_daily_loss_usd=200.0,
        risk_max_open_positions=5,
        risk_min_net_profit_usd=1.0,
        max_position_percent=10.0,
        max_total_exposure_percent=50.0,
        max_daily_loss_percent=3.0,
        max_drawdown_percent=5.0,
        max_trades_per_minute=30,
        max_slippage_percent=0.10,
        max_market_data_age_ms=1000.0,
        max_execution_latency_ms=2000.0,
        max_abnormal_price_move_percent=5.0,
        min_liquidity_base=0.01,
        profitability_fee_rate=0.001,
        profitability_maker_fee_rate=0.0008,
        profitability_taker_fee_rate=0.001,
        profitability_slippage_bps=5.0,
        profitability_market_impact_factor=1.0,
        profitability_thin_book_penalty_bps=25.0,
        profitability_funding_rate=0.0001,
        profitability_apply_funding=True,
        profitability_execution_buffer_bps=10.0,
        profitability_min_net_profit_usd=1.0,
        profitability_min_net_return=0.001,
    )


@pytest.fixture
def market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCUSDT",
        bid=Decimal("100.00"),
        ask=Decimal("100.20"),
        last=Decimal("100.10"),
        funding_rate=Decimal("0.0001"),
    )


@pytest.fixture
def opportunity(market_snapshot: MarketSnapshot) -> TradeOpportunity:
    return TradeOpportunity(
        strategy_name="test",
        symbol="BTCUSDT",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100.20"),
        expected_exit_price=Decimal("101.00"),
        confidence=0.7,
        rationale="unit test",
        market=market_snapshot,
    )


@pytest.fixture
def market_data(market_snapshot: MarketSnapshot) -> StaticMarketDataProvider:
    return StaticMarketDataProvider({market_snapshot.symbol: market_snapshot})


@pytest.fixture
def strategy() -> StubStrategy:
    return StubStrategy(min_spread=Decimal("0.05"), quantity=Decimal("1"))


@pytest.fixture
def profitability(settings: Settings) -> DefaultProfitabilityEngine:
    return DefaultProfitabilityEngine(settings)


@pytest.fixture
def risk(settings: Settings) -> DefaultRiskEngine:
    return DefaultRiskEngine(settings)


@pytest.fixture
def portfolio() -> InMemoryPortfolioService:
    return InMemoryPortfolioService(equity_usd=Decimal("10000"))


@pytest.fixture
def paper_executor(settings: Settings) -> PaperExecutor:
    paper_settings = settings.model_copy(
        update={
            "execution_mode": "paper",
            "paper_starting_eur": 10_000.0,
            "paper_simulated_latency_ms": 0.0,
            "paper_fee_rate": 0.001,
            "paper_slippage_mode": "fixed",
            "paper_fixed_slippage_pct": 0.0,
        }
    )
    portfolio = PaperPortfolio(paper_settings, starting_eur=Decimal("10000"))
    return PaperExecutor(paper_settings, portfolio=portfolio)
