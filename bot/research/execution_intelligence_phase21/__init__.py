"""Phase 2.1 combined economic intelligence research report.

Usage:
  python -m bot.research.execution_intelligence_phase21 data/live_audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.intelligence.adverse_selection import assess_adverse_selection
from bot.intelligence.economic_attribution import EconomicAttributionStore
from bot.intelligence.execution_quality import assess_execution, classify_urgency
from bot.intelligence.market_regime_engine import MarketRegime, classify_market_regime, regime_fit_for_strategy
from bot.intelligence.outcome_learning import OutcomeLearningStore, empirical_multiplier, learning_confidence
from bot.intelligence.parameter_recommendation import generate_recommendations, recommendations_to_dict
from bot.research.execution_intelligence_ablation import (
    ABLATION_CONFIGS,
    compare_verdict,
    load_audit_candidates,
    run_ablation,
)
from bot.research.execution_intelligence_calibration import analyze_thresholds

_ZERO = Decimal("0")


def _regime_replay(candidates: list[Any]) -> dict[str, Any]:
    """Test regime mappings observation-only — never activates scoring."""
    mappings = {
        "neutral_all": {r: Decimal("1.0") for r in MarketRegime},
        "maker_default": None,
    }
    results: dict[str, Any] = {}
    for name, override in mappings.items():
        accepted = 0
        est_net = _ZERO
        for cand in candidates:
            marks = cand.marks if len(cand.marks) >= 2 else cand.marks + [cand.price] * max(0, 6 - len(cand.marks))
            regime = classify_market_regime(marks=marks, candidate_count=10)
            if override:
                fit = override.get(regime.regime, Decimal("0.5"))
            else:
                fit = regime_fit_for_strategy(strategy="maker_inventory", regime=regime.regime)
            # Observation-only: fit recorded but score contribution = 0 in live
            if fit >= Decimal("0.35") and regime.regime != MarketRegime.UNKNOWN:
                accepted += 1
                est_net += cand.notional * Decimal("0.008") * fit
        n = len(candidates) or 1
        results[name] = {
            "accept_rate": str((Decimal(accepted) / Decimal(n)).quantize(Decimal("0.001"))),
            "estimated_net_eur": str(est_net.quantize(Decimal("0.01"))),
            "live_scoring": "OFF",
        }
    return results


def _maker_taker_analysis(candidates: list[Any]) -> dict[str, Any]:
    maker_net = _ZERO
    taker_net = _ZERO
    maker_n = taker_n = 0
    for cand in candidates[:200]:
        est = cand.notional * Decimal("0.008")
        adv = assess_adverse_selection(marks=cand.marks or [cand.price], side="buy", order_price=cand.price)
        ex = assess_execution(
            maker_net_eur=est * Decimal("0.65"),
            taker_net_eur=est * Decimal("0.92"),
            urgency=classify_urgency(),
        )
        if ex.decision.value == "MAKER":
            maker_net += est * Decimal("0.65")
            maker_n += 1
        elif ex.decision.value == "TAKER":
            taker_net += est * Decimal("0.92")
            taker_n += 1
    return {
        "maker_decisions": maker_n,
        "taker_decisions": taker_n,
        "estimated_maker_net_eur": str(maker_net.quantize(Decimal("0.01"))),
        "estimated_taker_net_eur": str(taker_net.quantize(Decimal("0.01"))),
    }


def _learning_analysis(store: OutcomeLearningStore) -> dict[str, Any]:
    rows = []
    for key, bucket in store.buckets.items():
        mult = empirical_multiplier(bucket=bucket)
        conf, n = learning_confidence(bucket)
        rows.append({"key": key, "samples": n, "confidence": conf, "multiplier": str(mult)})
    return {"buckets": rows}


def build_report(source: Path) -> dict[str, Any]:
    candidates = load_audit_candidates(source)
    ablation = run_ablation(candidates)
    ablation_snap = {k: v.snapshot() for k, v in ablation.items()}
    verdict = compare_verdict(ablation)

    attr_store = EconomicAttributionStore()
    for cand in candidates:
        marks = cand.marks if len(cand.marks) >= 2 else cand.marks + [cand.price] * max(0, 6 - len(cand.marks))
        adv = assess_adverse_selection(marks=marks, side="buy", order_price=cand.price)
        rec = attr_store.record_opportunity(
            record_id=f"replay-{cand.symbol}-{cand.ts.isoformat()}",
            symbol=cand.symbol,
            venue=cand.venue,
            strategy="maker_inventory",
            side="buy",
            score_before=Decimal("60"),
            score_after=Decimal("58"),
            adverse_score=adv.adverse_selection_score,
            expected_net=cand.notional * Decimal("0.008"),
            order_price=cand.price,
            size=cand.quantity,
            experiment_id="baseline",
        )
        if adv.adverse_selection_score >= Decimal("0.70"):
            attr_store.record_cancel(
                rec,
                reason="adverse_selection_high",
                avoided_loss=cand.notional * Decimal("0.008") * adv.adverse_selection_score,
                missed_opportunity=cand.notional * Decimal("0.008") * Decimal("0.1"),
                live_executed=False,
            )

    threshold_analysis = analyze_thresholds(candidates)
    recommendations = generate_recommendations(
        attr_store,
        adverse_threshold=Decimal("0.70"),
        regime_scoring_enabled=False,
    )

    total_net = Decimal(ablation_snap.get("BASELINE", {}).get("estimated_net_eur", "0"))
    n = len(candidates) or 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "historical_fills": n,
        "baseline": ablation_snap.get("BASELINE"),
        "adverse_threshold_analysis": threshold_analysis,
        "ablation": ablation_snap,
        "ablation_verdict": verdict,
        "maker_taker": _maker_taker_analysis(candidates),
        "capital_velocity": {
            "baseline_net_eur": ablation_snap.get("BASELINE", {}).get("estimated_net_eur"),
            "note": "NET/capital-hour requires paired exit data — unavailable from audit-only replay",
            "net_per_capital_hour": "unavailable",
        },
        "symbol_analysis": attr_store.top_groups(attr_store.symbol_stats),
        "venue_analysis": attr_store.top_groups(attr_store.venue_stats),
        "regime_replay": _regime_replay(candidates),
        "adverse_calibration": attr_store.adverse_calibration(),
        "fill_probability_calibration": attr_store.fill_probability_calibration(),
        "score_calibration": attr_store.score_calibration(),
        "score_monotonicity_ok": attr_store.score_monotonicity_ok(),
        "learning": _learning_analysis(OutcomeLearningStore()),
        "cancel_alpha": attr_store.cancel_alpha_summary(),
        "recommendations": recommendations_to_dict(recommendations),
        "regime_live_scoring": "OFF",
    }


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report.get("baseline") or {}
    thresh = report.get("adverse_threshold_analysis") or {}
    recs = report.get("recommendations") or []
    lines = [
        "# Execution Intelligence Phase 2.1 Report",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Source: {report.get('source')}",
        f"Historical candidates: {report.get('historical_fills')}",
        "",
        "## Baseline",
        f"- NET (estimated): €{baseline.get('estimated_net_eur', '—')}",
        f"- Accept rate: {baseline.get('accept_rate', '—')}",
        "",
        "## Best adverse threshold (cancel alpha)",
        f"- Threshold: {thresh.get('best_threshold_by_cancel_alpha', '—')}",
        f"- Cancel alpha: €{thresh.get('best_cancel_alpha_eur', '—')}",
        "",
        "## Cancel alpha",
        json.dumps(report.get("cancel_alpha") or {}, indent=2),
        "",
        "## Regime",
        "- LIVE SCORING: OFF",
        f"- Replay: {json.dumps(report.get('regime_replay') or {}, indent=2)}",
        "",
        "## Recommendations (auto_apply=false)",
    ]
    for r in recs:
        lines.append(f"- **{r.get('parameter')}**: current={r.get('current')} → recommended={r.get('recommended')} ({r.get('confidence')})")
    lines.append("")
    lines.append("## Ablation verdict")
    lines.append(json.dumps(report.get("ablation_verdict") or {}, indent=2))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 2.1 economic intelligence report")
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/research/execution_intelligence_phase21.json"),
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=Path("data/research/execution_intelligence_phase21.md"),
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"Not found: {args.source}", file=sys.stderr)
        return 1

    report = build_report(args.source)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.md_out.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
