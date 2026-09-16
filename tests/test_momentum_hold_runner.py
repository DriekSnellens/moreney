"""Tests for the spot buy&hold sleeve (soft book + manager)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.live.momentum_hold_runner import (
    HoldSleeveConfig,
    _hold_flag_path,
    _read_hold_flag,
    get_hold_desk_manager,
    hold_config_from_settings,
    hold_desk_config,
    hold_desk_flagged_running,
    hold_reserved_eur,
    parse_hold_bases,
    reset_hold_desk_manager,
)
from bot.live.momentum_runner import MomentumDeskRunner, RunnerOptions, desk_config_from_settings


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_hold_desk_manager()
    yield
    reset_hold_desk_manager()


def test_parse_hold_bases_config_universe():
    assert parse_hold_bases("BTC") == ("BTC",)
    assert parse_hold_bases("btc, eth") == ("BTC", "ETH")
    assert parse_hold_bases("") == ("BTC",)


def test_hold_desk_config_enables_mom_style_trail():
    hold = HoldSleeveConfig(
        book_eur=4_000.0,
        bases=("BTC",),
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.25,
    )
    cfg = hold_desk_config(hold)
    assert cfg.trail_pct == 0.03
    assert cfg.trail_tight_after == 0.04
    assert cfg.trail_tight_pct == 0.02
    assert cfg.early_stop_pct == 0.0
    assert cfg.hard_stop_pct == 0.25
    assert cfg.time_exit_hours >= 1e6


def test_hold_config_from_settings_book_and_bases():
    settings = Settings(
        momentum_hold_book_eur=15_000.0,
        momentum_hold_bases="BTC,ETH",
    )
    hold = hold_config_from_settings(settings)
    assert hold.book_eur == 15_000.0
    assert hold.bases == ("BTC", "ETH")
    cfg = hold_desk_config(hold)
    assert cfg.book_eur == 15_000.0
    assert cfg.universe == ("BTC", "ETH")
    assert cfg.decision_hours_utc == ()
    assert cfg.trail_pct == 0.03


def test_hold_config_trail_from_settings():
    settings = Settings(
        momentum_hold_book_eur=4_000.0,
        momentum_hold_trail_pct=0.03,
        momentum_hold_trail_tight_after=0.04,
        momentum_hold_trail_tight_pct=0.02,
        momentum_hold_refill_cooldown_sec=3_600.0,
    )
    hold = hold_config_from_settings(settings)
    assert hold.trail_pct == 0.03
    assert hold.trail_tight_after == 0.04
    assert hold.trail_tight_pct == 0.02
    assert hold.refill_cooldown_sec == 3_600.0


@pytest.mark.asyncio
async def test_hold_trail_exit_on_closed_bar(tmp_path: Path):
    """Hold sleeve uses the same evaluate_exit trail as the momentum desk."""
    from bot.live.momentum_desk import Position
    from bot.live.momentum_hold_runner import HoldDeskRunner, HoldSleeveConfig
    from bot.live.momentum_runner import Holding, RunnerOptions

    hold = HoldSleeveConfig(
        book_eur=2_000.0,
        bases=("BTC",),
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.25,
        refill_cooldown_sec=3_600.0,
        rebalance_interval_sec=86_400.0,
    )
    cfg = hold_desk_config(hold)
    clock = {"t": 1_700_000_000.0}

    class _Feed:
        async def candles(self, base: str, n: int):
            # Closed 15m bar: peak was 110, close 106 → -3.6% from peak → trail.
            bar_open_ms = int((clock["t"] * 1000) // 900_000 * 900_000) - 900_000
            return [[bar_open_ms, 110.0, 110.0, 105.0, 106.0, 1.0]]

        async def last_price(self, base: str):
            return 106.0

    async def _noop_sleep(_s: float) -> None:
        return None

    runner = HoldDeskRunner(
        cfg,
        None,
        hold=hold,
        options=RunnerOptions(
            venues=("bitvavo",),
            dry_run=True,
            state_path=str(tmp_path / "state.json"),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            mark_tick_sec=0.0,
            bar_close_grace_sec=0.0,
        ),
        feed=_Feed(),
        clock=lambda: clock["t"],
        sleep=_noop_sleep,
    )
    pos = Position(
        base="BTC",
        entry_price=100.0,
        quantity=10.0,
        notional_eur=1_000.0,
        opened_ms=int(clock["t"] * 1000) - 3_600_000,
        peak=110.0,
        venue="bitvavo",
    )
    runner.holdings = [Holding(pos=pos, holding_id="h1", last_bar_ms=0)]
    runner.marks["BTC"] = 106.0

    exits: list[str] = []

    async def _fake_exit(h, decision):
        exits.append(decision.reason)
        runner.holdings = [x for x in runner.holdings if x is not h]
        runner._arm_refill_cooldown(decision.reason)
        return None

    runner._exit = _fake_exit  # type: ignore[method-assign]
    await runner._manage_exits(int(clock["t"] * 1000))
    assert exits == ["trail"]
    assert runner.refill_blocked() is True
    blocked = await runner.fill_to_book()
    assert blocked.get("reason") == "refill_cooldown"


def test_hold_reserved_eur_when_enabled():
    settings = Settings(
        momentum_hold_enabled=True,
        momentum_hold_book_eur=10_000.0,
    )
    assert hold_reserved_eur(settings, deployed_eur=0.0) == 10_000.0
    assert hold_reserved_eur(settings, deployed_eur=4_000.0) == 6_000.0
    settings_off = Settings(momentum_hold_enabled=False, momentum_hold_book_eur=10_000.0)
    assert hold_reserved_eur(settings_off, deployed_eur=0.0) == 0.0


def test_core_route_entry_respects_hold_reserve(monkeypatch):
    settings = Settings(
        momentum_hold_enabled=True,
        momentum_hold_book_eur=3_000.0,
        momentum_desk_book_eur=0.0,
        momentum_desk_clip_eur=1_000.0,
    )
    monkeypatch.setattr(
        "bot.live.momentum_hold_runner.get_settings", lambda: settings
    )
    cfg = desk_config_from_settings(settings)
    runner = MomentumDeskRunner(cfg, None, options=RunnerOptions(venues=("bitvavo",)))
    # Fake live balances: €3500 cash, €3000 reserved for hold → €500 free.
    runner._gws = {"bitvavo": object()}  # noqa: SLF001
    runner.cash_by_venue = {"bitvavo": 3_500.0}
    route = runner._route_entry(1_000.0)  # noqa: SLF001
    assert route is not None
    venue, clip = route
    assert venue == "bitvavo"
    assert clip <= 500.0 + 1e-6
    # Fully reserved → no entry.
    runner.cash_by_venue = {"bitvavo": 2_900.0}
    assert runner._route_entry(500.0) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_hold_manager_requires_enabled_flag(tmp_path: Path):
    settings = Settings(
        momentum_hold_enabled=False,
        momentum_hold_state_path=str(tmp_path / "state.json"),
        momentum_hold_ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    mgr = get_hold_desk_manager()
    res = await mgr.start(settings=settings, dry_run=True)
    assert res["started"] is False
    assert "enabled" in str(res.get("reason", "")).lower()


@pytest.mark.asyncio
async def test_hold_manager_dry_run_start_stop(tmp_path: Path):
    settings = Settings(
        momentum_hold_enabled=True,
        momentum_hold_allow_live=False,
        momentum_hold_book_eur=5_000.0,
        momentum_hold_bases="BTC",
        momentum_hold_state_path=str(tmp_path / "state.json"),
        momentum_hold_ledger_path=str(tmp_path / "ledger.jsonl"),
        momentum_hold_venues="bitvavo",
    )
    mgr = get_hold_desk_manager()
    res = await mgr.start(settings=settings, dry_run=True, venue="bitvavo")
    assert res.get("started") is True
    assert mgr.running() is True
    flag_path = _hold_flag_path(settings.momentum_hold_state_path)
    assert flag_path.name == "momentum_hold_running.json"
    assert flag_path.exists()
    flag = _read_hold_flag(settings.momentum_hold_state_path)
    assert flag and flag.get("running") is True
    assert hold_desk_flagged_running(settings) is True
    status = mgr.status()
    assert status.get("desk") == "momentum_hold"
    assert status.get("dry_run") is True
    assert status.get("book_eur") == 5_000.0
    stopped = await mgr.stop()
    assert stopped.get("stopped") is True
    assert mgr.running() is False
