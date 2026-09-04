"""Capital playbook router — TREND / FLAT / ADVERSE for live micro.

Chooses a capital-allocation playbook from session velocity + tape health,
then returns knob overlays the bridge applies live. Never-loss on normal
BE+ harvests is unchanged; FLAT/ADVERSE only loosen fill-seeking / recycle
within the existing sleeve and risk caps.

Pre-crash FLAT: when sell velocity dies with high near-BE inventory, force
de-risk to cash *before* an underwater cluster (today's 12:28 MTM cliff).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class CapitalPlaybook(str, Enum):
    TREND = "TREND"
    FLAT = "FLAT"
    ADVERSE = "ADVERSE"


@dataclass(frozen=True, slots=True)
class CapitalPlaybookInputs:
    """Features available at evaluation time (no I/O)."""

    sell_fills_last_60m: int = 0
    sell_fills_last_180m: int = 0
    underwater_bag_count: int = 0
    underwater_notional_eur: float = 0.0
    near_be_stuck_count: int = 0
    near_be_notional_eur: float = 0.0
    alphai_macro_active: bool = False
    alphai_wait_ratio: float = 0.0
    # Signed median short-horizon mark return across liquid bags (e.g. ~5 samples).
    median_mom: float | None = None
    free_quote_eur: float = 0.0
    inventory_mtm_eur: float = 0.0
    time_stop_below_be_skips: int = 0
    winnable_gap_eur: float = 0.0


@dataclass(frozen=True, slots=True)
class CapitalPlaybookDecision:
    playbook: CapitalPlaybook
    confidence: float
    reasons: tuple[str, ...]
    overlays: Mapping[str, Any] = field(default_factory=dict)
    pre_crash: bool = False


# Overlay keys applied onto MicroBridgeExecutor private fields.
# Values are Python natives; the bridge coerces to Decimal/float/bool.
PLAYBOOK_OVERLAYS: dict[CapitalPlaybook, dict[str, Any]] = {
    CapitalPlaybook.TREND: {
        # Empty → restore session baselines (max-deploy).
    },
    CapitalPlaybook.FLAT: {
        "active_ring_eur": 1200.0,
        "ring_soft_max_active_eur": 1200.0,
        "winner_add_enabled": False,
        "alphai_strong_clip_eur": 160.0,
        "exit_taker_cushion_bps": 2.0,
        "exit_taker_after_maker_fails": 1,
        "be_harvest_min_gain_pct": 0.00005,
        "be_harvest_partial_pct": 0.85,
        "uw_near_min_age_sec": 600.0,
        "uw_non_alphai_min_age_sec": 900.0,
        "uw_idle_min_age_sec": 300.0,
        "uw_idle_below_be_pct": 0.003,
        "uw_near_below_be_pct": 0.004,
        "alphai_intraday_min_freshness": 0.45,
        "block_new_buys": False,
    },
    CapitalPlaybook.ADVERSE: {
        "active_ring_eur": 700.0,
        "ring_soft_max_active_eur": 700.0,
        "winner_add_enabled": False,
        "alphai_strong_clip_eur": 0.0,
        "exit_taker_cushion_bps": 2.0,
        "exit_taker_after_maker_fails": 1,
        "be_harvest_min_gain_pct": 0.00005,
        "be_harvest_partial_pct": 0.90,
        "uw_near_min_age_sec": 300.0,
        "uw_non_alphai_min_age_sec": 450.0,
        "uw_idle_min_age_sec": 120.0,
        "uw_idle_below_be_pct": 0.003,
        "uw_near_below_be_pct": 0.005,
        # Faster AlphaI recycle on red days (was 1.5%/3600s — bags stayed stuck).
        "uw_alphai_below_be_pct": 0.010,
        "uw_alphai_min_age_sec": 900.0,
        "early_cut_loss_below_be_pct": 0.008,
        "trail_hold_rising_n": 0,
        "alphai_intraday_min_freshness": 0.55,
        "alphai_intraday_require_rising": True,
        "alphai_cross_venue_deploy": False,
        "alphai_idle_deploy_blocked": True,
        "block_new_buys": True,  # new bases only
    },
}

# Stronger FLAT when velocity is dead with capital still near BE (pre-crash).
PRE_CRASH_FLAT_OVERLAYS: dict[str, Any] = {
    "active_ring_eur": 500.0,
    "ring_soft_max_active_eur": 500.0,
    "winner_add_enabled": False,
    "alphai_strong_clip_eur": 0.0,
    "exit_taker_cushion_bps": 2.0,
    "exit_taker_after_maker_fails": 1,
    "be_harvest_min_gain_pct": 0.00005,
    "be_harvest_partial_pct": 0.90,
    "uw_near_min_age_sec": 180.0,
    "uw_non_alphai_min_age_sec": 300.0,
    "uw_idle_min_age_sec": 90.0,
    "uw_idle_below_be_pct": 0.005,
    "uw_near_below_be_pct": 0.005,
    "uw_near_max_depth_pct": 0.008,
    "uw_alphai_below_be_pct": 0.008,
    "uw_alphai_min_age_sec": 600.0,
    "early_cut_loss_below_be_pct": 0.008,
    "trail_hold_rising_n": 0,
    "alphai_intraday_min_freshness": 0.50,
    "alphai_intraday_require_rising": True,
    "alphai_cross_venue_deploy": False,
    "alphai_idle_deploy_blocked": True,
    "block_new_buys": True,
}


def classify_capital_playbook(
    inputs: CapitalPlaybookInputs,
    *,
    current: CapitalPlaybook | None = None,
    held_sec: float = 0.0,
    min_hold_sec: float = 900.0,
    adverse_interrupt: bool = True,
) -> CapitalPlaybookDecision:
    """Classify TREND / FLAT / ADVERSE with light hysteresis."""
    raw, confidence, reasons, pre_crash = _raw_classify(inputs)
    playbook = raw

    if current is not None and current != raw and held_sec < min_hold_sec:
        # ADVERSE and pre-crash FLAT may interrupt immediately.
        allow_interrupt = (
            (adverse_interrupt and raw == CapitalPlaybook.ADVERSE)
            or (pre_crash and raw == CapitalPlaybook.FLAT)
        )
        if not allow_interrupt:
            playbook = current
            reasons = tuple([*reasons, f"hysteresis_hold_{current.value}"])
            confidence = min(confidence, 0.55)
            pre_crash = False

    if playbook == CapitalPlaybook.FLAT and pre_crash:
        overlays = dict(PRE_CRASH_FLAT_OVERLAYS)
    else:
        overlays = dict(PLAYBOOK_OVERLAYS.get(playbook, {}))
    return CapitalPlaybookDecision(
        playbook=playbook,
        confidence=float(confidence),
        reasons=tuple(reasons),
        overlays=overlays,
        pre_crash=bool(pre_crash and playbook == CapitalPlaybook.FLAT),
    )


def _pre_crash_derisk(
    inputs: CapitalPlaybookInputs, *, mom: float | None
) -> bool:
    """Velocity dead + capital stuck near BE → de-risk before the dump."""
    if inputs.sell_fills_last_60m > 1:
        return False
    if inputs.inventory_mtm_eur < 600.0:
        return False
    flat_tape = mom is not None and abs(mom) <= 0.0008
    near_be_heavy = (
        inputs.near_be_stuck_count >= 2
        or inputs.near_be_notional_eur >= 400.0
    )
    # Early underwater (not yet ADVERSE cluster) still counts as at-risk capital.
    early_uw = (
        inputs.underwater_bag_count >= 1
        and inputs.underwater_notional_eur >= 80.0
        and inputs.underwater_notional_eur < 250.0
    )
    return bool(near_be_heavy or flat_tape or early_uw)


def _raw_classify(
    inputs: CapitalPlaybookInputs,
) -> tuple[CapitalPlaybook, float, list[str], bool]:
    reasons: list[str] = []
    mom = inputs.median_mom

    # --- ADVERSE ---
    adverse_score = 0.0
    if inputs.alphai_macro_active:
        adverse_score += 2.0
        reasons.append("alphai_macro")
    if inputs.underwater_bag_count >= 3 and inputs.underwater_notional_eur >= 120.0:
        adverse_score += 2.0
        reasons.append("underwater_cluster")
    elif inputs.underwater_notional_eur >= 250.0:
        adverse_score += 1.5
        reasons.append("underwater_notional")
    if mom is not None and mom < -0.0015:
        adverse_score += 1.5
        reasons.append("negative_tape")
    if inputs.alphai_wait_ratio >= 0.65:
        adverse_score += 0.5
        reasons.append("alphai_wait_heavy")

    if adverse_score >= 2.0:
        return CapitalPlaybook.ADVERSE, min(0.95, 0.55 + 0.1 * adverse_score), reasons, False

    # --- PRE-CRASH DERISK (force FLAT before underwater cluster) ---
    if _pre_crash_derisk(inputs, mom=mom):
        reasons.append("pre_crash_derisk")
        if inputs.sell_fills_last_60m <= 1:
            reasons.append("low_sell_velocity_60m")
        if inputs.inventory_mtm_eur >= 600.0:
            reasons.append("high_inventory")
        if inputs.near_be_stuck_count >= 2 or inputs.near_be_notional_eur >= 400.0:
            reasons.append("near_be_capital")
        if mom is not None and abs(mom) <= 0.0008:
            reasons.append("flat_tape")
        return CapitalPlaybook.FLAT, 0.88, reasons, True

    # --- FLAT (low velocity / stuck near BE) ---
    flat_score = 0.0
    if inputs.sell_fills_last_60m <= 1:
        flat_score += 1.5
        reasons.append("low_sell_velocity_60m")
    if inputs.sell_fills_last_180m <= 3:
        flat_score += 0.75
        reasons.append("low_sell_velocity_180m")
    if inputs.near_be_stuck_count >= 2:
        flat_score += 1.25
        reasons.append("near_be_stuck")
    if inputs.winnable_gap_eur >= 3.0 and inputs.sell_fills_last_60m <= 2:
        flat_score += 1.0
        reasons.append("winnable_gap_unfilled")
    if inputs.time_stop_below_be_skips >= 40 and inputs.sell_fills_last_60m <= 2:
        flat_score += 0.75
        reasons.append("time_stop_below_be_pileup")
    if mom is not None and abs(mom) <= 0.0008:
        flat_score += 1.0
        reasons.append("flat_tape")
    if inputs.alphai_wait_ratio >= 0.45:
        flat_score += 0.5
        reasons.append("alphai_mixed_wait")

    # Strong velocity overrides flat when tape is rising.
    if inputs.sell_fills_last_60m >= 4 and mom is not None and mom > 0.001:
        return (
            CapitalPlaybook.TREND,
            0.8,
            reasons + ["sell_velocity_trend"],
            False,
        )

    if flat_score >= 2.0:
        # Velocity alone is not enough (cold start / restart looks like zero fills).
        confirm = (
            inputs.near_be_stuck_count >= 1
            or inputs.winnable_gap_eur >= 2.0
            or inputs.time_stop_below_be_skips >= 20
            or inputs.inventory_mtm_eur >= 400.0
            or (mom is not None and abs(mom) <= 0.0008)
        )
        if confirm:
            return CapitalPlaybook.FLAT, min(0.9, 0.5 + 0.1 * flat_score), reasons, False
        reasons.append("flat_unconfirmed")

    # --- TREND default ---
    if inputs.sell_fills_last_60m >= 3:
        reasons.append("healthy_sell_velocity")
        return CapitalPlaybook.TREND, 0.75, reasons, False
    if mom is not None and mom > 0.0015:
        reasons.append("rising_tape")
        return CapitalPlaybook.TREND, 0.7, reasons, False

    reasons.append("default_trend")
    return CapitalPlaybook.TREND, 0.5, reasons, False


def decision_public_dict(decision: CapitalPlaybookDecision) -> dict[str, Any]:
    return {
        "playbook": decision.playbook.value,
        "confidence": round(decision.confidence, 3),
        "reasons": list(decision.reasons),
        "overlays": dict(decision.overlays),
        "pre_crash": bool(decision.pre_crash),
    }
