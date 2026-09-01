"""H-0005 replication state machine. Does not declare live alpha."""

from __future__ import annotations

from enum import Enum
from typing import Any

from bot.research.accounting.protocol import (
    CONCENTRATION_THRESHOLD,
    MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
    ROUTE_CONCENTRATION_THRESHOLD,
)

STATES = (
    "CANDIDATE",
    "FIRST_OOS_PASS",
    "REPLICATING",
    "REPLICATION_PASS",
    "ROBUST_PAPER_CANDIDATE",
    "REJECTED",
)


class ReplicationState(str, Enum):
    CANDIDATE = "CANDIDATE"
    FIRST_OOS_PASS = "FIRST_OOS_PASS"
    REPLICATING = "REPLICATING"
    REPLICATION_PASS = "REPLICATION_PASS"
    ROBUST_PAPER_CANDIDATE = "ROBUST_PAPER_CANDIDATE"
    REJECTED = "REJECTED"


def replication_advance(
    *,
    accounting_audit_pass: bool,
    independent_complete_windows: int,
    paired_comparison_present: bool,
    aggregate_paired_delta_positive: bool,
    window_concentration_ok: bool,
    symbol_concentration_ok: bool,
    route_limitation_reported: bool,
    cost_stress_positive: bool,
    no_leakage: bool,
    no_parameter_retune_after_oos: bool,
    mechanical_first_oos_pass: bool,
    production_execution_disabled: bool,
) -> dict[str, Any]:
    """Advance H-0005. Positive aggregate NET alone is not sufficient."""
    reasons: list[str] = []
    blockers: list[str] = []

    if not production_execution_disabled:
        return {
            "state": ReplicationState.REJECTED.value,
            "blockers": ["production_execution_must_remain_disabled"],
            "criteria": {},
        }

    state = ReplicationState.CANDIDATE
    if mechanical_first_oos_pass:
        state = ReplicationState.FIRST_OOS_PASS
        reasons.append("mechanical_first_oos_pass")
    if independent_complete_windows >= 1 and mechanical_first_oos_pass:
        state = ReplicationState.REPLICATING
        reasons.append("walk_forward_started")

    criteria = {
        "accounting_audit_pass": accounting_audit_pass,
        "independent_complete_windows": independent_complete_windows,
        "min_independent_windows": MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
        "min_windows_source": "protocol_assumption_not_fit_on_oos",
        "paired_comparison_present": paired_comparison_present,
        "aggregate_paired_delta_positive": aggregate_paired_delta_positive,
        "window_concentration_ok": window_concentration_ok,
        "symbol_concentration_ok": symbol_concentration_ok,
        "concentration_threshold": CONCENTRATION_THRESHOLD,
        "route_concentration_threshold": ROUTE_CONCENTRATION_THRESHOLD,
        "route_limitation_reported": route_limitation_reported,
        "cost_stress_positive": cost_stress_positive,
        "no_leakage": no_leakage,
        "no_parameter_retune_after_oos": no_parameter_retune_after_oos,
        "positive_aggregate_net_alone_insufficient": True,
    }

    def _need(ok: bool, name: str) -> None:
        if not ok:
            blockers.append(name)

    _need(accounting_audit_pass, "accounting_audit_pass")
    _need(
        independent_complete_windows >= MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
        f"independent_windows>={MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS}",
    )
    _need(paired_comparison_present, "paired_child_vs_parent")
    _need(aggregate_paired_delta_positive, "positive_aggregate_paired_delta")
    _need(window_concentration_ok, "window_concentration")
    _need(symbol_concentration_ok, "symbol_concentration")
    _need(route_limitation_reported, "route_limitation_reported")
    _need(cost_stress_positive, "frozen_cost_stress_positive")
    _need(no_leakage, "no_leakage")
    _need(no_parameter_retune_after_oos, "no_parameter_retune_after_oos")

    if state == ReplicationState.REPLICATING and not blockers:
        state = ReplicationState.REPLICATION_PASS
        reasons.append("all_replication_pass_criteria")

    # ROBUST_PAPER_CANDIDATE additionally requires accounting PASS (already in
    # blockers) and must not be granted when execution could be implied.
    if state == ReplicationState.REPLICATION_PASS and accounting_audit_pass:
        # Still do not auto-declare live alpha. Paper candidate is research-only.
        state = ReplicationState.ROBUST_PAPER_CANDIDATE
        reasons.append("replication_pass_and_accounting_pass")

    return {
        "state": state.value,
        "reasons": reasons,
        "blockers": blockers,
        "criteria": criteria,
        "live_alpha_declared": False,
        "note": (
            "H-0005 must not advance on positive aggregate NET alone. "
            "ROBUST_PAPER_CANDIDATE is research-only and does not enable execution."
        ),
    }
