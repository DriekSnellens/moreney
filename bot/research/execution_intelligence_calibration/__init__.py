"""Adverse threshold calibration replay.

Usage:
  python -m bot.research.execution_intelligence_calibration data/live_audit.jsonl
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
from bot.intelligence.economic_attribution import SHADOW_THRESHOLDS, _d
from bot.research.execution_intelligence_ablation import load_audit_candidates

_ZERO = Decimal("0")
_ONE = Decimal("1")


def analyze_thresholds(candidates: list[Any]) -> dict[str, Any]:
    """Per-threshold cancel/fill/alpha analysis — no look-ahead."""
    thresholds = [Decimal(str(t)) for t in SHADOW_THRESHOLDS]
    results: dict[str, dict[str, Any]] = {}

    for thr in thresholds:
        key = str(thr)
        cancels = 0
        fills_kept = 0
        avoided = _ZERO
        missed = _ZERO
        est_net_kept = _ZERO
        est_net_all = _ZERO

        for cand in candidates:
            marks = cand.marks if len(cand.marks) >= 2 else cand.marks + [cand.price] * max(0, 6 - len(cand.marks))
            adv = assess_adverse_selection(
                marks=marks,
                side="buy",
                order_price=cand.price,
            )
            est_net = cand.notional * Decimal("0.008")
            est_net_all += est_net

            if adv.adverse_selection_score >= thr:
                cancels += 1
                # Proxy: high adverse → likely toxic, avoided loss = est_net * adverse
                avoided += est_net * adv.adverse_selection_score
                missed += est_net * (_ONE - adv.adverse_selection_score) * Decimal("0.3")
            else:
                fills_kept += 1
                est_net_kept += est_net

        cancel_alpha = avoided - missed
        n = len(candidates) or 1
        results[key] = {
            "threshold": key,
            "cancels": cancels,
            "fills_kept": fills_kept,
            "cancel_rate": str((Decimal(cancels) / Decimal(n)).quantize(Decimal("0.001"))),
            "estimated_avoided_loss_eur": str(avoided.quantize(Decimal("0.01"))),
            "estimated_missed_opportunity_eur": str(missed.quantize(Decimal("0.01"))),
            "cancel_alpha_eur": str(cancel_alpha.quantize(Decimal("0.01"))),
            "estimated_net_kept_eur": str(est_net_kept.quantize(Decimal("0.01"))),
            "estimated_net_all_eur": str(est_net_all.quantize(Decimal("0.01"))),
        }

    best = max(results.values(), key=lambda r: _d(r.get("cancel_alpha_eur")))
    return {
        "candidate_count": len(candidates),
        "thresholds": results,
        "best_threshold_by_cancel_alpha": best.get("threshold"),
        "best_cancel_alpha_eur": best.get("cancel_alpha_eur"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adverse threshold calibration")
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/execution_intelligence_calibration.json"),
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"Not found: {args.source}", file=sys.stderr)
        return 1

    candidates = load_audit_candidates(args.source)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(args.source),
        **analyze_thresholds(candidates),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
