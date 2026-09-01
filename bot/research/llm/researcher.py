"""Hypothesis proposal via local LLM + validation/dedupe/budget."""

from __future__ import annotations

from typing import Any

from bot.research.llm.budget import ExperimentBudget
from bot.research.llm.experiment_planner import StrategySpecValidator
from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.llm.prompts import PROPOSAL_SYSTEM
from bot.research.llm.provider import ProviderError, ResearchLLMProvider
from bot.research.llm.schemas import HypothesisBatch, HypothesisProposal
from bot.research.accounting.protocol import H0007_AUTO_CHILD_GENERATION
from bot.research.alpha_attribution.protocol import reject_auto_strategy


def propose_and_filter(
    provider: ResearchLLMProvider,
    *,
    context: dict[str, Any],
    registry: HypothesisRegistry,
    budget: ExperimentBudget,
    supported_horizons: set[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ask LLM for hypotheses; validate; dedupe; apply budget."""
    validator = StrategySpecValidator()
    out: dict[str, Any] = {
        "llm_status": "OK",
        "proposed": [],
        "rejected_duplicate": [],
        "rejected_validator": [],
        "accepted": [],
        "specs": [],
    }
    if budget.remaining_llm_calls() <= 0:
        out["llm_status"] = "LLM_CALL_BUDGET_EXHAUSTED"
        return out
    try:
        budget.record_llm_call()
        batch = provider.generate_structured(
            system_prompt=PROPOSAL_SYSTEM,
            context=context,
            schema_model=HypothesisBatch,
        )
    except ProviderError as exc:
        out["llm_status"] = f"UNAVAILABLE:{exc}"
        return out

    # Sort by information value then priority
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    hyps = sorted(
        batch.hypotheses,
        key=lambda h: (order.get(h.information_value, 9), h.priority, h.title),
    )
    for hyp in hyps[: budget.max_new_hypotheses_per_run * 2]:
        out["proposed"].append(hyp.model_dump())
        parent_id = getattr(hyp, "parent_hypothesis_id", None)
        blocked = reject_auto_strategy(
            parent_id=parent_id,
            title=getattr(hyp, "title", "") or "",
            source="llm",
        )
        if blocked:
            out["rejected_validator"].append({"title": hyp.title, "reasons": [blocked]})
            continue
        if (not H0007_AUTO_CHILD_GENERATION) and parent_id == "H-0007":
            out["rejected_validator"].append(
                {
                    "title": hyp.title,
                    "reasons": ["H-0007_GATE_INACTIVE_no_automatic_child_hypotheses"],
                }
            )
            continue
        dups = registry.find_duplicates(hyp)
        if dups:
            # Allow if explicitly differentiated
            diff = (hyp.difference_from_prior_failures or "").strip()
            named = set(hyp.not_equivalent_to)
            prior_ids = {str(d.get("hypothesis_id")) for d in dups}
            if not diff or not (named & prior_ids or named):
                out["rejected_duplicate"].append(
                    {
                        "title": hyp.title,
                        "duplicates": [d.get("hypothesis_id") for d in dups],
                        "reason": "equivalent_without_differentiation",
                    }
                )
                if not dry_run:
                    registry.register_proposal(hyp, source="llm", status="DUPLICATE", dry_run=False)
                    # mark duplicate status already
                continue
        vr = validator.validate(
            hyp, budget=budget, supported_horizons=supported_horizons
        )
        if not vr.ok:
            out["rejected_validator"].append(
                {"title": hyp.title, "reasons": vr.reasons}
            )
            if not dry_run:
                rec = registry.register_proposal(hyp, source="llm", status="INVALID")
                registry.update_status_append(
                    rec["hypothesis_id"],
                    status="INVALID",
                    final_reason=",".join(vr.reasons),
                )
            continue
        if budget.remaining_hypotheses_this_run() <= 0:
            out["rejected_validator"].append(
                {"title": hyp.title, "reasons": ["budget_exhausted"]}
            )
            break
        budget.record_hypothesis(n_params=max(1, len(hyp.required_horizons_ms)))
        rec = registry.register_proposal(
            hyp,
            source="llm",
            status="ACCEPTED_FOR_RESEARCH",
            dry_run=dry_run,
        )
        out["accepted"].append(rec)
        out["specs"].append(vr.spec)

    out["analysis_observations"] = [o.model_dump() for o in batch.analysis.observations]
    return out
