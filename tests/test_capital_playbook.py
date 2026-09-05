"""Tests for capital playbook router (TREND / FLAT / ADVERSE)."""

from __future__ import annotations

from bot.live.capital_playbook import (
    CapitalPlaybook,
    CapitalPlaybookInputs,
    PLAYBOOK_OVERLAYS,
    PRE_CRASH_FLAT_OVERLAYS,
    classify_capital_playbook,
)


def test_flat_day_low_velocity_classifies_flat() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=0,
            sell_fills_last_180m=2,
            near_be_stuck_count=3,
            near_be_notional_eur=500.0,
            median_mom=0.0002,
            winnable_gap_eur=5.0,
            time_stop_below_be_skips=80,
            inventory_mtm_eur=900.0,
        ),
        current=None,
        held_sec=0,
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.FLAT
    assert decision.pre_crash is True
    assert "pre_crash_derisk" in decision.reasons
    assert decision.overlays.get("active_ring_eur") == 900.0
    assert decision.overlays.get("block_new_buys") is True


def test_velocity_alone_does_not_force_flat() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=0,
            sell_fills_last_180m=0,
            inventory_mtm_eur=0.0,
        ),
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.TREND
    assert "flat_unconfirmed" in decision.reasons
    assert decision.pre_crash is False


def test_pre_crash_interrupts_trend_hysteresis() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=0,
            inventory_mtm_eur=900.0,
            near_be_stuck_count=3,
            near_be_notional_eur=700.0,
            median_mom=0.0001,
        ),
        current=CapitalPlaybook.TREND,
        held_sec=120.0,
        min_hold_sec=900.0,
    )
    assert decision.playbook == CapitalPlaybook.FLAT
    assert decision.pre_crash is True
    assert decision.overlays == PRE_CRASH_FLAT_OVERLAYS


def test_pre_crash_high_inventory_flat_tape() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=1,
            inventory_mtm_eur=800.0,
            median_mom=0.0003,
            near_be_stuck_count=0,
        ),
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.FLAT
    assert decision.pre_crash is True


def test_adverse_still_beats_pre_crash() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=0,
            inventory_mtm_eur=900.0,
            near_be_stuck_count=3,
            underwater_bag_count=4,
            underwater_notional_eur=200.0,
            alphai_macro_active=True,
        ),
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.ADVERSE
    assert decision.pre_crash is False


def test_adverse_macro_and_underwater() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            alphai_macro_active=True,
            underwater_bag_count=4,
            underwater_notional_eur=200.0,
            sell_fills_last_60m=1,
        ),
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.ADVERSE
    assert decision.overlays.get("block_new_buys") is True
    assert decision.overlays.get("alphai_strong_clip_eur") == 220.0


def test_trend_healthy_velocity() -> None:
    decision = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=5,
            sell_fills_last_180m=12,
            median_mom=0.002,
            near_be_stuck_count=0,
        ),
        min_hold_sec=0,
    )
    assert decision.playbook == CapitalPlaybook.TREND
    assert decision.overlays == {}


def test_hysteresis_holds_flat_unless_adverse_or_pre_crash() -> None:
    held = classify_capital_playbook(
        CapitalPlaybookInputs(
            sell_fills_last_60m=5,
            median_mom=0.002,
        ),
        current=CapitalPlaybook.FLAT,
        held_sec=120.0,
        min_hold_sec=900.0,
    )
    assert held.playbook == CapitalPlaybook.FLAT
    assert any("hysteresis" in r for r in held.reasons)

    interrupt = classify_capital_playbook(
        CapitalPlaybookInputs(
            alphai_macro_active=True,
            underwater_bag_count=3,
            underwater_notional_eur=150.0,
        ),
        current=CapitalPlaybook.FLAT,
        held_sec=120.0,
        min_hold_sec=900.0,
    )
    assert interrupt.playbook == CapitalPlaybook.ADVERSE


def test_overlays_defined_for_all_playbooks() -> None:
    assert CapitalPlaybook.TREND in PLAYBOOK_OVERLAYS
    assert CapitalPlaybook.FLAT in PLAYBOOK_OVERLAYS
    assert CapitalPlaybook.ADVERSE in PLAYBOOK_OVERLAYS
    assert PLAYBOOK_OVERLAYS[CapitalPlaybook.FLAT]["exit_taker_cushion_bps"] < 5
    assert PRE_CRASH_FLAT_OVERLAYS["active_ring_eur"] < PLAYBOOK_OVERLAYS[
        CapitalPlaybook.FLAT
    ]["active_ring_eur"]


def test_adverse_and_precrash_speed_up_recycle_and_block_deploy() -> None:
    adverse = PLAYBOOK_OVERLAYS[CapitalPlaybook.ADVERSE]
    assert adverse["uw_alphai_min_age_sec"] <= 900.0
    assert adverse["uw_alphai_below_be_pct"] <= 0.010
    # Soft-ADVERSE: keep AlphaI sleeve deployable + hold winners while rising.
    assert adverse["active_ring_eur"] >= 1200.0
    assert adverse["alphai_strong_clip_eur"] >= 200.0
    assert adverse["be_harvest_min_gain_pct"] >= 0.006
    assert adverse["be_harvest_partial_pct"] <= 0.50
    assert adverse["trail_hold_rising_n"] >= 2
    assert adverse["alphai_idle_deploy_blocked"] is False
    assert adverse["alphai_intraday_require_rising"] is True
    assert adverse["early_cut_loss_below_be_pct"] <= 0.008
    assert adverse["block_new_buys"] is True  # non-sleeve new bases still blocked

    pre = PRE_CRASH_FLAT_OVERLAYS
    assert pre["uw_alphai_min_age_sec"] <= 600.0
    assert pre["active_ring_eur"] >= 800.0
    assert pre["alphai_strong_clip_eur"] >= 150.0
    assert pre["trail_hold_rising_n"] >= 1
    assert pre["alphai_idle_deploy_blocked"] is False
    assert pre["block_new_buys"] is True


def test_adverse_keeps_alphai_sleeve_capacity() -> None:
    """Soft-ADVERSE must not zero the AlphaI deploy sleeve."""
    adverse = PLAYBOOK_OVERLAYS[CapitalPlaybook.ADVERSE]
    trend_ring = 1850.0  # micro session baseline active ring
    assert adverse["active_ring_eur"] >= 1200.0
    assert adverse["active_ring_eur"] < trend_ring
    assert adverse["alphai_cross_venue_deploy"] is True
