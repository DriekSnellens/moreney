"""Exactly one final research decision. Mechanical OOS_PASS is not overridden."""

from __future__ import annotations

from typing import Any

from bot.research.robustness.protocol import WINDOW_DOMINANCE_SHARE

DECISIONS = (
    "REJECT",
    "COLLECT_MORE_DATA",
    "PROMISING_REPLICATION_REQUIRED",
    "ROBUST_PAPER_CANDIDATE",
    "INSUFFICIENT_REGIME_DIVERSITY",
    "MODEL_UNCERTAINTY_TOO_HIGH",
)


def window_dominance(window_nets: list[float]) -> dict[str, Any]:
    abs_vals = [abs(float(x)) for x in window_nets]
    tot = sum(abs_vals) or 1.0
    top = max(abs_vals) / tot if abs_vals else 1.0
    return {
        "top_window_share": top,
        "dominates": top > WINDOW_DOMINANCE_SHARE,
        "n": len(window_nets),
        "cap": WINDOW_DOMINANCE_SHARE,
    }


def research_decision(
    *,
    accounting_pass: bool,
    interpretation: str,
    independent_windows: int,
    window_nets: list[float],
    survives_reasonable_stress: bool,
    gate_selective: bool,
    parent_comparison_available: bool,
    production_loosened: bool,
    model_uncertainty_too_high: bool,
    regime_diversity_ok: bool,
    required_regime_diversity: bool,
) -> str:
    if required_regime_diversity and not regime_diversity_ok:
        return "INSUFFICIENT_REGIME_DIVERSITY"
    if interpretation == "GATE_INACTIVE" and required_regime_diversity:
        # Gate never selected; do not promote. Diversity may still be the blocker.
        if not regime_diversity_ok:
            return "INSUFFICIENT_REGIME_DIVERSITY"
        return "INSUFFICIENT_REGIME_DIVERSITY"
    if model_uncertainty_too_high or interpretation == "MODEL_UNCERTAINTY_TOO_HIGH":
        return "MODEL_UNCERTAINTY_TOO_HIGH"
    if interpretation == "GATE_INACTIVE":
        return "INSUFFICIENT_REGIME_DIVERSITY" if required_regime_diversity else "COLLECT_MORE_DATA"

    agg = sum(window_nets) if window_nets else 0.0
    dom = window_dominance(window_nets)
    n_pos = sum(1 for x in window_nets if x > 0)
    n_neg = sum(1 for x in window_nets if x < 0)

    robust_ok = (
        independent_windows >= 2
        and agg > 0
        and not dom["dominates"]
        and survives_reasonable_stress
        and gate_selective
        and parent_comparison_available
        and accounting_pass
        and not production_loosened
        and regime_diversity_ok
    )
    if robust_ok:
        return "ROBUST_PAPER_CANDIDATE"
    if independent_windows < 2:
        if agg > 0 and gate_selective:
            return "PROMISING_REPLICATION_REQUIRED"
        return "COLLECT_MORE_DATA"
    if n_neg > n_pos and agg <= 0:
        return "REJECT"
    if agg > 0 and gate_selective:
        return "PROMISING_REPLICATION_REQUIRED"
    if not gate_selective:
        return "COLLECT_MORE_DATA"
    return "COLLECT_MORE_DATA"
