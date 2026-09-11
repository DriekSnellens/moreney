"""Tests for the live volatile midcap sleeve (dry-run manager path)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.live.momentum_volatile_runner import (
    _read_volatile_flag,
    _volatile_flag_path,
    get_volatile_desk_manager,
    reset_volatile_desk_manager,
    volatile_desk_config,
    volatile_desk_flagged_running,
    volatile_shadow_from_settings,
)


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_volatile_desk_manager()
    yield
    reset_volatile_desk_manager()


def test_volatile_shadow_from_settings_applies_book_and_clip():
    settings = Settings(
        momentum_volatile_book_eur=500.0,
        momentum_volatile_clip_eur=400.0,
        momentum_volatile_max_positions=1,
    )
    shadow = volatile_shadow_from_settings(settings)
    assert shadow.book_eur == 500.0
    assert shadow.clip_eur == 400.0
    assert shadow.max_positions == 1
    cfg = volatile_desk_config(shadow)
    assert cfg.hard_stop_pct == shadow.hard_stop_pct
    assert cfg.max_positions == 1
    assert cfg.book_eur == 500.0
    # Volatile hours stay independent of core desk.
    assert tuple(cfg.decision_hours_utc) == (7, 13, 16)


@pytest.mark.asyncio
async def test_volatile_manager_requires_enabled_flag(tmp_path: Path):
    settings = Settings(
        momentum_volatile_enabled=False,
        momentum_volatile_state_path=str(tmp_path / "state.json"),
        momentum_volatile_ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    mgr = get_volatile_desk_manager()
    res = await mgr.start(settings=settings, dry_run=True)
    assert res["started"] is False
    assert "enabled" in str(res.get("reason", "")).lower()


@pytest.mark.asyncio
async def test_volatile_manager_dry_run_start_stop_writes_own_flag(tmp_path: Path):
    settings = Settings(
        momentum_volatile_enabled=True,
        momentum_volatile_state_path=str(tmp_path / "state.json"),
        momentum_volatile_ledger_path=str(tmp_path / "ledger.jsonl"),
        momentum_volatile_venues="bitvavo",
    )
    mgr = get_volatile_desk_manager()
    res = await mgr.start(settings=settings, dry_run=True, venue="bitvavo")
    assert res.get("started") is True
    assert mgr.running() is True
    flag_path = _volatile_flag_path(settings.momentum_volatile_state_path)
    assert flag_path.name == "momentum_volatile_running.json"
    assert flag_path.exists()
    flag = _read_volatile_flag(settings.momentum_volatile_state_path)
    assert flag and flag.get("running") is True
    assert volatile_desk_flagged_running(settings) is True
    # Must not write the core desk flag name.
    assert not (tmp_path / "momentum_desk_running.json").exists()

    status = mgr.status()
    assert status.get("desk") == "momentum_volatile"
    assert status.get("dry_run") is True

    stopped = await mgr.stop()
    assert stopped.get("stopped") is True
    assert mgr.running() is False
    flag_after = _read_volatile_flag(settings.momentum_volatile_state_path)
    assert flag_after and flag_after.get("running") is False
