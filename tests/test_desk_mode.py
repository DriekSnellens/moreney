"""Tests for auto desk mode (CERTAINTY ↔ VELOCITY)."""

from __future__ import annotations

from bot.live.desk_mode import (
    CERTAINTY_OVERLAYS,
    DeskMode,
    DeskModeInputs,
    VELOCITY_OVERLAYS,
    classify_desk_mode,
)


def test_no_confirmed_picks_stays_certainty() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(playbook="TREND", confirmed_pick_count=0),
        min_hold_sec=0,
    )
    assert decision.mode == DeskMode.CERTAINTY
    assert "no_confirmed_picks" in decision.reasons
    assert decision.overlays.get("winner_add_enabled") is False
    assert decision.overlays.get("allow_ring_expand_above_baseline") is False
    # CERTAINTY must not force ring (playbook may shrink for pre-crash).
    assert "active_ring_eur" not in decision.overlays


def test_confirmed_picks_enter_velocity() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="TREND",
            confirmed_pick_count=1,
            best_confirm=0.70,
            best_conviction=0.40,
            satellite_eur=700.0,
            certainty_ring_eur=350.0,
            sell_fills_last_60m=2,
        ),
        min_hold_sec=0,
    )
    assert decision.mode == DeskMode.VELOCITY
    assert "confirmed_picks" in decision.reasons
    assert decision.overlays.get("allow_ring_expand_above_baseline") is True
    assert decision.overlays.get("winner_add_enabled") is True
    ring = float(decision.overlays.get("active_ring_eur") or 0)
    assert ring >= 350.0
    assert ring <= 700.0


def test_adverse_hard_exits_velocity_despite_hysteresis() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="ADVERSE",
            confirmed_pick_count=2,
            best_confirm=0.80,
            best_conviction=0.50,
        ),
        current=DeskMode.VELOCITY,
        held_sec=60.0,
        min_hold_sec=900.0,
    )
    assert decision.mode == DeskMode.CERTAINTY
    assert "playbook_adverse" in decision.reasons


def test_lost_picks_hard_exit_velocity() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="TREND",
            confirmed_pick_count=0,
            best_confirm=0.0,
            best_conviction=0.0,
        ),
        current=DeskMode.VELOCITY,
        held_sec=30.0,
        min_hold_sec=900.0,
    )
    assert decision.mode == DeskMode.CERTAINTY
    assert "no_confirmed_picks" in decision.reasons


def test_hysteresis_holds_velocity_briefly() -> None:
    # Soft reason to leave VELOCITY: confirm dips but still has a pick count.
    # Actually confirm_below_floor returns CERTAINTY raw — but confirmed_pick_count
    # still >= 1. Hard exit only when confirmed_pick_count < min. Confirm dip is soft.
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="TREND",
            confirmed_pick_count=1,
            best_confirm=0.40,  # below floor → raw CERTAINTY
            best_conviction=0.40,
        ),
        current=DeskMode.VELOCITY,
        held_sec=60.0,
        min_hold_sec=900.0,
        min_confirm=0.55,
    )
    # Soft exit: hysteresis keeps VELOCITY until min_hold.
    assert decision.mode == DeskMode.VELOCITY
    assert any(r.startswith("hysteresis_hold_") for r in decision.reasons)


def test_pre_crash_forces_certainty() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="FLAT",
            pre_crash=True,
            confirmed_pick_count=2,
            best_confirm=0.80,
            best_conviction=0.50,
        ),
        min_hold_sec=0,
    )
    assert decision.mode == DeskMode.CERTAINTY
    assert "pre_crash" in decision.reasons


def test_underwater_book_forces_certainty() -> None:
    decision = classify_desk_mode(
        DeskModeInputs(
            playbook="TREND",
            confirmed_pick_count=2,
            best_confirm=0.80,
            best_conviction=0.50,
            underwater_bag_count=3,
            underwater_notional_eur=150.0,
        ),
        min_hold_sec=0,
        max_underwater_eur=120.0,
    )
    assert decision.mode == DeskMode.CERTAINTY
    assert "underwater_book" in decision.reasons


def test_overlay_constants_survive() -> None:
    assert CERTAINTY_OVERLAYS["be_harvest_min_gain_pct"] == 0.006
    assert VELOCITY_OVERLAYS["be_harvest_min_gain_pct"] == 0.008
    assert VELOCITY_OVERLAYS["allow_ring_expand_above_baseline"] is True
