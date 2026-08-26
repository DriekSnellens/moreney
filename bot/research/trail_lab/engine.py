"""Grid search + walk-forward over trail harvest settings."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.trail_lab.paths import BagSeed, build_scenario_paths, load_bag_seeds
from bot.research.trail_lab.protocol import CURRENT_LIVE, config_id, iter_grid
from bot.research.trail_lab.simulator import score_result, simulate_bag


def _split_marks(marks: list[float]) -> tuple[list[float], list[float]]:
    mid = max(2, len(marks) // 2)
    return marks[:mid], marks[mid:]


def _eval_config(
    cfg: dict[str, float],
    scenarios: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    total_score = 0.0
    total_realized = 0.0
    total_be = 0
    total_exits = 0
    n = 0
    for sc in scenarios:
        bag: BagSeed = sc["bag"]
        marks: list[float] = sc["marks"]
        is_marks, oos_marks = _split_marks(marks)
        use = is_marks if phase == "is" else oos_marks
        # Continue trail state across IS→OOS by replaying full path for OOS
        # would leak; instead OOS starts fresh from OOS[0] with same cost/qty
        # residual capital assumption: full bag still held at OOS start.
        if phase == "oos" and use:
            start = use[0]
            # Scale qty notionally unchanged; path continues from OOS marks.
            pass
        res = simulate_bag(
            use,
            qty=bag.qty,
            cost=bag.cost,
            soft_arm_pct=cfg["soft_arm_pct"],
            soft_partial_pct=cfg["soft_partial_pct"],
            soft_drawdown_pct=cfg["soft_drawdown_pct"],
            hard_arm_pct=cfg["hard_arm_pct"],
            hard_drawdown_pct=cfg["hard_drawdown_pct"],
            hard_partial_pct=cfg["hard_partial_pct"],
            fee_rate=cfg["fee_rate"],
            sell_buffer_bps=cfg["sell_buffer_bps"],
        )
        sc_score = score_result(res)
        total_score += sc_score["score"]
        total_realized += sc_score["realized_eur"]
        total_be += int(sc_score["be_blocks"])
        total_exits += int(sc_score["trail_exits"])
        n += 1
    return {
        "n_scenarios": n,
        "score": total_score,
        "realized_eur": total_realized,
        "be_blocks": total_be,
        "trail_exits": total_exits,
        "score_per_scenario": total_score / n if n else 0.0,
        "realized_per_scenario": total_realized / n if n else 0.0,
    }


def run_trail_lab(
    *,
    bridge_path: Path | None = None,
    out_path: Path | None = None,
    n_ticks: int = 800,
    base_seed: int = 42,
) -> dict[str, Any]:
    bags = load_bag_seeds(bridge_path)
    scenarios = build_scenario_paths(bags, n_ticks=n_ticks, base_seed=base_seed)
    configs = iter_grid()

    rows: list[dict[str, Any]] = []
    for cfg in configs:
        is_m = _eval_config(cfg, scenarios, phase="is")
        oos_m = _eval_config(cfg, scenarios, phase="oos")
        rows.append(
            {
                "id": config_id(cfg),
                "config": cfg,
                "is": is_m,
                "oos": oos_m,
            }
        )

    # Pick on IS score; report OOS.
    rows_sorted = sorted(rows, key=lambda r: r["is"]["score"], reverse=True)
    best = rows_sorted[0]
    baseline_cfg = dict(CURRENT_LIVE)
    baseline = {
        "id": config_id(baseline_cfg),
        "config": baseline_cfg,
        "is": _eval_config(baseline_cfg, scenarios, phase="is"),
        "oos": _eval_config(baseline_cfg, scenarios, phase="oos"),
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data_mode": "SYNTHETIC_PATHS_PLUS_LIVE_BAG_COSTS",
        "bags": [asdict(b) if hasattr(b, "__dataclass_fields__") else b for b in bags],
        "n_scenarios": len(scenarios),
        "n_configs": len(configs),
        "baseline": baseline,
        "best_is": best,
        "top5_is": rows_sorted[:5],
        "delta_oos_realized_vs_baseline": (
            best["oos"]["realized_eur"] - baseline["oos"]["realized_eur"]
        ),
        "delta_oos_score_vs_baseline": (
            best["oos"]["score"] - baseline["oos"]["score"]
        ),
        "recommendation": _recommend(best, baseline),
        "all_results": rows_sorted,
    }

    target = out_path or Path("./data/trail_lab/results.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _recommend(best: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    oos_delta = best["oos"]["realized_eur"] - baseline["oos"]["realized_eur"]
    apply = oos_delta > 0.5  # at least +€0.50 aggregate OOS across scenarios
    return {
        "apply": apply,
        "reason": (
            "OOS realized beat baseline"
            if apply
            else "OOS edge vs baseline too small / negative — keep live settings"
        ),
        "best_config": best["config"],
        "baseline_config": baseline["config"],
        "oos_realized_delta_eur": oos_delta,
    }


def main() -> None:
    report = run_trail_lab()
    rec = report["recommendation"]
    best = report["best_is"]
    base = report["baseline"]
    print("TRAIL LAB")
    print(
        f"bags={len(report['bags'])} scenarios={report['n_scenarios']} "
        f"configs={report['n_configs']}"
    )
    print(
        f"baseline IS realized={base['is']['realized_eur']:.2f} "
        f"OOS realized={base['oos']['realized_eur']:.2f}"
    )
    print(
        f"best_is {best['id']} IS realized={best['is']['realized_eur']:.2f} "
        f"OOS realized={best['oos']['realized_eur']:.2f}"
    )
    print(
        f"OOS delta vs baseline: {report['delta_oos_realized_vs_baseline']:.2f} EUR"
    )
    print(f"recommend_apply={rec['apply']} ({rec['reason']})")
    print(f"best_config={rec['best_config']}")


if __name__ == "__main__":
    main()
