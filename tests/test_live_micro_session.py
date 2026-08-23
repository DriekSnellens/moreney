"""Tests for full-bot micro session bridge (no real exchange calls)."""

from __future__ import annotations

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
    assert cfg.live_micro_execute_venues == "bitvavo"
    assert cfg.live_micro_cross_venue_enabled is True
    assert "EURUSDT" in cfg.market_data_symbols
    assert "SOLUSDT" in cfg.market_data_symbols
    assert cfg.paper_venue_inventory is True
    assert cfg.paper_max_holding_sec == 0.0
    assert cfg.paper_maker_allow_buy_only is True
    assert cfg.paper_maker_one_leg_exit is False
    assert cfg.paper_inventory_ask_improve_bps == 0.0
    assert cfg.paper_inventory_buy_dip_bps >= 2.0
    assert cfg.paper_ladder_buy_pcts.startswith("0,")
    assert cfg.paper_maker_sell_profit_buffer_bps >= 15.0
    assert cfg.paper_dust_exit_slack_bps == 0.0
    assert cfg.paper_trail_take_profit_enabled is True
    assert cfg.paper_trail_arm_gain_pct == 0.06
    assert cfg.paper_trail_drawdown_pct == 0.03
    assert cfg.paper_trail_partial_enabled is True
    assert cfg.paper_trail_partial_pct == 0.25
    assert cfg.paper_trail_soft_arm_pct == 0.012
    assert cfg.paper_trail_hard_arm_pct == 0.06
    assert cfg.paper_trail_session_buys_only is False
    assert cfg.paper_trail_atr_enabled is False
    assert cfg.live_disable_research_hooks is True
    assert cfg.paper_buy_momentum_enabled is False
    assert cfg.live_micro_max_per_corr_group == 6
    assert cfg.paper_daily_kill_eur == 50.0
    assert cfg.paper_ladder_buy_enabled is True
    assert cfg.paper_time_stop_enabled is True
    assert cfg.paper_dust_policy == "top_up_or_exit"
    assert cfg.paper_regime_block_buys is True
    assert cfg.paper_maker_min_net_return >= 0.0010
    assert cfg.paper_maker_min_notional_eur >= 40.0
    assert cfg.max_simultaneous_positions == 8
    assert cfg.live_micro_max_alt_bases == 8
    assert cfg.live_micro_max_open_orders >= 12
    assert cfg.live_micro_resting_max_age_sec >= 480.0
    assert cfg.paper_min_alt_inventory_pct >= 8.0
    assert cfg.paper_max_alt_inventory_pct <= 30.0
    assert cfg.paper_markout_enabled is True
    assert cfg.paper_seed_usdt_pct == 0.0
    assert "BTCEUR" not in cfg.market_data_symbols
    # Liquid day-trade allowlist.
    from bot.live.micro_session import _liquid_symbols

    liquid = _liquid_symbols(Settings(), exclude_btc=True)
    assert len(liquid) >= 20
    assert "ETHEUR" in liquid
    assert "SOLEUR" in liquid
    assert "LINKEUR" in liquid
    assert "BNBEUR" not in liquid


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
async def test_bridge_mirrors_live_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _unlocked()
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
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.30"))  # noqa: SLF001
    assert st["newly_hard"] is True
    assert st["hard_armed"] is True
    bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.50"))  # noqa: SLF001
    # 12% off 1.50 = 1.32 — still holding under hard dd
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.33"))  # noqa: SLF001
    assert st["triggered"] is False
    st = bridge._trail_update_state("bitvavo", "ADA", cost=cost, mark=Decimal("1.32"))  # noqa: SLF001
    assert st["triggered"] is True


def test_trail_runner_drawdown_uses_12pct_in_session_settings(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(),
        budget_eur=Decimal("2024"),
        symbols=["ADAEUR"],
        persist_path=tmp_path / "t.json",
    )
    assert cfg.paper_trail_drawdown_pct == 0.03
    assert cfg.paper_trail_soft_arm_pct == 0.012
    assert cfg.paper_trail_hard_arm_pct == 0.06
    assert cfg.paper_trail_partial_pct == 0.25
    assert cfg.live_micro_max_notional_eur <= 150.0
    assert cfg.paper_markout_enabled is True
    assert cfg.live_disable_research_hooks is True


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


def test_session_lots_only_for_trail_cost() -> None:
    settings = _unlocked(paper_trail_session_buys_only=True)
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
    held = bridge._held_alt_bases()  # noqa: SLF001
    assert held >= {"ADA", "ATOM", "NEAR"}
    assert bridge._max_alt_bases == 3  # noqa: SLF001


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
