"""Auto desk mode — CERTAINTY vs VELOCITY for the AlphaI daytrader.

CERTAINTY (default): tight ring, no urgency, peak-fade harvest floors, survive
no-pick / chop tapes.

VELOCITY: expand ring toward satellite, allow winner-adds, Soft-ADVERSE-style
harvest — only when AlphaI has confirmed deployable picks and tape is not
adverse/pre-crash.

Detection is coin-agnostic: confirmed pick count / confirm / conviction /
playbook health / underwater book — never per-coin special cases.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeskMode(str, Enum):
    CERTAINTY = "CERTAINTY"
    VELOCITY = "VELOCITY"


@dataclass(frozen=True, slots=True)
class DeskModeInputs:
    """Features available at evaluation time (no I/O)."""

    playbook: str = "TREND"
    pre_crash: bool = False
    alphai_macro_active: bool = False
    confirmed_pick_count: int = 0
    best_confirm: float = 0.0
    best_conviction: float = 0.0
    underwater_bag_count: int = 0
    underwater_notional_eur: float = 0.0
    sell_fills_last_60m: int = 0
    median_mom: float | None = None
    satellite_eur: float = 700.0
    certainty_ring_eur: float = 350.0
    # Tape RS leaders (AlphaI-quiet days) and share of universe beating BTC.
    tape_confirmed_count: int = 0
    tape_breadth: float = 0.0


@dataclass(frozen=True, slots=True)
class DeskModeDecision:
    mode: DeskMode
    confidence: float
    reasons: tuple[str, ...]
    overlays: Mapping[str, Any] = field(default_factory=dict)


# CERTAINTY keeps session daytrader baselines (tight). Explicit overlays only
# where we want to reinforce survival vs accidental playbook expansion.
CERTAINTY_OVERLAYS: dict[str, Any] = {
    "winner_add_enabled": False,
    "be_harvest_min_gain_pct": 0.010,
    "be_harvest_partial_pct": 0.40,
    "allow_ring_expand_above_baseline": False,
    "daytrader_sleeve_urgency_enabled": False,
}

# VELOCITY expands deploy surface when confirmed picks exist.
# Ring grows toward satellite (applied in bridge; not capped to certainty baseline).
VELOCITY_OVERLAYS: dict[str, Any] = {
    "winner_add_enabled": True,
    "be_harvest_min_gain_pct": 0.012,
    "be_harvest_partial_pct": 0.40,
    "allow_ring_expand_above_baseline": True,
    "trail_hold_rising_n": 2,
    "alphai_intraday_require_rising": True,
    "daytrader_sleeve_urgency_enabled": False,  # still confirm-gated, not urgency spam
    "exit_taker_cushion_bps": 3.0,
}


def classify_desk_mode(
    inputs: DeskModeInputs,
    *,
    current: DeskMode | None = None,
    held_sec: float = 0.0,
    min_hold_sec: float = 900.0,
    min_confirm: float = 0.55,
    min_conviction: float = 0.25,
    min_confirmed_picks: int = 1,
    max_underwater_eur: float = 120.0,
    velocity_ring_fraction_of_satellite: float = 0.90,
    velocity_ring_mult_of_certainty: float = 2.0,
    velocity_sleeve_loss_cap_eur: float | None = 35.0,
) -> DeskModeDecision:
    """Classify CERTAINTY / VELOCITY with light hysteresis."""
    raw, confidence, reasons = _raw_classify(
        inputs,
        min_confirm=min_confirm,
        min_conviction=min_conviction,
        min_confirmed_picks=min_confirmed_picks,
        max_underwater_eur=max_underwater_eur,
    )
    mode = raw

    if current is not None and current != raw and held_sec < min_hold_sec:
        # Hard interrupts may leave VELOCITY immediately.
        hard_exit = raw == DeskMode.CERTAINTY and (
            inputs.pre_crash
            or inputs.alphai_macro_active
            or str(inputs.playbook).upper() == "ADVERSE"
            or (
                inputs.confirmed_pick_count < min_confirmed_picks
                and inputs.tape_confirmed_count < 2
            )
        )
        if not hard_exit:
            mode = current
            reasons = tuple([*reasons, f"hysteresis_hold_{current.value}"])
            confidence = min(confidence, 0.55)

    tape_led = (
        mode == DeskMode.VELOCITY
        and "tape_leaders" in reasons
        and "confirmed_picks" not in reasons
    )
    overlays = _overlays_for(
        mode,
        inputs,
        velocity_ring_fraction_of_satellite=velocity_ring_fraction_of_satellite,
        velocity_ring_mult_of_certainty=velocity_ring_mult_of_certainty,
        velocity_sleeve_loss_cap_eur=velocity_sleeve_loss_cap_eur,
        tape_led=tape_led,
    )
    return DeskModeDecision(
        mode=mode,
        confidence=float(confidence),
        reasons=tuple(reasons),
        overlays=overlays,
    )


def _raw_classify(
    inputs: DeskModeInputs,
    *,
    min_confirm: float,
    min_conviction: float,
    min_confirmed_picks: int,
    max_underwater_eur: float,
) -> tuple[DeskMode, float, list[str]]:
    reasons: list[str] = []
    pb = str(inputs.playbook or "TREND").upper()

    if inputs.alphai_macro_active:
        reasons.append("macro_active")
        return DeskMode.CERTAINTY, 0.9, reasons
    if inputs.pre_crash:
        reasons.append("pre_crash")
        return DeskMode.CERTAINTY, 0.92, reasons
    if pb == "ADVERSE":
        reasons.append("playbook_adverse")
        return DeskMode.CERTAINTY, 0.88, reasons
    if inputs.underwater_notional_eur >= max_underwater_eur and inputs.underwater_bag_count > 0:
        reasons.append("underwater_book")
        return DeskMode.CERTAINTY, 0.8, reasons
    if inputs.confirmed_pick_count < min_confirmed_picks:
        # Tape-led VELOCITY-lite: ≥2 RS leaders on a broad tape, TREND playbook,
        # not dumping. Ring expansion is damped in _overlays_for.
        if (
            inputs.tape_confirmed_count >= 2
            and inputs.tape_breadth >= 0.55
            and pb == "TREND"
            and (inputs.median_mom is None or inputs.median_mom >= -0.0005)
        ):
            reasons.append("tape_leaders")
            reasons.append("breadth_ok")
            conf = 0.62 + min(0.1, 0.03 * (inputs.tape_confirmed_count - 2))
            if inputs.tape_breadth >= 0.65:
                reasons.append("breadth_strong")
                conf += 0.05
            return DeskMode.VELOCITY, min(0.8, conf), reasons
        reasons.append("no_confirmed_picks")
        return DeskMode.CERTAINTY, 0.85, reasons
    if inputs.best_confirm < min_confirm:
        reasons.append("confirm_below_floor")
        return DeskMode.CERTAINTY, 0.82, reasons
    if inputs.best_conviction < min_conviction:
        reasons.append("conviction_below_floor")
        return DeskMode.CERTAINTY, 0.8, reasons
    if inputs.median_mom is not None and inputs.median_mom < -0.0015:
        reasons.append("tape_dumping")
        return DeskMode.CERTAINTY, 0.75, reasons

    reasons.append("confirmed_picks")
    if pb == "TREND":
        reasons.append("playbook_trend")
    elif pb == "FLAT":
        reasons.append("playbook_flat_ok")
    if inputs.sell_fills_last_60m >= 2:
        reasons.append("sell_velocity")
    if inputs.best_confirm >= 0.70:
        reasons.append("strong_confirm")
    conf = 0.7
    if inputs.confirmed_pick_count >= 2:
        conf += 0.1
    if inputs.best_confirm >= 0.70:
        conf += 0.08
    if inputs.sell_fills_last_60m >= 2:
        conf += 0.05
    return DeskMode.VELOCITY, min(0.95, conf), reasons


def _overlays_for(
    mode: DeskMode,
    inputs: DeskModeInputs,
    *,
    velocity_ring_fraction_of_satellite: float,
    velocity_ring_mult_of_certainty: float,
    velocity_sleeve_loss_cap_eur: float | None,
    tape_led: bool = False,
) -> dict[str, Any]:
    if mode == DeskMode.CERTAINTY:
        # Do not override ring here — capital playbook may shrink for FLAT /
        # pre-crash / ADVERSE. CERTAINTY only reinforces survival knobs.
        return dict(CERTAINTY_OVERLAYS)

    out = dict(VELOCITY_OVERLAYS)
    sat = max(float(inputs.satellite_eur or 0), 0.0)
    cert = max(float(inputs.certainty_ring_eur or 0), 0.0)
    # Tape-led expansion is damped (no headline backing): 0.75× of AlphaI path.
    damp = 0.75 if tape_led else 1.0
    target = 0.0
    if sat > 0:
        target = sat * float(velocity_ring_fraction_of_satellite) * damp
    if cert > 0:
        target = max(target, cert * float(velocity_ring_mult_of_certainty) * damp)
    if sat > 0 and target > 0:
        target = min(target, sat)
    if tape_led:
        # Tape entries: no winner-adds (no news catalyst to pyramid on).
        out["winner_add_enabled"] = False
    if target > 0:
        out["active_ring_eur"] = float(round(target, 2))
        out["ring_soft_max_active_eur"] = float(round(target, 2))
    if velocity_sleeve_loss_cap_eur is not None and velocity_sleeve_loss_cap_eur > 0:
        out["sleeve_daily_loss_cap_eur"] = float(velocity_sleeve_loss_cap_eur)
    # Slightly larger strong clips when expanding.
    out["alphai_strong_clip_eur"] = min(280.0, max(220.0, target * 0.35 if target else 220.0))
    return out


def decision_public_dict(decision: DeskModeDecision) -> dict[str, Any]:
    return {
        "mode": decision.mode.value,
        "confidence": round(decision.confidence, 3),
        "reasons": list(decision.reasons),
        "overlays": dict(decision.overlays),
    }
