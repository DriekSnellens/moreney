"""Tests for the spot buy&hold sleeve (soft book + manager)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.live.momentum_hold_runner import (
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
