"""Tests for full-bot micro session bridge (no real exchange calls)."""

from __future__ import annotations

import json
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
    assert cfg.arbitrage_max_emits_per_cycle == 6
    assert cfg.paper_maker_max_open_quotes == 12
    assert cfg.live_micro_execute_venues == "bitvavo"
    dual = _session_settings(
        Settings(live_micro_execute_venues="bitvavo,okx"),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR"],
        persist_path=tmp_path / "dual.json",
    )
    # Aggregate equity ~€4k must still size clips near the €150 ceiling.
    assert dual.arbitrage_position_pct == 3.75
    assert dual.arbitrage_max_emits_per_cycle == 6
    assert dual.live_micro_max_open_orders == 8
    assert dual.live_micro_max_open_orders_per_venue == 8
    assert cfg.live_micro_cross_venue_enabled is True
    assert "EURUSDT" in cfg.market_data_symbols
    assert "SOLUSDT" in cfg.market_data_symbols
    assert cfg.paper_venue_inventory is True
    assert cfg.paper_max_holding_sec == 0.0
    assert cfg.paper_maker_allow_buy_only is True
    assert cfg.paper_maker_one_leg_exit is False
    assert cfg.paper_inventory_ask_improve_bps == 2.0
    assert cfg.paper_inventory_buy_dip_bps >= 2.0
    assert cfg.paper_ladder_buy_pcts.startswith("0,")
    assert cfg.paper_maker_sell_profit_buffer_bps >= 10.0
    assert cfg.paper_dust_exit_slack_bps == 0.0
    assert cfg.paper_trail_take_profit_enabled is True
    assert cfg.paper_trail_arm_gain_pct == 0.06
    assert cfg.paper_trail_drawdown_pct == 0.03
    assert cfg.paper_trail_partial_enabled is True
    assert cfg.paper_trail_partial_pct == 0.40
    assert cfg.paper_trail_soft_arm_pct == 0.009
    assert cfg.paper_trail_hard_arm_pct == 0.06
    assert cfg.paper_trail_session_buys_only is False
    assert cfg.paper_trail_atr_enabled is False
    assert cfg.live_disable_research_hooks is True
    assert cfg.paper_buy_momentum_enabled is False
    assert cfg.live_micro_max_per_corr_group == 3
    assert cfg.paper_daily_kill_eur == 50.0
    assert cfg.paper_ladder_buy_enabled is True
    assert cfg.paper_time_stop_enabled is True
    assert cfg.paper_dust_policy == "top_up_or_exit"
    assert cfg.paper_regime_block_buys is True
    assert cfg.paper_maker_min_net_return >= 0.0010
    assert cfg.paper_maker_min_notional_eur >= 55.0
    assert cfg.max_simultaneous_positions == 5
    assert cfg.live_micro_max_alt_bases == 5
    assert cfg.live_micro_max_open_orders == 8
    assert cfg.live_micro_max_open_orders_per_venue == 8
    assert cfg.live_micro_resting_max_age_sec >= 480.0
    assert cfg.paper_min_alt_inventory_pct >= 18.0
    assert cfg.paper_max_alt_inventory_pct <= 45.0
    assert cfg.paper_trail_soft_partial_pct == 0.0
    assert "ADA" in (cfg.live_micro_okx_deploy_bases or "")
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


def test_trail_ignores_peak_spike_after_soft_arm() -> None:
    settings = _unlocked(
        paper_trail_take_profit_enabled=True,
        paper_trail_soft_arm_pct=0.009,
        paper_trail_soft_drawdown_pct=0.006,
        paper_trail_hard_arm_pct=0.06,
        paper_trail_hard_drawdown_pct=0.03,
        paper_trail_atr_enabled=False,
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
    assert cfg.paper_trail_drawdown_pct == 0.03
    assert cfg.paper_trail_soft_arm_pct == 0.009
    assert cfg.paper_trail_hard_arm_pct == 0.06
    assert cfg.paper_trail_partial_pct == 0.40
    assert cfg.live_micro_max_notional_eur <= 150.0
    assert cfg.paper_markout_enabled is True
    assert cfg.live_disable_research_hooks is True
    assert cfg.max_drawdown_percent == 12.0
    assert cfg.live_micro_reset_drawdown_on_start is True


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
