"""Second-level interpretation. Does not replace mechanical tournament verdicts."""

from __future__ import annotations

from typing import Any

from bot.research.robustness.protocol import MIN_GATE_SELECTIVITY

INTERPRETATIONS = (
    "PROMISING_BUT_UNCONFIRMED",
    "GATE_INACTIVE",
    "NO_SELECTIVE_EVIDENCE",
    "ROBUST_REPLICATION_REQUIRED",
    "MODEL_UNCERTAINTY_TOO_HIGH",
    "INSUFFICIENT_REGIME_DIVERSITY",
)


def gate_selectivity(
    *,
    admitted: int,
    candidates: int,
    parent_signals: int | None = None,
    rejected: int | None = None,
) -> dict[str, Any]:
    cand = max(int(candidates or 0), 0)
    adm = max(int(admitted or 0), 0)
    rej = int(rejected) if rejected is not None else max(0, cand - adm)
    parent_n = int(parent_signals) if parent_signals is not None else None
    sel = (rej / cand) if cand else None
    inactive = False
    reasons: list[str] = []
    if cand > 0 and adm == cand and rej == 0:
        inactive = True
        reasons.append("admitted_equals_candidates")
    if parent_n is not None and parent_n > 0 and adm == parent_n and (cand == 0 or cand == parent_n):
        inactive = True
        reasons.append("admitted_equals_parent_candidates")
    if sel is not None and sel < MIN_GATE_SELECTIVITY:
        inactive = True
        reasons.append("selectivity_below_min")
    return {
        "admitted": adm,
        "candidates": cand,
        "rejected": rej,
        "parent_signals": parent_n,
        "selectivity": sel,
        "min_gate_selectivity": MIN_GATE_SELECTIVITY,
        "inactive": inactive,
        "reasons": reasons,
    }


def interpretation_verdict(
    *,
    mechanical: str | None,
    selectivity: dict[str, Any],
    regime_diversity: dict[str, Any] | None,
    edge_to_uncertainty: float | None,
    incremental_positive: bool | None,
    independent_windows: int,
    independently_positive: bool | None,
) -> str:
    """Exactly one interpretation. Mechanical OOS_PASS is left untouched."""
    if selectivity.get("inactive"):
        return "GATE_INACTIVE"
    rd = regime_diversity or {}
    if rd.get("required") and not rd.get("both_states"):
        return "INSUFFICIENT_REGIME_DIVERSITY"
    if edge_to_uncertainty is not None and edge_to_uncertainty < 1.0:
        return "MODEL_UNCERTAINTY_TOO_HIGH"
    if independently_positive and incremental_positive is False:
        return "PROMISING_BUT_UNCONFIRMED"
    if incremental_positive is False or incremental_positive is None:
        if not independently_positive:
            return "NO_SELECTIVE_EVIDENCE"
    if independently_positive and independent_windows < 2:
        return "PROMISING_BUT_UNCONFIRMED"
    if independently_positive and independent_windows >= 2:
        return "ROBUST_REPLICATION_REQUIRED"
    if mechanical == "OOS_PASS":
        return "PROMISING_BUT_UNCONFIRMED"
    return "NO_SELECTIVE_EVIDENCE"
