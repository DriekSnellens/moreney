"""Frozen shadow-validation decision function. No post-hoc retuning."""

from __future__ import annotations

from typing import Any

from bot.research.shadow_validation.protocol import (
    AUTOMATIC_RETUNING_ALLOWED,
    HYPOTHESIS_GENERATOR_ENABLED,
    MAX_DATA_INVALID_RATE,
    MAX_HEDGE_FAILURE_RATE,
    MAX_MEAN_GAP_RATIO,
    MAX_TOP_WINDOW_SHARE,
    MIN_CALENDAR_DAYS,
    MIN_COMPLETE_WINDOWS,
    MIN_FILL_RATE_NOT_FRAGILE,
    MIN_FILL_RATE_VALIDATED,
    MIN_FOLLOWER_AVAILABILITY_RATE,
    MIN_QUOTE_SURVIVAL_RATE,
    MIN_VALID_OBSERVATIONS,
    NEXT_ACTIONS,
    UNCERTAINTY_GAP_RATIO,
    VERDICTS,
)


def decide(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return exactly one verdict and exactly one next action."""
    if AUTOMATIC_RETUNING_ALLOWED or HYPOTHESIS_GENERATOR_ENABLED:
        raise RuntimeError("shadow validation protocol forbids retuning / LLM generation")

    reasons: list[str] = []
    windows = int(snapshot.get("complete_windows") or 0)
    days = float(snapshot.get("calendar_days") or 0.0)
    valid = int(snapshot.get("valid_observations") or 0)
    rates = snapshot.get("rates") or {}
    fill_rate = float(rates.get("fill_rate") or 0.0)
    invalid_rate = float(rates.get("data_invalid_rate") or 0.0)
    hedge_fail = float(rates.get("hedge_failure_rate") or 0.0)
    quote_surv = float(rates.get("quote_survival_rate") or 0.0)
    follower = float(rates.get("follower_availability_rate") or 0.0)
    shadow_net = float(snapshot.get("LIVE_SHADOW_EXECUTION_NET") or 0.0)
    expected_net = float(snapshot.get("RESEARCH_EXPECTED_NET") or 0.0)
    gap = snapshot.get("execution_gap") or {}
    mean_gap = gap.get("mean")
    mean_gap_f = float(mean_gap) if mean_gap is not None else 0.0
    top_share = float(snapshot.get("top_window_share") or 0.0)
    accounting_fail = int(snapshot.get("accounting_fail") or 0)
    horizon_met = bool(snapshot.get("sample_horizon_met"))
    volume_met = bool(snapshot.get("sample_volume_met"))
    complete = bool(snapshot.get("sample_complete"))

    mean_expected = (expected_net / valid) if valid else 0.0
    gap_ratio = 0.0
    if mean_expected != 0.0:
        gap_ratio = abs(mean_gap_f) / abs(mean_expected)

    if not horizon_met:
        verdict = "INSUFFICIENT_LIVE_SAMPLE"
        reasons.append(
            f"Observation horizon not reached: windows {windows}/{MIN_COMPLETE_WINDOWS}, "
            f"calendar days {days:.3f}/{MIN_CALENDAR_DAYS}."
        )
        reasons.append("Do not stop early. Do not retune.")
    elif not volume_met:
        if shadow_net > 0.0:
            verdict = "SHADOW_PROMISING"
            reasons.append(
                f"Horizon met but valid observations {valid} < {MIN_VALID_OBSERVATIONS}. "
                "Continue collecting. No retuning."
            )
        else:
            verdict = "INSUFFICIENT_LIVE_SAMPLE"
            reasons.append(
                f"Horizon met but valid observations {valid} < {MIN_VALID_OBSERVATIONS}; "
                "economics not yet positive. Continue collecting."
            )
    elif accounting_fail > 0:
        verdict = "SHADOW_REJECTED"
        reasons.append(f"Accounting identity failed on {accounting_fail} observations.")
    elif invalid_rate > MAX_DATA_INVALID_RATE:
        verdict = "SHADOW_REJECTED"
        reasons.append(
            f"DATA_INVALID rate {invalid_rate:.3f} > frozen cap {MAX_DATA_INVALID_RATE}."
        )
    elif shadow_net <= 0.0:
        verdict = "SHADOW_REJECTED"
        reasons.append(
            f"Aggregate LIVE_SHADOW_EXECUTION_NET is {shadow_net:.4f} EUR (not positive)."
        )
    elif fill_rate < MIN_FILL_RATE_NOT_FRAGILE:
        verdict = "SHADOW_EXECUTION_FRAGILE"
        reasons.append(
            f"Observed fill_rate {fill_rate:.3f} < {MIN_FILL_RATE_NOT_FRAGILE}. "
            "Live quotes are not executable under the frozen model."
        )
        if expected_net > 0.0:
            reasons.append("Theoretical expected economics remain positive; fills do not.")
    elif hedge_fail > MAX_HEDGE_FAILURE_RATE:
        verdict = "SHADOW_EXECUTION_FRAGILE"
        reasons.append(
            f"Hedge failure rate {hedge_fail:.3f} > {MAX_HEDGE_FAILURE_RATE}."
        )
    elif quote_surv < MIN_QUOTE_SURVIVAL_RATE:
        verdict = "SHADOW_EXECUTION_FRAGILE"
        reasons.append(
            f"Quote survival {quote_surv:.3f} < {MIN_QUOTE_SURVIVAL_RATE}."
        )
    elif follower < MIN_FOLLOWER_AVAILABILITY_RATE:
        verdict = "SHADOW_EXECUTION_FRAGILE"
        reasons.append(
            f"Follower availability {follower:.3f} < {MIN_FOLLOWER_AVAILABILITY_RATE}."
        )
    elif mean_gap_f < 0.0 and gap_ratio > MAX_MEAN_GAP_RATIO:
        verdict = "SHADOW_EXECUTION_FRAGILE"
        reasons.append(
            f"Mean execution gap {mean_gap_f:.4f} is worse than "
            f"{MAX_MEAN_GAP_RATIO:.2f} × |mean expected_net|."
        )
    elif top_share >= MAX_TOP_WINDOW_SHARE:
        verdict = "SHADOW_REJECTED"
        reasons.append(
            f"Top window share {top_share:.3f} >= dominance cap {MAX_TOP_WINDOW_SHARE}."
        )
    elif (
        fill_rate < MIN_FILL_RATE_VALIDATED
        or (mean_gap_f < 0.0 and gap_ratio > UNCERTAINTY_GAP_RATIO)
    ):
        verdict = "SHADOW_PROMISING"
        reasons.append("Sample complete and aggregate shadow NET positive, but uncertainty remains material.")
        if fill_rate < MIN_FILL_RATE_VALIDATED:
            reasons.append(
                f"fill_rate {fill_rate:.3f} < validated floor {MIN_FILL_RATE_VALIDATED}."
            )
        if mean_gap_f < 0.0 and gap_ratio > UNCERTAINTY_GAP_RATIO:
            reasons.append(f"execution-gap ratio {gap_ratio:.3f} exceeds uncertainty band.")
    else:
        verdict = "SHADOW_VALIDATED"
        reasons.append("Frozen sample complete. Observed live execution does not invalidate the model.")
        reasons.append(f"LIVE_SHADOW_EXECUTION_NET {shadow_net:.4f} EUR; fill_rate {fill_rate:.3f}.")
        reasons.append("Next action is a proposal only — production execution stays DISABLED.")

    if verdict not in VERDICTS:
        raise RuntimeError(f"non-canonical verdict {verdict}")
    action = NEXT_ACTIONS[verdict]
    return {
        "SHADOW_VALIDATION_VERDICT": verdict,
        "NEXT_ACTION": action,
        "WHY": reasons,
        "complete": complete,
        "provisional": not complete,
        "preferred_complete": bool(snapshot.get("preferred_complete")),
        "continue_passive": True,
        "early_stop": False,
        "retuning_allowed": False,
        "llm_generation_allowed": False,
        "production_execution": "DISABLED",
    }
