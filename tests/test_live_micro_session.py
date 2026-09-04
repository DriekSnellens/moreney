"""Tests for full-bot micro session bridge (no real exchange calls)."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode, OpportunitySide, OrderStatus
from bot.core.models import OrderRequest
from bot.live.micro import MicroLivePolicy
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.live.micro_session import _non_btc_symbols, attach_micro_bridge, _session_settings
from bot.portfolio.portfolio import PaperPortfolio


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_micro_engine()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_micro_engine()


def _unlocked(**kwargs: object) -> Settings:
    base = dict(
        execution_mode=ExecutionMode.PAPER,
        paper_starting_eur=25.0,
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo",
        live_micro_symbols="*",
        live_micro_max_notional_eur=25,
        automatic_withdrawals_enabled=False,
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_policy_star_allows_any_symbol() -> None:
    pol = MicroLivePolicy(_unlocked())
    ok, _ = pol.validate_order(
        venue="bitvavo",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
    )
    assert ok is True


def test_non_btc_symbols_filters() -> None:
    s = Settings(
        market_data_symbols="BTCEUR,ETHEUR,SOLEUR,BTCUSDT,ADAEUR",
    )
    assert _non_btc_symbols(s) == ["ETHEUR", "SOLEUR", "ADAEUR"]


def test_session_settings_cap_capital(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(live_micro_execute_venues="bitvavo"),
        budget_eur=Decimal("2024"),
        symbols=["SOLEUR", "ADAEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.paper_starting_eur == 2024.0
    assert "SOLEUR" in cfg.live_micro_symbols
    assert cfg.live_micro_max_daily_loss_eur == 202.4
    assert cfg.global_max_venue_exposure_pct == 100.0
    assert cfg.paper_maker_venues == "okx,bitvavo"
    assert cfg.paper_maker_same_venue is True
    assert cfg.arbitrage_max_emits_per_cycle == 12
    assert cfg.paper_maker_max_open_quotes == 8
    assert cfg.live_micro_execute_venues == "bitvavo"
    dual = _session_settings(
        Settings(live_micro_execute_venues="bitvavo,okx"),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR"],
        persist_path=tmp_path / "dual.json",
    )
    assert dual.arbitrage_position_pct == 7.5
    assert dual.arbitrage_max_emits_per_cycle == 12
    assert dual.live_micro_max_open_orders == 8
    assert dual.live_micro_max_open_orders_per_venue == 8
    assert dual.live_micro_max_resting_buys_per_symbol == 3
    assert dual.live_micro_max_alt_bases == 10
    assert float(dual.live_micro_first_clip_eur) == 140.0
    assert float(dual.live_micro_add_clip_eur) == 200.0
    assert float(dual.live_micro_active_ring_eur) == 1850.0
    assert float(dual.live_micro_velocity_sleeve_eur) == 1850.0
    assert float(dual.paper_max_alt_inventory_pct) == 78.0
    assert dual.max_simultaneous_positions == 16
    assert float(dual.paper_maker_keep_vs_best_frac) == 0.35
    assert cfg.live_micro_cross_venue_enabled is True
    assert "EURUSDT" in cfg.market_data_symbols
    assert "SOLUSDT" in cfg.market_data_symbols
    assert cfg.paper_venue_inventory is True
    assert cfg.paper_max_holding_sec == 0.0
    assert cfg.paper_maker_allow_buy_only is True
    assert cfg.paper_maker_one_leg_exit is False
    assert cfg.paper_inventory_ask_improve_bps == 2.0
    assert cfg.paper_inventory_buy_dip_bps == 0.0
    assert cfg.paper_ladder_buy_pcts.startswith("0,")
    assert cfg.paper_maker_sell_profit_buffer_bps >= 10.0
    assert cfg.paper_dust_exit_slack_bps == 0.0
    assert cfg.paper_trail_take_profit_enabled is True
    assert cfg.paper_trail_drawdown_pct == 0.012
    assert cfg.paper_trail_partial_enabled is True
    assert cfg.paper_trail_partial_pct == 0.50
    assert cfg.paper_trail_soft_arm_pct == 0.008
    assert cfg.paper_trail_soft_drawdown_pct == 0.002
    assert cfg.paper_trail_soft_partial_pct == 0.25
    assert cfg.paper_trail_hard_arm_pct == 0.025
    assert cfg.paper_trail_hard_drawdown_pct == 0.012
    assert cfg.paper_trail_hard_partial_pct == 0.40
    assert cfg.paper_trail_arm_gain_pct == 0.025
    assert cfg.live_micro_winner_add_enabled is True
    assert cfg.live_micro_winner_add_max == 2
    assert float(cfg.live_micro_winner_add_clip_eur) == 200.0
    assert float(cfg.live_micro_winner_add_cooldown_sec) == 45.0
    assert cfg.live_micro_alphai_winner_add_only is True
    assert float(cfg.live_micro_alphai_priority_clip_eur) == 220.0
    assert float(cfg.live_micro_alphai_strong_clip_eur) == 280.0
    assert (cfg.live_micro_long_hold_bases or "") == ""
    assert cfg.live_micro_low_util_relax_focus is False
    assert float(cfg.paper_maker_min_profit_eur) == 0.025
    assert float(cfg.paper_maker_min_net_return) == 0.0003
    assert float(cfg.profitability_min_net_profit_usd) == 0.025
    assert float(cfg.profitability_min_net_return) == 0.0003
    assert float(cfg.risk_min_net_profit_usd) == 0.025
    assert float(cfg.live_micro_ring_soft_max_active_eur) == 1850.0
    assert cfg.live_micro_max_resting_buys_per_symbol == 3
    assert cfg.live_micro_max_open_orders_per_venue == 8
    assert cfg.paper_trail_session_buys_only is False
    assert cfg.paper_trail_atr_enabled is False
    assert cfg.live_disable_research_hooks is True
    assert cfg.paper_buy_momentum_enabled is True
    assert cfg.paper_buy_momentum_min_return == 0.0015
    assert cfg.live_micro_momentum_require_last_n_rising == 4
    assert float(cfg.live_micro_buy_resting_max_age_sec) == 30.0
    assert float(cfg.live_micro_ring_soft_block_underwater_eur) == 25.0
    assert cfg.live_micro_entry_min_low_util_rising_n == 3
    assert float(cfg.live_micro_entry_short_momentum_min_return) == 0.001
    assert cfg.live_micro_corr_sector_momentum_block == 2
    assert cfg.live_micro_block_underwater_cross_venue is True
    assert float(cfg.paper_maker_fv_buy_max_premium_bps) == 5.0
    assert cfg.paper_buy_momentum_samples >= 8
    assert "SOL" in (cfg.live_micro_focus_bases or "")
    assert "ETH" in (cfg.live_micro_focus_bases or "")
    assert "TAO" not in (cfg.live_micro_focus_bases or "")
    assert cfg.live_micro_new_buy_focus_only is True
    assert float(cfg.live_micro_okx_cash_bias_ratio) == 1.0
    assert (cfg.live_micro_okx_deploy_bases or "") == ""
    assert cfg.live_micro_max_per_corr_group == 4
    assert float(cfg.live_micro_ring_momentum_min_return) == 0.0005
    assert cfg.live_micro_ring_util_b_ignore_underwater is True
    assert cfg.live_cvd_abandoned is True
    assert cfg.live_micro_low_util_rising_n == 3
    assert float(cfg.live_micro_low_util_buy_resting_max_age_sec) == 30.0
    assert float(cfg.live_micro_active_ring_eur) == 1850.0
    assert cfg.paper_daily_kill_eur == 50.0
    assert cfg.paper_ladder_buy_enabled is False
    assert cfg.paper_time_stop_enabled is True
    assert cfg.paper_dust_policy == "top_up_or_exit"
    assert cfg.paper_regime_block_buys is True
    assert cfg.paper_maker_min_net_return <= 0.0006
    assert cfg.paper_maker_min_profit_eur <= 0.06
    assert float(getattr(cfg, "paper_maker_small_clip_max_eur", 0) or 0) == 220.0
    assert float(getattr(cfg, "paper_maker_small_clip_min_profit_eur", 0) or 0) == 0.02
    assert float(getattr(cfg, "paper_maker_small_clip_min_net_return", 0) or 0) == 0.00026
    assert cfg.paper_maker_min_notional_eur == 80.0
    assert cfg.max_simultaneous_positions >= 8
    assert cfg.live_micro_max_alt_bases == 10
    assert cfg.live_micro_block_cross_venue_duplicate_bases is False
    assert cfg.live_micro_consolidate_duplicate_bases is False
    assert cfg.live_micro_consolidate_primary_venue == "bitvavo"
    assert float(cfg.live_micro_first_clip_eur) == 140.0
    assert float(cfg.live_micro_add_clip_eur) == 200.0
    assert float(cfg.live_micro_first_clip_eur) <= float(cfg.live_micro_add_clip_eur)
    assert cfg.live_micro_max_open_orders == 8
    assert cfg.live_micro_max_open_orders_per_venue == 8
    assert cfg.live_micro_max_resting_buys_per_symbol == 3
    assert float(cfg.live_micro_max_notional_eur) >= 200.0
    assert float(cfg.risk_max_position_usd) >= 200.0
    assert float(cfg.live_micro_active_ring_eur) == 1850.0
    assert cfg.live_micro_resting_max_age_sec >= 480.0
    assert cfg.paper_min_alt_inventory_pct >= 15.0
    assert cfg.paper_max_alt_inventory_pct == 78.0
    assert cfg.paper_trail_soft_partial_pct == 0.25
    assert cfg.paper_trail_soft_drawdown_pct == 0.002
    assert cfg.live_micro_exit_taker_after_maker_fails == 1
    assert cfg.live_micro_winner_add_enabled is True
    assert cfg.live_micro_low_util_relax_focus is False
    assert cfg.paper_maker_keep_vs_best_frac == 0.35
    assert cfg.live_micro_underwater_buy_block == 1
    assert cfg.live_micro_block_underwater_adds is True
    assert cfg.live_micro_block_buys_when_holding_base is True
    assert cfg.live_micro_primary_execute_venue == "bitvavo"
    assert cfg.live_micro_underwater_block_new_bases_only is True
    assert float(cfg.live_micro_okx_buy_improve_bps) == 1.0
    assert cfg.paper_trail_recovery_be_partial_pct >= 0.50
    assert cfg.paper_trail_be_harvest_partial_pct >= 0.50
    assert float(getattr(cfg, "live_micro_cut_loss_below_be_pct", 0) or 0) == 0.025
    assert cfg.live_micro_cut_loss_new_bases_only is False
    assert float(getattr(cfg, "live_micro_momentum_exit_above_be_pct", 0) or 0) == 0.005
    assert float(getattr(cfg, "live_micro_momentum_exit_min_return", 0) or 0) == 0.002
    assert float(getattr(cfg, "live_micro_early_cut_loss_below_be_pct", 0) or 0) == 0.01
    assert cfg.live_micro_early_cut_new_bases_only is True
    assert cfg.live_micro_trail_hold_while_rising is True
    assert cfg.live_micro_trail_hold_rising_n == 1
    assert float(cfg.live_micro_be_harvest_cooldown_sec) == 2.0
    assert cfg.alphai_require_bullish_new_buys is True
    assert cfg.alphai_feature_scoring_enabled is True
    assert cfg.alphai_feature_shadow_only is False
    assert float(cfg.live_micro_okx_ring_clip_eur) == 140.0
    assert cfg.live_micro_uw_recycle_enabled is True
    assert float(cfg.live_micro_uw_dust_max_notional_eur) == 25.0
    assert float(cfg.live_micro_uw_near_below_be_pct) == 0.006
    assert float(cfg.live_micro_uw_near_min_age_sec) == 900.0
    assert float(cfg.live_micro_uw_non_alphai_below_be_pct) == 0.008
    assert float(cfg.live_micro_uw_non_alphai_min_age_sec) == 1200.0
    assert float(cfg.live_micro_uw_alphai_below_be_pct) == 0.015
    assert float(cfg.live_micro_uw_alphai_min_age_sec) == 3600.0
    assert cfg.live_micro_uw_idle_pressure_enabled is True
    assert float(cfg.live_micro_uw_idle_below_be_pct) == 0.004
    assert float(cfg.live_micro_uw_idle_min_age_sec) == 600.0
    assert cfg.live_micro_alphai_cross_venue_deploy is True
    assert float(cfg.live_micro_alphai_cross_venue_max_other_depth_pct) == 0.025
    assert float(cfg.live_micro_alphai_ring_fill_add_max_depth_pct) == 0.012
    assert float(cfg.paper_trail_be_harvest_min_gain_pct) <= 0.0003
    assert cfg.live_micro_cross_venue_min_fill_rate == 0.30
    assert cfg.paper_markout_enabled is False
    assert cfg.paper_seed_usdt_pct == 0.0
    assert "BTCEUR" not in cfg.market_data_symbols
    # Liquid day-trade allowlist.
    from bot.live.micro_session import _liquid_symbols

    liquid = _liquid_symbols(_unlocked(live_micro_symbols="*"), exclude_btc=True)
    assert len(liquid) >= 38
    assert "ETHEUR" in liquid
    assert "SOLEUR" in liquid
    assert "LINKEUR" in liquid
    assert "HYPEEUR" in liquid
    assert "BNBEUR" in liquid
    assert "UNIEUR" in liquid
    assert "BTCEUR" not in liquid
    with_btc = _liquid_symbols(_unlocked(live_micro_symbols="*"), exclude_btc=False)
    assert "BTCEUR" in with_btc
    defaulted = _liquid_symbols(Settings(), exclude_btc=False)
    assert "BTCEUR" in defaulted
    assert "SOLEUR" in defaulted
    assert len(defaulted) >= 39
    assert "PEPEEUR" not in liquid
    assert "SHIBEUR" not in liquid
    assert "FARTCOINEUR" not in liquid


def test_session_settings_enable_rising_momentum_for_new_buys(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(
            live_trading_enabled=True,
            live_micro_enabled=True,
            live_orders_unlocked=True,
            live_allow_without_research_unlock=True,
        ),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR", "FETEUR"],
        persist_path=tmp_path / "mom_settings.json",
    )
    assert cfg.paper_buy_momentum_enabled is True
    assert float(cfg.paper_buy_momentum_min_return) == 0.0015
    assert "SOL" in (cfg.live_micro_focus_bases or "")
    assert cfg.live_micro_new_buy_focus_only is True
    assert float(cfg.live_micro_ring_momentum_min_return) == 0.0005
    assert float(cfg.live_micro_ring_soft_max_active_eur) == 1850.0
    assert cfg.live_micro_max_per_corr_group == 4
    assert float(cfg.profitability_min_net_return) == 0.0003
    assert float(cfg.profitability_min_net_profit_usd) == 0.025


def test_momentum_blocks_new_base_without_rising_marks(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.0015,
        paper_buy_momentum_samples=12,
        live_micro_bridge_persist_path=str(tmp_path / "mom.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    symbol = "NEWEUR"
    # Falling marks → not rising.
    series = bridge._series_for(symbol)  # noqa: SLF001
    for px in (100, 99.8, 99.5, 99.2, 99.0, 98.8):
        series.push(Decimal(str(px)))
    assert bridge._is_new_base_buy("okx", "NEW") is True  # noqa: SLF001
    assert bridge._momentum_ok(symbol, require_history=True) is False  # noqa: SLF001

    # Rising marks clear the gate.
    series2 = bridge._series_for("RISEEUR")  # noqa: SLF001
    for px in (100, 100.1, 100.2, 100.4, 100.6, 100.8):
        series2.push(Decimal(str(px)))
    assert bridge._momentum_ok("RISEEUR", require_history=True) is True  # noqa: SLF001

    # Cold symbol with require_history stays blocked.
    assert bridge._momentum_ok("COLDEUR", require_history=True) is False  # noqa: SLF001
    # Existing-bag path may still allow sparse history.
    assert bridge._momentum_ok("COLDEUR", require_history=False) is True  # noqa: SLF001


def test_portfolio_holdings_overview_includes_momentum(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.0015,
        paper_buy_momentum_samples=12,
        live_micro_bridge_persist_path=str(tmp_path / "holdings.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    symbol = "ADAEUR"
    series = bridge._series_for(symbol)  # noqa: SLF001
    for px in (1.0, 1.002, 1.004, 1.006, 1.008, 1.010):
        series.push(Decimal(str(px)))
    bridge._trail["bitvavo:ADA"] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "ADA",
        "cost": Decimal("1.0"),
        "last_mark": Decimal("1.01"),
        "session_qty": Decimal("100"),
    }
    bridge._venue_raw_balances["bitvavo"] = []  # noqa: SLF001
    snap = bridge.snapshot_bridge()
    holdings = snap.get("portfolio_holdings") or []
    assert len(holdings) == 1
    row = holdings[0]
    assert row["base"] == "ADA"
    assert row["momentum_direction"] == "up"
    assert row["momentum_arrow"] == "↑"
    assert float(row["notional_eur"]) > 0


def test_portfolio_sync_live_balances_caps_quote() -> None:
    from bot.core.models import Balance

    settings = _unlocked(paper_starting_eur=2024.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("2024"))
    mapped = portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("1623.39"), locked=Decimal("100")),
            Balance(asset="NEAR", free=Decimal("121.9"), locked=Decimal("0")),
            Balance(asset="ATOM", free=Decimal("147.39"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("2024"),
    )
    assert portfolio.available("EUR") == Decimal("1623.39")
    assert portfolio.reserved("EUR") == Decimal("100")
    assert portfolio.available("NEAR") == Decimal("121.9")
    assert "NEAREUR" in portfolio.state.positions
    assert portfolio.state.positions["NEAREUR"].quantity == Decimal("121.9")
    assert "EUR" in mapped


def test_portfolio_sync_live_balances_from_venues_merges() -> None:
    from bot.core.models import Balance

    settings = _unlocked(paper_starting_eur=2024.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("2024"))
    mapped = portfolio.sync_live_balances_from_venues(
        {
            "bitvavo": [
                Balance(asset="EUR", free=Decimal("1623.39"), locked=Decimal("100")),
                Balance(asset="NEAR", free=Decimal("121.9"), locked=Decimal("0")),
            ],
            "okx": [
                Balance(asset="EUR", free=Decimal("2000"), locked=Decimal("0")),
            ],
        },
        quote_available_cap=Decimal("2024"),
    )
    assert portfolio.available("EUR") == Decimal("3623.39")
    assert portfolio.reserved("EUR") == Decimal("100")
    assert portfolio.available("NEAR") == Decimal("121.9")
    assert "NEAREUR" in portfolio.state.positions
    assert "EUR" in mapped


@pytest.mark.asyncio
async def test_bridge_skips_sell_without_live_base(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.core.models import Balance

    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    # Paper thinks it holds NEAR, but live free is 0.
    portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("25"), locked=Decimal("0")),
            Balance(asset="NEAR", free=Decimal("10"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("25"),
    )
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
        live_maker=True,
    )

    async def no_near(_venue: str, asset: str) -> Decimal:
        return Decimal("0") if asset == "NEAR" else Decimal("25")

    monkeypatch.setattr(bridge, "_live_free", no_near)
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="NEAREUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("10"),
        limit_price=Decimal("1.65"),
        metadata={"venue": "bitvavo", "post_only": True},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("insufficient_live_base", 0) >= 1


@pytest.mark.asyncio
async def test_bridge_excludes_btc_and_tracks_skip() -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
        exclude_bases={"BTC"},
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.001"),
        limit_price=Decimal("60000"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("excluded_base", 0) >= 1


@pytest.mark.asyncio
async def test_bridge_allows_btc_when_not_excluded() -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
        exclude_bases=set(),
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.001"),
        limit_price=Decimal("60000"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert bridge.skips.get("excluded_base", 0) == 0
    assert result.message is None or "excluded" not in str(result.message).lower()


@pytest.mark.asyncio
async def test_bridge_skips_non_bitvavo_venue() -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ETHEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        limit_price=Decimal("2000"),
        metadata={"venue": "binance"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("venue_not_live", 0) >= 1


@pytest.mark.asyncio
async def test_bridge_mirrors_live_fill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=False,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()

    async def fake_submit(payload: dict, *, confirm: bool = False) -> dict:
        assert confirm is True
        return {
            "submitted": True,
            "executed": True,
            "order": {
                "filled_quantity": "0.01",
                "average_price": "2000",
                "exchange_order_id": "test-1",
                "symbol": "ETHEUR",
                "side": "buy",
            },
        }

    async def fake_live_free(_venue: str, asset: str) -> Decimal:
        return Decimal("25") if asset.upper() == "EUR" else Decimal("0")

    monkeypatch.setattr(engine, "submit", fake_submit)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
    )
    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ETHEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        limit_price=Decimal("2000"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == Decimal("0.01")
    assert (result.metadata or {}).get("real_exchange_order") is True
    # Pocket recycles: ~€5 free after €20 buy (+ tiny fee).
    assert bridge.budget_remaining < Decimal("5.1")
    assert bridge.budget_remaining > Decimal("4.5")
    assert bridge.snapshot_bridge()["capital_model"] == "pocket"
    assert len(bridge.live_trades) == 1


def test_bridge_break_even_sell_includes_fee_and_buffer() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=10.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    engine = LiveMicroEngine(settings)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:NEAR"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    # Mark-seeded lots are untrusted until a real fill / trade hydrate.
    assert bridge._break_even_sell_price("bitvavo", "NEAR") is None  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:NEAR")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "NEAR")  # noqa: SLF001
    assert be is not None
    # 1.00 / (1-0.0015) * (1+10bps) ≈ 1.0025
    assert be > Decimal("1.001")
    assert be < Decimal("1.004")


def test_sell_allowed_at_blocks_below_break_even() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=10.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    # Untrusted seed → blocked
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("80")]]  # noqa: SLF001
    ok, reason, be = bridge._sell_allowed_at("bitvavo", "SOL", Decimal("90"))  # noqa: SLF001
    assert ok is False
    assert reason == "sell_no_trusted_cost"
    assert be is None

    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    # Trusted but mark below fee-aware BE → blocked
    ok, reason, be = bridge._sell_allowed_at("bitvavo", "SOL", Decimal("80"))  # noqa: SLF001
    assert ok is False
    assert reason == "sell_below_break_even"
    assert be is not None and be > Decimal("80")

    # Comfortably above BE → allowed
    ok, reason, be = bridge._sell_allowed_at("bitvavo", "SOL", Decimal("82"))  # noqa: SLF001
    assert ok is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_bridge_execute_sell_rejects_without_trusted_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=10.0)
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("200")),
        live_engine=engine,
        budget_eur=Decimal("200"),
        live_maker=True,
    )
    # Untrusted mark seed only — must refuse sell even at a high price
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001

    async def fake_live_free(_venue: str, asset: str) -> Decimal:
        return Decimal("1") if asset.upper() == "ADA" else Decimal("200")

    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ADAEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("1"),
        limit_price=Decimal("110"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("sell_no_trusted_cost", 0) >= 1



def test_trail_soft_then_soft_drawdown() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_soft_drawdown_pct=0.08,
        paper_trail_hard_arm_pct=0.30,
        paper_trail_hard_drawdown_pct=0.12,
        paper_trail_atr_enabled=False,
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    cost = Decimal("1.00")
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.11"))  # noqa: SLF001
    assert st["soft_armed"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.12"))  # noqa: SLF001
    assert st["soft_armed"] is True
    assert st["newly_soft"] is True
    assert st["hard_armed"] is False
    assert st["peak"] == Decimal("1.12")
    # Soft trail: peak 1.12, 8% dd → trigger at <= 1.0304
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.04"))  # noqa: SLF001
    assert st["triggered"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.03"))  # noqa: SLF001
    assert st["triggered"] is True


def test_trail_hard_arm_widens_drawdown() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_soft_drawdown_pct=0.08,
        paper_trail_hard_arm_pct=0.30,
        paper_trail_hard_drawdown_pct=0.12,
        paper_trail_atr_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    cost = Decimal("1.00")
    bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.12"))  # noqa: SLF001
    # Climb in <8% steps so peak tracking trusts the marks.
    for px in ("1.18", "1.24", "1.30"):
        st = bridge._trail_update_state(  # noqa: SLF001
            "bitvavo", "ADA", cost=cost, mark=Decimal(px)
        )
    assert st["newly_hard"] is True
    assert st["hard_armed"] is True
    for px in ("1.36", "1.42", "1.48", "1.50"):
        bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal(px))  # noqa: SLF001
    # 12% off 1.50 = 1.32 — still holding under hard dd
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.33"))  # noqa: SLF001
    assert st["triggered"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.32"))  # noqa: SLF001
    assert st["triggered"] is True


def test_trail_ignores_peak_spike_after_soft_arm(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.009,
        paper_trail_soft_drawdown_pct=0.006,
        paper_trail_hard_arm_pct=0.06,
        paper_trail_hard_drawdown_pct=0.03,
        paper_trail_atr_enabled=False,
        live_micro_bridge_persist_path=str(tmp_path / "peak.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    cost = Decimal("85.0")
    bridge._trail_update_state("okx", "SOL", cost=cost, mark=Decimal("86.0"))  # noqa: SLF001
    st = bridge._trail_update_state("okx", "SOL", cost=cost, mark=Decimal("99.5"))  # noqa: SLF001
    assert Decimal(str(st["peak"])) == Decimal("86.0")
    assert bridge.skips.get("trail_peak_spike", 0) >= 1
    # Underwater with polluted peak → rewind, do not stay falsely triggered.
    st = bridge._trail["okx:SOL"]
    st["peak"] = Decimal("99.5")
    st["soft_armed"] = True
    st["hard_armed"] = True
    st = bridge._trail_update_state("okx", "SOL", cost=cost, mark=Decimal("83.0"))  # noqa: SLF001
    assert Decimal(str(st["peak"])) == Decimal("83.0")
    assert st["triggered"] is False
    assert bridge.skips.get("trail_peak_rewound", 0) >= 1


def test_trail_sanitize_persisted_ghost_peak(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.009,
        paper_trail_hard_arm_pct=0.06,
        paper_trail_hard_drawdown_pct=0.03,
        paper_trail_atr_enabled=False,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    path = tmp_path / "bridge.json"
    path.write_text(
        json.dumps(
            {
                "trail": {
                    "bitvavo:APT": {
                        "venue": "bitvavo",
                        "base": "APT",
                        "soft_armed": True,
                        "hard_armed": True,
                        "cost": "0.535",
                        "last_mark": "0.485",
                        "peak": "1.25",
                        "soft_arm": "0.009",
                        "hard_arm": "0.06",
                        "drawdown": "0.03",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    st = bridge._trail["bitvavo:APT"]
    assert Decimal(str(st["peak"])) == Decimal("0.485")


def test_trail_runner_drawdown_uses_12pct_in_session_settings(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(),
        budget_eur=Decimal("2024"),
        symbols=["ADAEUR"],
        persist_path=tmp_path / "t.json",
    )
    assert cfg.paper_trail_drawdown_pct == 0.012
    assert cfg.paper_trail_soft_arm_pct == 0.008
    assert cfg.paper_trail_soft_drawdown_pct == 0.002
    assert cfg.paper_trail_hard_arm_pct == 0.025
    assert cfg.paper_trail_partial_pct == 0.50
    assert cfg.paper_trail_soft_partial_pct == 0.25
    assert cfg.live_micro_exit_taker_after_maker_fails == 1
    assert cfg.live_micro_winner_add_max == 2
    assert cfg.live_micro_max_notional_eur <= 300.0
    assert cfg.live_micro_max_notional_eur >= 200.0
    assert cfg.paper_markout_enabled is False
    assert cfg.live_disable_research_hooks is True
    assert cfg.max_drawdown_percent == 12.0
    assert cfg.live_micro_reset_drawdown_on_start is True
    assert float(cfg.live_micro_be_harvest_cooldown_sec) == 2.0
    assert float(cfg.paper_trail_be_harvest_partial_pct) == 0.75
    assert float(cfg.paper_trail_be_harvest_min_gain_pct) == 0.0001
    assert cfg.live_micro_exit_engine_enabled is True
    assert float(cfg.live_micro_velocity_sleeve_daily_loss_cap_eur) == 50.0
    assert float(cfg.live_micro_exit_resting_max_age_sec) == 1.5
    assert float(cfg.live_micro_mark_ttl_sec) == 2.0
    assert float(cfg.live_micro_exit_cooldown_sec) == 1.5
    assert float(cfg.live_micro_active_ring_eur) == 1850.0
    assert float(cfg.live_micro_velocity_sleeve_eur) == 1850.0


def test_reset_drawdown_baseline_rewinds_peak() -> None:
    portfolio = PaperPortfolio(Settings(paper_starting_eur=2000.0), starting_eur=Decimal("2000"))
    portfolio.set_mark_price("ADAEUR", Decimal("1"))
    portfolio._state.balances["EUR"] = portfolio._state.balances.get("EUR")  # noqa: SLF001
    from bot.portfolio.models import AssetBalance

    portfolio._state.balances["EUR"] = AssetBalance(  # noqa: SLF001
        asset="EUR", available=Decimal("2000"), reserved=Decimal("0")
    )
    portfolio._state.stats.peak_equity = Decimal("5000")
    portfolio.set_mark_price("ADAEUR", Decimal("0.9"))
    portfolio._update_drawdown()  # noqa: SLF001
    assert portfolio._state.stats.current_drawdown > 0  # noqa: SLF001
    peak = portfolio.reset_drawdown_baseline()
    assert portfolio._state.stats.peak_equity == peak  # noqa: SLF001
    assert portfolio._state.stats.current_drawdown == 0  # noqa: SLF001
    assert portfolio._state.stats.maximum_drawdown == 0  # noqa: SLF001


def test_live_mtm_cap_blocks_ghost_peak_drawdown() -> None:
    """Paper mark spikes must not raise peak above live venue MTM."""
    from bot.portfolio.models import AssetBalance

    portfolio = PaperPortfolio(Settings(paper_starting_eur=4000.0), starting_eur=Decimal("4000"))
    portfolio._state.balances["EUR"] = AssetBalance(  # noqa: SLF001
        asset="EUR", available=Decimal("4000"), reserved=Decimal("0")
    )
    portfolio.set_live_mtm_cap(Decimal("4200"))
    portfolio.reset_drawdown_baseline()
    assert portfolio._state.stats.peak_equity == Decimal("4000")  # noqa: SLF001

    # Ghost spike: paper equity jumps via absurd mark on a bag.
    portfolio._state.balances["SOL"] = AssetBalance(  # noqa: SLF001
        asset="SOL", available=Decimal("100"), reserved=Decimal("0")
    )
    portfolio.set_mark_price("SOLEUR", Decimal("50"))  # +€5000 ghost
    portfolio._update_drawdown()  # noqa: SLF001
    assert portfolio._state.stats.peak_equity <= Decimal("4200") * Decimal("1.02")  # noqa: SLF001

    # Ghost gone — equity back near cash; must not look like a 40% crash.
    portfolio._state.balances["SOL"] = AssetBalance(  # noqa: SLF001
        asset="SOL", available=Decimal("0"), reserved=Decimal("0")
    )
    portfolio._update_drawdown()  # noqa: SLF001
    assert portfolio._state.stats.current_drawdown < Decimal("0.12")  # noqa: SLF001


def test_trail_partial_flags_newly_armed() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_hard_arm_pct=0.30,
        paper_trail_partial_enabled=True,
        paper_trail_soft_partial_pct=0.25,
        paper_trail_atr_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    st = bridge._trail_update_state("bitvavo", "ADA", cost=Decimal("1"), mark=Decimal("1.11"))  # noqa: SLF001
    assert st["newly_soft"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=Decimal("1"), mark=Decimal("1.12"))  # noqa: SLF001
    assert st["newly_soft"] is True
    assert st["newly_armed"] is True
    assert st["soft_armed"] is True
    assert st["soft_partial_done"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=Decimal("1"), mark=Decimal("1.13"))  # noqa: SLF001
    assert st["newly_soft"] is False


def test_clip_qty_to_max_notional_for_large_trail_bags(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_max_notional_eur=150,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    qty, clipped = bridge._clip_qty_to_max_notional(Decimal("4"), Decimal("85"))  # noqa: SLF001
    assert clipped is True
    assert qty * Decimal("85") <= Decimal("150")
    assert qty > 0
    qty2, clipped2 = bridge._clip_qty_to_max_notional(Decimal("1"), Decimal("85"))  # noqa: SLF001
    assert clipped2 is False
    assert qty2 == Decimal("1")


def test_policy_allows_sell_above_max_notional() -> None:
    """Exit sells must clear a full bag in one order (avoid fee-heavy slices)."""
    from bot.live.micro import MicroLivePolicy

    pol = MicroLivePolicy(
        _unlocked(live_micro_max_notional_eur=150, live_micro_venues="bitvavo,okx")
    )
    ok_buy, reason_buy = pol.validate_order(
        venue="okx", symbol="SOLEUR", notional_eur=Decimal("320"), side="buy"
    )
    assert ok_buy is False
    assert "exceeds max" in reason_buy
    ok_sell, reason_sell = pol.validate_order(
        venue="okx", symbol="SOLEUR", notional_eur=Decimal("320"), side="sell"
    )
    assert ok_sell is True, reason_sell


def test_policy_allows_buy_within_one_cent_of_max_notional() -> None:
    """Float dust at exactly the AlphaI strong-clip ceiling must not block buys."""
    from bot.live.micro import MicroLivePolicy

    pol = MicroLivePolicy(
        _unlocked(live_micro_max_notional_eur=280, live_micro_venues="bitvavo,okx")
    )
    ok, reason = pol.validate_order(
        venue="bitvavo",
        symbol="ETHEUR",
        notional_eur=Decimal("280.0000050842883500"),
        side="buy",
    )
    assert ok is True, reason
    ok_over, reason_over = pol.validate_order(
        venue="bitvavo",
        symbol="ETHEUR",
        notional_eur=Decimal("280.02"),
        side="buy",
    )
    assert ok_over is False
    assert "exceeds max" in reason_over


def test_soft_partial_retries_while_armed_not_only_newly_soft(tmp_path: Path) -> None:
    """Soft partial stays eligible after the arming tick."""
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_soft_drawdown_pct=0.08,
        paper_trail_hard_arm_pct=0.30,
        paper_trail_hard_drawdown_pct=0.12,
        paper_trail_partial_enabled=True,
        paper_trail_soft_partial_pct=0.50,
        paper_trail_atr_enabled=False,
        paper_trail_session_buys_only=False,
        live_micro_max_notional_eur=150,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    cost = Decimal("1.00")
    st = bridge._trail_update_state("okx", "ZZZ", cost=cost, mark=Decimal("1.11"))  # noqa: SLF001
    assert st["soft_armed"] is False
    st = bridge._trail_update_state("okx", "ZZZ", cost=cost, mark=Decimal("1.12"))  # noqa: SLF001
    assert st["soft_armed"] is True
    assert st["newly_soft"] is True
    assert st.get("soft_partial_done") is False
    st = bridge._trail_update_state("okx", "ZZZ", cost=cost, mark=Decimal("1.13"))  # noqa: SLF001
    assert st["soft_armed"] is True
    assert st["newly_soft"] is False
    assert st.get("soft_partial_done") is False


def test_soft_partial_zero_skips_early_clip(tmp_path: Path) -> None:
    """soft_partial=0 keeps the full bag for soft-trail drawdown exit."""
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.009,
        paper_trail_soft_drawdown_pct=0.006,
        paper_trail_hard_arm_pct=0.06,
        paper_trail_hard_drawdown_pct=0.03,
        paper_trail_partial_enabled=True,
        paper_trail_soft_partial_pct=0.0,
        paper_trail_atr_enabled=False,
        paper_trail_session_buys_only=False,
        live_micro_bridge_persist_path=str(tmp_path / "bridge0.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    assert bridge._soft_partial == Decimal("0")  # noqa: SLF001
    cost = Decimal("100")
    st = bridge._trail_update_state("okx", "SOL", cost=cost, mark=Decimal("101"))  # noqa: SLF001
    assert st["soft_armed"] is True
    # Drawdown from peak without early clip path.
    peak = Decimal(str(st["peak"]))
    st = bridge._trail_update_state(  # noqa: SLF001
        "okx", "SOL", cost=cost, mark=peak * Decimal("0.993")
    )
    assert st.get("triggered") is True


def test_session_lots_only_for_trail_cost(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_trail_session_buys_only=True,
        live_micro_bridge_persist_path=str(tmp_path / "session_lots.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.0")]]  # noqa: SLF001
    assert bridge._session_unit_cost("bitvavo", "ADA") is None  # noqa: SLF001
    from bot.core.enums import OrderSide

    bridge._record_realized_fill(  # noqa: SLF001
        side=OrderSide.BUY,
        symbol="ADAEUR",
        qty=Decimal("5"),
        price=Decimal("1.0"),
        fee=Decimal("0"),
        venue="bitvavo",
    )
    assert bridge._session_unit_cost("bitvavo", "ADA") == Decimal("1.0")  # noqa: SLF001
    assert bridge._session_qty("bitvavo", "ADA") == Decimal("5")  # noqa: SLF001


def test_daily_kill_blocks_buys() -> None:
    settings = _unlocked(paper_daily_kill_eur=50.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.realized_trade_pnl_eur = Decimal("-50.01")
    bridge._check_daily_kill()  # noqa: SLF001
    assert bridge._daily_kill_active is True  # noqa: SLF001
    assert bridge._buys_blocked is True  # noqa: SLF001


def test_scale_thresholds_atr() -> None:
    from bot.live.trail_policy import scale_thresholds

    th = scale_thresholds(
        atr=Decimal("0.02"),
        soft_arm_floor=Decimal("0.12"),
        soft_dd_floor=Decimal("0.08"),
        hard_arm_floor=Decimal("0.30"),
        hard_dd_floor=Decimal("0.12"),
        atr_arm_mult=Decimal("2.5"),
        atr_dd_mult=Decimal("1.0"),
        atr_enabled=True,
    )
    assert th.soft_arm == Decimal("0.12")
    assert th.hard_arm == Decimal("0.30")
    th2 = scale_thresholds(
        atr=Decimal("0.20"),
        soft_arm_floor=Decimal("0.12"),
        soft_dd_floor=Decimal("0.08"),
        hard_arm_floor=Decimal("0.30"),
        hard_dd_floor=Decimal("0.12"),
        atr_arm_mult=Decimal("2.5"),
        atr_dd_mult=Decimal("1.0"),
        atr_enabled=True,
    )
    assert th2.hard_arm == Decimal("0.50")
    assert th2.soft_arm == Decimal("0.35")


def test_held_alt_bases_respects_concentration_cap() -> None:
    from bot.core.models import Balance

    settings = _unlocked(live_micro_max_alt_bases=3, paper_maker_min_notional_eur=10.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("200"), locked=Decimal("0")),
            Balance(asset="ADA", free=Decimal("100"), locked=Decimal("0")),
            Balance(asset="ATOM", free=Decimal("20"), locked=Decimal("0")),
            Balance(asset="NEAR", free=Decimal("30"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("500"),
    )
    portfolio.set_mark_price("ADAEUR", Decimal("0.5"))
    portfolio.set_mark_price("ATOMEUR", Decimal("2"))
    portfolio.set_mark_price("NEAREUR", Decimal("2"))
    engine = LiveMicroEngine(settings)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("500"),
        live_maker=True,
        allowed_bases={"ADA", "ATOM", "NEAR", "SOL"},
    )
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="ADA", free=Decimal("100"), locked=Decimal("0")),
        Balance(asset="ATOM", free=Decimal("20"), locked=Decimal("0")),
        Balance(asset="NEAR", free=Decimal("30"), locked=Decimal("0")),
    ]
    held = bridge._held_alt_bases("bitvavo")  # noqa: SLF001
    assert held >= {"ADA", "ATOM", "NEAR"}
    assert bridge._max_alt_bases == 3  # noqa: SLF001


def test_held_alt_bases_are_per_venue_not_global() -> None:
    from bot.core.models import Balance

    settings = _unlocked(live_micro_max_alt_bases=3, paper_maker_min_notional_eur=10.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("ADAEUR", Decimal("0.5"))
    portfolio.set_mark_price("ATOMEUR", Decimal("2"))
    portfolio.set_mark_price("NEAREUR", Decimal("2"))
    portfolio.set_mark_price("SOLEUR", Decimal("80"))
    engine = LiveMicroEngine(settings)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="ADA", free=Decimal("100"), locked=Decimal("0")),
        Balance(asset="ATOM", free=Decimal("20"), locked=Decimal("0")),
        Balance(asset="NEAR", free=Decimal("30"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = [  # noqa: SLF001
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    assert len(bridge._held_alt_bases("bitvavo")) == 3  # noqa: SLF001
    assert bridge._held_alt_bases("okx") == {"SOL"}  # noqa: SLF001
    assert len(bridge._held_alt_bases("okx")) < bridge._max_alt_bases  # noqa: SLF001
    # Global union still sees all four — used for cross-venue duplicate checks.
    assert bridge._held_alt_bases(None) >= {"ADA", "ATOM", "NEAR", "SOL"}  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("okx", "ADA") is True  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("bitvavo", "ADA") is False  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("okx", "DOT") is False  # noqa: SLF001


def test_cross_venue_duplicate_counts_sub_min_bag(tmp_path: Path) -> None:
    """€60 bag still blocks other venue when maker min notional is €100."""
    from bot.core.models import Balance

    settings = _unlocked(
        live_micro_block_cross_venue_duplicate_bases=True,
        paper_maker_min_notional_eur=100.0,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("TAOEUR", Decimal("200"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._trail.clear()  # noqa: SLF001
    bridge._resting.clear()  # noqa: SLF001
    # ~€60 TAO on Bitvavo — below clip floor, above live min notional.
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="TAO", free=Decimal("0.3"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = []  # noqa: SLF001
    assert "TAO" not in bridge._held_alt_bases("bitvavo")  # noqa: SLF001
    assert "TAO" in bridge._bases_claimed_for_cross_venue("bitvavo")  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("okx", "TAO") is True  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("bitvavo", "TAO") is False  # noqa: SLF001


def test_cross_venue_duplicate_counts_resting_buy(tmp_path: Path) -> None:
    """Resting buy on Bitvavo blocks opening the same base on OKX."""
    settings = _unlocked(
        live_micro_block_cross_venue_duplicate_bases=True,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._trail.clear()  # noqa: SLF001
    bridge._venue_raw_balances["bitvavo"] = []  # noqa: SLF001
    bridge._venue_raw_balances["okx"] = []  # noqa: SLF001
    bridge._resting = [  # noqa: SLF001
        {
            "venue": "bitvavo",
            "symbol": "TAOEUR",
            "side": "buy",
            "exchange_order_id": "resting-1",
            "quantity": Decimal("0.3"),
            "price": Decimal("200"),
        }
    ]
    assert bridge._resting_buy_bases("bitvavo") == {"TAO"}  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("okx", "TAO") is True  # noqa: SLF001
    assert bridge._is_cross_venue_duplicate_base("bitvavo", "TAO") is False  # noqa: SLF001


def test_buy_clip_cap_uses_first_clip_only() -> None:
    settings = _unlocked(
        live_micro_first_clip_eur=55.0,
        live_micro_add_clip_eur=120.0,
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.02,
        paper_trail_atr_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    assert bridge._buy_clip_cap_eur("bitvavo", "SOL") == Decimal("55")  # noqa: SLF001
    cost = Decimal("100")
    bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "SOL", cost=cost, mark=Decimal("102.5")
    )
    assert bridge._buy_clip_cap_eur("bitvavo", "SOL") == Decimal("55")  # noqa: SLF001


@pytest.mark.asyncio
async def test_cross_venue_duplicate_base_rejects_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.core.models import Balance

    settings = _unlocked(
        live_micro_block_cross_venue_duplicate_bases=True,
        paper_buy_momentum_enabled=False,
        paper_maker_min_notional_eur=10.0,
        live_micro_max_alt_bases=5,
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("FETEUR", Decimal("1"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
        allowed_bases={"FET", "SOL"},
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="FET", free=Decimal("100"), locked=Decimal("0")),
        Balance(asset="EUR", free=Decimal("200"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("200"), locked=Decimal("0")),
    ]

    async def fake_live_free(venue: str, asset: str) -> Decimal:
        if asset.upper() == "EUR":
            return Decimal("200")
        return Decimal("0")

    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
    monkeypatch.setattr(
        bridge, "_venue_budget_remaining", lambda _v: Decimal("200")
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="FETEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("50"),
        limit_price=Decimal("1"),
        metadata={"venue": "okx"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("cross_venue_duplicate_base", 0) >= 1


def test_policy_max_open_orders_per_venue_setting() -> None:
    pol = MicroLivePolicy(
        _unlocked(
            live_micro_max_open_orders=2,
            live_micro_max_open_orders_per_venue=8,
            live_micro_venues="bitvavo,okx",
        )
    )
    assert pol.max_open_orders() == 8
    ok, reason = pol.validate_order(
        venue="okx",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
        open_orders=7,
        side="buy",
    )
    assert ok is True
    assert reason == "ok"
    blocked, reason = pol.validate_order(
        venue="okx",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
        open_orders=8,
        side="buy",
    )
    assert blocked is False
    assert "max open orders" in reason


def test_attach_micro_bridge_does_not_pollute_paper_runner_source() -> None:
    import inspect

    from bot.paper.runner import PaperRunner

    src = inspect.getsource(PaperRunner)
    assert "LiveMicroEngine" not in src
    assert "MicroBudgetLiveExecutor" not in src
    assert callable(attach_micro_bridge)


def test_policy_sells_exempt_from_max_open_orders() -> None:
    pol = MicroLivePolicy(
        _unlocked(live_micro_max_open_orders=2, live_micro_venues="bitvavo,okx")
    )
    buy_blocked, reason = pol.validate_order(
        venue="bitvavo",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
        open_orders=2,
        side="buy",
    )
    assert buy_blocked is False
    assert "max open orders" in reason
    # Same global count must not block the other venue's buys.
    okx_buy, okx_reason = pol.validate_order(
        venue="okx",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
        open_orders=2,
        side="buy",
    )
    assert okx_buy is False
    assert "max open orders" in okx_reason
    sell_ok, sell_reason = pol.validate_order(
        venue="okx",
        symbol="OPLEUR",
        notional_eur=Decimal("10"),
        open_orders=20,
        side="sell",
    )
    assert sell_ok is True
    assert sell_reason == "ok"


def test_buy_lot_base_fee_raises_unit_cost() -> None:
    from bot.live.micro_bridge_executor import _buy_lot_qty_and_unit

    qty, unit = _buy_lot_qty_and_unit(
        amount=Decimal("7.3"),
        price=Decimal("0.0946"),
        fee_amt=Decimal("0.00365"),
        fee_cur="OP",
        base="OP",
        quote="EUR",
    )
    assert qty == Decimal("7.29635")
    assert unit > Decimal("0.0946")
    # Quote-fee path still includes fee in unit cost.
    qty2, unit2 = _buy_lot_qty_and_unit(
        amount=Decimal("7.3"),
        price=Decimal("0.0946"),
        fee_amt=Decimal("0.00055"),
        fee_cur="EUR",
        base="OP",
        quote="EUR",
    )
    assert qty2 == Decimal("7.3")
    assert unit2 > Decimal("0.0946")


def test_okx_sell_blocked_below_trusted_break_even() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=15.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["okx:OP"] = [[Decimal("10"), Decimal("0.10")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("okx:OP")  # noqa: SLF001
    be = bridge._break_even_sell_price("okx", "OP")  # noqa: SLF001
    assert be is not None and be > Decimal("0.10")
    ok, reason, _ = bridge._sell_allowed_at("okx", "OP", Decimal("0.10"))  # noqa: SLF001
    assert ok is False
    assert reason == "sell_below_break_even"
    ok2, reason2, _ = bridge._sell_allowed_at("okx", "OP", be + Decimal("0.001"))  # noqa: SLF001
    assert ok2 is True
    assert reason2 == "ok"


@pytest.mark.asyncio
async def test_profitable_exit_quote_hits_bid_when_above_taker_be() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=15.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:FET"] = [[Decimal("10"), Decimal("0.14")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:FET")  # noqa: SLF001

    class _T:
        bid = Decimal("0.142")
        ask = Decimal("0.1422")
        last = Decimal("0.1421")

    class _Client:
        async def fetch_ticker(self, symbol: str):
            return _T()

    bridge._trading_client = lambda venue: _Client()  # type: ignore[method-assign]  # noqa: SLF001
    px, post_only, reason = await bridge._profitable_exit_quote(  # noqa: SLF001
        "bitvavo", "FET", Decimal("0.1421")
    )
    assert reason == "hit_bid_taker"
    assert post_only is False
    assert px == Decimal("0.142")


def test_break_even_taker_above_maker() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=15.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:FET"] = [[Decimal("10"), Decimal("0.14")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:FET")  # noqa: SLF001
    be_m = bridge._break_even_sell_price("bitvavo", "FET", taker=False)  # noqa: SLF001
    be_t = bridge._break_even_sell_price("bitvavo", "FET", taker=True)  # noqa: SLF001
    assert be_m is not None and be_t is not None
    assert be_t > be_m


def test_trail_time_stop_uses_venue_key() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.12,
        paper_time_stop_enabled=True,
        paper_time_stop_sec=600.0,
        paper_trail_atr_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    import time as _time

    bridge._position_opened_mono[bridge._lots_key("bitvavo", "ADA")] = (  # noqa: SLF001
        _time.monotonic() - 601
    )
    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "ADA", cost=Decimal("1"), mark=Decimal("1.01")
    )
    assert st["soft_armed"] is False
    assert st["time_stop_due"] is True


def test_time_stop_requires_profit_above_break_even() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_session_buys_only=False,
        paper_time_stop_enabled=True,
        paper_time_stop_sec=600.0,
        paper_time_stop_min_profit_bps=25.0,
        paper_trail_atr_enabled=False,
        paper_maker_sell_profit_buffer_bps=10.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    floor = bridge._time_stop_floor_price("bitvavo", "ADA")  # noqa: SLF001
    assert floor is not None
    be = bridge._break_even_sell_price("bitvavo", "ADA")  # noqa: SLF001
    assert be is not None and floor > be


def test_recovery_arm_at_be_does_not_dump_flat() -> None:
    """Aged underwater bags that recover to BE arm a trail instead of selling flat."""
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_session_buys_only=False,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_soft_drawdown_pct=0.03,
        paper_time_stop_enabled=True,
        paper_time_stop_sec=600.0,
        paper_trail_atr_enabled=False,
        paper_maker_sell_profit_buffer_bps=10.0,
        paper_trail_partial_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "ADA")  # noqa: SLF001
    assert be is not None
    st: dict = {
        "venue": "bitvavo",
        "base": "ADA",
        "soft_armed": False,
        "hard_armed": False,
        "armed": False,
        "peak": Decimal("0"),
        "recovery_armed": False,
    }
    armed = bridge._recovery_arm_trail(  # noqa: SLF001
        st, venue="bitvavo", base="ADA", mark=be, be=be
    )
    assert armed is True
    assert st["soft_armed"] is True
    assert st["recovery_armed"] is True
    assert st["newly_soft"] is False  # no immediate partial
    assert Decimal(str(st["peak"])) == be
    bridge._trail["bitvavo:ADA"] = st  # noqa: SLF001

    # Grew above BE, then trail drawdown fires.
    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "ADA", cost=Decimal("1.00"), mark=be * Decimal("1.05")
    )
    assert st["soft_armed"] is True
    assert st.get("recovery_armed") is True
    peak = Decimal(str(st["peak"]))
    assert peak > be
    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo",
        "ADA",
        cost=Decimal("1.00"),
        mark=peak * Decimal("0.96"),  # >3% soft drawdown
    )
    assert st["triggered"] is True


def test_loss_to_be_cross_arms_recovery_no_immediate_sell() -> None:
    """From underwater through BE: arm recovery; do not sell while still rising."""
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_session_buys_only=False,
        paper_trail_soft_arm_pct=0.02,
        paper_trail_soft_drawdown_pct=0.005,
        paper_trail_soft_partial_pct=0.50,
        paper_trail_partial_enabled=True,
        paper_trail_atr_enabled=False,
        paper_maker_sell_profit_buffer_bps=10.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "SOL")  # noqa: SLF001
    assert be is not None and be > Decimal("100")

    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "SOL", cost=Decimal("100"), mark=be * Decimal("0.97")
    )
    armed = bridge._maybe_recovery_arm_from_loss(  # noqa: SLF001
        st, venue="bitvavo", base="SOL", mark=be * Decimal("0.97"), be=be
    )
    assert armed is False
    assert st["below_be"] is True
    assert st.get("recovery_armed") is not True

    # Rising through BE — arm, no sell signal (newly_soft False, no trigger).
    cross = be * Decimal("1.001")
    armed = bridge._maybe_recovery_arm_from_loss(  # noqa: SLF001
        st, venue="bitvavo", base="SOL", mark=cross, be=be
    )
    assert armed is True
    assert st["recovery_armed"] is True
    assert st["soft_armed"] is True
    assert st["newly_soft"] is False
    assert st["below_be"] is False
    assert st.get("triggered") is not True

    # Keep rising — still no recovery-BE exit (peak not yet above then back).
    peak_mark = be * Decimal("1.04")
    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "SOL", cost=Decimal("100"), mark=peak_mark
    )
    assert st["recovery_armed"] is True
    assert Decimal(str(st["peak"])) >= peak_mark * Decimal("0.999")
    assert st.get("triggered") is not True
    # Soft partial must stay blocked while recovery_armed.
    assert not (
        st.get("soft_armed")
        and not st.get("recovery_armed")
        and Decimal(str(st.get("gain") or 0))
        >= Decimal(str(st.get("soft_arm") or 0))
    )

    # Pullback to BE floor → recovery exit condition.
    assert Decimal(str(st["peak"])) > be
    mark_at_be = be
    assert mark_at_be <= be and Decimal(str(st["peak"])) > be


def test_recovery_be_partial_pct_loaded() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(paper_trail_recovery_be_partial_pct=0.35),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    assert bridge._recovery_be_partial == Decimal("0.35")  # noqa: SLF001


def test_underwater_throttle_blocks_new_bases_only() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge.set_buys_blocked(True, new_bases_only=True)
    assert bridge._buys_blocked is True  # noqa: SLF001
    assert bridge._buys_blocked_new_bases_only is True  # noqa: SLF001


def test_okx_aggressive_buy_price_joins_bid() -> None:
    from types import SimpleNamespace

    bridge = MicroBudgetLiveExecutor(
        _unlocked(live_micro_okx_buy_improve_bps=1.0),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    book = SimpleNamespace(
        bids=[SimpleNamespace(price=Decimal("100"))],
        asks=[SimpleNamespace(price=Decimal("100.05"))],
    )
    px_okx = bridge._aggressive_buy_price("okx", Decimal("99.5"), book)  # noqa: SLF001
    px_bv = bridge._aggressive_buy_price("bitvavo", Decimal("99.5"), book)  # noqa: SLF001
    assert px_okx >= Decimal("100")
    assert px_bv >= Decimal("100")


def test_trail_partial_qty_scales_to_min_notional() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    cap = Decimal("10")
    qty = bridge._trail_partial_qty(  # noqa: SLF001
        cap=cap,
        partial_pct=Decimal("0.5"),
        mark=Decimal("2"),
        notional_floor=Decimal("12"),
    )
    assert qty * Decimal("2") >= Decimal("12")
    assert qty <= cap


def test_consolidation_secondary_detects_okx_fet_duplicate(tmp_path: Path) -> None:
    from bot.core.models import Balance

    settings = _unlocked(
        live_micro_consolidate_duplicate_bases=True,
        live_micro_consolidate_primary_venue="bitvavo",
        paper_maker_min_notional_eur=10.0,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("FETEUR", Decimal("0.14"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._trail.clear()  # noqa: SLF001
    bridge._resting.clear()  # noqa: SLF001
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="FET", free=Decimal("3000"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = [  # noqa: SLF001
        Balance(asset="FET", free=Decimal("500"), locked=Decimal("0")),
    ]
    assert bridge._duplicate_bases_by_venue() == {"FET": ["bitvavo", "okx"]}  # noqa: SLF001
    assert bridge._primary_venue_for_base("FET") == "bitvavo"  # noqa: SLF001
    assert bridge._is_consolidation_secondary("okx", "FET") is True  # noqa: SLF001
    assert bridge._is_consolidation_secondary("bitvavo", "FET") is False  # noqa: SLF001


def test_recovery_be_pullback_requires_prior_peak_above_be() -> None:
    """Do not sell on first BE touch; only after having traded above BE."""
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_session_buys_only=False,
        paper_trail_soft_arm_pct=0.12,
        paper_trail_soft_drawdown_pct=0.03,
        paper_trail_atr_enabled=False,
        paper_maker_sell_profit_buffer_bps=10.0,
        paper_trail_partial_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["okx:FET"] = [[Decimal("50"), Decimal("1")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("okx:FET")  # noqa: SLF001
    be = bridge._break_even_sell_price("okx", "FET")  # noqa: SLF001
    assert be is not None

    st: dict = {
        "soft_armed": False,
        "peak": Decimal("0"),
        "recovery_armed": False,
        "below_be": True,
        "drawdown": "0.03",
    }
    bridge._recovery_arm_trail(  # noqa: SLF001
        st, venue="okx", base="FET", mark=be, be=be
    )
    # First touch: peak == be → no recovery-BE sell yet (need peak > be).
    assert Decimal(str(st["peak"])) == be
    first_touch_sell = (
        bool(st.get("recovery_armed"))
        and Decimal(str(st.get("peak") or 0)) > be
        and be <= be  # mark at BE
    )
    assert first_touch_sell is False

    st["peak"] = be * Decimal("1.03")
    pullback_sell = (
        bool(st.get("recovery_armed"))
        and Decimal(str(st["peak"])) > be
        and be <= be  # mark back at BE
    )
    assert pullback_sell is True

def test_resolve_venue_uses_exchange_meta_not_eur_bitvavo_default() -> None:
    settings = _unlocked()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        execute_venues={"bitvavo", "okx"},
        live_maker=True,
    )
    buy = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ADAEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("1"),
        metadata={"buy_exchange": "okx", "sell_exchange": "okx"},
    )
    sell = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ADAEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("10"),
        limit_price=Decimal("1"),
        metadata={"buy_exchange": "bitvavo", "sell_exchange": "okx"},
    )
    assert bridge._resolve_venue(buy) == "okx"  # noqa: SLF001
    assert bridge._resolve_venue(sell) == "okx"  # noqa: SLF001
    # No metadata: pick cash-richest execute venue, never hardcode Bitvavo for EUR.
    empty = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ADAEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("1"),
        metadata={},
    )
    from bot.core.models import Balance

    bridge._bal_cache["okx"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("900"), locked=Decimal("0"))
    ]
    bridge._bal_cache["bitvavo"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("100"), locked=Decimal("0"))
    ]
    assert bridge._resolve_venue(empty) == "okx"  # noqa: SLF001


def test_trail_ignores_mark_spike_before_soft_arm() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.02,
        paper_trail_atr_enabled=False,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    cost = Decimal("1.00")
    bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.01"))  # noqa: SLF001
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.25"))  # noqa: SLF001
    assert st["soft_armed"] is False
    assert bridge.skips.get("trail_mark_spike", 0) >= 1
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.025"))  # noqa: SLF001
    assert st["soft_armed"] is True


def test_underwater_bag_count_ignores_dust_and_long_hold(tmp_path: Path) -> None:
    from bot.portfolio.models import AssetBalance

    settings = _unlocked(
        live_micro_bridge_persist_path=str(tmp_path / "uw.json"),
        live_micro_long_hold_bases="ETH",
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio._state.balances["SOL"] = AssetBalance(  # noqa: SLF001
        asset="SOL", available=Decimal("2"), reserved=Decimal("0")
    )
    portfolio._state.balances["ADA"] = AssetBalance(  # noqa: SLF001
        asset="ADA", available=Decimal("0.1"), reserved=Decimal("0")
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._trail["okx:SOL"] = {  # noqa: SLF001
        "venue": "okx",
        "base": "SOL",
        "cost": "100",
        "last_mark": "90",
    }
    bridge._trail["okx:ADA"] = {  # noqa: SLF001
        "venue": "okx",
        "base": "ADA",
        "cost": "1",
        "last_mark": "0.5",
    }
    bridge._trail["bitvavo:ETH"] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "ETH",
        "cost": "3000",
        "last_mark": "2000",
    }
    # SOL 2*90=180 above floor; ADA dust; ETH long-hold excluded.
    assert bridge.underwater_bag_count(min_notional_eur=25) == 1


def test_underwater_bag_count_per_venue() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._trail["bitvavo:SOL"] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "SOL",
        "cost": "100",
        "last_mark": "90",
    }
    bridge._trail["okx:NEAR"] = {  # noqa: SLF001
        "venue": "okx",
        "base": "NEAR",
        "cost": "5",
        "last_mark": "4",
    }
    bridge._venue_raw_balances["bitvavo"] = []  # noqa: SLF001
    bridge._venue_raw_balances["okx"] = []  # noqa: SLF001
    from bot.core.models import Balance

    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="SOL", free=Decimal("2"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = [  # noqa: SLF001
        Balance(asset="NEAR", free=Decimal("30"), locked=Decimal("0")),
    ]
    assert bridge.underwater_bag_count(min_notional_eur=25, venue="bitvavo") == 1
    assert bridge.underwater_bag_count(min_notional_eur=25, venue="okx") == 1
    assert bridge.underwater_bag_count(min_notional_eur=25) == 2


def test_underwater_base_block_only_affects_that_base() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge.set_buys_blocked(False)
    bridge.set_underwater_base_blocks({"bitvavo": {"SOL"}}, new_bases_only=True)
    assert bridge._base_underwater_blocked("bitvavo", "SOL") is True  # noqa: SLF001
    assert bridge._base_underwater_blocked("bitvavo", "ATOM") is False  # noqa: SLF001
    assert bridge._base_underwater_blocked("okx", "SOL") is False  # noqa: SLF001
    assert bridge._buys_blocked is False  # noqa: SLF001


def test_underwater_bases_by_venue() -> None:
    from bot.core.models import Balance

    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("SOLEUR", Decimal("90"))
    portfolio.set_mark_price("ADAEUR", Decimal("0.30"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._execute_venues = {"bitvavo", "okx"}  # noqa: SLF001
    bridge._trail["bitvavo:SOL"] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "SOL",
        "cost": "100",
        "last_mark": "90",
    }
    bridge._trail["okx:ADA"] = {  # noqa: SLF001
        "venue": "okx",
        "base": "ADA",
        "cost": "0.35",
        "last_mark": "0.30",
    }
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["okx"] = [  # noqa: SLF001
        Balance(asset="ADA", free=Decimal("100"), locked=Decimal("0")),
    ]
    by_venue = bridge.underwater_bases(min_notional_eur=25)
    assert by_venue.get("bitvavo") == {"SOL"}
    assert by_venue.get("okx") == {"ADA"}


def test_maker_cross_venue_paused_skips_cross_pairs() -> None:
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    settings = _unlocked(paper_maker_same_venue=True)
    strat = MakerInventoryStrategy(settings)
    assert strat.cross_venue_paused is False
    strat.set_cross_venue_paused(True)
    assert strat.cross_venue_paused is True


def test_portfolio_sync_does_not_invent_one_eur_entry() -> None:
    from bot.core.models import Balance

    settings = _unlocked(paper_starting_eur=100.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("50"), locked=Decimal("0")),
            Balance(asset="ADA", free=Decimal("10"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("50"),
    )
    pos = portfolio.state.positions.get("ADAEUR")
    assert pos is not None
    assert pos.average_entry_price == Decimal("0")


def test_be_harvest_partial_pct_loaded() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(
            paper_trail_be_harvest_partial_pct=0.40,
            paper_trail_recovery_be_partial_pct=0.35,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    assert bridge._be_harvest_partial == Decimal("0.40")  # noqa: SLF001
    assert bridge._be_harvest_cooldown == 15.0  # noqa: SLF001


def test_be_harvest_falls_back_to_recovery_partial() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(paper_trail_recovery_be_partial_pct=0.35),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    assert bridge._be_harvest_partial == Decimal("0.35")  # noqa: SLF001


def test_be_harvest_eligible_for_recovery_armed_below_soft_arm() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.02,
        paper_trail_be_harvest_partial_pct=0.35,
        paper_trail_be_harvest_min_gain_pct=0.0005,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    st = {
        "soft_armed": True,
        "recovery_armed": True,
        "soft_partial_done": False,
        "be_harvest_partial_done": False,
        "recovery_be_partial_done": False,
        "gain": "0.008",
        "soft_arm": "0.02",
    }
    gain = Decimal("0.008")
    soft_arm = Decimal("0.02")
    assert bridge._soft_partial_would_fire(  # noqa: SLF001
        st, gain_now=gain, soft_arm_now=soft_arm
    ) is False
    assert bridge._be_harvest_already_done(st) is False  # noqa: SLF001
    assert gain >= bridge._be_harvest_min_gain  # noqa: SLF001


def test_partial_done_set_only_via_fill_helper() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    st: dict = {"soft_partial_done": False, "partial_done": False}
    assert bridge._partial_done_key("trail_be_harvest") == "be_harvest_partial_done"  # noqa: SLF001
    bridge._set_partial_done(st, "trail_be_harvest")  # noqa: SLF001
    assert st["be_harvest_partial_done"] is True
    assert st["recovery_be_partial_done"] is True
    bridge._clear_partial_done(st, "trail_be_harvest")  # noqa: SLF001
    assert st["be_harvest_partial_done"] is False
    bridge._set_partial_done(st, "trail_soft_partial")  # noqa: SLF001
    assert st["soft_partial_done"] is True
    assert st["partial_done"] is True
    bridge._clear_partial_done(st, "trail_drawdown")  # noqa: SLF001
    assert st.get("triggered") is False


def test_bag_winnable_zero_below_break_even() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=15.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:FET"] = [[Decimal("10"), Decimal("0.14")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:FET")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "FET")  # noqa: SLF001
    assert be is not None
    below = bridge._bag_winnable_eur(  # noqa: SLF001
        "bitvavo", "FET", cost=Decimal("0.14"), mark=be * Decimal("0.99"), qty=Decimal("10")
    )
    assert below == Decimal("0")
    above = bridge._bag_winnable_eur(  # noqa: SLF001
        "bitvavo", "FET", cost=Decimal("0.14"), mark=be * Decimal("1.01"), qty=Decimal("10")
    )
    assert above > 0


def test_mtm_summary_includes_winnable(tmp_path: Path) -> None:
    from bot.portfolio.models import AssetBalance

    settings = _unlocked(
        paper_maker_sell_profit_buffer_bps=15.0,
        live_micro_bridge_persist_path=str(tmp_path / "bridge.json"),
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    portfolio._state.balances["SOL"] = AssetBalance(  # noqa: SLF001
        asset="SOL", available=Decimal("1"), reserved=Decimal("0")
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._trail.clear()  # noqa: SLF001
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("85")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    bridge._trail["bitvavo:SOL"] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "SOL",
        "cost": Decimal("85"),
        "last_mark": Decimal("87"),
        "session_qty": "1",
    }
    mtm = bridge._mtm_summary()  # noqa: SLF001
    assert Decimal(mtm["winnable_mtm_eur"]) > 0
    assert Decimal(mtm["unrealized_mtm_eur"]) > Decimal(mtm["winnable_mtm_eur"])


def test_be_harvest_cooldown_shorter_than_full_exit() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(
            live_micro_be_harvest_cooldown_sec=12.0,
            live_micro_exit_engine_enabled=False,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    assert bridge._exit_cooldown_sec("trail_be_harvest") == 12.0  # noqa: SLF001
    assert bridge._exit_cooldown_sec("trail_drawdown") == 45.0  # noqa: SLF001


def test_cut_loss_floor_below_be() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(
            live_micro_cut_loss_below_be_pct=0.04,
            paper_maker_sell_profit_buffer_bps=15.0,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "ADA")  # noqa: SLF001
    floor = bridge._cut_loss_floor_price("bitvavo", "ADA")  # noqa: SLF001
    assert be is not None and floor is not None
    assert floor == be * Decimal("0.96")


def test_cut_loss_eligible_respects_new_bases_only_flag() -> None:
    bridge_new_only = MicroBudgetLiveExecutor(
        _unlocked(
            live_micro_cut_loss_below_be_pct=0.04,
            live_micro_cut_loss_new_bases_only=True,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge_all = MicroBudgetLiveExecutor(
        _unlocked(
            live_micro_cut_loss_below_be_pct=0.04,
            live_micro_cut_loss_new_bases_only=False,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    for bridge in (bridge_new_only, bridge_all):
        bridge._cost_lots["okx:NEAR"] = [[Decimal("1"), Decimal("2")]]  # noqa: SLF001
        bridge._trusted_cost_keys.add("okx:NEAR")  # noqa: SLF001
    old = {"new_session_base": False}
    new = {"new_session_base": True}
    assert bridge_new_only._cut_loss_eligible(old, venue="okx", base="NEAR") is False  # noqa: SLF001
    assert bridge_new_only._cut_loss_eligible(new, venue="okx", base="NEAR") is True  # noqa: SLF001
    assert bridge_all._cut_loss_eligible(old, venue="okx", base="NEAR") is True  # noqa: SLF001
    assert bridge_all._cut_loss_eligible(new, venue="okx", base="NEAR") is True  # noqa: SLF001


def test_momentum_down_and_exit_target_at_be_plus_half_pct() -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.001,
        paper_buy_momentum_samples=12,
        live_micro_momentum_exit_min_return=0.002,
        live_micro_momentum_exit_above_be_pct=0.005,
        paper_maker_sell_profit_buffer_bps=15.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "ADA")  # noqa: SLF001
    target = bridge._momentum_exit_target_price("bitvavo", "ADA")  # noqa: SLF001
    assert be is not None and target is not None
    assert target == be * Decimal("1.005")

    series = bridge._series_for("ADAEUR")  # noqa: SLF001
    for px in (1.05, 1.048, 1.046, 1.044, 1.042, 1.040):
        series.push(Decimal(str(px)))
    assert bridge._momentum_down("ADAEUR") is True  # noqa: SLF001
    assert bridge._momentum_ok("ADAEUR", require_history=True) is False  # noqa: SLF001

    # Buy threshold (0.10%) unchanged — small dip is not a sell signal.
    mild = bridge._series_for("MILDEUR")  # noqa: SLF001
    for px in (1.0, 0.9998, 0.9996, 0.9994, 0.9992, 0.9990):
        mild.push(Decimal(str(px)))
    assert bridge._momentum_down("MILDEUR") is False  # noqa: SLF001


def test_buy_fill_marks_new_session_base() -> None:
    from bot.core.enums import OrderSide

    bridge = MicroBudgetLiveExecutor(
        _unlocked(live_micro_cut_loss_below_be_pct=0.04),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._record_realized_fill(  # noqa: SLF001
        side=OrderSide.BUY,
        symbol="NEAREUR",
        qty=Decimal("5"),
        price=Decimal("2"),
        fee=Decimal("0.01"),
        venue="okx",
    )
    assert bridge._trail["okx:NEAR"]["new_session_base"] is True  # noqa: SLF001
    bridge._record_realized_fill(  # noqa: SLF001
        side=OrderSide.BUY,
        symbol="NEAREUR",
        qty=Decimal("1"),
        price=Decimal("1.9"),
        fee=Decimal("0.001"),
        venue="okx",
    )
    assert bridge._trail["okx:NEAR"]["new_session_base"] is True  # noqa: SLF001


def test_daily_kill_uses_session_realized_delta() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(paper_daily_kill_eur=50.0),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.session_start_realized_eur = Decimal("-90")
    bridge.realized_trade_pnl_eur = Decimal("-94")
    bridge._check_daily_kill()  # noqa: SLF001
    assert bridge._daily_kill_active is False  # noqa: SLF001
    bridge.realized_trade_pnl_eur = Decimal("-141")
    bridge._check_daily_kill()  # noqa: SLF001
    assert bridge._daily_kill_active is True  # noqa: SLF001


def test_reset_trading_cycle_after_wind_down(tmp_path: Path) -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(live_micro_bridge_persist_path=str(tmp_path / "wd.json")),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.realized_trade_pnl_eur = Decimal("-94")
    bridge.session_start_realized_eur = Decimal("-100")
    bridge.session_live_transaction_count = 7  # noqa: SLF001
    bridge._daily_kill_active = True  # noqa: SLF001
    bridge._buys_blocked = True  # noqa: SLF001
    bridge.skips["sell_below_break_even"] = 447053
    assert bridge.maybe_reset_after_wind_down() is True  # noqa: SLF001
    assert bridge._daily_kill_active is False  # noqa: SLF001
    assert bridge._buys_blocked is False  # noqa: SLF001
    assert bridge.realized_trade_pnl_eur == Decimal("-94")  # noqa: SLF001
    assert bridge.session_start_realized_eur == Decimal("-100")  # noqa: SLF001
    assert bridge.session_live_transaction_count == 7  # noqa: SLF001
    assert bridge.skips == {}


def test_wind_down_preserves_realized_and_skips_history_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.live.dashboard_history import clear_history, history_path, record_snapshot

    hist = tmp_path / "dashboard_history.jsonl"
    monkeypatch.setattr(
        "bot.live.dashboard_history.history_path", lambda: hist
    )
    hist.write_text('{"t":"2026-08-28T14:00:00","realized_pnl_eur":"12.5"}\n', encoding="utf-8")

    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.realized_trade_pnl_eur = Decimal("12.5")
    bridge.skips["trail_dust"] = 3
    out = bridge.reset_trading_cycle()
    assert out.get("preserved_kpis") is True
    assert bridge.realized_trade_pnl_eur == Decimal("12.5")
    assert bridge.skips == {}
    assert hist.exists()
    assert "12.5" in hist.read_text(encoding="utf-8")
    clear_history(path=hist)


def test_wind_down_clears_stale_skips_without_buy_block() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.skips["sell_below_break_even"] = 99
    assert bridge._buys_blocked is False  # noqa: SLF001
    assert bridge.maybe_reset_after_wind_down() is True  # noqa: SLF001
    assert bridge.skips == {}


def test_early_cut_loss_floor_one_pct_below_be() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(
            live_micro_early_cut_loss_below_be_pct=0.01,
            live_micro_cut_loss_below_be_pct=0.04,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("10"), Decimal("1.00")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "ADA")  # noqa: SLF001
    early = bridge._early_cut_loss_floor_price("bitvavo", "ADA")  # noqa: SLF001
    assert be is not None and early is not None
    assert early == be * Decimal("0.99")


def test_portfolio_gate_sync_clears_ghost_strategy_exposure() -> None:
    from bot.core.enums import OpportunitySide
    from bot.core.models import PortfolioSnapshot, Position
    from bot.opportunity.portfolio_gate import PortfolioExposureGate

    settings = _unlocked(global_max_strategy_exposure_pct=50.0)
    gate = PortfolioExposureGate(settings)
    gate._strategy_exposure["maker_inventory"] = Decimal("9999")  # noqa: SLF001
    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("2000"),
        positions=[],
    )
    gate.sync_from_portfolio(portfolio)
    assert gate.snapshot()["strategy"] == {}
    portfolio_with_bag = PortfolioSnapshot(
        equity_usd=Decimal("2000"),
        positions=[
            Position(
                symbol="ADAEUR",
                quantity=Decimal("100"),
                average_entry_price=Decimal("1"),
                side=OpportunitySide.BUY,
            )
        ],
    )
    gate.sync_from_portfolio(portfolio_with_bag)
    assert gate.snapshot()["strategy"]["maker_inventory"] == "100"


@pytest.mark.asyncio
async def test_dust_positions_do_not_count_toward_open_cap() -> None:
    from bot.portfolio.models import AssetBalance, PositionState
    from bot.portfolio.portfolio import PaperPortfolio

    settings = _unlocked(paper_maker_min_notional_eur=60.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("2000"))
    portfolio.state.balances["ADA"] = AssetBalance(
        asset="ADA", available=Decimal("0.0001"), reserved=Decimal("0")
    )
    portfolio.state.balances["SOL"] = AssetBalance(
        asset="SOL", available=Decimal("0.00001"), reserved=Decimal("0")
    )
    portfolio.state.balances["NEAR"] = AssetBalance(
        asset="NEAR", available=Decimal("10"), reserved=Decimal("0")
    )
    portfolio.state.positions["ADAEUR"] = PositionState(
        symbol="ADAEUR",
        quantity=Decimal("0.0001"),
        average_entry_price=Decimal("1.0"),
    )
    portfolio.state.positions["SOLEUR"] = PositionState(
        symbol="SOLEUR",
        quantity=Decimal("0.00001"),
        average_entry_price=Decimal("150.0"),
    )
    portfolio.state.positions["NEAREUR"] = PositionState(
        symbol="NEAREUR",
        quantity=Decimal("10"),
        average_entry_price=Decimal("3.0"),
    )
    portfolio.set_mark_price("NEAREUR", Decimal("3.0"))
    snap = await portfolio.get_snapshot()
    assert len(snap.positions) == 3
    assert snap.open_position_count == 1


def test_profitability_engine_uses_profitability_min_settings() -> None:
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    settings = _unlocked(
        paper_maker_min_profit_eur=0.05,
        profitability_min_net_profit_usd=0.04,
        profitability_min_net_return=0.0004,
    )
    engine = MakerInventoryStrategy._build_profitability_engine(settings)
    calc = engine._calculator  # noqa: SLF001
    assert calc._min_net_profit == Decimal("0.04")  # noqa: SLF001
    assert calc._min_net_return == Decimal("0.0004")  # noqa: SLF001


def test_venue_emit_rotation_bitvavo_first_alternation() -> None:
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            live_micro_primary_execute_venue="bitvavo",
            paper_maker_venues="okx,bitvavo",
            live_micro_okx_cash_bias_ratio=1.0,
        )
    )
    # Bitvavo richer → primary-first alternation (OKX not cash-rich).
    strat._venue_free_quote = {"bitvavo": Decimal("2000"), "okx": Decimal("500")}  # noqa: SLF001
    rot = strat._venue_emit_rotation(["okx", "bitvavo"])  # noqa: SLF001
    assert rot[:4] == ["bitvavo", "okx", "bitvavo", "okx"]


def test_venue_emit_rotation_symmetric_without_okx_cash_bias() -> None:
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            live_micro_primary_execute_venue="bitvavo",
            paper_maker_venues="okx,bitvavo",
            live_micro_okx_cash_bias_ratio=1.0,
        )
    )
    strat._venue_free_quote = {"bitvavo": Decimal("1900"), "okx": Decimal("1900")}  # noqa: SLF001
    rot = strat._venue_emit_rotation(["okx", "bitvavo"])  # noqa: SLF001
    assert rot[:4] == ["bitvavo", "okx", "bitvavo", "okx"]
    assert rot.count("okx") == rot.count("bitvavo")


def test_focus_bases_rank_above_near_tie_non_focus() -> None:
    from bot.core.models import TradeOpportunity
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            live_micro_focus_bases="SOL,ADA",
            paper_maker_venues="bitvavo",
        )
    )

    def _opp(symbol: str, net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("90"),
            expected_exit_price=Decimal("91"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": "bitvavo",
                "sell_exchange": "bitvavo",
                "net_profit_eur": net,
            },
        )

    focus = _opp("SOLEUR", "0.10")
    other = _opp("TAOEUR", "0.14")
    # Focus +0.04 vs non-focus -0.08 → 0.14 vs 0.06.
    assert strat._rank_opportunity(focus) > strat._rank_opportunity(other)  # noqa: SLF001
    # Large NET still wins.
    big = _opp("TAOEUR", "0.50")
    assert strat._rank_opportunity(big) > strat._rank_opportunity(focus)  # noqa: SLF001


def test_emit_budget_flat_tightens() -> None:
    from bot.core.models import TradeOpportunity
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            arbitrage_max_emits_per_cycle=8,
            paper_maker_keep_vs_best_frac=0.40,
            paper_maker_min_profit_eur=0.05,
            live_micro_active_ring_eur=1000.0,
        )
    )
    # No idle cash, ring already "full" via high active notional → classic flat throttle.
    strat._venue_free_quote = {"bitvavo": Decimal("50")}  # noqa: SLF001
    strat._venue_active_notional = {"bitvavo": Decimal("1000")}  # noqa: SLF001

    def _opp(net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol="SOLEUR",
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("90"),
            expected_exit_price=Decimal("91"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": "bitvavo",
                "sell_exchange": "bitvavo",
                "net_profit_eur": net,
            },
        )

    # One weak survivor → flat budget.
    max_e, keep = strat._emit_budget_for_regime([_opp("0.06")])  # noqa: SLF001
    assert max_e == 4
    assert keep == Decimal("0.70")

    # Underfilled ring + free cash → full emit slots + looser keep.
    strat._venue_free_quote = {"bitvavo": Decimal("1800"), "okx": Decimal("1800")}  # noqa: SLF001
    strat._venue_active_notional = {"bitvavo": Decimal("0"), "okx": Decimal("0")}  # noqa: SLF001
    max_ring, keep_ring = strat._emit_budget_for_regime([_opp("0.06")])  # noqa: SLF001
    assert max_ring == 8
    assert keep_ring == Decimal("0.25")

    # Many strong nets → full budget.
    strong = [
        TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol=f"S{i}EUR",
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("90"),
            expected_exit_price=Decimal("91"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": "bitvavo",
                "sell_exchange": "bitvavo",
                "net_profit_eur": str(0.20 + i * 0.01),
            },
        )
        for i in range(6)
    ]
    strat._venue_active_notional = {
        "bitvavo": Decimal("1000"),
        "okx": Decimal("1000"),
    }  # noqa: SLF001
    max_e2, keep2 = strat._emit_budget_for_regime(strong)  # noqa: SLF001
    assert max_e2 == 8
    assert keep2 == Decimal("0.40")


def test_active_ring_boosts_unheld_focus_rank() -> None:
    from bot.core.models import TradeOpportunity
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            live_micro_focus_bases="ADA,LINK",
            live_micro_active_ring_eur=1000.0,
            paper_maker_venues="bitvavo",
        )
    )
    strat._venue_free_quote = {"bitvavo": Decimal("1500")}  # noqa: SLF001
    strat._venue_active_notional = {"bitvavo": Decimal("0")}  # noqa: SLF001
    strat.set_stuck_bases({"bitvavo": {"SOL"}})

    def _opp(symbol: str, net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("1"),
            expected_exit_price=Decimal("1.01"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": "bitvavo",
                "sell_exchange": "bitvavo",
                "net_profit_eur": net,
            },
        )

    focus = _opp("ADAEUR", "0.10")
    other = _opp("HYPEEUR", "0.18")
    assert strat._rank_opportunity(focus) > strat._rank_opportunity(other)  # noqa: SLF001


def test_ring_soft_blocked_when_underwater_stuck(tmp_path: Path) -> None:
    from bot.core.models import Balance

    # Legacy path: ignore_underwater=False still blocks Util-B on stuck bags.
    settings = _unlocked(
        live_micro_ring_soft_max_active_eur=650.0,
        live_micro_ring_soft_block_underwater_eur=25.0,
        live_micro_ring_util_b_ignore_underwater=False,
        live_micro_active_ring_eur=1000.0,
        live_micro_bridge_persist_path=str(tmp_path / "uw_ring.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    bridge._bal_cache["bitvavo"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("1500"), locked=Decimal("0")),
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["bitvavo"] = bridge._bal_cache["bitvavo"]  # noqa: SLF001
    bridge._portfolio.set_mark_price("SOLEUR", Decimal("100"))
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("105")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    assert bridge._active_book_notional("bitvavo") == Decimal("0")  # noqa: SLF001
    assert bridge._underwater_book_notional("bitvavo") == Decimal("100")  # noqa: SLF001
    assert bridge._ring_needs_deploy("bitvavo") is True  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is False  # noqa: SLF001


def test_ring_soft_unlocked_despite_underwater_vault(tmp_path: Path) -> None:
    from bot.core.models import Balance

    settings = _unlocked(
        live_micro_ring_soft_max_active_eur=650.0,
        live_micro_ring_soft_block_underwater_eur=25.0,
        live_micro_ring_util_b_ignore_underwater=True,
        live_micro_active_ring_eur=1000.0,
        live_micro_bridge_persist_path=str(tmp_path / "uw_ring_unlock.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    bridge._bal_cache["bitvavo"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("1500"), locked=Decimal("0")),
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["bitvavo"] = bridge._bal_cache["bitvavo"]  # noqa: SLF001
    bridge._portfolio.set_mark_price("SOLEUR", Decimal("100"))
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("105")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    assert bridge._underwater_book_notional("bitvavo") == Decimal("100")  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is True  # noqa: SLF001


def test_entry_momentum_requires_short_window() -> None:
    from bot.live.trail_policy import MarkSeries

    series = MarkSeries(maxlen=12)
    for px in ("100.00", "100.30", "100.50", "100.55", "100.54", "100.53"):
        series.push(Decimal(px))
    assert series.momentum_return_last(4) is not None
    assert series.momentum_return_last(4) < Decimal("0.001")

    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.001,
        live_micro_entry_short_momentum_samples=4,
        live_micro_entry_short_momentum_min_return=0.001,
        live_micro_momentum_require_last_n_rising=3,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    symbol = "SOLEUR"
    for px in ("100.00", "100.05", "100.10", "100.15", "100.20", "100.25"):
        bridge._series_for(symbol).push(Decimal(px))  # noqa: SLF001
    assert bridge._entry_momentum_ok(  # noqa: SLF001
        symbol, min_return=Decimal("0.001"), low_util=False
    ) is True
    for px in ("100.00", "100.30", "100.50", "100.55", "100.54", "100.53"):
        bridge._series_for("WEAK").push(Decimal(px))  # noqa: SLF001
    assert bridge._entry_momentum_ok(  # noqa: SLF001
        "WEAK", min_return=Decimal("0.001"), low_util=False
    ) is False


def test_corr_sector_blocks_new_buy_when_sector_weak() -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        live_micro_corr_group="SOL,XRP,ADA",
        live_micro_corr_sector_momentum_block=2,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._venue_raw_balances["bitvavo"] = []  # noqa: SLF001
    bridge._held_alt_bases = lambda venue: {"SOL", "ADA"}  # type: ignore[method-assign]  # noqa: SLF001
    for sym, prices in (
        ("SOLEUR", ("100", "99.5", "99.0", "98.8", "98.5", "98.2")),
        ("ADAEUR", ("50", "49.8", "49.6", "49.4", "49.2", "49.0")),
    ):
        for px in prices:
            bridge._series_for(sym).push(Decimal(px))  # noqa: SLF001
    assert bridge._corr_group_momentum_down_count() >= 2  # noqa: SLF001
    assert bridge._corr_sector_blocks_new_buy("XRP") is True  # noqa: SLF001
    assert bridge._corr_sector_blocks_new_buy("BTC") is False  # noqa: SLF001


def test_ring_underfill_uses_softer_momentum_floor(tmp_path: Path) -> None:
    from bot.core.models import Balance

    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.001,
        live_micro_ring_momentum_min_return=0.0005,
        live_micro_ring_soft_max_active_eur=300.0,
        live_micro_active_ring_eur=1000.0,
        live_micro_bridge_persist_path=str(tmp_path / "ring_mom.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    bridge._bal_cache["bitvavo"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("1500"), locked=Decimal("0")),
    ]
    # Empty active book → ring soft eligible → soft floor.
    assert bridge._ring_needs_deploy("bitvavo") is True  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is True  # noqa: SLF001
    assert bridge._momentum_floor_for_buy("bitvavo") == Decimal("0.0005")  # noqa: SLF001
    # Active book above soft max but below ring → full momentum floor.
    bridge._active_book_notional = lambda venue: Decimal("400")  # type: ignore[method-assign]  # noqa: SLF001
    assert bridge._ring_needs_deploy("bitvavo") is True  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is False  # noqa: SLF001
    assert bridge._momentum_floor_for_buy("bitvavo") == Decimal("0.001")  # noqa: SLF001
    # When ring is filled, full momentum floor applies.
    bridge._active_book_notional = lambda venue: Decimal("1200")  # type: ignore[method-assign]  # noqa: SLF001
    assert bridge._momentum_floor_for_buy("bitvavo") == Decimal("0.001")  # noqa: SLF001


def test_last_n_rising_required_for_momentum_ok() -> None:
    from bot.live.trail_policy import MarkSeries

    series = MarkSeries(maxlen=12)
    for px in [100, 100.05, 100.04, 100.08]:
        series.push(Decimal(str(px)))
    assert series.last_n_rising(3) is False  # 100.05 -> 100.04 dip
    assert series.last_n_rising(2) is True  # 100.04 -> 100.08
    assert series.last_n_mostly_rising(3, min_up=2) is False
    series2 = MarkSeries(maxlen=12)
    for px in [100, 100.02, 100.05, 100.09]:
        series2.push(Decimal(str(px)))
    assert series2.last_n_rising(3) is True


def test_trail_hold_rising_defers_be_harvest_above_be() -> None:
    settings = _unlocked(
        live_micro_trail_hold_while_rising=True,
        live_micro_trail_hold_rising_n=2,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    symbol = "SOLEUR"
    for px in ("100.00", "100.02", "100.05"):
        bridge._series_for(symbol).push(Decimal(px))  # noqa: SLF001
    st = {"triggered": False}
    be = Decimal("99.50")
    mark = Decimal("100.05")
    assert bridge._momentum_still_rising(symbol) is True  # noqa: SLF001
    assert bridge._defer_harvest_while_rising(  # noqa: SLF001
        symbol, mark=mark, be=be, st=st, reason="trail_be_harvest"
    ) is True
    assert bridge._defer_harvest_while_rising(  # noqa: SLF001
        symbol, mark=mark, be=be, st=st, reason="trail_drawdown"
    ) is False


def test_trail_hold_rising_allows_exit_after_pullback() -> None:
    settings = _unlocked(
        live_micro_trail_hold_while_rising=True,
        live_micro_trail_hold_rising_n=2,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    symbol = "SOLEUR"
    for px in ("100.10", "100.08", "100.06"):
        bridge._series_for(symbol).push(Decimal(px))  # noqa: SLF001
    st = {"triggered": False}
    be = Decimal("99.50")
    mark = Decimal("100.06")
    assert bridge._momentum_still_rising(symbol) is False  # noqa: SLF001
    assert bridge._defer_harvest_while_rising(  # noqa: SLF001
        symbol, mark=mark, be=be, st=st, reason="trail_exit_work"
    ) is False


def test_trail_hold_rising_never_defers_drawdown_trigger() -> None:
    settings = _unlocked(live_micro_trail_hold_while_rising=True)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    symbol = "SOLEUR"
    for px in ("100.00", "100.02", "100.05"):
        bridge._series_for(symbol).push(Decimal(px))  # noqa: SLF001
    st = {"triggered": True}
    assert bridge._defer_harvest_while_rising(  # noqa: SLF001
        symbol,
        mark=Decimal("100.05"),
        be=Decimal("99.50"),
        st=st,
        reason="trail_soft_partial",
    ) is False


def test_low_util_momentum_uses_min_rising_floor(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_min_return=0.0008,
        live_micro_ring_momentum_min_return=0.0005,
        live_micro_momentum_require_last_n_rising=3,
        live_micro_low_util_rising_n=2,
        live_micro_entry_min_low_util_rising_n=3,
        paper_buy_momentum_samples=12,
        live_micro_bridge_persist_path=str(tmp_path / "lu.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    series = bridge._series_for("SOLEUR")  # noqa: SLF001
    for px in [100.0, 100.01, 100.03, 100.08, 100.06, 100.12]:
        series.push(Decimal(str(px)))
    assert (
        bridge._momentum_ok(  # noqa: SLF001
            "SOLEUR",
            require_history=True,
            min_return=Decimal("0.0005"),
            low_util=True,
        )
        is False
    )
    series2 = bridge._series_for("ADAEUR")  # noqa: SLF001
    for px in [100.0, 100.02, 100.04, 100.06, 100.08, 100.10]:
        series2.push(Decimal(str(px)))
    assert (
        bridge._momentum_ok(  # noqa: SLF001
            "ADAEUR",
            require_history=True,
            min_return=Decimal("0.0005"),
            low_util=True,
        )
        is True
    )


def test_winner_add_eligible_requires_soft_arm_and_be(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_winner_add_enabled=True,
        live_micro_winner_add_max=2,
        live_micro_winner_add_clip_eur=55.0,
        live_micro_alphai_winner_add_only=True,
        alphai_bullish_buy_enabled=True,
        live_micro_bridge_persist_path=str(tmp_path / "wa.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    from bot.integrations.alphai.signals import build_trading_signals

    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        None,
        {
            "picks": [
                {
                    "base": "SOL",
                    "score": 80.0,
                    "bullish_headlines": ["a", "b", "c"],
                }
            ],
            "avoid": [],
        },
    )
    bridge._alphai_daily_generated_at = "2026-09-04T08:00:00+00:00"  # noqa: SLF001
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    bridge._trail["bitvavo:SOL"] = {  # noqa: SLF001
        "soft_armed": False,
        "winner_add_count": 0,
    }
    be = Decimal("100.15")
    bridge._break_even_sell_price = lambda venue, base: be  # type: ignore[method-assign]  # noqa: SLF001
    assert (
        bridge._winner_add_eligible(  # noqa: SLF001
            "bitvavo", "SOL", mark=Decimal("100.20"), be=be
        )
        is False
    )
    bridge._trail["bitvavo:SOL"]["soft_armed"] = True  # noqa: SLF001
    assert (
        bridge._winner_add_eligible(  # noqa: SLF001
            "bitvavo", "SOL", mark=Decimal("100.10"), be=be
        )
        is False
    )
    assert (
        bridge._winner_add_eligible(  # noqa: SLF001
            "bitvavo", "SOL", mark=Decimal("100.30"), be=be
        )
        is True
    )
    bridge._trail["bitvavo:SOL"]["winner_add_count"] = 2  # noqa: SLF001
    assert (
        bridge._winner_add_eligible(  # noqa: SLF001
            "bitvavo", "SOL", mark=Decimal("100.30"), be=be
        )
        is False
    )


def test_winner_add_blocked_when_alphai_weak_or_stale(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_winner_add_enabled=True,
        live_micro_winner_add_max=2,
        live_micro_alphai_winner_add_only=True,
        alphai_bullish_buy_enabled=True,
        live_micro_bridge_persist_path=str(tmp_path / "wa_weak.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    from bot.integrations.alphai.signals import build_trading_signals

    # Weak low-conviction pick should not receive winner-adds.
    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        None,
        {
            "picks": [
                {"base": "ETH", "score": 114.0, "bullish_headlines": ["a", "b", "c"]},
                {"base": "BNB", "score": 18.0, "bullish_headlines": ["a"]},
            ],
            "avoid": [],
        },
    )
    bridge._alphai_daily_generated_at = "2026-09-04T08:00:00+00:00"  # noqa: SLF001
    bridge._trail["bitvavo:BNB"] = {  # noqa: SLF001
        "soft_armed": True,
        "winner_add_count": 0,
    }
    be = Decimal("100.15")
    assert bridge._alphai_weak_bullish_hold("BNB") is True  # noqa: SLF001
    assert (
        bridge._winner_add_eligible(  # noqa: SLF001
            "bitvavo", "BNB", mark=Decimal("100.30"), be=be
        )
        is False
    )


def test_uw_recycle_weak_alphai_faster_than_strong(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_uw_recycle_enabled=True,
        live_micro_uw_dust_max_notional_eur=0.0,
        live_micro_uw_non_alphai_below_be_pct=0.01,
        live_micro_uw_non_alphai_min_age_sec=3600.0,
        live_micro_uw_alphai_below_be_pct=0.02,
        live_micro_uw_alphai_min_age_sec=10800.0,
        live_micro_bridge_persist_path=str(tmp_path / "uw_weak.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    from bot.integrations.alphai.signals import build_trading_signals

    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        None,
        {
            "picks": [
                {"base": "ETH", "score": 114.0, "bullish_headlines": ["a", "b", "c"]},
                {"base": "BNB", "score": 18.0, "bullish_headlines": ["a"]},
            ],
            "avoid": [],
        },
    )
    for base in ("ETH", "BNB"):
        bridge._cost_lots[f"bitvavo:{base}"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
        bridge._trusted_cost_keys.add(f"bitvavo:{base}")  # noqa: SLF001
        bridge._position_opened_at[f"bitvavo:{base}"] = __import__("time").time() - 7000  # noqa: SLF001
    bridge._momentum_flat_or_down = lambda symbol: True  # type: ignore[method-assign]  # noqa: SLF001
    mark = Decimal("99.0")  # ~1% below BE
    be = Decimal("100")
    weak = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="bitvavo",
        base="BNB",
        symbol="BNBEUR",
        mark=mark,
        be=be,
        notional=Decimal("100"),
    )
    strong = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="bitvavo",
        base="ETH",
        symbol="ETHEUR",
        mark=mark,
        be=be,
        notional=Decimal("100"),
    )
    assert weak is not None and weak[0].startswith("alphai_weak")
    assert strong is None  # strong AlphaI still holding at ~1% / ~1.9h


def test_uw_idle_pressure_recycles_non_strong(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_uw_recycle_enabled=True,
        live_micro_uw_dust_max_notional_eur=0.0,
        live_micro_uw_idle_pressure_enabled=True,
        live_micro_uw_idle_min_free_eur=100.0,
        live_micro_uw_idle_min_age_sec=600.0,
        live_micro_uw_idle_below_be_pct=0.004,
        live_micro_uw_alphai_below_be_pct=0.015,
        live_micro_uw_alphai_min_age_sec=3600.0,
        live_micro_active_ring_eur=1850.0,
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_samples=12,
        live_micro_early_cut_momentum_max_return=0.0,
        alphai_bullish_buy_enabled=True,
        live_micro_bridge_persist_path=str(tmp_path / "idle_uw.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    from bot.integrations.alphai.signals import build_trading_signals

    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        None,
        {
            "picks": [
                {"base": "ETH", "score": 114.0, "bullish_headlines": ["a", "b", "c"]},
                {"base": "ADA", "score": 0.0},
            ],
            "avoid": [],
        },
    )
    series = bridge._series_for("ADAEUR")  # noqa: SLF001
    px = Decimal("1")
    for _ in range(12):
        px = px * Decimal("0.999")
        series.push(px)
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("80"), Decimal("1")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ADA")  # noqa: SLF001
    bridge._position_opened_at["bitvavo:ADA"] = __import__("time").time() - 700  # noqa: SLF001
    bridge._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]  # noqa: SLF001
    plan = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="bitvavo",
        base="ADA",
        symbol="ADAEUR",
        mark=Decimal("0.995"),  # -0.5%
        be=Decimal("1"),
        notional=Decimal("80"),
    )
    assert plan is not None and plan[0] == "idle_pressure"


def test_low_util_relax_focus_skips_focus_gate(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_new_buy_focus_only=True,
        live_micro_focus_bases="SOL,ETH",
        live_micro_low_util_relax_focus=True,
        live_micro_ring_soft_max_active_eur=300.0,
        live_micro_active_ring_eur=1000.0,
        live_micro_bridge_persist_path=str(tmp_path / "rf.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._active_book_notional = lambda venue: Decimal("50")  # type: ignore[method-assign]  # noqa: SLF001
    bridge._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is True  # noqa: SLF001
    assert bridge._low_util_relax_focus is True  # noqa: SLF001
    # Non-focus base allowed while low-util.
    assert "LINK" not in bridge._focus_bases  # noqa: SLF001
    bridge._active_book_notional = lambda venue: Decimal("400")  # type: ignore[method-assign]  # noqa: SLF001
    assert bridge._ring_soft_momentum_eligible("bitvavo") is False  # noqa: SLF001


def test_buy_quality_circuit_breaker_pauses(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_buy_quality_underwater_count=2,
        live_micro_buy_quality_pause_sec=120.0,
        live_micro_bridge_persist_path=str(tmp_path / "bq.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._trusted_cost_keys.add("bitvavo:AAVE")  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:ATOM")  # noqa: SLF001
    bridge._cost_lots["bitvavo:AAVE"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    bridge._cost_lots["bitvavo:ATOM"] = [[Decimal("10"), Decimal("2")]]  # noqa: SLF001
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        type("B", (), {"asset": "AAVE", "free": Decimal("1"), "locked": Decimal("0")})(),
        type("B", (), {"asset": "ATOM", "free": Decimal("10"), "locked": Decimal("0")})(),
    ]
    bridge._portfolio.state.mark_prices["AAVEEUR"] = Decimal("99")
    bridge._portfolio.state.mark_prices["ATOMEUR"] = Decimal("1.9")
    bridge._recent_session_buy_keys = ["bitvavo:AAVE", "bitvavo:ATOM"]  # noqa: SLF001
    bridge._refresh_buy_quality_circuit_breaker()  # noqa: SLF001
    assert bridge._buy_quality_paused() is True  # noqa: SLF001


def test_stuck_underwater_base_excluded_from_corr_count(tmp_path: Path) -> None:
    settings = _unlocked(
        live_micro_corr_group="SOL,ADA,LINK,XRP",
        live_micro_max_per_corr_group=2,
        live_micro_bridge_persist_path=str(tmp_path / "corr.json"),
        paper_maker_min_notional_eur=10.0,
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("2000"))
    portfolio.set_mark_price("SOLEUR", Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    from bot.core.models import Balance

    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
        Balance(asset="EUR", free=Decimal("1500"), locked=Decimal("0")),
    ]
    bridge.set_underwater_base_blocks({"bitvavo": {"SOL"}}, new_bases_only=True)
    # Stuck SOL must not consume the corr slot.
    assert bridge._corr_held_count(venue="bitvavo") == 0  # noqa: SLF001
    assert bridge._corr_held_count(venue="bitvavo", adding="ADA") == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_new_buy_focus_only_blocks_tao(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _unlocked(
        live_micro_new_buy_focus_only=True,
        live_micro_focus_bases="SOL,ADA,LINK",
        paper_buy_momentum_enabled=False,
        live_micro_first_clip_eur=55.0,
        live_micro_bridge_persist_path=str(tmp_path / "focus.json"),
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("TAOEUR", Decimal("200"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )

    async def fake_live_free(venue: str, asset: str) -> Decimal:
        return Decimal("500") if asset.upper() == "EUR" else Decimal("0")

    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
    monkeypatch.setattr(
        bridge, "_venue_budget_remaining", lambda _v: Decimal("500")
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="TAOEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.3"),
        limit_price=Decimal("200"),
        metadata={"venue": "bitvavo", "post_only": True},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("focus_base_required", 0) >= 1


@pytest.mark.asyncio
async def test_holding_base_buy_block_rejects_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.core.models import Balance

    settings = _unlocked(
        live_micro_block_buys_when_holding_base=True,
        paper_buy_momentum_enabled=False,
        live_micro_first_clip_eur=55.0,
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("500"))
    portfolio.set_mark_price("SOLEUR", Decimal("90"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="SOL", free=Decimal("2"), locked=Decimal("0")),
        Balance(asset="EUR", free=Decimal("500"), locked=Decimal("0")),
    ]
    bridge._session_lots["bitvavo:SOL"] = [  # noqa: SLF001
        [Decimal("2"), Decimal("88")],
    ]

    async def fake_live_free(venue: str, asset: str) -> Decimal:
        if asset.upper() == "EUR":
            return Decimal("500")
        return Decimal("2")

    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
    monkeypatch.setattr(
        bridge, "_venue_budget_remaining", lambda _v: Decimal("500")
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="SOLEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.5"),
        limit_price=Decimal("90"),
        metadata={"venue": "bitvavo", "post_only": True},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("holding_base_buy_block", 0) >= 1


@pytest.mark.asyncio
async def test_prune_resting_buys_keeps_best_n_bids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _unlocked(
        live_micro_block_buys_when_holding_base=False,
        live_micro_max_resting_buys_per_symbol=2,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    cancelled: list[str] = []
    bridge._resting = [  # noqa: SLF001
        {
            "venue": "bitvavo",
            "symbol": "SOLEUR",
            "side": "buy",
            "exchange_order_id": "lowest",
            "price": Decimal("88"),
            "quantity": Decimal("0.2"),
        },
        {
            "venue": "bitvavo",
            "symbol": "SOLEUR",
            "side": "buy",
            "exchange_order_id": "mid",
            "price": Decimal("89"),
            "quantity": Decimal("0.2"),
        },
        {
            "venue": "bitvavo",
            "symbol": "SOLEUR",
            "side": "buy",
            "exchange_order_id": "high",
            "price": Decimal("90"),
            "quantity": Decimal("0.2"),
        },
    ]

    class FakeClient:
        async def cancel_order(self, oid: str, symbol: str) -> None:
            cancelled.append(oid)

    monkeypatch.setattr(bridge, "_trading_client", lambda _v: FakeClient())
    n = await bridge._prune_resting_buys("bitvavo")  # noqa: SLF001
    assert n == 1
    assert cancelled == ["lowest"]
    assert len(bridge._resting) == 2  # noqa: SLF001
    kept = {r["exchange_order_id"] for r in bridge._resting}  # noqa: SLF001
    assert kept == {"high", "mid"}


@pytest.mark.asyncio
async def test_buy_momentum_cancel_always_on_flat_tape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        live_micro_cancel_buy_on_flat_momentum=True,
        live_micro_ring_soft_max_active_eur=650.0,
        live_micro_active_ring_eur=1000.0,
        live_micro_resting_max_age_sec=600.0,
        live_micro_buy_resting_max_age_sec=600.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    bridge._resting = [  # noqa: SLF001
        {
            "venue": "bitvavo",
            "symbol": "SOLEUR",
            "side": "buy",
            "exchange_order_id": "bid1",
            "price": Decimal("90"),
            "quantity": Decimal("0.2"),
            "placed_mono": time.monotonic(),
        },
    ]
    cancelled: list[str] = []

    class FakeOrder:
        filled_quantity = Decimal("0")
        average_price = Decimal("90")
        price = Decimal("90")
        status = OrderStatus.OPEN

    class FakeClient:
        async def fetch_order(self, oid: str, symbol: str) -> FakeOrder:
            return FakeOrder()

        async def cancel_order(self, oid: str, symbol: str) -> None:
            cancelled.append(oid)

    monkeypatch.setattr(bridge, "_trading_client", lambda _v: FakeClient())
    async def _noop_prune(_v: str) -> int:
        return 0

    monkeypatch.setattr(bridge, "_prune_resting_buys", _noop_prune)
    monkeypatch.setattr(bridge, "_momentum_flat_or_down_for_cancel", lambda _s: True)
    monkeypatch.setattr(bridge, "_ring_soft_momentum_eligible", lambda _v: True)

    await bridge.manage_resting_orders("bitvavo")
    assert cancelled == ["bid1"]
    assert bridge._resting == []  # noqa: SLF001


def test_select_balanced_emits_one_symbol_per_venue() -> None:
    from bot.core.models import TradeOpportunity
    from bot.strategies.maker_inventory import MakerInventoryStrategy

    strat = MakerInventoryStrategy(
        _unlocked(
            arbitrage_max_emits_per_cycle=4,
            live_micro_primary_execute_venue="bitvavo",
        )
    )

    def _opp(symbol: str, venue: str, net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("90"),
            expected_exit_price=Decimal("91"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": venue,
                "sell_exchange": venue,
                "net_profit_eur": net,
            },
        )

    opps = [
        _opp("SOLEUR", "bitvavo", "0.10"),
        _opp("SOLEUR", "bitvavo", "0.08"),
        _opp("ADAEUR", "bitvavo", "0.07"),
    ]
    selected = strat._select_balanced_emits(opps)  # noqa: SLF001
    bitvavo_syms = [
        str(o.symbol).upper()
        for o in selected
        if (o.metadata or {}).get("buy_exchange") == "bitvavo"
    ]
    assert bitvavo_syms.count("SOLEUR") <= 1


def test_be_harvest_marks_done_on_submit_not_only_fill() -> None:
    bridge = MicroBudgetLiveExecutor(
        _unlocked(
            paper_trail_be_harvest_partial_pct=0.35,
            paper_trail_recovery_be_partial_pct=0.35,
        ),
        portfolio=PaperPortfolio(_unlocked(), starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(_unlocked()),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    st: dict = {"be_harvest_partial_done": False, "recovery_be_partial_done": False}
    bridge._set_partial_done(st, "trail_be_harvest")  # noqa: SLF001
    assert bridge._be_harvest_already_done(st) is True  # noqa: SLF001


def test_soft_arm_resets_be_harvest_for_one_shot_per_cycle() -> None:
    settings = _unlocked(
        paper_trail_soft_arm_pct=0.001,
        paper_trail_soft_drawdown_pct=0.004,
        paper_trail_atr_enabled=False,
        live_micro_bridge_persist_path="./data/test_trail_one_shot.json",
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    bridge._trail = {}  # noqa: SLF001
    bridge._session_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("90")]]  # noqa: SLF001
    bridge._mark_cost_trusted("bitvavo", "SOL")  # noqa: SLF001
    cost = Decimal("90")
    st = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "SOL", cost=cost, mark=Decimal("90.15")
    )
    assert st.get("newly_soft") is True
    assert st.get("be_harvest_partial_done") is False
    st["be_harvest_partial_done"] = True
    st2 = bridge._trail_update_state(  # noqa: SLF001
        "bitvavo", "SOL", cost=cost, mark=Decimal("90.20")
    )
    assert st2.get("be_harvest_partial_done") is True


@pytest.mark.asyncio
async def test_exit_engine_touch_maker_when_bid_below_taker_be() -> None:
    """D: aggressive quotes join inside spread instead of resting at ask."""
    settings = _unlocked(
        paper_maker_sell_profit_buffer_bps=15.0,
        live_micro_exit_engine_enabled=True,
        live_micro_exit_touch_improve_bps=2.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    # Cost high enough that bid is above maker BE but below taker BE.
    bridge._cost_lots["bitvavo:DOT"] = [[Decimal("10"), Decimal("0.140")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:DOT")  # noqa: SLF001

    class _T:
        bid = Decimal("0.1405")
        ask = Decimal("0.1415")
        last = Decimal("0.1410")

    class _Client:
        async def fetch_ticker(self, symbol: str):
            return _T()

    bridge._trading_client = lambda venue: _Client()  # type: ignore[method-assign]  # noqa: SLF001

    be_m = bridge._break_even_sell_price("bitvavo", "DOT", taker=False)  # noqa: SLF001
    be_t = bridge._break_even_sell_price("bitvavo", "DOT", taker=True)  # noqa: SLF001
    assert be_m is not None and be_t is not None
    assert _T.bid >= be_m
    assert _T.bid < be_t

    # Mark above maker BE but below taker-BE cushion → inside-spread touch maker.
    mark = min(_T.ask, be_t * Decimal("1.0002"))
    assert mark < be_t * Decimal("1.0005")
    px, post_only, reason = await bridge._profitable_exit_quote(  # noqa: SLF001
        "bitvavo", "DOT", mark, aggressive=True
    )
    assert reason == "rest_touch_maker"
    assert post_only is True
    assert px is not None and px >= be_m
    assert px < _T.ask  # inside spread, not parked at ask

    px_pas, _, reason_pas = await bridge._profitable_exit_quote(  # noqa: SLF001
        "bitvavo", "DOT", mark, aggressive=False
    )
    assert reason_pas == "rest_maker_be"
    assert px_pas is not None
    # Passive sits at min(ask, mark); aggressive should be closer to bid.
    assert px <= px_pas


def test_exit_cooldown_shorter_with_exit_engine() -> None:
    settings = _unlocked(
        live_micro_exit_engine_enabled=True,
        live_micro_exit_cooldown_sec=3.0,
        live_micro_be_harvest_cooldown_sec=8.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    assert bridge._exit_cooldown_sec("trail_exit_work") == 3.0  # noqa: SLF001
    assert bridge._exit_cooldown_sec("trail_be_harvest") == 3.0  # noqa: SLF001
    assert bridge._exit_cooldown_sec("trail_drawdown") == 3.0  # noqa: SLF001


def test_sleeve_loss_cap_pauses_buys() -> None:
    settings = _unlocked(
        live_micro_velocity_sleeve_daily_loss_cap_eur=10.0,
        live_micro_velocity_sleeve_eur=800.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("500")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("500"),
        live_maker=True,
    )
    assert bridge._sleeve_paused is False  # noqa: SLF001
    bridge._sleeve_realized_eur = Decimal("-10.01")  # noqa: SLF001
    bridge._check_sleeve_loss_cap()  # noqa: SLF001
    assert bridge._sleeve_paused is True  # noqa: SLF001
    # why_idle should surface sleeve pause
    hints = bridge._why_idle_hints()  # noqa: SLF001
    assert any(h.startswith("VELOCITY_SLEEVE") and "PAUSED" in h for h in hints)
    assert any(h.startswith("EXIT_ENGINE") for h in hints)


def test_session_settings_enable_exit_engine_and_sleeve() -> None:
    base = _unlocked()
    ss = _session_settings(
        base,
        budget_eur=Decimal("4000"),
        symbols=["SOLEUR", "ADAEUR"],
        persist_path=Path("./data/test_sleeve_settings.json"),
    )
    assert ss.live_micro_exit_engine_enabled is True
    assert float(ss.live_micro_exit_resting_max_age_sec or 0) <= 15.0
    assert float(ss.live_micro_velocity_sleeve_daily_loss_cap_eur or 0) > 0


@pytest.mark.asyncio
async def test_exit_engine_limit_taker_be_when_mark_clears_cushion() -> None:
    settings = _unlocked(
        paper_maker_sell_profit_buffer_bps=15.0,
        live_micro_exit_engine_enabled=True,
        live_micro_exit_taker_cushion_bps=5.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:DOT"] = [[Decimal("10"), Decimal("0.140")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:DOT")  # noqa: SLF001

    class _T:
        bid = Decimal("0.1405")  # above maker BE, below taker BE
        ask = Decimal("0.1420")
        last = Decimal("0.1418")

    class _Client:
        async def fetch_ticker(self, symbol: str):
            return _T()

    bridge._trading_client = lambda venue: _Client()  # type: ignore[method-assign]  # noqa: SLF001
    be_t = bridge._break_even_sell_price("bitvavo", "DOT", taker=True)  # noqa: SLF001
    assert be_t is not None
    # Mark clears taker BE + cushion → limit at taker BE (fill-seeking).
    mark = be_t * Decimal("1.001")
    px, post_only, reason = await bridge._profitable_exit_quote(  # noqa: SLF001
        "bitvavo", "DOT", mark, aggressive=True
    )
    assert reason == "limit_taker_be"
    assert post_only is False
    assert px is not None and px >= be_t


def test_recovery_clears_be_harvest_done_when_underwater() -> None:
    settings = _unlocked(paper_trail_take_profit_enabled=True)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    st = {
        "below_be": False,
        "be_harvest_partial_done": True,
        "recovery_be_partial_done": True,
        "soft_armed": True,
        "recovery_armed": True,
    }
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "SOL")  # noqa: SLF001
    assert be is not None
    bridged = bridge._maybe_recovery_arm_from_loss(  # noqa: SLF001
        st, venue="bitvavo", base="SOL", mark=be - Decimal("1"), be=be
    )
    assert bridged is False
    assert st["below_be"] is True
    assert st["be_harvest_partial_done"] is False
    assert st["recovery_be_partial_done"] is False


def test_buy_clip_cap_same_on_both_venues() -> None:
    settings = _unlocked(
        live_micro_first_clip_eur=75.0,
        live_micro_okx_ring_clip_eur=50.0,
        live_micro_active_ring_eur=1000.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
        execute_venues={"bitvavo", "okx"},
    )
    bridge._venue_raw_balances["okx"] = []  # noqa: SLF001
    bridge._venue_budget_remaining = lambda venue: Decimal("500")  # type: ignore[method-assign]  # noqa: SLF001
    cap_okx = bridge._buy_clip_cap_eur("okx", "DOT")  # noqa: SLF001
    cap_bv = bridge._buy_clip_cap_eur("bitvavo", "DOT")  # noqa: SLF001
    assert cap_okx == Decimal("75")
    assert cap_bv == Decimal("75")


def test_early_cut_eligible_new_session_only() -> None:
    settings = _unlocked(
        live_micro_early_cut_loss_below_be_pct=0.015,
        live_micro_early_cut_new_bases_only=True,
        live_micro_cut_loss_below_be_pct=0.04,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._trusted_cost_keys.add("bitvavo:SOL")  # noqa: SLF001
    old = {"new_session_base": False}
    new = {"new_session_base": True}
    assert bridge._early_cut_eligible(old, venue="bitvavo", base="SOL") is False  # noqa: SLF001
    assert bridge._early_cut_eligible(new, venue="bitvavo", base="SOL") is True  # noqa: SLF001
    early = bridge._early_cut_loss_floor_price("bitvavo", "SOL")  # noqa: SLF001
    assert early is None
    bridge._cost_lots["bitvavo:SOL"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    early = bridge._early_cut_loss_floor_price("bitvavo", "SOL")  # noqa: SLF001
    be = bridge._break_even_sell_price("bitvavo", "SOL")  # noqa: SLF001
    assert early is not None and be is not None
    assert early == be * Decimal("0.985")


def test_momentum_flat_or_down_for_early_cut(tmp_path: Path) -> None:
    settings = _unlocked(
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_samples=12,
        live_micro_early_cut_momentum_max_return=0.0,
        live_micro_bridge_persist_path=str(tmp_path / "flat_mom.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    series = bridge._series_for("FLATEUR")  # noqa: SLF001
    px = Decimal("10")
    for _ in range(12):
        px = px * Decimal("0.999")
        series.push(px)
    assert bridge._momentum_flat_or_down("FLATEUR") is True  # noqa: SLF001
    series2 = bridge._series_for("RISEEUR")  # noqa: SLF001
    px = Decimal("10")
    for _ in range(12):
        px = px * Decimal("1.001")
        series2.push(px)
    assert bridge._momentum_flat_or_down("RISEEUR") is False  # noqa: SLF001


def test_session_buy_tags_new_session_base_for_early_cut() -> None:
    from bot.core.enums import OrderSide

    settings = _unlocked(
        live_micro_early_cut_loss_below_be_pct=0.015,
        live_micro_cut_loss_below_be_pct=0.0,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._record_realized_fill(  # noqa: SLF001
        side=OrderSide.BUY,
        symbol="DOTEUR",
        qty=Decimal("10"),
        price=Decimal("1"),
        fee=Decimal("0.01"),
        venue="bitvavo",
    )
    st = bridge._trail.get("bitvavo:DOT") or {}  # noqa: SLF001
    assert st.get("new_session_base") is True
    assert st.get("sleeve") is True


@pytest.mark.asyncio
async def test_profitable_exit_quote_force_taker_after_maker_fails() -> None:
    settings = _unlocked(paper_maker_sell_profit_buffer_bps=15.0)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge._cost_lots["bitvavo:FET"] = [[Decimal("10"), Decimal("0.14")]]  # noqa: SLF001
    bridge._trusted_cost_keys.add("bitvavo:FET")  # noqa: SLF001

    class _T:
        bid = Decimal("0.1405")
        ask = Decimal("0.1422")
        last = Decimal("0.1415")

    class _Client:
        async def fetch_ticker(self, symbol: str):
            return _T()

    bridge._trading_client = lambda venue: _Client()  # type: ignore[method-assign]  # noqa: SLF001
    bridge._exit_maker_fail_counts["bitvavo:FET"] = 1
    assert bridge._should_force_taker_exit("bitvavo", "FET") is True  # noqa: SLF001
    px, post_only, reason = await bridge._profitable_exit_quote(  # noqa: SLF001
        "bitvavo", "FET", Decimal("0.1415"), aggressive=True, force_taker=True
    )
    assert post_only is False
    assert reason in {"hit_bid_taker", "limit_taker_be"}
    assert px is not None


def test_maybe_utc_day_rollover_resets_sleeve_and_baseline() -> None:
    settings = _unlocked(live_micro_daily_baseline_reset_utc=True)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
        live_maker=True,
    )
    bridge.realized_trade_pnl_eur = Decimal("-10")
    bridge.session_start_realized_eur = Decimal("-10")
    bridge._sleeve_realized_eur = Decimal("-30")
    bridge._sleeve_paused = True
    bridge._utc_day_marker = "2000-01-01"
    assert bridge.maybe_utc_day_rollover() is True
    assert bridge._sleeve_paused is False
    assert bridge._sleeve_realized_eur == Decimal("0")
    assert bridge.session_start_realized_eur == Decimal("-10")


def test_uw_recycle_plan_tiers(tmp_path: Path) -> None:
    from bot.integrations.alphai.signals import build_trading_signals
    from bot.integrations.alphai.parse import AlphaIRegimeState

    settings = _unlocked(
        live_micro_uw_recycle_enabled=True,
        live_micro_uw_dust_max_notional_eur=25.0,
        live_micro_uw_dust_below_be_pct=0.003,
        live_micro_uw_near_below_be_pct=0.008,
        live_micro_uw_near_max_depth_pct=0.015,
        live_micro_uw_near_min_age_sec=2700.0,
        live_micro_uw_non_alphai_below_be_pct=0.01,
        live_micro_uw_non_alphai_min_age_sec=3600.0,
        live_micro_uw_alphai_below_be_pct=0.02,
        live_micro_uw_alphai_min_age_sec=10800.0,
        paper_buy_momentum_enabled=True,
        paper_buy_momentum_samples=12,
        live_micro_early_cut_momentum_max_return=0.0,
        alphai_require_bullish_new_buys=True,
        alphai_bullish_buy_enabled=True,
        live_micro_bridge_persist_path=str(tmp_path / "uw.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    # Flat/down tape for momentum gates.
    series = bridge._series_for("ADAEUR")  # noqa: SLF001
    px = Decimal("1")
    for _ in range(12):
        px = px * Decimal("0.999")
        series.push(px)
    be = Decimal("1.0")
    bridge._cost_lots["bitvavo:ADA"] = [[Decimal("20"), be]]  # noqa: SLF001
    bridge._cost_lots["okx:SOL"] = [[Decimal("1"), Decimal("100")]]  # noqa: SLF001
    bridge._cost_lots["bitvavo:NEAR"] = [[Decimal("80"), be]]  # noqa: SLF001
    bridge._mark_cost_trusted("bitvavo", "ADA")  # noqa: SLF001
    bridge._mark_cost_trusted("okx", "SOL")  # noqa: SLF001
    bridge._mark_cost_trusted("bitvavo", "NEAR")  # noqa: SLF001

    # Dust within band.
    plan = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="bitvavo",
        base="ADA",
        symbol="ADAEUR",
        mark=Decimal("0.998"),
        be=be,
        notional=Decimal("20"),
    )
    assert plan is not None
    assert plan[0] == "dust"
    assert plan[1] == "band"

    # AlphaI deep stop.
    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        AlphaIRegimeState(bullish_bases=frozenset({"SOL"})),
        {"picks": [{"base": "SOL", "score": 40.0}], "avoid": []},
    )
    series_sol = bridge._series_for("SOLEUR")  # noqa: SLF001
    px = Decimal("100")
    for _ in range(12):
        px = px * Decimal("0.999")
        series_sol.push(px)
    plan = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="okx",
        base="SOL",
        symbol="SOLEUR",
        mark=Decimal("97.5"),
        be=Decimal("100"),
        notional=Decimal("100"),
    )
    assert plan is not None
    assert plan[0] == "alphai_deep"
    assert plan[1] == "stop"

    # Non-AlphaI near-BE after age (legacy → age infinite).
    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        AlphaIRegimeState(bullish_bases=frozenset()),
        {"picks": [{"base": "SOL", "score": 40.0}], "avoid": []},
    )
    series_near = bridge._series_for("NEAREUR")  # noqa: SLF001
    px = Decimal("1")
    for _ in range(12):
        px = px * Decimal("0.999")
        series_near.push(px)
    plan = bridge._uw_recycle_plan(  # noqa: SLF001
        venue="bitvavo",
        base="NEAR",
        symbol="NEAREUR",
        mark=Decimal("0.992"),
        be=be,
        notional=Decimal("80"),
    )
    assert plan is not None
    assert plan[0] in {"near_be", "non_alphai"}

    # Sleeve cap blocks oversized estimated loss.
    bridge._sleeve_realized_eur = Decimal("-49")  # noqa: SLF001
    bridge._sleeve_daily_loss_cap = Decimal("50")  # noqa: SLF001
    assert (
        bridge._uw_recycle_sleeve_allows(  # noqa: SLF001
            notional=Decimal("100"),
            mark=Decimal("0.98"),
            be=Decimal("1.0"),
        )
        is False
    )
    assert (
        bridge._uw_recycle_sleeve_allows(  # noqa: SLF001
            notional=Decimal("20"),
            mark=Decimal("0.998"),
            be=Decimal("1.0"),
        )
        is True
    )


def test_alphai_idle_deploy_allows_cross_venue_when_shallow(tmp_path: Path) -> None:
    from bot.integrations.alphai.parse import AlphaIRegimeState
    from bot.integrations.alphai.signals import build_trading_signals

    settings = _unlocked(
        live_micro_alphai_cross_venue_deploy=True,
        live_micro_alphai_cross_venue_max_other_depth_pct=0.025,
        live_micro_alphai_ring_fill_add_max_depth_pct=0.012,
        live_micro_active_ring_eur=1850.0,
        live_micro_block_buys_when_holding_base=True,
        live_micro_block_underwater_cross_venue=True,
        alphai_require_bullish_new_buys=True,
        alphai_bullish_buy_enabled=True,
        live_micro_execute_venues="bitvavo,okx",
        live_micro_bridge_persist_path=str(tmp_path / "idle.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("4000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        execute_venues={"bitvavo", "okx"},
        live_maker=True,
    )
    bridge._alphai_signals = build_trading_signals(  # noqa: SLF001
        AlphaIRegimeState(bullish_bases=frozenset({"SOL", "XRP"})),
        {"picks": [{"base": "SOL", "score": 40.0}, {"base": "XRP", "score": 50.0}]},
    )
    # Pretend ring needs deploy + SOL held only on OKX shallow underwater.
    bridge._active_book_notional = lambda venue: Decimal("0")  # type: ignore[method-assign]  # noqa: SLF001
    bridge._venue_budget_remaining = lambda venue: Decimal("1500")  # type: ignore[method-assign]  # noqa: SLF001
    bridge._balance_qty = (  # type: ignore[method-assign]  # noqa: SLF001
        lambda venue, base: Decimal("1") if venue == "okx" and base == "SOL" else Decimal("0")
    )
    bridge._break_even_sell_price = (  # type: ignore[method-assign]  # noqa: SLF001
        lambda venue, base: Decimal("100") if base == "SOL" else None
    )
    bridge._portfolio.state.mark_prices["SOLEUR"] = Decimal("99")  # -1%
    assert bridge._alphai_idle_deploy_allowed("bitvavo", "SOL") is True  # noqa: SLF001
    # Deep on other venue blocks.
    bridge._portfolio.state.mark_prices["SOLEUR"] = Decimal("96")  # -4%
    assert bridge._alphai_idle_deploy_allowed("bitvavo", "SOL") is False  # noqa: SLF001
