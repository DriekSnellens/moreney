"""CLI: run toxicity evaluation on frozen paper state.

Usage:
  .venv/bin/python -m bot.opportunity.toxicity.runner data/paper_25000live.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bot.opportunity.toxicity.dataset import build_labeled_events, simulator_fingerprint
from bot.opportunity.toxicity.walkforward import (
    age_ablation,
    calibration_by_decile,
    compare_policies,
    loss_forensics,
    trade_through_analysis,
    untouched_oos_eval,
    walk_forward_toxicity,
)


def run(path: Path) -> dict[str, Any]:
    fingerprint = simulator_fingerprint(path)
    events = build_labeled_events(path)
    policies = compare_policies(events)
    # Strip nested _events from policy summaries for top-level size; keep forensics
    policy_summaries = {}
    for name, body in policies["policies"].items():
        policy_summaries[name] = {k: v for k, v in body.items() if k != "_events"}

    report: dict[str, Any] = {
        "source": str(path),
        "n_labeled_fills": len(events),
        "simulator_fingerprint": fingerprint,
        "A_forensics_losses": loss_forensics(events),
        "B_feature_notes": {
            "available_pretrade": [
                "venue",
                "route",
                "symbol",
                "side",
                "strategy",
                "fill_type(expected trade_through)",
                "spread_bps(from expected_gross/notional)",
                "spread_bucket",
                "book_age_ms(when present in decision_log)",
                "expected_net/fees/slippage/buffer",
            ],
            "missing_or_sparse": [
                "live volatility",
                "imbalance",
                "microprice",
                "quote age (often unknown in historical dump)",
                "fair-value deviation",
            ],
            "label": (
                "5s adverse bps proxy = realized_adverse_eur / notional * 1e4 "
                "(per-trade horizon join not stored in markout export)"
            ),
        },
        "C_model_comparison": policies["models_under_toxicity_policy"],
        "D_calibration": calibration_by_decile(events),
        "E_causal_replay_policies": policy_summaries,
        "F_untouched_oos": untouched_oos_eval(events),
        "G_quote_age_ablation": age_ablation(events),
        "H_trade_through": trade_through_analysis(events),
        "I_shadow_policy": {
            "mode": "shadow_only",
            "alters_execution": False,
            "in_sample": policy_summaries.get("C_TOXICITY"),
            "with_early_stop": policy_summaries.get("D_TOXICITY_PLUS_EARLY_STOP"),
        },
        "J_production_recommendation": None,  # filled below
    }

    # Decision rule for recommendation
    oos = report["F_untouched_oos"]
    cal = report["D_calibration"]
    tox = policy_summaries.get("C_TOXICITY") or {}
    base = policy_summaries.get("A_BASELINE") or {}
    separates = bool(cal.get("separates_tail"))
    oos_hier = oos.get("C_HIERARCHICAL") or {}
    oos_base = oos.get("BASELINE_TAKE_ALL") or {}
    try:
        oos_better = float(oos_hier.get("realized_net") or 0) > float(
            oos_base.get("realized_net") or 0
        )
    except Exception:
        oos_better = False
    rejects_everything = int(tox.get("completed_trades") or 0) == 0 and int(
        tox.get("rejected") or 0
    ) > 0
    if rejects_everything:
        rec = "reject_model_as_not_predictive"
        why = (
            "In-sample toxicity admission rejects all quotes: expected NET margin "
            "(~8–12 bps of notional) is below E[adverse|fill] prior (~15–30 bps "
            "observed). Gate is not selectively filtering a toxic tail — it "
            "implies the quote set is not +EV under trade-through adverse. "
            "Keep shadow instrumentation; do not enable live blocking."
        )
    elif separates and oos_better and int(oos_hier.get("completed_trades") or 0) > 0:
        rec = "shadow_only"
        why = (
            "Some separation and OOS NET improvement on tiny n — keep shadow; "
            "do not enable live blocking until larger OOS."
        )
    elif not separates:
        rec = "reject_model_as_not_predictive"
        why = (
            "Walk-forward predictions do not reliably separate higher vs lower "
            "observed adverse on this sample."
        )
    else:
        rec = "shadow_only"
        why = "Inconclusive tiny-n; remain in shadow mode."

    report["J_production_recommendation"] = {
        "recommendation": rec,
        "why": why,
        "n": len(events),
        "separates_tail": separates,
        "oos_toxicity_net": oos_hier.get("realized_net"),
        "oos_baseline_net": oos_base.get("realized_net"),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    path = Path(argv[1] if len(argv) > 1 else "data/paper_25000live.json")
    report = run(path)
    dest = Path("data/toxicity_pretrade_report.json")
    try:
        dest.write_text(json.dumps(report, indent=2, default=str))
    except OSError:
        dest = Path("/tmp/toxicity_pretrade_report.json")
        dest.write_text(json.dumps(report, indent=2, default=str))
    # Compact stdout
    summary = {
        "n": report["n_labeled_fills"],
        "fingerprint_net": report["simulator_fingerprint"]["realized_net_sum"],
        "policies": {
            k: {
                "net": v.get("realized_net"),
                "completed": v.get("completed_trades"),
                "rejected": v.get("rejected"),
            }
            for k, v in report["E_causal_replay_policies"].items()
        },
        "oos": report["F_untouched_oos"],
        "calibration_separates": report["D_calibration"].get("separates_tail"),
        "recommendation": report["J_production_recommendation"],
        "wrote": str(dest),
    }
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
