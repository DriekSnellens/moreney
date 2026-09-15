"""Accounting audit: waterfall identity, labeling, no world mixing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.research.accounting.protocol import (
    MEAN_EDGE_REPLAY_VERSION,
    REPLAY_VERSION,
    WATERFALL_TOLERANCE,
)
from bot.research.accounting.schema import AccountingIdentityError, EconomicWorld
from bot.research.accounting.waterfall import CanonicalEconomics, assert_waterfall_identity


def audit_canonical(econ: CanonicalEconomics) -> dict[str, Any]:
    issues: list[str] = []
    try:
        assert_waterfall_identity(econ.lines)
    except AccountingIdentityError as exc:
        issues.append(str(exc))

    summed = sum((ln.realized_replay_net for ln in econ.lines), Decimal("0"))
    if abs(summed - econ.replay_net.value) > WATERFALL_TOLERANCE and len(econ.lines) != 1:
        issues.append("aggregate_replay_net_ne_sum_of_signals")

    n = econ.signals.value
    if n:
        expected_ps = econ.replay_net.value / Decimal(n)
        if abs(expected_ps - econ.replay_net_per_signal.value) > WATERFALL_TOLERANCE:
            issues.append("replay_net_per_signal_arithmetic")

    fills = econ.fills.value
    if fills and econ.replay_net_per_fill is not None:
        got = econ.replay_net_per_fill.value
        want = econ.replay_net.value / Decimal(fills)
        if abs(got - want) > WATERFALL_TOLERANCE:
            issues.append("replay_net_per_fill_arithmetic")

    if econ.world != EconomicWorld.EXECUTION_REPLAY:
        issues.append("canonical_result_world_is_not_execution_replay")
    if econ.replay_version != REPLAY_VERSION:
        issues.append("replay_version_mismatch")

    sidecar = econ.mean_edge_execution_replay_net_per_fill
    if sidecar is not None and econ.replay_net_per_fill is not None:
        if sidecar.metadata.replay_version != MEAN_EDGE_REPLAY_VERSION:
            issues.append("mean_edge_sidecar_unlabeled")
        if sidecar.quantity == econ.replay_net_per_fill.quantity:
            issues.append("mean_edge_occupies_canonical_per_fill_name")
        if "meanedge" not in sidecar.quantity.lower().replace("_", ""):
            issues.append("mean_edge_per_fill_not_distinctly_named")

    for q in (
        econ.replay_net,
        econ.replay_net_per_signal,
        econ.expected_net_per_signal,
        econ.gross,
        econ.fees,
    ):
        if not q.metadata.economic_world or not q.metadata.replay_version:
            issues.append(f"{q.quantity}_missing_metadata")

    if econ.expected_net_per_signal.metadata.economic_world != EconomicWorld.SIGNAL_EXPECTATION:
        issues.append("expected_world_misfiled")
    if econ.replay_net.metadata.economic_world != EconomicWorld.EXECUTION_REPLAY:
        issues.append("replay_world_misfiled")

    verdict = "PASS" if not issues else "FAIL"
    return {
        "ACCOUNTING_AUDIT": verdict,
        "issues": issues,
        "replay_version": REPLAY_VERSION,
        "tolerance": str(WATERFALL_TOLERANCE),
        "canonical_replay_net_eur": str(econ.replay_net.value),
        "canonical_replay_net_per_fill_eur": (
            None if econ.replay_net_per_fill is None else str(econ.replay_net_per_fill.value)
        ),
        "canonical_replay_net_per_signal_eur": str(econ.replay_net_per_signal.value),
        "mean_edge_execution_replay_net_per_fill_eur": (
            None if sidecar is None else str(sidecar.value)
        ),
        "note": "PASS means identities hold and worlds are labeled. PASS is not profitability.",
    }


def audit_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Fail if a generic NET/fill is published without world/replay metadata."""
    issues: list[str] = []
    generic = payload.get("NET_per_fill")
    if generic is not None and not payload.get("NET_per_fill_world"):
        issues.append("unlabeled_NET_per_fill")
    if payload.get("NET/fill") is not None and not payload.get("NET/fill_world"):
        issues.append("unlabeled_NET/fill")
    if payload.get("NET") is not None and not payload.get("NET_world"):
        issues.append("unlabeled_NET")
    if payload.get("EV") is not None and not payload.get("EV_world"):
        issues.append("unlabeled_EV")
    return {
        "ACCOUNTING_AUDIT": "FAIL" if issues else "PASS",
        "issues": issues,
    }
