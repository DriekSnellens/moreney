"""Integration: mocked Binance/Kraken books → arb → risk → paper → portfolio → API.

Does NOT call real exchange APIs. Does NOT place live orders.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings
from bot.core.enums import ExecutionMode, OrderSide, OrderStatus
from bot.engine.orchestrator import TradingEngine
from bot.execution.paper_executor import PaperExecutor
from bot.main import app, get_market_data_service, reset_risk_singletons, set_last_paper_cycle
from bot.market_data.provider_realtime import RealtimeMarketDataProvider
from bot.market_data.service import MarketDataService
from bot.portfolio.portfolio import PaperPortfolio
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.risk.risk_engine import RiskEngine
from bot.strategies.arbitrage import CrossExchangeArbitrageStrategy


def _integration_settings() -> Settings:
    return Settings(
        app_env="development",
        execution_mode="paper",
        market_data_exchanges="binance,kraken",
        market_data_symbols="BTCEUR",
        max_market_data_age_ms=5000.0,
        arbitrage_min_profit_eur=1.0,
        arbitrage_min_profit_pct=0.0001,
        arbitrage_min_liquidity_base=0.01,
        arbitrage_max_quantity=0.5,
        arbitrage_max_latency_ms=5000.0,
        arbitrage_max_book_age_ms=5000.0,
        profitability_fee_rate=0.0001,
        profitability_maker_fee_rate=0.0001,
        profitability_taker_fee_rate=0.0001,
        profitability_slippage_bps=1.0,
        profitability_execution_buffer_bps=1.0,
        profitability_apply_funding=False,
        profitability_min_net_profit_usd=1.0,
        profitability_min_net_return=0.0001,
        paper_starting_eur=200_000.0,
        paper_quote_asset="EUR",
        paper_fee_rate=0.0001,
        paper_slippage_mode="order_book",
        paper_simulated_latency_ms=0.0,
        risk_max_position_usd=100_000.0,
        max_position_percent=80.0,
        max_total_exposure_percent=90.0,
        max_slippage_percent=5.0,
        max_daily_loss_percent=50.0,
        max_drawdown_percent=50.0,
        risk_min_net_profit_usd=1.0,
        min_liquidity_base=0.01,
    )


@pytest.fixture(autouse=True)
def _reset_api() -> None:
    reset_risk_singletons()
    yield
    reset_risk_singletons()


@pytest.mark.asyncio
async def test_cross_exchange_paper_pipeline_binance_kraken() -> None:
    settings = _integration_settings()
    service = MarketDataService(settings)

    # Simulated public order books (no live APIs)
    service.inject_snapshot(
        "binance",
        "BTCEUR",
        bid=Decimal("99900"),
        ask=Decimal("100000"),
        bid_size=Decimal("2"),
        ask_size=Decimal("2"),
        sequence=1,
    )
    service.inject_snapshot(
        "kraken",
        "BTCEUR",
        bid=Decimal("100150"),
        ask=Decimal("100250"),
        bid_size=Decimal("2"),
        ask_size=Decimal("2"),
        sequence=1,
    )

    # 1–2: receive + synchronize
    assert service.get_local_book("binance", "BTCEUR") is not None
    assert service.get_local_book("binance", "BTCEUR").synchronized  # type: ignore[union-attr]
    assert service.get_local_book("kraken", "BTCEUR").synchronized  # type: ignore[union-attr]

    snapshots = service.snapshots_for_arbitrage("BTCEUR")
    assert len(snapshots) == 2

    provider = RealtimeMarketDataProvider(service)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("200000"))
    paper = PaperExecutor(settings, portfolio=portfolio)
    strategy = CrossExchangeArbitrageStrategy(settings)
    risk = RiskEngine(settings)
    profitability = DefaultProfitabilityEngine(settings)

    engine = TradingEngine(
        market_data=provider,
        strategy=strategy,
        profitability=profitability,
        risk=risk,
        portfolio=portfolio,
        executor=paper,
    )

    starting_eur = portfolio.available("EUR")
    starting_btc = portfolio.available("BTC")

    # 3–8: detect → NET profit → risk → paper execute → portfolio → record
    result = await engine.run_once("BTCEUR")

    assert len(result.opportunities) >= 1
    opp = result.opportunities[0]
    assert opp.metadata["buy_exchange"] == "binance"
    assert opp.metadata["sell_exchange"] == "kraken"
    assert Decimal(str(opp.metadata["net_profit_eur"])) > 0

    assert len(result.profitability) >= 1
    assert result.profitability[0].trade_allowed is True
    assert result.profitability[0].net_profit_usd > 0

    assert len(result.risk_decisions) >= 1
    assert result.risk_decisions[0].approved is True

    assert len(result.executions) >= 2
    assert result.executions[0].status == OrderStatus.FILLED
    assert result.executions[0].metadata.get("real_exchange_order") is False
    assert result.executions[1].status == OrderStatus.FILLED
    assert len(result.fills) >= 2
    assert any(f.side == OrderSide.BUY for f in result.fills)
    assert any(f.side == OrderSide.SELL for f in result.fills)
    assert len(result.orders) >= 2

    # Round-trip arb: BTC inventory returns to start; EUR rises net of fees.
    assert portfolio.available("BTC") == starting_btc
    assert portfolio.available("EUR") > starting_eur
    assert result.portfolio_equity is not None
    assert result.portfolio_equity > Decimal("200000")

    # 9: expose through API
    cycle_payload = {
        "symbol": result.symbol,
        "opportunities": len(result.opportunities),
        "net_profit_eur": str(result.profitability[0].net_profit_usd),
        "approved": result.risk_decisions[0].approved,
        "filled": result.executions[0].status.value,
        "portfolio_equity": str(result.portfolio_equity),
        "btc_balance": str(portfolio.available("BTC")),
        "eur_balance": str(portfolio.available("EUR")),
        "real_exchange_order": False,
        "execution_mode": ExecutionMode.PAPER.value,
    }
    # Wire into app singleton for /paper/last-cycle and /market-data/status
    reset_risk_singletons()
    app_service = get_market_data_service()
    app_service.inject_snapshot(
        "binance",
        "BTCEUR",
        bid=Decimal("99900"),
        ask=Decimal("100000"),
        sequence=1,
    )
    app_service.inject_snapshot(
        "kraken",
        "BTCEUR",
        bid=Decimal("100150"),
        ask=Decimal("100250"),
        sequence=1,
    )
    set_last_paper_cycle(cycle_payload)

    client = TestClient(app)
    md = client.get("/market-data/status")
    assert md.status_code == 200
    body = md.json()
    assert "binance" in body
    assert "kraken" in body
    assert body["binance"]["stale"] is False
    assert body["binance"]["synchronized"] is True

    last = client.get("/paper/last-cycle")
    assert last.status_code == 200
    last_body = last.json()
    assert last_body["available"] is True
    assert last_body["cycle"]["approved"] is True
    assert last_body["cycle"]["real_exchange_order"] is False

    status = client.get("/status")
    assert status.json()["execution_mode"] == "paper"
    assert status.json()["live_trading_enabled"] is False
    assert status.json()["withdrawals_supported"] is False
    assert status.json()["leverage_supported"] is False
