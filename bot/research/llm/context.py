"""Deterministic bounded research context — OOS-blind before freeze."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.llm.schemas import ALLOWED_FEATURES, ALLOWED_HORIZONS_MS, ALLOWED_STRATEGY_FAMILIES
from bot.research.llm.safety import context_is_oos_blind
from bot.research.tournament.criteria import DIRECTED_ROUTES

MAX_CONTEXT_EXPERIMENTS = 20
MAX_CONTEXT_HYPOTHESES = 30
MAX_CONTEXT_BYTES = 48_000


def _trim(obj: Any, *, max_bytes: int = MAX_CONTEXT_BYTES) -> Any:
    raw = json.dumps(obj, sort_keys=True, default=str)
    if len(raw.encode("utf-8")) <= max_bytes:
        return obj
    # Deterministic shrink: drop long lists first
    if isinstance(obj, dict):
        out = dict(obj)
        for key in ("failed_hypotheses", "experiment_history", "promising"):
            if isinstance(out.get(key), list) and len(out[key]) > 5:
                out[key] = out[key][:5]
        raw2 = json.dumps(out, sort_keys=True, default=str)
        if len(raw2.encode("utf-8")) > max_bytes:
            out["note"] = "context_truncated"
            out["failed_hypotheses"] = (out.get("failed_hypotheses") or [])[:3]
            out["experiment_history"] = (out.get("experiment_history") or [])[:3]
        return out
    return obj


def list_supported_catalog() -> dict[str, Any]:
    return {
        "strategy_families": list(ALLOWED_STRATEGY_FAMILIES),
        "features": list(ALLOWED_FEATURES),
        "horizons_ms": list(ALLOWED_HORIZONS_MS),
        "venue_pairs": [f"{a}->{b}" for a, b in DIRECTED_ROUTES],
        "cost_model": "shared_retail_taker_roundtrip",
        "execution_mode": "research_shadow_trade_through",
        "oos_rules": "chronological_dev_freeze_oos_immutable",
    }


def build_research_context(
    *,
    dataset_id: str | None,
    data_duration: float | None,
    venues: list[str] | None,
    symbols: list[str] | None,
    readiness: dict[str, str] | None,
    hypotheses: list[dict[str, Any]],
    tournament_summary: dict[str, Any] | None,
    budget: dict[str, Any],
    include_oos_result_summary: bool = False,
    oos_result_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build OOS-blind context by default.

    Untouched OOS raw values are never included. After freeze+eval,
    only an optional OOS RESULT SUMMARY may be attached.
    """
    supported = []
    unsupported = []
    for k, v in sorted((readiness or {}).items()):
        if v in {"READY", "READY_WITH_CAUTION"}:
            supported.append(k)
        else:
            unsupported.append(k)

    failed = []
    promising = []
    for row in hypotheses[-MAX_CONTEXT_HYPOTHESES:]:
        if row.get("event") == "status_update":
            continue
        status = str(row.get("status") or "")
        item = {
            "hypothesis_id": row.get("hypothesis_id"),
            "mechanism": (row.get("mechanism") or "")[:240],
            "verdict": status,
            "failure_gate": row.get("final_reason") or row.get("status"),
            "key_evidence": (row.get("evidence_summary") or "")[:240],
            "why_failed": (row.get("final_reason") or "")[:240],
            "strategy_family": row.get("strategy_family"),
        }
        if status in {
            "REJECTED",
            "DATA_UNSUPPORTED",
            "NO_SIGNAL",
            "INSUFFICIENT_SAMPLE",
            "OOS_FAILED",
            "COST_NEGATIVE",
            "EXECUTION_NEGATIVE",
            "UNSTABLE",
            "DUPLICATE",
            "INVALID",
        }:
            failed.append(item)
        elif status in {"PAPER_CANDIDATE", "ACCEPTED_FOR_RESEARCH"}:
            promising.append(item)

    scoreboard = (tournament_summary or {}).get("scoreboard") or []
    history = []
    for row in scoreboard[:MAX_CONTEXT_EXPERIMENTS]:
        history.append(
            {
                "strategy": row.get("STRATEGY"),
                "verdict": row.get("VERDICT"),
                "failed_gate": row.get("FAILED_GATE"),
                "dev_signals": row.get("DEV_SIGNALS"),
                "oos_signals": row.get("OOS_SIGNALS"),
                # OOS effect only as prior experiment result summary — not raw OOS tape
                "oos_effect": row.get("OOS_EFFECT"),
            }
        )

    tested_families = {str(r.get("STRATEGY")) for r in scoreboard}
    gaps = {
        "families_never_paper_candidate": [
            f for f in ALLOWED_STRATEGY_FAMILIES if f not in {
                str(r.get("STRATEGY")) for r in scoreboard if r.get("VERDICT") == "PAPER_CANDIDATE"
            }
        ],
        "supported_horizons_not_emphasized": supported[:10],
        "venue_pairs": [f"{a}->{b}" for a, b in DIRECTED_ROUTES],
        "features_available": list(ALLOWED_FEATURES),
    }

    counts = {
        "hypotheses_total": len([h for h in hypotheses if h.get("hypothesis_id") and not h.get("event")]),
        "experiments_in_last_tournament": len(scoreboard),
        "rejected": sum(1 for r in scoreboard if r.get("VERDICT") not in {"PAPER_CANDIDATE"}),
        "oos_survivors": sum(
            1
            for r in scoreboard
            if r.get("VERDICT")
            in {"COST_NEGATIVE", "EXECUTION_NEGATIVE", "UNSTABLE", "PAPER_CANDIDATE"}
        ),
        "cost_positive": sum(
            1
            for r in scoreboard
            if r.get("VERDICT") in {"EXECUTION_NEGATIVE", "UNSTABLE", "PAPER_CANDIDATE"}
        ),
        "execution_positive": sum(
            1 for r in scoreboard if r.get("VERDICT") in {"UNSTABLE", "PAPER_CANDIDATE"}
        ),
        "paper_candidates": sum(1 for r in scoreboard if r.get("VERDICT") == "PAPER_CANDIDATE"),
    }

    context: dict[str, Any] = {
        "CURRENT_DATASET": {
            "dataset_id": dataset_id,
            "data_duration": data_duration,
            "venues": venues or [],
            "symbols": (symbols or [])[:40],
            "supported_horizons": supported,
            "unsupported_horizons": unsupported,
        },
        "RESEARCH_HISTORY": counts,
        "FAILED_HYPOTHESES": failed[-MAX_CONTEXT_HYPOTHESES:],
        "PROMISING_BUT_UNPROVEN": promising[-10:],
        "CURRENT_GAPS": gaps,
        "CURRENT_CONSTRAINTS": {
            "canonical_cost_model": "shared_retail_taker_roundtrip",
            "fill_assumptions": "trade_through_conservative_no_queue_fills",
            "execution_restrictions": "research_only_execution_disabled",
            "oos_rules": "chronological_immutable_after_freeze",
            "experiment_budget": budget,
        },
        "CATALOG": list_supported_catalog(),
        "experiment_history": history,
        "oos_blind": True,
        "multiple_testing_exposure": {
            "hypotheses_attempted": counts["hypotheses_total"],
            "parameter_combinations_note": "tracked via experiment budget",
            "oos_survivors": counts["oos_survivors"],
            "paper_candidates": counts["paper_candidates"],
        },
    }
    if include_oos_result_summary and oos_result_summary is not None:
        # Post-evaluation only: RESULT SUMMARY, never raw OOS tape
        context["OOS_RESULT_SUMMARY"] = oos_result_summary
        context["oos_blind"] = False
        context["oos_phase"] = "post_freeze_result_summary_only"

    context = _trim(context)
    assert context_is_oos_blind(context) or include_oos_result_summary
    if not include_oos_result_summary and not context_is_oos_blind(context):
        raise RuntimeError("OOS blindness violated in research context")
    return context


def load_tournament_summary(path: Path | str = "data/research_tournament_report.json") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
