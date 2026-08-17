"""Run concentration forensics on frozen tournament survivors of STABILITY."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.forensics import (
    FORENSICS_CRITERIA_VERSION,
    PACKAGE_LABEL,
    TARGET_STRATEGIES,
)
from bot.research.forensics.analysis import (
    all_decompositions,
    all_loo,
    all_regimes,
    chrono_block_table,
    null_checks,
    top_contributor_report,
    totals,
)
from bot.research.forensics.buckets import bucket_manifest
from bot.research.forensics.classify import classify
from bot.research.forensics.events import attach_economics, enrich_events, replay_oos_events
from bot.research.forensics.hypotheses import register_forensics_hypotheses
from bot.research.forensics.llm_advisory import maybe_llm_advisory
from bot.research.forensics.report import compact_dashboard, write_markdown
from bot.research.tournament.tape_index import build_tape_index


def load_tournament_report(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _venues_for(strategy_id: str, params: dict[str, Any]) -> tuple[str, str | None]:
    if strategy_id == "cross_venue_dislocation":
        return str(params.get("venue_a") or params.get("leader") or "binance"), str(
            params.get("venue_b") or params.get("follower") or "bitvavo"
        )
    return str(params.get("venue") or "bitvavo"), None


def analyze_strategy(
    *,
    index,
    strategy_id: str,
    candidate: dict[str, Any],
    oos: dict[str, Any],
) -> dict[str, Any]:
    params = dict(candidate.get("frozen_params") or {})
    supported = list(candidate.get("supported_horizons") or [500, 1000, 2000, 5000])
    raw = replay_oos_events(
        index=index,
        strategy_id=strategy_id,
        frozen_params=params,
        oos_start_ns=int(oos["start_ts_ns"]),
        oos_end_ns_inclusive=int(oos["end_ts_ns_inclusive"]),
        supported_horizons=supported,
    )
    venue, venue_exit = _venues_for(strategy_id, params)
    priced = attach_economics(raw, venue=venue, venue_exit=venue_exit)
    events = enrich_events(
        priced,
        index=index,
        strategy_id=strategy_id,
        frozen_params=params,
        oos_start_ns=int(oos["start_ts_ns"]),
        oos_end_ns_inclusive=int(oos["end_ts_ns_inclusive"]),
    )
    top = top_contributor_report(events)
    blocks = chrono_block_table(events)
    loo = all_loo(events)
    regimes = all_regimes(events)
    nulls = null_checks(events)
    stability = candidate.get("stability") or {}
    decision = classify(
        n_signals=len(events),
        blocks_with_signals=int(blocks.get("blocks_with_signals") or 0),
        top=top,
        loo=loo,
        regimes=regimes,
        nulls=nulls,
        tournament_top_route_share=stability.get("top_route_share"),
    )
    # Drop bulky per-event list from the persisted report; keep counts only.
    return {
        "strategy_id": strategy_id,
        "parent_verdict": candidate.get("verdict"),
        "parent_failed_gate": candidate.get("failed_gate"),
        "frozen_params": params,
        "tournament_expected_net": candidate.get("expected_net"),
        "tournament_execution_net": candidate.get("execution_net"),
        "tournament_stability": {
            "label": stability.get("label"),
            "top_symbol_share": stability.get("top_symbol_share"),
            "top_route_share": stability.get("top_route_share"),
            "by_symbol": stability.get("by_symbol"),
            "by_route": stability.get("by_route"),
        },
        "oos_replay_signals": len(events),
        "forensic_totals": totals(events),
        "top_contributors": top,
        "chrono_blocks": blocks,
        "decompositions": all_decompositions(events),
        "leave_one_out": loo,
        "regime_explanation": regimes,
        "null_checks": nulls,
        "classification": decision,
        "CONCENTRATION_SOURCE": decision["CONCENTRATION_SOURCE"],
        "CONCENTRATION_CLASS": decision["CONCENTRATION_CLASS"],
        "STRUCTURAL_FEATURE_FOUND": decision["STRUCTURAL_FEATURE_FOUND"],
        "RECOMMENDED_ACTION": decision["RECOMMENDED_ACTION"],
        "note": (
            "forensic_totals.NET is the sum of per-event waterfalls (descriptive). "
            "It is not tournament EXPECTED_NET and is not a new acceptance gate."
        ),
    }


def run_forensics(
    *,
    research_path: Path | str = "data/research_marketdata",
    tournament_report: Path | str = "data/research_tournament_report.json",
    out_dir: Path | str | None = None,
    docs_path: Path | str = "docs/CONCENTRATION_FORENSICS_REPORT.md",
    max_events: int | None = None,
    stride: int = 4,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    report = load_tournament_report(tournament_report)
    oos = report.get("OOS_WINDOW") or {}
    candidates = report.get("candidates") or {}
    missing = [s for s in TARGET_STRATEGIES if s not in candidates]
    if not oos or missing:
        return {
            "STATUS": "BLOCKED_BY_TOURNAMENT_REPORT",
            "PACKAGE": PACKAGE_LABEL,
            "criteria_version": FORENSICS_CRITERIA_VERSION,
            "missing": missing,
            "PRODUCTION_TRADING_CHANGED": False,
        }

    index = build_tape_index(Path(research_path), max_events=max_events, stride=stride)
    analyzed: dict[str, Any] = {}
    for sid in TARGET_STRATEGIES:
        analyzed[sid] = analyze_strategy(
            index=index,
            strategy_id=sid,
            candidate=candidates[sid],
            oos=oos,
        )

    hyp = register_forensics_hypotheses(analyzed)
    llm = maybe_llm_advisory(analyzed, enabled=llm_enabled)

    out: dict[str, Any] = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "criteria_version": FORENSICS_CRITERIA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "DATASET": report.get("DATASET_ID"),
        "DATA_DURATION": report.get("DATA_DURATION"),
        "STRATEGIES_ANALYZED": list(TARGET_STRATEGIES),
        "OOS_WINDOW": oos,
        "DEVELOPMENT_WINDOW": report.get("DEVELOPMENT_WINDOW"),
        "FREEZE_BOUNDARY": report.get("FREEZE_BOUNDARY"),
        "frozen_params_source": str(tournament_report),
        "stride": stride,
        "tape_points_indexed": index.peak_points,
        "tape_dataset_id": index.dataset_id,
        "bucket_definitions": bucket_manifest(),
        "strategies": analyzed,
        "CROSS_VENUE_DISLOCATION": _card(analyzed["cross_venue_dislocation"]),
        "SHORT_HORIZON_MEAN_REVERSION": _card(analyzed["short_horizon_mean_reversion"]),
        "NEW_HYPOTHESES_CREATED": hyp.get("created_ids") or [],
        "hypothesis_records": hyp,
        "LLM_USED": llm.get("used"),
        "llm_advisory": llm,
        "PRODUCTION_TRADING_CHANGED": False,
        "execution_enabled": False,
        "parent_strategies_modified": False,
        "NEXT_RESEARCH_ACTION": _next_action(analyzed, hyp),
        "notes": [
            "Rejected strategies were not modified.",
            "No parameter optimization.",
            "No fees/fills/PnL/OOS/execution changes.",
            "Do not claim profitability.",
        ],
    }

    dest = Path(out_dir or "data/research_forensics")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(
        json.dumps(out, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    compact = compact_dashboard(out)
    Path("data/concentration_forensics_report.json").write_text(
        json.dumps(compact, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    write_markdown(out, Path(docs_path))
    return out


def _card(block: dict[str, Any]) -> dict[str, Any]:
    top = block.get("top_contributors") or {}
    return {
        "CONCENTRATION_SOURCE": block.get("CONCENTRATION_SOURCE"),
        "CONCENTRATION_CLASS": block.get("CONCENTRATION_CLASS"),
        "STRUCTURAL_FEATURE_FOUND": block.get("STRUCTURAL_FEATURE_FOUND"),
        "RECOMMENDED_ACTION": block.get("RECOMMENDED_ACTION"),
        "forensic_NET": (block.get("forensic_totals") or {}).get("NET"),
        "top_symbol": top.get("top_symbol"),
        "top_venue_pair": top.get("top_venue_pair"),
        "top_chrono_block": top.get("top_chrono_block"),
        "positive_blocks": (block.get("chrono_blocks") or {}).get("positive_blocks"),
        "negative_blocks": (block.get("chrono_blocks") or {}).get("negative_blocks"),
        "route_share_tautology": top.get("route_share_tautology"),
    }


def _next_action(analyzed: dict[str, Any], hyp: dict[str, Any]) -> str:
    classes = {b.get("CONCENTRATION_CLASS") for b in analyzed.values()}
    created = hyp.get("created_ids") or []
    if created:
        return (
            "Queue the new independent hypotheses for a fresh DEV/OOS tournament. "
            "Do not inherit parent PnL. Do not implement as production strategies yet."
        )
    if "INSUFFICIENT_EVIDENCE" in classes:
        return "Keep recording tape; re-run forensics on a longer OOS without retuning."
    if "TIME_SPECIFIC" in classes:
        return "Inspect market conditions in the dominant chronological block. Do not add a time filter."
    if "RANDOM_CONCENTRATION" in classes:
        return "Leave both families REJECTED. Do not rescue via parameter search."
    return "Parents remain REJECTED. No production change."
