"""Frozen decision function. No post-hoc category invention."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.research.final_validation.protocol import DECISION_RULES, NEXT_ACTIONS, VERDICTS

_ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def decide(scenario_results: list[dict[str, Any]], *, n_windows: int) -> dict[str, Any]:
    by_id = {r["scenario_id"]: r for r in scenario_results}
    baseline = by_id["BASELINE"]
    mild = by_id["MILD_REALISM"]
    moderate = by_id["MODERATE_REALISM"]
    reasons: list[str] = []

    min_w = int(DECISION_RULES["min_independent_windows_for_robust"])
    cap = float(DECISION_RULES["window_dominance_cap"])
    base_net = _d(baseline.get("execution_net_eur"))
    mild_net = _d(mild.get("execution_net_eur"))
    mod_net = _d(moderate.get("execution_net_eur"))
    base_audit = str(baseline.get("accounting_identity_status") or "FAIL")
    top_share = float(baseline.get("top_window_share") or 0.0)
    mild_pos_w = int(mild.get("positive_windows") or 0)
    mild_neg_w = int(mild.get("negative_windows") or 0)
    mod_pos_w = int(moderate.get("positive_windows") or 0)
    mod_neg_w = int(moderate.get("negative_windows") or 0)

    if base_audit != "PASS":
        verdict = "REJECTED"
        reasons.append(f"BASELINE accounting identity is {base_audit}.")
    elif base_net <= _ZERO:
        verdict = "REJECTED"
        reasons.append(f"BASELINE EXECUTION_NET is {base_net} EUR (not positive).")
    elif mild_net <= _ZERO:
        verdict = "REJECTED"
        reasons.append(
            f"MILD_REALISM EXECUTION_NET is {mild_net} EUR; mild degradation destroys the edge."
        )
    elif n_windows > 0 and mild_neg_w > mild_pos_w:
        verdict = "REJECTED"
        reasons.append(
            f"MILD_REALISM is negative in {mild_neg_w}/{n_windows} windows (majority negative)."
        )
    elif mod_net <= _ZERO:
        verdict = "EXECUTION_FRAGILE"
        reasons.append(
            f"BASELINE and MILD stay positive but MODERATE_REALISM EXECUTION_NET is {mod_net} EUR."
        )
        reasons.append("Profitability depends on optimistic fill/latency/fee assumptions.")
    elif n_windows < min_w:
        verdict = "PROMISING_BUT_INSUFFICIENT"
        reasons.append(f"Only {n_windows} complete independent windows; {min_w} required for ROBUST.")
        reasons.append(f"BASELINE EXECUTION_NET {base_net} EUR and MILD {mild_net} EUR remain positive.")
        if mod_net > _ZERO:
            reasons.append(f"MODERATE_REALISM remains positive at {mod_net} EUR.")
    elif top_share >= cap:
        verdict = "PROMISING_BUT_INSUFFICIENT"
        reasons.append(
            f"Top window share {top_share:.3f} >= dominance cap {cap:.2f}; economics are concentrated."
        )
    elif mod_pos_w <= mod_neg_w:
        verdict = "PROMISING_BUT_INSUFFICIENT"
        reasons.append(
            f"MODERATE_REALISM window split {mod_pos_w} positive / {mod_neg_w} negative is not a majority."
        )
    else:
        verdict = "ROBUST_PAPER_CANDIDATE"
        reasons.append(f"BASELINE EXECUTION_NET {base_net} EUR with accounting PASS.")
        reasons.append(f"MILD_REALISM {mild_net} EUR and MODERATE_REALISM {mod_net} EUR stay positive.")
        reasons.append(
            f"{n_windows} complete windows (>= {min_w}); MODERATE {mod_pos_w} positive / {mod_neg_w} negative."
        )
        reasons.append(f"Top window share {top_share:.3f} < {cap:.2f}.")

    while len(reasons) < 3:
        reasons.append("Scenario definitions were frozen before replay and were not retuned.")
    if len(reasons) < 4:
        reasons.append("Production execution remains DISABLED; no new strategies were created.")

    assert verdict in VERDICTS
    return {
        "FINAL_VALIDATION_VERDICT": verdict,
        "WHY": reasons[:5],
        "NEXT_ACTION": NEXT_ACTIONS[verdict],
    }
