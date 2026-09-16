#!/usr/bin/env python3
"""Search desk configs for higher weekly PnL on a €20k Bitvavo book."""
from __future__ import annotations

import itertools
import json
import statistics as stats
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "weekly_pnl_config_search.json"
BOOK = 20_000.0
N_WORKERS = 8

_CANDLES = None
_START_MS = 0
_END_MS = 0
_MID_MS = 0


def monday(dt: datetime) -> datetime:
    d = dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


def init_worker(end_ms: int, start_ms: int, mid_ms: int) -> None:
    global _CANDLES, _START_MS, _END_MS, _MID_MS
    _START_MS, _END_MS, _MID_MS = start_ms, end_ms, mid_ms
    days = int((end_ms - start_ms) / 86_400_000) + 10
    _CANDLES = load_candles(("BTC", *DeskConfig().universe), days=days, end_ms=end_ms)


def make_cfg(**ov: Any) -> DeskConfig:
    base: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        # Proportional micro clip (1300 on ~4036). Recommended util pack uses
        # clip_eur=10000 + max_positions=3 on this €20k book.
        clip_eur=6_442.0,
        book_eur=BOOK,
        max_positions=3,
        day_loss_limit_eur=500.0,
        week_loss_limit_eur=1_250.0,
        fee_rt=0.003,
        exit_on_touch=False,
        skip_weekend_entries=True,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.0,
        midflat_hours=0.0,
        soft_regime_on_weak_tape=True,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        soft_regime_fee_buffer_mult=6.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        time_exit_hours=36.0,
        green_deadline_hours=0.0,
        green_min_peak=0.01,
        btc_min_ret=-0.01,
        min_breadth=0.5,
        decision_every_bar=False,
        decision_interval_sec=0.0,
        fade_eta_sec=0.0,
        refill_on_exit=False,
        alphai_rank_boost=0.01,
        alphai_clip_mult=1.3,
        alphai_size_mode="binary",
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        soft_regime_clip_mult=0.5,
    )
    base.update(ov)
    return DeskConfig().with_overrides(**base)


def week_stats(res) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for t in res.closed:
        d = datetime.fromtimestamp(t.closed_ms / 1000, UTC)
        key = monday(d).date().isoformat()
        buckets.setdefault(key, []).append(float(t.net_eur))
    weeks = []
    for key, pnls in sorted(buckets.items()):
        weeks.append(
            {
                "week": key,
                "pnl": round(sum(pnls), 2),
                "n": len(pnls),
                "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            }
        )
    pnls = [w["pnl"] for w in weeks]
    s = res.summary()
    n = int(s.get("trades") or 0)
    return {
        "total_eur": round(float(s.get("total_eur") or 0), 2),
        "max_dd_eur": round(float(s.get("max_drawdown_eur") or 0), 2),
        "trades": n,
        "win_rate": round(float(s.get("win_rate") or 0), 4) if n else 0.0,
        "n_weeks": len(weeks),
        "mean_week_eur": round(stats.mean(pnls), 2) if pnls else 0.0,
        "median_week_eur": round(stats.median(pnls), 2) if pnls else 0.0,
        "green_week_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3) if pnls else 0.0,
        "worst_week_eur": round(min(pnls), 2) if pnls else 0.0,
        "best_week_eur": round(max(pnls), 2) if pnls else 0.0,
        "weeks_ge_1000": int(sum(1 for p in pnls if p >= 1000)),
        "pct_weeks_ge_1000": round(sum(1 for p in pnls if p >= 1000) / len(pnls), 3)
        if pnls
        else 0.0,
        "weeks": weeks,
    }


def score(row: dict[str, Any]) -> float:
    med = float(row["median_week_eur"])
    mean = float(row["mean_week_eur"])
    green = float(row["green_week_rate"])
    total = float(row["total_eur"])
    dd = abs(float(row["max_dd_eur"]))
    wr = float(row["win_rate"])
    target_gap = max(0.0, 1000.0 - med)
    return (
        1.6 * med
        + 0.6 * mean
        + 0.15 * total
        + 800.0 * green
        + 400.0 * wr
        - 0.55 * dd
        - 0.35 * target_gap
        - 0.4 * abs(float(row["worst_week_eur"]))
    )


def eval_one(payload: dict[str, Any]) -> dict[str, Any]:
    ov = payload["overrides"]
    cfg = make_cfg(**ov)
    full = week_stats(simulate(_CANDLES, cfg, start_ms=_START_MS, end_ms=_END_MS))
    is_ = week_stats(simulate(_CANDLES, cfg, start_ms=_START_MS, end_ms=_MID_MS))
    oos = week_stats(simulate(_CANDLES, cfg, start_ms=_MID_MS, end_ms=_END_MS))
    return {
        "name": payload["name"],
        "overrides": ov,
        "full": full,
        "is": is_,
        "oos": oos,
        "score_full": round(score(full), 2),
        "score_oos": round(score(oos), 2),
    }


def grid() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(name: str, **ov: Any) -> None:
        rows.append({"name": name, "overrides": ov})

    add("baseline_wr_20k")
    add("baseline_with_refill", refill_on_exit=True)

    for clip in (5_000, 6_442, 8_000, 10_000):
        for max_pos in (3, 4, 5):
            add(f"util_c{clip}_p{max_pos}", clip_eur=float(clip), max_positions=max_pos)

    for hours in [(7, 13), (7, 13, 16), (7, 10, 13, 16), (6, 9, 12, 15, 18)]:
        add(f"hours_{'_'.join(map(str, hours))}", decision_hours_utc=hours)
    for interval in (3600, 7200, 10800):
        add(
            f"interval_{interval}s",
            decision_hours_utc=(),
            decision_interval_sec=float(interval),
        )

    for excess in (0.015, 0.02, 0.025, 0.03, 0.035):
        add(f"excess_{excess}", min_excess=excess)
    for breadth in (0.35, 0.45, 0.5, 0.6):
        add(f"breadth_{breadth}", min_breadth=breadth)
    add("soft_off", soft_regime_on_weak_tape=False)
    add("soft_clip_0.7", soft_regime_clip_mult=0.7)
    add("strong_clip_1.5", strong_clip_mult=1.5)
    add("strong_no_quality_gate", strong_clip_requires_quality=False)

    for trail, tight_after, tight in [
        (0.025, 0.035, 0.015),
        (0.03, 0.04, 0.02),
        (0.035, 0.05, 0.02),
        (0.04, 0.05, 0.025),
        (0.05, 0.06, 0.03),
    ]:
        add(
            f"trail_{trail}_{tight_after}_{tight}",
            trail_pct=trail,
            trail_tight_after=tight_after,
            trail_tight_pct=tight,
        )
    for th in (18, 24, 36, 48):
        add(f"time_{th}h", time_exit_hours=float(th))
    for gh in (0, 4, 8):
        add(f"green_{gh}h", green_deadline_hours=float(gh), green_min_peak=0.01)
    for stop in (0.02, 0.025, 0.03, 0.04):
        add(f"stop_{stop}", hard_stop_pct=stop)

    for clip, hours, excess, trail in itertools.product(
        (8_000, 10_000),
        ((7, 10, 13, 16), (6, 9, 12, 15, 18)),
        (0.02, 0.025),
        ((0.03, 0.04, 0.02), (0.035, 0.05, 0.02)),
    ):
        add(
            f"push_c{clip}_h{len(hours)}_e{excess}_t{trail[0]}",
            clip_eur=float(clip),
            max_positions=5,
            decision_hours_utc=hours,
            min_excess=excess,
            trail_pct=trail[0],
            trail_tight_after=trail[1],
            trail_tight_pct=trail[2],
            soft_regime_clip_mult=0.7,
            strong_clip_mult=1.4,
        )
    return rows


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    this_monday = monday(end)
    first = this_monday - timedelta(weeks=11)
    start_ms = int(first.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    mid = first + timedelta(weeks=8)
    mid_ms = int(mid.timestamp() * 1000)

    payloads = grid()
    print(
        f"grid={len(payloads)} window={first.date()}->{end.date()} mid={mid.date()}",
        flush=True,
    )
    t0 = time.time()
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=N_WORKERS, initializer=init_worker, initargs=(end_ms, start_ms, mid_ms)
    ) as pool:
        futs = {pool.submit(eval_one, p): p["name"] for p in payloads}
        for i, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if i % 25 == 0 or i == len(payloads):
                print(f"  {i}/{len(payloads)} done", flush=True)

    results.sort(key=lambda r: r["score_oos"], reverse=True)
    top_oos = results[:15]
    top_full = sorted(results, key=lambda r: r["score_full"], reverse=True)[:15]
    feasible = [
        r
        for r in results
        if r["full"]["median_week_eur"] >= 400
        and abs(r["full"]["max_dd_eur"]) <= 2_500
        and r["full"]["win_rate"] >= 0.45
        and r["oos"]["total_eur"] > 0
    ]
    feasible.sort(key=lambda r: (r["oos"]["median_week_eur"], r["score_oos"]), reverse=True)
    baseline = next(r for r in results if r["name"] == "baseline_wr_20k")
    rec = feasible[0] if feasible else top_oos[0]

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "window": {
            "start": first.isoformat(),
            "end": end.isoformat(),
            "is_end": mid.isoformat(),
            "n_configs": len(results),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "target_median_week_eur": 1000.0,
        "baseline": baseline,
        "top_oos": top_oos,
        "top_full": top_full,
        "best_feasible": feasible[:10],
        "recommendation": rec,
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(
        json.dumps(
            {
                "baseline_median_week": baseline["full"]["median_week_eur"],
                "baseline_mean_week": baseline["full"]["mean_week_eur"],
                "baseline_total": baseline["full"]["total_eur"],
                "baseline_dd": baseline["full"]["max_dd_eur"],
                "rec_name": rec["name"],
                "rec_overrides": rec["overrides"],
                "rec_full_median_week": rec["full"]["median_week_eur"],
                "rec_full_mean_week": rec["full"]["mean_week_eur"],
                "rec_full_total": rec["full"]["total_eur"],
                "rec_full_dd": rec["full"]["max_dd_eur"],
                "rec_full_wr": rec["full"]["win_rate"],
                "rec_full_pct_weeks_ge_1000": rec["full"]["pct_weeks_ge_1000"],
                "rec_oos_median_week": rec["oos"]["median_week_eur"],
                "rec_oos_total": rec["oos"]["total_eur"],
                "rec_oos_dd": rec["oos"]["max_dd_eur"],
            },
            indent=2,
        )
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
