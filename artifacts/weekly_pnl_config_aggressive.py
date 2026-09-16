#!/usr/bin/env python3
"""Aggressive follow-up around higher clip utilization on €20k / 12w."""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import artifacts.weekly_pnl_config_search as s

OUT = Path(__file__).resolve().parent / "weekly_pnl_config_aggressive.json"


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    first = s.monday(end) - timedelta(weeks=11)
    mid = first + timedelta(weeks=8)
    start_ms = int(first.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    mid_ms = int(mid.timestamp() * 1000)
    print(f"window {first.date()} -> {end.date()}", flush=True)

    cands = [
        dict(name="wr_base", overrides={}),
        dict(name="clip10k_p3", overrides=dict(clip_eur=10000, max_positions=3)),
        dict(name="clip12k_p2", overrides=dict(clip_eur=12000, max_positions=2)),
        dict(name="clip15k_p2", overrides=dict(clip_eur=15000, max_positions=2)),
        dict(
            name="clip10k_e02",
            overrides=dict(clip_eur=10000, max_positions=3, min_excess=0.02),
        ),
        dict(
            name="clip10k_h2",
            overrides=dict(
                clip_eur=10000,
                max_positions=3,
                decision_hours_utc=(7, 13),
                min_excess=0.025,
            ),
        ),
        dict(
            name="clip12k_p3",
            overrides=dict(clip_eur=12000, max_positions=3, min_excess=0.025),
        ),
        dict(name="mp4_c8k", overrides=dict(clip_eur=8000, max_positions=4)),
        dict(
            name="loose_trail_c10k",
            overrides=dict(
                clip_eur=10000,
                max_positions=3,
                trail_pct=0.05,
                trail_tight_after=0.06,
                trail_tight_pct=0.03,
                time_exit_hours=48,
            ),
        ),
        dict(
            name="clip10k_conv",
            overrides=dict(
                clip_eur=10000, max_positions=3, alphai_size_mode="conviction"
            ),
        ),
        dict(
            name="clip10k_limits",
            overrides=dict(
                clip_eur=10000,
                max_positions=3,
                day_loss_limit_eur=750,
                week_loss_limit_eur=2000,
            ),
        ),
        dict(
            name="rec_pack",
            overrides=dict(
                clip_eur=10000,
                max_positions=3,
                day_loss_limit_eur=750,
                week_loss_limit_eur=2000,
                alphai_size_mode="conviction",
            ),
        ),
    ]

    rows: list[dict] = []
    with ProcessPoolExecutor(
        max_workers=6, initializer=s.init_worker, initargs=(end_ms, start_ms, mid_ms)
    ) as pool:
        futs = {pool.submit(s.eval_one, c): c["name"] for c in cands}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            rows.append(r)
            f = r["full"]
            print(
                f"{i}/{len(cands)} {r['name']:22s} med={f['median_week_eur']:7.1f} "
                f"mean={f['mean_week_eur']:7.1f} ge1k={f['pct_weeks_ge_1000']:.0%} "
                f"tot={f['total_eur']:8.0f} dd={f['max_dd_eur']:8.0f} n={f['trades']:3d} "
                f"wr={f['win_rate']:.0%}",
                flush=True,
            )

    best_med = max(
        rows,
        key=lambda r: (
            r["full"]["median_week_eur"],
            r["full"]["mean_week_eur"],
            -abs(r["full"]["max_dd_eur"]),
        ),
    )
    best_mean = max(
        rows, key=lambda r: (r["full"]["mean_week_eur"], r["full"]["median_week_eur"])
    )
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"start": first.isoformat(), "end": end.isoformat()},
        "best_med": best_med,
        "best_mean": best_mean,
        "rows": sorted(
            rows,
            key=lambda r: (
                -r["full"]["median_week_eur"],
                -r["full"]["mean_week_eur"],
            ),
        ),
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print("BEST_MED", best_med["name"], flush=True)
    print("BEST_MEAN", best_mean["name"], flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
