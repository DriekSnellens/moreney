"""Research readiness score — data quality only, never profitability."""

from __future__ import annotations

from typing import Any

from bot.market_data.research.quality import horizon_readiness
from bot.market_data.research.schema import TimestampQuality


def overall_readiness_verdict(horizon_scores: dict[str, str]) -> str:
    """DATA_READY_FOR_LEAD_LAG | DATA_PARTIALLY_READY | DATA_NOT_READY"""
    if not horizon_scores:
        return "DATA_NOT_READY"
    values = list(horizon_scores.values())
    if all(v == "NOT_READY" for v in values):
        return "DATA_NOT_READY"
    if any(v == "READY" for v in values) and all(
        v in {"READY", "READY_WITH_CAUTION"} for v in values if not v.endswith("50MS") and not v.endswith("100MS")
    ):
        # At least some >=500 ready
        readyish = [v for k, v in horizon_scores.items() if k.endswith(("500MS", "1000MS", "2000MS", "5000MS"))]
        if readyish and all(x in {"READY", "READY_WITH_CAUTION"} for x in readyish):
            if all(x == "READY" for x in readyish):
                return "DATA_READY_FOR_LEAD_LAG"
            return "DATA_PARTIALLY_READY"
    if any(v in {"READY", "READY_WITH_CAUTION"} for v in values):
        return "DATA_PARTIALLY_READY"
    return "DATA_NOT_READY"


def compute_readiness(
    *,
    quality_grade: str,
    sync_usable_rate_by_tol: dict[str, float] | None = None,
    has_recordings: bool,
) -> dict[str, Any]:
    if not has_recordings:
        scores = {f"LEAD_LAG_{h}MS": "NOT_READY" for h in (50, 100, 250, 500, 1000, 2000, 5000)}
        return {
            "horizon_scores": scores,
            "verdict": "DATA_NOT_READY",
            "reason": "no_research_recordings",
            "quality_grade": quality_grade or TimestampQuality.UNSUPPORTED.value,
        }
    scores = horizon_readiness(quality_grade, sync_usable_rate_by_tol=sync_usable_rate_by_tol)
    return {
        "horizon_scores": scores,
        "verdict": overall_readiness_verdict(scores),
        "reason": f"quality_grade={quality_grade}",
        "quality_grade": quality_grade,
    }
