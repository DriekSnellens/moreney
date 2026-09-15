"""Tournament scoring — only after all gates; failed gates score 0."""

from __future__ import annotations

from typing import Any

from bot.research.tournament.contract import CandidateResult
from bot.research.tournament.criteria import SCORE_WEIGHTS


def score_candidate(result: CandidateResult) -> float:
    if result.verdict != "PAPER_CANDIDATE":
        # Non-candidates get 0 tournament promotion score
        if result.verdict in {
            "DATA_UNSUPPORTED",
            "NO_SIGNAL",
            "INSUFFICIENT_SAMPLE",
            "OOS_FAILED",
            "COST_NEGATIVE",
            "EXECUTION_NEGATIVE",
            "UNSTABLE",
            "IN_SAMPLE_ONLY",
        }:
            return 0.0
    oos = result.oos_stats
    oos_pred = 0.0
    if oos and oos.effect_size:
        oos_pred = min(1.0, abs(oos.effect_size) / 0.001)
        if result.oos_class == "CONSISTENT":
            oos_pred *= 1.0
        elif result.oos_class == "WEAKENED":
            oos_pred *= 0.5
        else:
            oos_pred = 0.0
    net_s = 0.0
    if result.expected_net is not None and result.expected_net > 0:
        net_s = min(1.0, result.expected_net / 1.0)
    exec_s = 0.0
    if result.execution_net is not None and result.execution_net > 0:
        exec_s = min(1.0, result.execution_net / 1.0)
    stab = float((result.stability or {}).get("stability_score") or 0.0)
    sample = 0.0
    if oos and oos.signals:
        sample = min(1.0, oos.signals / 100.0)
    w = SCORE_WEIGHTS
    return float(
        w["oos_predictive"] * oos_pred
        + w["expected_net"] * net_s
        + w["execution"] * exec_s
        + w["stability"] * stab
        + w["sample"] * sample
    )


def build_scoreboard(results: list[CandidateResult]) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        r.tournament_score = score_candidate(r)
        rows.append(
            {
                "STRATEGY": r.strategy_id,
                "DATA_STATUS": (
                    "UNSUPPORTED"
                    if r.verdict == "DATA_UNSUPPORTED"
                    else ("OK" if r.supported_horizons else "PARTIAL")
                ),
                "DEV_OBSERVATIONS": r.dev_stats.observations,
                "DEV_SIGNALS": r.dev_stats.signals,
                "DEV_EFFECT": r.dev_stats.conditional_forward_mean,
                "OOS_OBSERVATIONS": r.oos_stats.observations if r.oos_stats else 0,
                "OOS_SIGNALS": r.oos_stats.signals if r.oos_stats else 0,
                "OOS_EFFECT": r.oos_stats.conditional_forward_mean if r.oos_stats else None,
                "EXPECTED_GROSS": r.expected_gross,
                "EXPECTED_NET": r.expected_net,
                "EXECUTION_NET": r.execution_net,
                "STABILITY": (r.stability or {}).get("label"),
                "UNCERTAINTY": r.dev_stats.ci_high,
                "VERDICT": r.verdict,
                "FAILED_GATE": r.failed_gate,
                "TOURNAMENT_SCORE": r.tournament_score,
            }
        )
    rows.sort(key=lambda x: (-float(x["TOURNAMENT_SCORE"] or 0), x["STRATEGY"]))
    return rows
