"""Comprehensive tests for global opportunity engine phases."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import AssetClass, MarketRegime, OpportunityDecisionAction
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot, PortfolioSnapshot, TradeOpportunity
from bot.core.enums import FeeRole, OpportunitySide
from bot.markets.calendar import MarketCalendarService
from bot.markets.registry import InstrumentRegistry
from bot.opportunity.decision_log import OpportunityDecisionLogger
from bot.opportunity.engine import GlobalOpportunityEngine
from bot.opportunity.ev_engine import ExpectedValueEngine
from bot.opportunity.ranker import OpportunityRanker
from bot.opportunity.scanner import TieredScanScheduler
from bot.opportunity.transfer_cost import CrossExchangeTransferCost
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.regime.detector import RegimeDetector
from bot.risk.risk_engine import RiskEngine
from bot.strategies.arbitrage import top_of_book_snapshot


def _settings(**overrides: object) -> Settings:
    base = {
        "execution_mode": "paper",
        "paper_maker_min_profit_eur": 0.001,
        "arbitrage_min_profit_pct": 0.0001,
        "profitability_min_net_profit_usd": 0.001,
        "profitability_min_net_return": 0.0001,
        "profitability_execution_buffer_bps": 1.0,
        "risk_min_net_profit_usd": 0.001,
        "global_opportunity_engine_enabled": True,
        "market_data_symbols": "BTCEUR,ETHEUR,BTCUSDT,EURUSDT",
    }
    base.update(overrides)
    return Settings(**base)


def test_instrument_registry_crypto() -> None:
    reg = InstrumentRegistry(_settings())
    assert reg.by_symbol("BTCEUR") is not None
    assert reg.by_symbol("BTCEUR").asset_class == AssetClass.CRYPTO_SPOT


def test_market_calendar_crypto_always_open() -> None:
    reg = InstrumentRegistry(_settings())
    cal = MarketCalendarService()
    inst = reg.by_symbol("BTCEUR")
    assert inst is not None
    assert cal.is_tradeable(inst)


def test_ev_engine_positive_for_profitable_opp() -> None:
    settings = _settings()
    ev = ExpectedValueEngine(settings)
    opp = TradeOpportunity(
        strategy_name="test",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        expected_exit_price=Decimal("101"),
        confidence=0.7,
    )
    from bot.profitability.engine import DefaultProfitabilityEngine

    prof_engine = DefaultProfitabilityEngine(settings)

    async def _run() -> None:
        prof = await prof_engine.evaluate(opp)
        data = ev.enrich(opp, prof)
        assert data["expected_value"] > 0

    import asyncio

    asyncio.run(_run())


def test_transfer_cost_cross_venue() -> None:
    settings = _settings()
    tc = CrossExchangeTransferCost(settings)
    opp = TradeOpportunity(
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        metadata={"buy_exchange": "okx", "sell_exchange": "binance"},
    )
    cost = tc.estimate(opp)
    assert cost > 0


def test_regime_detector_classifies() -> None:
    det = RegimeDetector()
    snaps = []
    for i in range(25):
        px = Decimal("100") + Decimal(str(i)) * Decimal("0.01")
        snaps.append(
            MarketSnapshot(
                symbol="BTCEUR",
                bid=px,
                ask=px + Decimal("0.1"),
                last=px,
            )
        )
    regimes = det.update(snaps)
    assert "BTCEUR" in regimes


def test_tiered_scanner_returns_symbols() -> None:
    settings = _settings()
    reg = InstrumentRegistry(settings)
    cal = MarketCalendarService()
    sched = TieredScanScheduler(settings, reg, cal)
    syms = sched.symbols_for_cycle(all_symbols=["BTCEUR", "ETHEUR"])
    assert "BTCEUR" in syms


def test_opportunity_ranker_orders_by_score() -> None:
    from bot.core.models import ProfitabilityResult
    from bot.opportunity.models import ScoredOpportunity
    from uuid import uuid4

    settings = _settings()
    ranker = OpportunityRanker(settings)

    def scored(ev: str, score: str) -> ScoredOpportunity:
        oid = uuid4()
        opp = TradeOpportunity(
            strategy_name="test",
            symbol="BTCEUR",
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
        )
        prof = ProfitabilityResult(
            opportunity_id=oid,
            gross_profit_usd=Decimal("1"),
            fees_usd=Decimal("0.1"),
            slippage_usd=Decimal("0"),
            funding_usd=Decimal("0"),
            execution_buffer_usd=Decimal("0"),
            net_profit_usd=Decimal("0.9"),
            is_profitable=True,
            trade_allowed=True,
        )
        opp.id = oid
        s = ScoredOpportunity(
            opportunity=opp,
            profitability=prof,
            expected_value=Decimal(ev),
            score=Decimal(score),
        )
        return s

    ranked = ranker.rank([scored("0.5", "1"), scored("2", "5")])
    assert ranked[0].expected_value == Decimal("2")


@pytest.mark.asyncio
async def test_global_opportunity_engine_batch() -> None:
    settings = _settings(
        opportunity_min_expected_value=0,
        max_simultaneous_positions=10,
    )
    profitability = DefaultProfitabilityEngine(settings)
    risk = RiskEngine(settings)
    engine = GlobalOpportunityEngine(
        settings,
        profitability=profitability,
        risk=risk,
        decision_log=OpportunityDecisionLogger(),
    )
    book = OrderBook(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("10"))],
    )
    snap = top_of_book_snapshot(
        exchange="okx",
        symbol="BTCEUR",
        order_book=book,
    )
    opp = TradeOpportunity(
        strategy_name="maker_inventory",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.5"),
        entry_price=Decimal("99"),
        expected_exit_price=Decimal("101"),
        entry_fee_role=FeeRole.MAKER,
        exit_fee_role=FeeRole.MAKER,
        market=snap,
        metadata={
            "post_only": True,
            "buy_exchange": "okx",
            "sell_exchange": "okx",
            "net_profit_eur": "0.5",
        },
    )
    portfolio = PortfolioSnapshot(equity_usd=Decimal("1000"))
    ranked, all_scored = await engine.evaluate_batch([opp], portfolio, venue_snapshots=[snap])
    assert len(all_scored) == 1
    assert all_scored[0].expected_value is not None


def test_decision_logger_records() -> None:
    from bot.opportunity.models import ScoredOpportunity
    from bot.core.models import ProfitabilityResult
    from uuid import uuid4

    log = OpportunityDecisionLogger()
    oid = uuid4()
    opp = TradeOpportunity(
        id=oid,
        strategy_name="test",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )
    prof = ProfitabilityResult(
        opportunity_id=oid,
        gross_profit_usd=Decimal("0"),
        fees_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0"),
        net_profit_usd=Decimal("0"),
        is_profitable=False,
        trade_allowed=False,
    )
    scored = ScoredOpportunity(opportunity=opp, profitability=prof)
    log.log(
        scored,
        action=OpportunityDecisionAction.REJECT,
        reason="test",
        stage="unit",
    )
    assert len(log.recent()) == 1


@pytest.mark.asyncio
async def test_walk_forward_validator() -> None:
    from backtesting.engine import BacktestEngine
    from backtesting.walk_forward import WalkForwardValidator
    from bot.strategies.stub import StubStrategy

    settings = _settings()
    strategy = StubStrategy(min_spread=Decimal("0.01"))
    engine = BacktestEngine(
        strategy=strategy,
        profitability=DefaultProfitabilityEngine(settings),
        risk=RiskEngine(settings),
    )
    wf = WalkForwardValidator(engine, train_ratio=0.5)
    snaps = [
        MarketSnapshot(
            symbol="BTCEUR",
            bid=Decimal("100"),
            ask=Decimal("100.5"),
            last=Decimal("100.25"),
        )
        for _ in range(10)
    ]
    result = await wf.run(snaps)
    assert result.windows >= 1
