"""Tests for trading engine orchestration with paper execution."""

from decimal import Decimal

import pytest

from bot.core.enums import OpportunitySide, OrderStatus, RiskDecisionStatus
from bot.core.exceptions import RiskRejectedError
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot, RiskDecision, TradeOpportunity
from bot.engine.orchestrator import TradingEngine
from bot.execution.paper_executor import PaperExecutor
from bot.market_data.provider import StaticMarketDataProvider
from bot.portfolio.portfolio import PaperPortfolio
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.engine import DefaultRiskEngine
from bot.strategies.stub import StubStrategy


def _paper_env(settings):
    return settings.model_copy(
        update={
            "execution_mode": "paper",
            "paper_starting_eur": 10_000.0,
            "paper_quote_asset": "EUR",
            "paper_fee_rate": 0.001,
            "paper_slippage_mode": "fixed",
            "paper_fixed_slippage_pct": 0.0,
            "paper_simulated_latency_ms": 0.0,
            "paper_venue_inventory": False,
            "paper_second_leg_adverse_bps": 0.0,
            "risk_max_position_usd": 5000.0,
            "max_position_percent": 50.0,
            "max_slippage_percent": 5.0,
            "max_market_data_age_ms": 60_000.0,
            "profitability_apply_funding": False,
        }
    )


@pytest.fixture
def engine(settings, market_snapshot: MarketSnapshot) -> TradingEngine:
    cfg = _paper_env(settings)
    portfolio = PaperPortfolio(cfg, starting_eur=Decimal("10000"))
    return TradingEngine(
        market_data=StaticMarketDataProvider({market_snapshot.symbol: market_snapshot}),
        strategy=StubStrategy(min_spread=Decimal("0.05")),
        profitability=DefaultProfitabilityEngine(cfg),
        risk=DefaultRiskEngine(cfg),
        portfolio=portfolio,
        executor=PaperExecutor(cfg, portfolio=portfolio),
    )


@pytest.mark.asyncio
async def test_run_once_executes_only_approved(engine: TradingEngine) -> None:
    result = await engine.run_once("BTCUSDT")
    assert result.symbol == "BTCUSDT"
    assert isinstance(result.opportunities, list)
    rejected_ids = {opp.id for opp, _ in result.rejected}
    executed_ids = {ex.opportunity_id for ex in result.executions}
    assert rejected_ids.isdisjoint(executed_ids)


@pytest.mark.asyncio
async def test_run_once_with_profitable_setup(settings) -> None:
    book = OrderBook(
        symbol="BTCEUR",
        asks=[OrderBookLevel(price=Decimal("100.50"), amount=Decimal("2"))],
        bids=[OrderBookLevel(price=Decimal("100.00"), amount=Decimal("2"))],
    )
    snap = MarketSnapshot(
        symbol="BTCEUR",
        bid=Decimal("100"),
        ask=Decimal("100.50"),
        last=Decimal("100.25"),
        order_book=book,
    )

    class AlwaysBuy(StubStrategy):
        async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
            return [
                TradeOpportunity(
                    strategy_name="always",
                    symbol=snapshot.symbol,
                    side=OpportunitySide.BUY,
                    quantity=Decimal("1"),
                    entry_price=snapshot.ask,
                    expected_exit_price=snapshot.ask + Decimal("5"),
                    confidence=1.0,
                    # Keep book off the opportunity so profitability does not
                    # double-count impact; execution still uses snapshot book.
                    market=snapshot.model_copy(update={"order_book": None}),
                    funding_periods=Decimal("0"),
                )
            ]

    cfg = _paper_env(settings)
    portfolio = PaperPortfolio(cfg, starting_eur=Decimal("10000"))
    paper = PaperExecutor(cfg, portfolio=portfolio)
    eng = TradingEngine(
        market_data=StaticMarketDataProvider({"BTCEUR": snap}),
        strategy=AlwaysBuy(),
        profitability=DefaultProfitabilityEngine(cfg),
        risk=DefaultRiskEngine(cfg),
        portfolio=portfolio,
        executor=paper,
    )
    result = await eng.run_once("BTCEUR")
    assert len(result.opportunities) == 1
    assert len(result.executions) == 1
    assert result.executions[0].status == OrderStatus.FILLED
    assert result.executions[0].metadata.get("real_exchange_order") is False
    assert len(result.rejected) == 0
    assert portfolio.available("BTC") == Decimal("1")


@pytest.mark.asyncio
async def test_execute_approved_rejects_unapproved(engine: TradingEngine) -> None:
    opp = TradeOpportunity(
        strategy_name="t",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    decision = RiskDecision(
        opportunity_id=opp.id,
        status=RiskDecisionStatus.REJECTED,
        reasons=["nope"],
    )
    with pytest.raises(RiskRejectedError):
        await engine.execute_approved(opp, decision)


def test_notional_helper() -> None:
    opp = TradeOpportunity(
        strategy_name="t",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("2"),
        entry_price=Decimal("50"),
    )
    assert TradingEngine.notional_usd(opp) == Decimal("100")
