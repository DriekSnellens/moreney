"""Mechanical verdicts. LLM must not choose these."""

from __future__ import annotations

from typing import Any

VERDICTS = (
    "OOS_PASS",
    "OOS_FAIL",
    "INSUFFICIENT_DATA",
    "INSUFFICIENT_FRESH_DATA",
    "UNSTABLE",
    "COST_NEGATIVE",
    "NO_SELECTIVE_EDGE",
    "NON_PARTICIPATION_ONLY",
    "UNSUPPORTED_DATA",
)

# Predeclared; not fit on OOS.
PARTICIPATION_RATIO = 0.50
MEAN_EDGE_REL_TOL = 0.10


def mechanical_verdict(
    *,
    data_status: str,
    tournament_verdict: str | None,
    failed_gate: str | None,
    gated_metrics: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    regime_only_metrics: dict[str, Any] | None,
    audit: dict[str, Any] | None,
    stability: dict[str, Any] | None,
) -> str:
    if data_status in {"INSUFFICIENT_FRESH_DATA", "NO_FRESH_TAPE"}:
        return "INSUFFICIENT_FRESH_DATA"
    if tournament_verdict == "DATA_UNSUPPORTED":
        return "UNSUPPORTED_DATA"
    if tournament_verdict in {"INSUFFICIENT_SAMPLE"} or data_status == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    if tournament_verdict in {"NO_SIGNAL", "OOS_FAILED"}:
        return "OOS_FAIL"
    if tournament_verdict == "COST_NEGATIVE" or tournament_verdict == "EXECUTION_NEGATIVE":
        return "COST_NEGATIVE"

    g = gated_metrics or {}
    p = parent_metrics or {}
    r = regime_only_metrics or {}
    adm = int((audit or {}).get("admitted") or g.get("signals") or 0)
    cand = int((audit or {}).get("candidates") or 0)
    parent_n = int(p.get("signals") or 0)
    g_mean = g.get("mean_forward")
    p_mean = p.get("mean_forward")
    g_net = g.get("EXPECTED_NET")
    p_net = p.get("EXPECTED_NET")
    r_net = r.get("EXPECTED_NET")

    if cand > 0 and adm / cand < PARTICIPATION_RATIO and parent_n > 0:
        if g_mean is not None and p_mean is not None:
            if abs(p_mean) > 0 and abs(g_mean - p_mean) <= MEAN_EDGE_REL_TOL * abs(p_mean):
                return "NON_PARTICIPATION_ONLY"

    if g_net is not None and (g_net <= 0):
        return "COST_NEGATIVE"

    selective = False
    if g_net is not None and p_net is not None and g_net > max(p_net, 0):
        selective = True
    if g_mean is not None and p_mean is not None and abs(g_mean) > abs(p_mean or 0) * (1 + MEAN_EDGE_REL_TOL):
        selective = True
    if r_net is not None and g_net is not None and g_net > max(float(r_net), 0) * (1 + MEAN_EDGE_REL_TOL):
        selective = True
    if not selective:
        return "NO_SELECTIVE_EDGE"

    if (stability or {}).get("concentrated") or tournament_verdict == "UNSTABLE":
        return "UNSTABLE"
    if tournament_verdict == "PAPER_CANDIDATE" and selective:
        return "OOS_PASS"
    if tournament_verdict == "PAPER_CANDIDATE":
        return "NO_SELECTIVE_EDGE"
    return "OOS_FAIL"
