"""Capital playbook router — TREND / FLAT / ADVERSE for live micro.

Chooses a capital-allocation playbook from session velocity + tape health,
then returns knob overlays the bridge applies live. Never-loss on normal
BE+ harvests is unchanged; FLAT/ADVERSE only loosen fill-seeking / recycle
within the existing sleeve and risk caps.
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
        "uw_near_min_age_sec": 450.0,
        "uw_non_alphai_min_age_sec": 600.0,
        "uw_idle_min_age_sec": 180.0,
        "uw_idle_below_be_pct": 0.003,
        "uw_near_below_be_pct": 0.005,
        "alphai_intraday_min_freshness": 0.55,
        "alphai_cross_venue_deploy": False,
        "block_new_buys": True,  # new bases only
    },
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
    raw, confidence, reasons = _raw_classify(inputs)
    playbook = raw

    if current is not None and current != raw and held_sec < min_hold_sec:
        # ADVERSE may interrupt immediately; other flips wait for min hold.
        if not (adverse_interrupt and raw == CapitalPlaybook.ADVERSE):
            playbook = current
            reasons = tuple([*reasons, f"hysteresis_hold_{current.value}"])
            confidence = min(confidence, 0.55)

    overlays = dict(PLAYBOOK_OVERLAYS.get(playbook, {}))
    return CapitalPlaybookDecision(
        playbook=playbook,
        confidence=float(confidence),
        reasons=tuple(reasons),
        overlays=overlays,
    )


def _raw_classify(
    inputs: CapitalPlaybookInputs,
) -> tuple[CapitalPlaybook, float, list[str]]:
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
        return CapitalPlaybook.ADVERSE, min(0.95, 0.55 + 0.1 * adverse_score), reasons

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
        )

    if flat_score >= 2.0:
        return CapitalPlaybook.FLAT, min(0.9, 0.5 + 0.1 * flat_score), reasons

    # --- TREND default ---
    if inputs.sell_fills_last_60m >= 3:
        reasons.append("healthy_sell_velocity")
        return CapitalPlaybook.TREND, 0.75, reasons
    if mom is not None and mom > 0.0015:
        reasons.append("rising_tape")
        return CapitalPlaybook.TREND, 0.7, reasons

    reasons.append("default_trend")
    return CapitalPlaybook.TREND, 0.5, reasons


def decision_public_dict(decision: CapitalPlaybookDecision) -> dict[str, Any]:
    return {
        "playbook": decision.playbook.value,
        "confidence": round(decision.confidence, 3),
        "reasons": list(decision.reasons),
        "overlays": dict(decision.overlays),
    }
