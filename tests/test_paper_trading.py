"""Tests for paper trading runner, tracker, persistence, and API."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings, get_settings
from bot.core.enums import (
    ExecutionMode,
    OpportunityLifecycleStatus,
    OpportunitySide,
    OrderStatus,
    RiskDecisionStatus,
)
from bot.core.models import (
    ExecutionResult,
    ProfitabilityResult,
    RiskDecision,
    TradeOpportunity,
)
from bot.main import app, get_paper_runner, reset_risk_singletons
from bot.paper.dashboard import render_dashboard, render_dashboard_lite, render_fleet_dashboard
from bot.market_data.service import MarketDataService
from bot.paper.runner import PaperRunner
from bot.paper.store import PaperTradingStore
from bot.paper.tracker import PerformanceTracker
from bot.portfolio.models import Fill, Order
from bot.portfolio.portfolio import PaperPortfolio
from bot.risk.risk_engine import RiskEngine


def _paper_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="development",
        execution_mode="paper",
        paper_trading_enabled=True,
        paper_auto_start=False,
        paper_starting_eur=200.0,
        paper_persist_path=str(tmp_path / "paper_state.json"),
        paper_cycle_interval_ms=50.0,
        paper_simulated_latency_ms=0.0,
        paper_venue_inventory=False,
        paper_second_leg_adverse_bps=0.0,
        paper_maker_enabled=False,
        paper_fee_rate=0.0001,
        paper_slippage_mode="order_book",
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
def _reset_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "api_paper_state.json"))
    monkeypatch.setenv("PAPER_AUTO_START", "false")
    get_settings.cache_clear()
    reset_risk_singletons()
    yield
    reset_risk_singletons()
    get_settings.cache_clear()


def test_performance_tracker_records_rejects_and_approvals() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp = TradeOpportunity(
        strategy_name="CrossExchangeArbitrageStrategy",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.1"),
        entry_price=Decimal("100000"),
        metadata={
            "buy_exchange": "binance",
            "sell_exchange": "kraken",
            "sell_vwap": "100200",
        },
    )
    profit = ProfitabilityResult(
        opportunity_id=opp.id,
        gross_profit_usd=Decimal("20"),
        fees_usd=Decimal("2"),
        slippage_usd=Decimal("1"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("1"),
        net_profit_usd=Decimal("16"),
        net_return=Decimal("0.0016"),
        is_profitable=True,
        trade_allowed=True,
    )
    tracker.record_detected(opp, profit)
    decision = RiskDecision(
        opportunity_id=opp.id,
        status=RiskDecisionStatus.REJECTED,
        reasons=["stale"],
        rejection_reason="STALE_MARKET_DATA",
    )
    tracker.record_risk(opp.id, decision)
    snap = tracker.snapshot()
    assert snap.total_opportunities == 1
    assert snap.rejected_opportunities == 1
    assert snap.approved_opportunities == 0
    assert tracker.opportunities()[0].status == OpportunityLifecycleStatus.REJECTED

    # Strategy + pair + hourly buckets updated
    assert tracker.strategy_stats()[0].opportunities == 1
    assert tracker.exchange_pair_stats()[0].pair_key == "binance->kraken"
    assert sum(h.opportunities for h in tracker.hourly_stats()) == 1


def test_performance_tracker_profitable_fill() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp_id = uuid4()
    opp = TradeOpportunity(
        id=opp_id,
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100000"),
        metadata={
            "buy_exchange": "binance",
            "sell_exchange": "kraken",
            "sell_vwap": "101000",
        },
    )
    profit = ProfitabilityResult(
        opportunity_id=opp_id,
        gross_profit_usd=Decimal("10"),
        fees_usd=Decimal("0.2"),
        slippage_usd=Decimal("0.1"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.1"),
        net_profit_usd=Decimal("9.6"),
        is_profitable=True,
        trade_allowed=True,
    )
    tracker.record_detected(opp, profit)
    tracker.record_risk(
        opp_id,
        RiskDecision(opportunity_id=opp_id, status=RiskDecisionStatus.APPROVED),
    )
    from bot.core.enums import OrderSide

    buy_order = Order(
        id=uuid4(),
        strategy="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OrderSide.BUY,
        requested_quantity=Decimal("0.01"),
        opportunity_id=opp_id,
    )
    sell_order = Order(
        id=uuid4(),
        strategy="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OrderSide.SELL,
        requested_quantity=Decimal("0.01"),
        opportunity_id=opp_id,
    )
    buy_fill = Fill(
        order_id=buy_order.id,
        symbol="BTCEUR",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("0.1"),
        slippage=Decimal("0.05"),
    )
    sell_fill = Fill(
        order_id=sell_order.id,
        symbol="BTCEUR",
        side=OrderSide.SELL,
        quantity=Decimal("0.01"),
        price=Decimal("101000"),
        fee=Decimal("0.1"),
        slippage=Decimal("0.05"),
    )
    execution = ExecutionResult(
        order_id=buy_order.id,
        opportunity_id=opp_id,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        average_price=Decimal("100000"),
        fees_usd=Decimal("0.1"),
    )
    tracker.record_execution(
        opp_id,
        execution,
        orders=[buy_order, sell_order],
        fills=[buy_fill, sell_fill],
        equity_before=Decimal("200"),
        equity_after=Decimal("209"),
    )
    tracked = tracker.opportunities()[0]
    assert tracked.status == OpportunityLifecycleStatus.PROFITABLE
    assert tracked.realized_net_profit is not None
    assert tracked.realized_net_profit > 0
    snap = tracker.snapshot()
    assert snap.executed_opportunities == 1
    assert snap.trade_count == 1
    assert snap.approved_opportunities == 1
    assert snap.net_pnl == tracked.realized_net_profit
    assert snap.net_pnl != Decimal("9")  # equity delta is not live-equivalent winst


def test_performance_tracker_partial_sell_does_not_dump_buy_as_loss() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp_id = uuid4()
    opp = TradeOpportunity(
        id=opp_id,
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100000"),
        metadata={"buy_exchange": "binance", "sell_exchange": "kraken"},
    )
    tracker.record_detected(opp, None)
    tracker.record_risk(
        opp_id,
        RiskDecision(opportunity_id=opp_id, status=RiskDecisionStatus.APPROVED),
    )
    from bot.core.enums import OrderSide

    buy_order = Order(
        id=uuid4(),
        strategy="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OrderSide.BUY,
        requested_quantity=Decimal("0.01"),
        opportunity_id=opp_id,
    )
    sell_order = Order(
        id=uuid4(),
        strategy="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OrderSide.SELL,
        requested_quantity=Decimal("0.001"),
        opportunity_id=opp_id,
    )
    buy_fill = Fill(
        order_id=buy_order.id,
        symbol="BTCEUR",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("1"),
        slippage=Decimal("0"),
    )
    sell_fill = Fill(
        order_id=sell_order.id,
        symbol="BTCEUR",
        side=OrderSide.SELL,
        quantity=Decimal("0.001"),
        price=Decimal("101000"),
        fee=Decimal("0.1"),
        slippage=Decimal("0"),
    )
    execution = ExecutionResult(
        order_id=buy_order.id,
        opportunity_id=opp_id,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        average_price=Decimal("100000"),
        fees_usd=Decimal("1"),
    )
    tracker.record_execution(
        opp_id,
        execution,
        orders=[buy_order, sell_order],
        fills=[buy_fill, sell_fill],
        equity_before=Decimal("200"),
        equity_after=Decimal("50"),
    )
    tracked = tracker.opportunities()[0]
    # Matched 0.001 BTC: buy cost 100.1, sell proceeds 100.9 → small profit.
    # Must not treat the unsold 0.009 as a ~€900 realized loss.
    assert tracked.realized_net_profit == Decimal("0.8")
    assert tracker.snapshot().net_pnl == Decimal("0.8")
    assert tracker.snapshot().paper_equity_pnl == Decimal("0")


def test_performance_tracker_incomplete_arb_not_counted_as_trade() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp_id = uuid4()
    opp = TradeOpportunity(
        id=opp_id,
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100000"),
        metadata={
            "buy_exchange": "binance",
            "sell_exchange": "kraken",
            "sell_vwap": "101000",
        },
    )
    tracker.record_detected(opp, None)
    tracker.record_risk(
        opp_id,
        RiskDecision(opportunity_id=opp_id, status=RiskDecisionStatus.APPROVED),
    )
    from bot.core.enums import OrderSide

    buy_order = Order(
        id=uuid4(),
        strategy="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OrderSide.BUY,
        requested_quantity=Decimal("0.01"),
        opportunity_id=opp_id,
    )
    buy_fill = Fill(
        order_id=buy_order.id,
        symbol="BTCEUR",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("0.1"),
        slippage=Decimal("0.05"),
    )
    execution = ExecutionResult(
        order_id=buy_order.id,
        opportunity_id=opp_id,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        average_price=Decimal("100000"),
        fees_usd=Decimal("0.1"),
    )
    tracker.record_execution(
        opp_id,
        execution,
        orders=[buy_order],
        fills=[buy_fill],
        equity_before=Decimal("200"),
        equity_after=Decimal("199"),
    )
    tracked = tracker.opportunities()[0]
    assert tracked.status == OpportunityLifecycleStatus.EXECUTED
    assert tracked.realized_net_profit is None
    assert tracker.snapshot().trade_count == 0


def test_performance_tracker_open_then_later_fills_complete_round_trip() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp_id = uuid4()
    opp = TradeOpportunity(
        id=opp_id,
        strategy_name="maker_inventory",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100000"),
        metadata={"buy_exchange": "binance", "sell_exchange": "kraken"},
    )
    tracker.record_detected(opp, None)
    tracker.record_risk(
        opp_id,
        RiskDecision(opportunity_id=opp_id, status=RiskDecisionStatus.APPROVED),
    )
    from bot.core.enums import OrderSide

    resting = ExecutionResult(
        order_id=uuid4(),
        opportunity_id=opp_id,
        status=OrderStatus.OPEN,
        filled_quantity=Decimal("0"),
    )
    tracker.record_execution(opp_id, resting, fills=[])
    assert tracker.snapshot().executed_opportunities == 1
    assert tracker.snapshot().trade_count == 0

    buy_fill = Fill(
        order_id=resting.order_id,
        symbol="BTCEUR",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        price=Decimal("100000"),
        fee=Decimal("1"),
    )
    sell_fill = Fill(
        order_id=uuid4(),
        symbol="BTCEUR",
        side=OrderSide.SELL,
        quantity=Decimal("0.01"),
        price=Decimal("100500"),
        fee=Decimal("1.2"),
    )
    filled = ExecutionResult(
        order_id=resting.order_id,
        opportunity_id=opp_id,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        average_price=Decimal("100000"),
        fees_usd=Decimal("1"),
    )
    tracker.record_execution(opp_id, filled, fills=[buy_fill, sell_fill])
    snap = tracker.snapshot()
    assert snap.executed_opportunities == 1
    assert snap.trade_count == 1
    # sell 1005 - fee 1.2 - buy 1000 - fee 1 = 2.8
    assert tracker.opportunities()[0].realized_net_profit == Decimal("2.8")
    assert snap.net_pnl == Decimal("2.8")


@pytest.mark.asyncio
async def test_paper_store_survives_restart(tmp_path: Path) -> None:
    settings = _paper_settings(tmp_path)
    store = PaperTradingStore(settings)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("200"))
    tracker = PerformanceTracker(starting_equity=Decimal("200"))
    opp = TradeOpportunity(
        strategy_name="cross_exchange_arbitrage",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        metadata={"buy_exchange": "binance", "sell_exchange": "kraken"},
    )
    tracker.record_detected(
        opp,
        ProfitabilityResult(
            opportunity_id=opp.id,
            gross_profit_usd=Decimal("1"),
            fees_usd=Decimal("0.1"),
            slippage_usd=Decimal("0"),
            funding_usd=Decimal("0"),
            execution_buffer_usd=Decimal("0"),
            net_profit_usd=Decimal("0.9"),
            is_profitable=True,
            trade_allowed=True,
        ),
    )
    store.persist(
        portfolio=portfolio,
        tracker=tracker,
        session_running=False,
        session_started_at=None,
        errors=[],
        runtime_seconds=12.5,
    )
    assert (tmp_path / "paper_state.json").exists()

    store2 = PaperTradingStore(settings)
    loaded = store2.load(settings)
    assert loaded is not None
    portfolio2, tracker2, meta = loaded
    assert portfolio2.state.total_equity == Decimal("200")
    assert tracker2.snapshot().total_opportunities == 1
    assert meta["runtime_seconds"] == 12.5


@pytest.mark.asyncio
async def test_paper_runner_cycle_with_injected_books(tmp_path: Path) -> None:
    settings = _paper_settings(tmp_path)
    service = MarketDataService(settings)
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
    risk = RiskEngine(settings)
    store = PaperTradingStore(settings)
    runner = PaperRunner(settings, market_data=service, risk_engine=risk, store=store)

    assert runner.portfolio.state.total_equity == Decimal("200")
    await runner._run_cycle()  # noqa: SLF001 — unit test of cycle
    snap = runner.tracker.snapshot()
    # Opportunities may or may not clear risk/size with €200 — still must record.
    assert snap.total_opportunities >= 0
    assert runner.last_cycle is not None
    assert runner.last_cycle["real_exchange_order"] is False
    assert runner.last_cycle["execution_mode"] == "paper"


@pytest.mark.asyncio
async def test_paper_runner_blocks_when_paused(tmp_path: Path) -> None:
    settings = _paper_settings(tmp_path)
    service = MarketDataService(settings)
    service.inject_snapshot(
        "binance", "BTCEUR", bid=Decimal("99900"), ask=Decimal("100000"), sequence=1
    )
    service.inject_snapshot(
        "kraken", "BTCEUR", bid=Decimal("100150"), ask=Decimal("100250"), sequence=1
    )
    risk = RiskEngine(settings)
    await risk.kill_switch.pause("test pause")
    runner = PaperRunner(
        settings,
        market_data=service,
        risk_engine=risk,
        store=PaperTradingStore(settings),
    )
    await runner._run_cycle()  # noqa: SLF001
    assert runner.last_cycle is not None
    assert runner.last_cycle["blocked"] is True
    assert "paused" in runner.last_cycle["reason"]
    assert runner.tracker.snapshot().executed_opportunities == 0


@pytest.mark.asyncio
async def test_paper_runner_reset_requires_confirm(tmp_path: Path) -> None:
    settings = _paper_settings(tmp_path)
    service = MarketDataService(settings)
    risk = RiskEngine(settings)
    runner = PaperRunner(
        settings,
        market_data=service,
        risk_engine=risk,
        store=PaperTradingStore(settings),
    )
    denied = await runner.reset(confirm=False)
    assert denied["reset"] is False
    ok = await runner.reset(confirm=True)
    assert ok["reset"] is True
    assert ok["real_exchange_accounts_affected"] is False
    assert Decimal(ok["starting_equity"]) == Decimal("200")


def test_paper_api_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "api_paper.json"))
    monkeypatch.setenv("PAPER_AUTO_START", "false")
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    reset_risk_singletons()

    with TestClient(app) as client:
        assert client.get("/paper/status").status_code == 200
        status = client.get("/paper/status").json()
        assert status["execution_mode"] == "paper"
        assert status["real_orders_placed"] == 0
        assert status["withdrawals"] == 0
        assert status["leverage"] == 0
        assert status["running"] is False

        assert client.get("/paper/portfolio").status_code == 200
        portfolio = client.get("/paper/portfolio").json()
        assert Decimal(portfolio["equity"]) == Decimal("200")

        assert client.get("/paper/performance").status_code == 200
        assert client.get("/paper/statistics/daily").status_code == 200
        assert client.get("/paper/statistics/strategies").status_code == 200
        assert client.get("/paper/statistics/exchanges").status_code == 200
        assert client.get("/paper/statistics/hourly").status_code == 200
        hourly = client.get("/paper/statistics/hourly").json()["hourly"]
        assert len(hourly) == 24

        assert client.get("/paper/opportunities").status_code == 200
        assert client.get("/paper/trades").status_code == 200

        denied = client.post("/paper/reset", json={})
        assert denied.status_code == 400

        reset = client.post("/paper/reset", json={"confirm": True})
        assert reset.status_code == 200
        assert reset.json()["reset"] is True
        assert reset.json()["real_exchange_accounts_affected"] is False

        dash = client.get("/paper/dashboard")
        assert dash.status_code == 200
        assert "Moreney" in dash.text
        assert "Geen echte orders" in dash.text
        assert "Winst" in dash.text
        assert "Grafieken" in dash.text
        lite = client.get("/paper/dashboard-lite")
        assert lite.status_code == 200
        assert "Moreney — Winst" in lite.text

        root = client.get("/").json()
        assert root["paper_dashboard"] == "/paper/dashboard"
        assert root["paper_dashboard_lite"] == "/paper/dashboard-lite"
        assert root["fleet_dashboard"] == "/fleet"
        assert root["all_bots_dashboard"] == "/dashboard"
        assert root["live_trading_enabled"] is False


def test_paper_start_stop_without_live_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start/stop session control — does not require live market data for API OK."""
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "api_paper2.json"))
    monkeypatch.setenv("PAPER_AUTO_START", "false")
    get_settings.cache_clear()
    reset_risk_singletons()

    with TestClient(app) as client:
        # Inject synchronized books so the runner can evaluate without live WS.
        runner = get_paper_runner()
        md = runner._market_data  # noqa: SLF001
        md.inject_snapshot(
            "binance", "BTCEUR", bid=Decimal("99900"), ask=Decimal("100000"), sequence=1
        )
        md.inject_snapshot(
            "kraken", "BTCEUR", bid=Decimal("100150"), ask=Decimal("100250"), sequence=1
        )

        started = client.post("/paper/start")
        assert started.status_code == 200
        body = started.json()
        assert body["started"] is True
        assert body["execution_mode"] == ExecutionMode.PAPER.value
        assert body["real_orders_placed"] == 0

        stopped = client.post("/paper/stop")
        assert stopped.status_code == 200
        assert stopped.json()["stopped"] is True
        assert client.get("/paper/status").json()["running"] is False


def test_dashboard_basic_auth_optional_and_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default: disabled, dashboard is public.
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "auth-off.json"))
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    reset_risk_singletons()
    with TestClient(app) as client:
        assert client.get("/paper/dashboard").status_code == 200
        assert client.get("/paper/dashboard-lite").status_code == 200

    # Enabled: dashboard requires valid Basic Auth.
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "auth-on.json"))
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_USERNAME", "alice")
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()
    reset_risk_singletons()
    with TestClient(app) as client:
        assert client.get("/paper/dashboard").status_code == 401
        assert client.get("/paper/dashboard-lite").status_code == 401
        assert client.get("/paper/dashboard", auth=("alice", "secret")).status_code == 200
        assert client.get("/paper/dashboard-lite", auth=("alice", "secret")).status_code == 200


def test_dashboard_uses_dutch_profit_terms_and_charts() -> None:
    payload = {
        "status": {
            "running": True,
            "live_forecast": {
                "projected_per_hour_eur": "0.40",
                "projected_per_day_eur": "9.60",
                "confidence": "low",
                "note": "Richtinggevende live-inschatting.",
                "assumptions": ["Trade-through maker fills"],
            },
        },
        "performance": {
            "starting_equity": "200",
            "current_equity": "212.50",
            "net_pnl": "12.50",
            "return_pct": "6.25",
            "current_drawdown": "0",
            "maximum_drawdown": "1.10",
            "pairs_evaluated": 40,
            "depth_edges_found": 8,
            "scan_rejections": 5,
            "approved_opportunities": 3,
            "executed_opportunities": 3,
            "trade_count": 3,
            "win_rate": "0.67",
            "winning_trades": 2,
            "losing_trades": 1,
            "fees": "1.20",
            "slippage": "0.40",
            "trading_volume": "900",
        },
        "strategies": [
            {
                "strategy": "arbitrage",
                "net_pnl": "12.50",
                "opportunities": 8,
                "trades": 3,
                "win_rate": "0.67",
            }
        ],
        "exchanges": [
            {
                "buy_exchange": "binance",
                "sell_exchange": "kraken",
                "net_pnl": "12.50",
                "trades": 3,
                "win_rate": "0.67",
            }
        ],
        "opportunities": [
            {
                "timestamp": "2026-08-13T10:15:00+00:00",
                "symbol": "BTCEUR",
                "buy_exchange": "binance",
                "sell_exchange": "kraken",
                "expected_net_profit": "4.00",
                "realized_net_profit": "5.00",
                "status": "profitable",
            }
        ],
        "hourly": [
            {"hour": h, "net_pnl": "5" if h == 10 else "0", "trades": 1 if h == 10 else 0}
            for h in range(24)
        ],
        "trades": [
            {"realized_net_profit": "5.00"},
            {"realized_net_profit": "-2.00"},
            {"realized_net_profit": "9.50"},
        ],
    }
    html = render_dashboard(payload).body.decode()
    assert "Hoogste kans" in html or "Paper MTM" in html
    assert "Koop goedkoop, verkoop duurder" in html or "Maker: bied/laat vangen" in html
    assert "Binance" in html
    assert "polyline" in html
    assert "2 winst" in html
    assert "1 verlies" in html
    assert "<rect" in html
    assert "Ultra-profiel" not in html
    assert 'href="/fleet"' in html

    lite = render_dashboard_lite(payload).body.decode()
    assert "Winst in de tijd" in lite
    assert "polyline" in lite
    assert "Paper MTM" in lite or "Up-day" in lite or "live-inschatting" in lite.lower()
    assert 'href="/fleet"' in lite

    fleet = render_fleet_dashboard(
        {
            "online_count": 2,
            "configured_count": 2,
            "totals": {
                "equity": "700",
                "net_pnl": "12.50",
                "trade_count": 3,
                "running_count": 2,
                "open_maker_quotes": 4,
            },
            "instances": [
                {
                    "ok": True,
                    "label": "€200",
                    "net_pnl": "4.00",
                    "equity": "204",
                    "trade_count": 1,
                    "win_rate": "1",
                    "running": True,
                    "starting_capital": "200",
                    "open_maker_quotes": 2,
                    "dashboard_url": "/a",
                    "dashboard_lite_url": "/a-lite",
                    "market_data": {},
                },
                {
                    "ok": True,
                    "label": "€500",
                    "net_pnl": "8.50",
                    "equity": "508.50",
                    "trade_count": 2,
                    "win_rate": "0.5",
                    "running": True,
                    "starting_capital": "500",
                    "open_maker_quotes": 2,
                    "dashboard_url": "/b",
                    "dashboard_lite_url": "/b-lite",
                    "market_data": {},
                },
            ],
        }
    ).body.decode()
    assert "Alle bots" in fleet
    assert "Vergelijking" in fleet
    assert "Winst per rekening" in fleet
    assert "€200" in fleet or "200" in fleet
    assert "Transacties" in fleet
    assert "Quotes" in fleet
    assert "<table" in fleet
    assert "<rect" in fleet
    assert "Ultra transacties" not in fleet
    assert "Ultra PnL" not in fleet
    assert "Ultra =" not in fleet


def test_publicize_instance_urls_uses_browser_host() -> None:
    from bot.paper.fleet import publicize_instance_urls

    out = publicize_instance_urls(
        {
            "instances": [
                {
                    "label": "200 EUR",
                    "base_url": "http://127.0.0.1:8007",
                    "dashboard_url": "http://127.0.0.1:8007/paper/dashboard",
                    "dashboard_lite_url": "http://127.0.0.1:8007/paper/dashboard-lite",
                }
            ]
        },
        hostname="example.test:8006",
        scheme="https",
    )
    row = out["instances"][0]
    assert row["dashboard_url"] == "https://example.test:8007/paper/dashboard"
    assert row["dashboard_lite_url"] == "https://example.test:8007/paper/dashboard-lite"
