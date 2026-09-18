#!/usr/bin/env python3
"""Re-score 12w hour winners on 84d / 168d / 365d. Research only."""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate
from artifacts.live_cfg_12w_sim import live_cfg

OUT = Path(__file__).resolve().parent / "live_cfg_hours_long_focused.json"
PREV = Path(__file__).resolve().parent / "live_cfg_12w_hours_search.json"
BASELINE = (7, 13, 16)
WINDOWS = (84, 168, 365)
WORKERS = 6

_CANDLES = None
_START = 0
_END = 0


def _init(c, s, e) -> None:
    global _CANDLES, _START, _END
    _CANDLES, _START, _END = c, s, e


def _run(hours: tuple[int, ...]) -> dict:
    cfg = live_cfg().with_overrides(
        decision_hours_utc=hours,
        decision_interval_sec=0.0,
        decision_every_bar=False,
    )
    s = simulate(_CANDLES, cfg, start_ms=_START, end_ms=_END, alphai=None).summary()
    return {
        "hours": list(hours),
        "trades": s["trades"],
        "win_rate": s["win_rate"],
        "realized_eur": s["realized_eur"],
        "open_mtm_eur": s["open_mtm_eur"],
        "total_eur": s["total_eur"],
        "max_drawdown_eur": s["max_drawdown_eur"],
        "calmar_proxy": round(s["total_eur"] / max(1.0, abs(s["max_drawdown_eur"])), 3),
    }


def main() -> None:
    prev = json.loads(PREV.read_text(encoding="utf-8"))
    hours: list[tuple[int, ...]] = [BASELINE]
    for r in prev["better_than_baseline_by_total"]:
        h = tuple(r["hours"])
        if h not in hours:
            hours.append(h)

    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    base = live_cfg()
    print("load candles…", flush=True)
    candles = load_candles(
        ("BTC", *base.universe), days=367, end_ms=end_ms, refresh=False
    )
    print(
        f"BTC bars={len(candles['BTC'])} from "
        f"{datetime.fromtimestamp(candles['BTC'][0][0]/1000, UTC)}",
        flush=True,
    )
    print(f"candidates={len(hours)}", flush=True)

    windows_out = []
    for days in WINDOWS:
        start_ms = end_ms - days * 86_400_000
        print(f"\n=== {days}d ===", flush=True)
        t0 = time.time()
        rows: list[dict] = []
        with ProcessPoolExecutor(
            max_workers=WORKERS, initializer=_init, initargs=(candles, start_ms, end_ms)
        ) as ex:
            futs = [ex.submit(_run, h) for h in hours]
            for fut in as_completed(futs):
                rows.append(fut.result())
        rows.sort(key=lambda r: -r["total_eur"])
        baseline = next(r for r in rows if tuple(r["hours"]) == BASELINE)
        better = [r for r in rows if r["total_eur"] > baseline["total_eur"] + 1e-6]
        better.sort(key=lambda r: -r["total_eur"])
        print(
            f"  baseline total={baseline['total_eur']:.0f} dd={baseline['max_drawdown_eur']:.0f} "
            f"n_better={len(better)} ({time.time()-t0:.0f}s)",
            flush=True,
        )
        for r in better:
            print(
                f"    {r['hours']}: tot={r['total_eur']:.0f} "
                f"Δ={r['total_eur']-baseline['total_eur']:+.0f} "
                f"dd={r['max_drawdown_eur']:.0f} wr={r['win_rate']:.0%}",
                flush=True,
            )
        windows_out.append(
            {
                "days": days,
                "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
                "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
                "baseline": baseline,
                "all": rows,
                "better_than_baseline": better,
            }
        )

    matrix = []
    for h in hours:
        by_w = {}
        for w in windows_out:
            r = next(x for x in w["all"] if tuple(x["hours"]) == h)
            b = w["baseline"]
            by_w[str(w["days"])] = {
                **r,
                "delta_total": round(r["total_eur"] - b["total_eur"], 2),
                "beats": r["total_eur"] > b["total_eur"] + 1e-6,
            }
        matrix.append(
            {
                "hours": list(h),
                "by_window": by_w,
                "beats_all": all(v["beats"] for v in by_w.values()),
                "beats_long": all(by_w[str(d)]["beats"] for d in (168, 365)),
            }
        )

    common_all = [m["hours"] for m in matrix if m["beats_all"] and tuple(m["hours"]) != BASELINE]
    common_long = [m["hours"] for m in matrix if m["beats_long"] and tuple(m["hours"]) != BASELINE]

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "method": "12w beaters + baseline on 84/168/365d",
        "windows": windows_out,
        "matrix": matrix,
        "beats_baseline_all_windows": common_all,
        "beats_baseline_long_windows_168_365": common_long,
        "caveats": [
            "15m closes; no AlphaI; live unchanged",
            "365d overlaps the 12w sample",
            "only re-scores 12w winners — not a fresh full grid on long history",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("\nbeats ALL windows:", common_all)
    print("beats 168+365:", common_long)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
