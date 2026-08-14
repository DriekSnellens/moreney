"""Phase 2: funding rates, transfer cost, walk-forward."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backtesting.engine import BacktestEngine
from backtesting.walk_forward import WalkForwardValidator
from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, PortfolioSnapshot, TradeOpportunity
from bot.market_data.funding import FundingRateService, perp_symbol_for
from bot.opportunity.engine import GlobalOpportunityEngine
from bot.opportunity.transfer_cost import CrossExchangeTransferCost
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.risk_engine import RiskEngine
from bot.strategies.funding_basis import FundingBasisStrategy
from bot.strategies.stub import StubStrategy


def test_perp_symbol_mapping() -> None:
    assert perp_symbol_for("BTCEUR") == "BTCUSDT"
    assert perp_symbol_for("ETHUSDT") == "ETHUSDT"


@pytest.mark.asyncio
async def test_funding_service_fetches_binance_rates() -> None:
    settings = Settings(
        execution_mode="paper",
        global_funding_strategy_enabled=True,
        market_data_symbols="BTCEUR,ETHUSDT",
    )
    service = FundingRateService(settings)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"lastFundingRate": "0.00015"}

    async def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    fake_client = AsyncMock()
    fake_client.get = fake_get

    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = fake_client
        await service._refresh_binance()

    assert service.rate_for_spot("binance", "BTCEUR") == Decimal("0.00015")


@pytest.mark.asyncio
async def test_funding_strategy_emits_when_rate_present() -> None:
    settings = Settings(
        execution_mode="paper",
        global_funding_strategy_enabled=True,
        global_min_funding_bps=1.0,
        profitability_min_net_profit_usd=0.0,
        profitability_min_net_return=0.0,
        profitability_execution_buffer_bps=0.0,
        profitability_apply_funding=True,
        profitability_maker_fee_rate=0.0001,
        profitability_taker_fee_rate=0.0001,
    )
    strategy = FundingBasisStrategy(settings)
    snap = MarketSnapshot(
        symbol="BTCUSDT",
        bid=Decimal("100"),
        ask=Decimal("100.2"),
        last=Decimal("100.1"),
        exchange="binance",
        funding_rate=Decimal("0.01"),
    )
    opps = await strategy.evaluate_markets([snap], equity=Decimal("1000"))
    assert len(opps) == 1
    assert opps[0].strategy_name == "funding_basis"
    assert opps[0].metadata.get("profitability_apply_funding") is True


def test_transfer_cost_applies_to_maker_cross_venue() -> None:
    settings = Settings()
    tc = CrossExchangeTransferCost(settings)
    opp = TradeOpportunity(
        strategy_name="maker_inventory",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        metadata={"buy_exchange": "okx", "sell_exchange": "binance"},
    )
    assert tc.estimate(opp) > 0


@pytest.mark.asyncio
async def test_walk_forward_rolling_windows() -> None:
    settings = Settings(execution_mode="paper")
    engine = BacktestEngine(
        strategy=StubStrategy(min_spread=Decimal("0.01")),
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
        for _ in range(20)
    ]
    result = await wf.run_rolling(snaps, window_size=10, step=5)
    assert result.windows >= 2


@pytest.mark.asyncio
async def test_global_engine_applies_transfer_cost_to_ev() -> None:
    settings = Settings(
        execution_mode="paper",
        global_transfer_fee_bps=20.0,
        global_transfer_latency_bps=10.0,
        opportunity_min_expected_value=0,
        max_simultaneous_positions=10,
        paper_maker_min_profit_eur=0.001,
        profitability_min_net_profit_usd=0.001,
        profitability_min_net_return=0.0001,
    )
    profitability = DefaultProfitabilityEngine(settings)
    risk = RiskEngine(settings)
    engine = GlobalOpportunityEngine(settings, profitability=profitability, risk=risk)
    opp = TradeOpportunity(
        strategy_name="maker_inventory",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        expected_exit_price=Decimal("101"),
        entry_fee_role=FeeRole.MAKER,
        exit_fee_role=FeeRole.MAKER,
        metadata={
            "post_only": True,
            "buy_exchange": "okx",
            "sell_exchange": "binance",
            "net_profit_eur": "0.5",
        },
    )
    ranked, all_scored = await engine.evaluate_batch(
        [opp],
        PortfolioSnapshot(equity_usd=Decimal("10000")),
    )
    assert all_scored[0].transfer_cost > 0
