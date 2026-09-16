#!/usr/bin/env python3
"""Pass-2 focused search around higher clip utilization."""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import artifacts.weekly_pnl_config_search as s

OUT = Path(__file__).resolve().parent / "weekly_pnl_config_search_pass2.json"


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    first = s.monday(end) - timedelta(weeks=11)
    mid = first + timedelta(weeks=8)
    start_ms = int(first.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    mid_ms = int(mid.timestamp() * 1000)

    combos: list[dict] = []

    def add(name: str, **ov) -> None:
        combos.append({"name": name, "overrides": ov})

    add("baseline_wr_20k")
    add("clip10k_p3", clip_eur=10_000.0, max_positions=3)
    for clip in (10_000, 12_000, 15_000):
        for hours, excess, green, breadth in [
            ((7, 13, 16), 0.025, 0.0, 0.5),
            ((7, 13), 0.025, 0.0, 0.5),
            ((7, 13, 16), 0.02, 8.0, 0.6),
            ((7, 13), 0.02, 8.0, 0.6),
            ((7, 10, 13, 16), 0.02, 0.0, 0.5),
            ((7, 13), 0.015, 8.0, 0.6),
        ]:
            add(
                f"c{clip}_h{len(hours)}_e{excess}_g{green}_b{breadth}",
                clip_eur=float(clip),
                max_positions=3,
                decision_hours_utc=hours,
                min_excess=excess,
                green_deadline_hours=green,
                green_min_peak=0.01,
                min_breadth=breadth,
            )

    print(f"combos={len(combos)}", flush=True)
    results = []
    with ProcessPoolExecutor(
        max_workers=8, initializer=s.init_worker, initargs=(end_ms, start_ms, mid_ms)
    ) as pool:
        futs = {pool.submit(s.eval_one, p): p["name"] for p in combos}
        for i, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(combos):
                print(f"  {i}/{len(combos)}", flush=True)

    results.sort(key=lambda r: (r["oos"]["median_week_eur"], r["score_oos"]), reverse=True)
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "best_oos": results[0],
        "best_full_median": sorted(
            results, key=lambda r: r["full"]["median_week_eur"], reverse=True
        )[0],
        "best_ge_1000": sorted(
            results,
            key=lambda r: (r["full"]["pct_weeks_ge_1000"], r["full"]["median_week_eur"]),
            reverse=True,
        )[0],
        "top": results[:15],
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    for r in results[:12]:
        f, o = r["full"], r["oos"]
        print(
            f"{r['name'][:46]:46} med={f['median_week_eur']:7.1f} mean={f['mean_week_eur']:7.1f} "
            f"ge1k={f['pct_weeks_ge_1000']:.0%} tot={f['total_eur']:8.0f} dd={f['max_dd_eur']:8.0f} "
            f"wr={f['win_rate']:.0%} | oos med={o['median_week_eur']:7.1f}"
        )
    print("BEST_OOS", out["best_oos"]["name"], out["best_oos"]["overrides"])
    print(
        "BEST_FULL_MED",
        out["best_full_median"]["name"],
        out["best_full_median"]["full"]["median_week_eur"],
    )
    print(
        "BEST_GE1K",
        out["best_ge_1000"]["name"],
        out["best_ge_1000"]["full"]["pct_weeks_ge_1000"],
        out["best_ge_1000"]["full"]["median_week_eur"],
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
