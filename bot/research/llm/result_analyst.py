"""Non-authoritative LLM analysis of tournament results."""

from __future__ import annotations

from typing import Any

from bot.research.llm.prompts import ANALYSIS_SYSTEM
from bot.research.llm.provider import ProviderError, ResearchLLMProvider
from bot.research.llm.schemas import ResultAnalysisBatch
from bot.research.llm.safety import analysis_cannot_override_verdict


def analyze_results(
    provider: ResearchLLMProvider,
    *,
    result_summary: dict[str, Any],
    budget_calls_remaining: int,
) -> tuple[ResultAnalysisBatch | None, dict[str, str], str]:
    """Return (analysis|None, canonical_verdicts_unchanged, status)."""
    canonical = {
        str(r.get("STRATEGY")): str(r.get("VERDICT"))
        for r in (result_summary.get("scoreboard") or [])
    }
    if budget_calls_remaining <= 0:
        return None, canonical, "LLM_CALL_BUDGET_EXHAUSTED"
    try:
        batch = provider.generate_structured(
            system_prompt=ANALYSIS_SYSTEM,
            context={
                "result_summary": {
                    "DATASET_ID": result_summary.get("DATASET_ID"),
                    "scoreboard": result_summary.get("scoreboard"),
                    "PAPER_CANDIDATES": result_summary.get("PAPER_CANDIDATES"),
                    "ALL_STRATEGIES_REJECTED": result_summary.get("ALL_STRATEGIES_REJECTED"),
                },
                "note": "NON_AUTHORITATIVE — do not change verdicts",
            },
            schema_model=ResultAnalysisBatch,
        )
        # Enforce label
        if batch.label != "NON_AUTHORITATIVE_ANALYSIS":
            batch = ResultAnalysisBatch(
                label="NON_AUTHORITATIVE_ANALYSIS",
                items=batch.items,
                shared_lessons=batch.shared_lessons,
            )
        unchanged = analysis_cannot_override_verdict(batch.model_dump(), canonical)
        return batch, unchanged, "OK"
    except ProviderError as exc:
        return None, canonical, f"ANALYSIS_SKIPPED:{exc}"
