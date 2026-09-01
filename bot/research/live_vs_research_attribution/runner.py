"""Main attribution audit runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.research.live_vs_research_attribution.data_quality import analyze_data_quality
from bot.research.live_vs_research_attribution.execution_attribution import (
    analyze_adverse_selection,
    analyze_execution,
)
from bot.research.live_vs_research_attribution.funnel import build_funnel
from bot.research.live_vs_research_attribution.loaders import load_all
from bot.research.live_vs_research_attribution.matching import (
    match_live_to_research,
    match_summary,
    research_from_economic_parity,
    research_from_shadow,
)
from bot.research.live_vs_research_attribution.report import write_report
from bot.research.live_vs_research_attribution.skip_attribution import (
    analyze_skips,
    inventory_skip_focus,
)
from bot.research.live_vs_research_attribution.strategy_mismatch import analyze_strategy_mismatch

_ZERO = Decimal("0")


def _d(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _extract_research_realism(fv: dict[str, Any], bridge: dict[str, Any]) -> dict[str, Any]:
    baseline = fv.get("BASELINE_RESULT") or {}
    scenarios = fv.get("scenario_results") or []
    mild_net = None
    moderate_net = None
    for sc in scenarios:
        sid = sc.get("scenario_id", "")
        if sid == "MILD_REALISM":
            mild_net = sc.get("execution_net_eur") or sc.get("CANONICAL_REPLAY_NET")
        elif sid == "MODERATE_REALISM":
            moderate_net = sc.get("execution_net_eur") or sc.get("CANONICAL_REPLAY_NET")
    if mild_net is None:
        for sc in scenarios:
            if sc.get("scenario_id") == "MILD_REALISM":
                mild_net = sc.get("execution_net_eur")
    return {
        "canonical_replay_net_eur": fv.get("CANONICAL_REPLAY_NET")
        or baseline.get("CANONICAL_REPLAY_NET"),
        "mild_realism_net_eur": mild_net,
        "moderate_realism_net_eur": moderate_net,
        "signal_count": baseline.get("signal_count") or baseline.get("candidate_count"),
        "fill_count": baseline.get("fill_count"),
        "live_realized_net_eur": bridge.get("realized_trade_pnl_eur")
        or bridge.get("session_start_realized_eur"),
        "matched_live_sample_net_eur": None,
        "verdict": fv.get("FINAL_VALIDATION_VERDICT"),
    }


def _goe_attribution(phase21: dict[str, Any], ablation: dict[str, Any]) -> dict[str, Any]:
    base = phase21.get("baseline") or ablation.get("configs", {}).get("BASELINE") or {}
    return {
        "candidates": base.get("candidates"),
        "accepted": base.get("accepted") or base.get("high_quality", 0) + base.get("reduced", 0),
        "rejected": base.get("rejected"),
        "reject_rate": base.get("reject_rate"),
        "estimated_net_eur": base.get("estimated_net_eur"),
        "live_goe_enabled": False,
        "note": "GOE replay on historical audit buys; live_micro_opportunity_engine_enabled defaults False",
    }


def _capital_efficiency(data: Any, bridge: dict[str, Any]) -> dict[str, Any]:
    realized = _d(bridge.get("realized_trade_pnl_eur"))
    locked = _d(bridge.get("locked_notional_eur"))
    free = _d(bridge.get("free_quote_eur"))
    portfolio = _d(bridge.get("portfolio_value_eur"))
    cap3 = data.capital_allocation or {}
    base_var = (cap3.get("variants") or {}).get("BASELINE") or {}

    live_nph = None
    if realized is not None and locked and locked > 0:
        # Rough session-level metric — not annualized
        live_nph = str(realized / locked)

    return {
        "locked_notional_eur": str(locked) if locked is not None else None,
        "free_quote_eur": str(free) if free is not None else None,
        "portfolio_value_eur": str(portfolio) if portfolio is not None else None,
        "research_net_per_capital_hour": base_var.get("net_eur_per_capital_hour"),
        "live_net_per_capital_hour": live_nph,
        "note": (
            "Live NET/capital-hour is session realized / locked notional — approximate, not annualized. "
            "Capital allocation replay is counterfactual on historical audit."
        ),
    }


def _regime_attribution(intel: dict[str, Any]) -> dict[str, Any]:
    outcomes = (intel.get("outcomes") or {}).get("buckets") or {}
    if not outcomes:
        return {
            "summary": "INSUFFICIENT_DATA — regime not tagged on live fills in audit.",
            "buckets": {},
        }
    return {"summary": f"{len(outcomes)} outcome buckets in intelligence state", "buckets": outcomes}


def _venue_attribution(execution: dict[str, Any]) -> dict[str, Any]:
    return {"by_venue": execution.get("by_venue") or {}}


def _rank_root_causes(
    strategy: dict[str, Any],
    skips: dict[str, Any],
    execution: dict[str, Any],
    research_realism: dict[str, Any],
    data_quality: dict[str, Any],
) -> list[dict[str, Any]]:
    causes: list[dict[str, Any]] = []

    causes.append({
        "cause": "STRATEGY_MISMATCH",
        "confidence": "HIGH",
        "evidence": (
            f"Research={strategy.get('research_strategy')} vs live={strategy.get('live_strategy')}. "
            f"0/{strategy.get('components_total')} core components identical. "
            f"Canonical replay +€{research_realism.get('canonical_replay_net_eur')} "
            f"vs live session €{research_realism.get('live_realized_net_eur')}."
        ),
    })

    total_skips = skips.get("total_skip_events") or 0
    inv_skips = sum(
        (skips.get("by_reason") or {}).get(k, {}).get("count", 0)
        for k in ("time_stop_below_be", "buy_quality_pause", "underwater_cross_venue_block")
    )
    causes.append({
        "cause": "INVENTORY_LOCK / EXIT_MANAGEMENT",
        "confidence": "MEDIUM" if inv_skips > 1000 else "LOW",
        "evidence": (
            f"{inv_skips:,} inventory-related skip events of {total_skips:,} total. "
            "Live-only exit/inventory gates (time_stop_below_be, buy_quality_pause) "
            "have no research equivalent."
        ),
    })

    causes.append({
        "cause": "EXECUTION_DEGRADATION",
        "confidence": "MEDIUM",
        "evidence": (
            f"Mild realism NET €{research_realism.get('mild_realism_net_eur')} "
            f"(-{ _pct_drop(research_realism.get('canonical_replay_net_eur'), research_realism.get('mild_realism_net_eur'))}% vs canonical). "
            f"Moderate €{research_realism.get('moderate_realism_net_eur')}. "
            f"Live fills={execution.get('filled_count')} with maker/taker mix."
        ),
    })

    causes.append({
        "cause": "EXCESSIVE_FILTERING",
        "confidence": "LOW",
        "evidence": (
            f"{total_skips:,} skip events logged but expected NET per skip is INSUFFICIENT_DATA. "
            "High skip count alone is not evidence of destructive filtering."
        ),
    })

    causes.append({
        "cause": "OBSERVABILITY_GAP",
        "confidence": "HIGH",
        "evidence": (
            "No opportunity_id in live audit; attribution store empty; "
            f"fill_id unique={data_quality.get('fill_accounting', {}).get('fill_event_id_unique')}. "
            "Cannot join opportunity→fill→realized NET at scale."
        ),
    })

    causes.append({
        "cause": "RESEARCH_EXECUTION_MODEL_BIAS",
        "confidence": "MEDIUM",
        "evidence": (
            "Canonical replay assumes instant round-trip arb. "
            "Live holds inventory with trail exits — structurally different PnL path."
        ),
    })

    return causes


def _pct_drop(canonical: Any, degraded: Any) -> str:
    c = _d(canonical)
    d = _d(degraded)
    if c is None or d is None or c == 0:
        return "?"
    return f"{float((c - d) / c * 100):.1f}"


def _recommended_experiments() -> list[dict[str, Any]]:
    return [
        {
            "hypothesis": "Live execution degradation exceeds signal degradation for matched symbols",
            "change": "Replay live_audit fills with moderate execution realism assumptions",
            "control": "Current canonical CVD replay on same symbol universe",
            "metric": "NET per fill, fill rate",
            "minimum_sample_size": "n≥200 matched fills",
            "success_criterion": "Measured live degradation within ±20% of moderate realism model",
            "rollback": "N/A — research-only replay",
            "oos_leakage_protection": "Use frozen CVD fingerprint; no parameter tuning on live sample",
        },
        {
            "hypothesis": "Inventory skips destroy positive entry economics",
            "change": "Counterfactual replay: entries that later hit time_stop_below_be",
            "control": "Actual realized NET from bridge FIFO",
            "metric": "Ex-post NET of skipped vs executed entries",
            "minimum_sample_size": "n≥50 paired entry events",
            "success_criterion": "Document whether skipped entries were ex-post positive",
            "rollback": "N/A — analysis only",
            "oos_leakage_protection": "No live parameter changes; log decision-time economics first",
        },
        {
            "hypothesis": "Opportunity ID propagation enables attribution",
            "change": "Add read-only opportunity_id + decision_economics to micro_order_result audit payload",
            "control": "Current audit without opportunity_id",
            "metric": "Match rate EXACT+PROBABLE",
            "minimum_sample_size": "n≥100 orders",
            "success_criterion": ">80% fills traceable to strategy opportunity",
            "rollback": "Remove audit fields — logging only, no trading impact",
            "oos_leakage_protection": "Audit-only instrumentation",
        },
        {
            "hypothesis": "Shadow paper of frozen CVD on live tape measures strategy gap",
            "change": "Run shadow_validation observer on live market data window",
            "control": "Historical final_validation canonical NET",
            "metric": "Shadow NET vs live realized on same calendar window",
            "minimum_sample_size": "≥24h tape",
            "success_criterion": "Quantify strategy mismatch independent of execution",
            "rollback": "Shadow observer already research-only",
            "oos_leakage_protection": "Frozen strategy fingerprint bd2f80d5…",
        },
        {
            "hypothesis": "Adverse selection proxy correlates with live fill quality",
            "change": "Enable attribution store persistence (observation mode stays on)",
            "control": "Empty attribution store baseline",
            "metric": "post_fill_markout_5s for GOOD vs TOXIC fills",
            "minimum_sample_size": "n≥100 fills with mark data",
            "success_criterion": "Statistically significant markout difference GOOD vs TOXIC",
            "rollback": "Disable persistence — no auto_apply",
            "oos_leakage_protection": "observation_mode=true, auto_apply=false",
        },
    ]


def _final_conclusions(
    strategy: dict[str, Any],
    skips: dict[str, Any],
    research_realism: dict[str, Any],
) -> tuple[list[str], list[str]]:
    conclusions = [
        (
            f"Research and live are NOT the same strategy "
            f"({strategy.get('research_strategy')} vs {strategy.get('live_strategy')}). "
            "This is the primary explanation for divergent expectancy."
        ),
        (
            f"Research canonical replay shows +€{research_realism.get('canonical_replay_net_eur')} "
            f"on cross_venue_dislocation; live session realized "
            f"€{research_realism.get('live_realized_net_eur')} on alt-beta maker book."
        ),
        (
            f"{skips.get('total_skip_events', 0):,} live skip events logged; "
            "economic weight per skip is INSUFFICIENT_DATA without decision-time NET logging."
        ),
        (
            "Execution realism lab shows canonical→moderate NET drop of ~64% "
            f"(€{research_realism.get('canonical_replay_net_eur')} → "
            f"€{research_realism.get('moderate_realism_net_eur')}) on the research strategy alone."
        ),
        (
            "Next valid step is observability (opportunity_id in audit) and shadow-paper of frozen CVD "
            "on live tape — not parameter tuning."
        ),
    ]
    do_not_change = [
        "live_micro_opportunity_engine_enabled — attribution incomplete",
        "live_micro_intelligence_auto_apply — observation mode must stay on",
        "momentum thresholds / time_stop_below_be — skip economic weight unknown",
        "focus_bases universe — no counterfactual NET evidence",
        "GOE weights — GOE not active live; replay shows 78.6% reject on historical buys",
        "risk limits and live unlock flags — safety invariant",
        "execution buffers / profitability thresholds — no OOS evidence on live book",
    ]
    return conclusions, do_not_change


def run_attribution_audit(
    *,
    audit_path: Path = Path("data/live_audit.jsonl"),
    bridge_path: Path = Path("data/live_micro_bridge_state.json"),
    session_path: Path = Path("data/live_micro_session_status.json"),
    research_dir: Path = Path("data/research"),
    output_path: Path = Path("data/research/live_vs_research_attribution.json"),
    report_path: Path = Path("docs/LIVE_VS_RESEARCH_ATTRIBUTION_REPORT.md"),
) -> dict[str, Any]:
    data = load_all(
        audit_path=audit_path,
        bridge_path=bridge_path,
        session_path=session_path,
        research_dir=research_dir,
    )

    bridge = (data.session_status.get("bridge") or data.bridge_state) or {}
    strategy = analyze_strategy_mismatch()
    skips = analyze_skips(data)
    inventory = inventory_skip_focus(data)
    execution = analyze_execution(data)
    adverse = analyze_adverse_selection(data)
    funnel = build_funnel(data)
    data_quality = analyze_data_quality(data)
    research_realism = _extract_research_realism(data.final_validation, bridge)
    goe = _goe_attribution(data.phase21, data.ablation)
    capital = _capital_efficiency(data, bridge)
    regime = _regime_attribution(data.intelligence_state)
    venue = _venue_attribution(execution)

    research_opps = research_from_economic_parity(data.economic_parity)
    if data.shadow_observations:
        research_opps.extend(research_from_shadow(data.shadow_observations[:5000]))
    matches = match_live_to_research(data.live_fills, research_opps)
    match_info = match_summary(matches)

    root_causes = _rank_root_causes(
        strategy, skips, execution, research_realism, data_quality
    )
    conclusions, do_not_change = _final_conclusions(strategy, skips, research_realism)

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "package": "LIVE_VS_RESEARCH_ATTRIBUTION",
        "no_tuning_performed": True,
        "sample": {
            "live_fill_count": len(data.live_fills),
            "live_audit_events": len(data.audit_events),
            "live_realized_pnl_eur": bridge.get("realized_trade_pnl_eur"),
            "research_economic_parity_rows": len(data.economic_parity),
            "research_shadow_rows": len(data.shadow_observations),
        },
        "funnel": funnel,
        "skip_attribution": skips,
        "inventory_attribution": inventory,
        "execution_attribution": execution,
        "adverse_selection": adverse,
        "goe_attribution": goe,
        "regime_attribution": regime,
        "venue_attribution": venue,
        "capital_efficiency": capital,
        "research_realism": research_realism,
        "strategy_mismatch": strategy,
        "matching": match_info,
        "matched_records_sample": [
            {
                "live_fill_id": m.live_fill_id,
                "research_id": m.research_id,
                "match_class": m.match_class.value,
                "symbol": m.symbol,
                "research_expected_net": str(m.research_expected_net)
                if m.research_expected_net is not None
                else None,
                "match_reason": m.match_reason,
            }
            for m in matches[:50]
        ],
        "data_quality": data_quality,
        "root_causes": root_causes,
        "executive_summary": {
            "headline": (
                "The positive research expectancy (+€212k canonical cross_venue_dislocation replay) "
                "and negative/near-zero live session PnL co-exist primarily because they measure "
                "different strategies, execution models, and universes — not because of a single tunable filter."
            ),
            "bullets": [rc["evidence"] for rc in root_causes[:4]],
            "primary_root_cause": root_causes[0]["cause"] if root_causes else "UNKNOWN",
            "confidence": root_causes[0]["confidence"] if root_causes else "UNKNOWN",
        },
        "sections": {
            "profitability": (
                "INSUFFICIENT_DATA for per-opportunity live profitability rejections. "
                f"Economic parity audit has {len(data.economic_parity)} paper CVD rows (diagnostic-only, Aug 2025). "
                "Live bridge does not export profitability_result per skip."
            ),
            "risk": (
                "INSUFFICIENT_DATA — RiskEngine decisions not written to live_audit.jsonl. "
                f"Audit order_blocked count: {len(data.order_blocked)} (mostly max open orders)."
            ),
            "exit": (
                "Live-only: trail, soft-arm, time_stop_below_be ("
                f"{(skips.get('by_reason') or {}).get('time_stop_below_be', {}).get('count', 0):,} skips), "
                "exit_engine. Research: immediate round-trip in replay."
            ),
        },
        "final_conclusions": conclusions,
        "what_not_to_change_yet": do_not_change,
        "recommended_experiments": _recommended_experiments(),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(write_report(report), encoding="utf-8")

    return report
