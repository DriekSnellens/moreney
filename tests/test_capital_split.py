"""Tests for core/satellite capital split planning."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bot.core.config import Settings
from bot.live.capital_split import resolve_capital_split, split_session_overrides
from bot.live.micro_session import _session_settings


def test_resolve_capital_split_default_cash_core() -> None:
    plan = resolve_capital_split(2000.0, n_venues=2, enabled=True)
    assert plan.enabled is True
    assert plan.core_mode == "cash"
    assert plan.core_eur == 1300.0
    assert plan.satellite_eur == 700.0
    assert plan.ring_per_venue_eur == 350.0
    assert plan.sleeve_daily_loss_cap_eur == 21.0
    assert plan.cut_loss_below_be_pct == 0.025
    assert plan.long_hold_bases == ""
    assert plan.max_alt_inventory_pct == 35.0


def test_resolve_capital_split_btc_eth_core_marks_long_hold() -> None:
    plan = resolve_capital_split(
        2000.0, n_venues=1, enabled=True, core_mode="btc_eth"
    )
    assert plan.core_mode == "btc_eth"
    assert plan.long_hold_bases == "BTC,ETH"
    assert plan.ring_per_venue_eur == 700.0


def test_session_settings_apply_capital_split(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(live_micro_execute_venues="bitvavo,okx"),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR", "ADAEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.live_micro_capital_split_enabled is True
    assert float(cfg.live_micro_core_eur) == 1300.0
    assert float(cfg.live_micro_satellite_eur) == 700.0
    assert float(cfg.live_micro_active_ring_eur) == 350.0
    assert float(cfg.live_micro_velocity_sleeve_eur) == 700.0
    assert float(cfg.live_micro_velocity_sleeve_daily_loss_cap_eur) == 21.0
    assert float(cfg.paper_max_alt_inventory_pct) == 35.0
    assert float(cfg.live_micro_cut_loss_below_be_pct) == 0.025
    assert float(cfg.live_micro_early_cut_loss_below_be_pct) == 0.01
    assert (cfg.live_micro_long_hold_bases or "") == ""
    assert float(cfg.live_micro_first_clip_eur) <= 120.0


def test_session_settings_can_disable_split(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(
            live_micro_execute_venues="bitvavo,okx",
            live_micro_capital_split_enabled=False,
        ),
        budget_eur=Decimal("2000"),
        symbols=["SOLEUR"],
        persist_path=tmp_path / "nosplit.json",
    )
    # Legacy full-pocket path when split is off.
    assert cfg.live_micro_capital_split_enabled is False
    assert float(cfg.live_micro_active_ring_eur) == 1850.0
    assert float(cfg.live_micro_velocity_sleeve_eur) == 1850.0
    assert split_session_overrides(
        resolve_capital_split(2000.0, enabled=False)
    ) == {}
