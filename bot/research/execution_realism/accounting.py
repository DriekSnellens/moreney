"""Accounting checks for execution realism results."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.accounting.protocol import WATERFALL_TOLERANCE
from bot.research.execution_realism.models import ExecutionWaterfall, FillStatus

_ZERO = Decimal("0")


def audit_waterfall(wf: ExecutionWaterfall) -> dict[str, Any]:
    """Verify waterfall identity holds exactly."""
    issues: list[str] = []
    if wf.fill_status == FillStatus.NO_FILL:
        if wf.execution_net != _ZERO:
            issues.append(f"NO_FILL but execution_net={wf.execution_net}")
    else:
        residual = wf.waterfall_residual()
        if abs(residual) > WATERFALL_TOLERANCE:
            issues.append(f"waterfall residual={residual} exceeds tolerance")
    return {
        "ACCOUNTING_AUDIT": "PASS" if not issues else "FAIL",
        "issues": issues,
    }


def audit_scenario(waterfalls: Sequence[ExecutionWaterfall]) -> dict[str, Any]:
    """Aggregate accounting for a complete scenario run."""
    issues: list[str] = []
    n_fail = 0
    for wf in waterfalls:
        a = audit_waterfall(wf)
        if a["ACCOUNTING_AUDIT"] == "FAIL":
            n_fail += 1
            issues.extend(a["issues"][:3])
    total = len(waterfalls)
    return {
        "ACCOUNTING_AUDIT": "PASS" if n_fail == 0 else "FAIL",
        "total_signals": total,
        "waterfall_failures": n_fail,
        "issues": issues[:10],
    }
