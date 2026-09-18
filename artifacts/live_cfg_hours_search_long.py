#!/usr/bin/env python3
"""Hour-set search on longer windows (24w / 52w). Research only — no live changes."""

from __future__ import annotations

import itertools
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate
from artifacts.live_cfg_12w_sim import live_cfg

OUT = Path(__file__).resolve().parent / "live_cfg_hours_search_long.json"
BASELINE = (7, 13, 16)
SEARCH_HOURS = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
MAX_K = 3
WORKERS = 8
WINDOWS_DAYS = (168, 365)  # 24w, ~52w

_CANDLES = None
_START_MS = 0
_END_MS = 0


def _init_worker(candles, start_ms: int, end_ms: int) -> None:
    global _CANDLES, _START_MS, _END_MS
    _CANDLES = candles
    _START_MS = start_ms
    _END_MS = end_ms


def _run_hours(hours: tuple[int, ...]) -> dict:
    cfg = live_cfg().with_overrides(
        decision_hours_utc=hours,
        decision_interval_sec=0.0,
        decision_every_bar=False,
    )
    s = simulate(
        _CANDLES, cfg, start_ms=_START_MS, end_ms=_END_MS, alphai=None
    ).summary()
    return {
        "hours": list(hours),
        "trades": s["trades"],
        "win_rate": s["win_rate"],
        "realized_eur": s["realized_eur"],
        "open_mtm_eur": s["open_mtm_eur"],
        "total_eur": s["total_eur"],
        "max_drawdown_eur": s["max_drawdown_eur"],
        "calmar_proxy": round(
            s["total_eur"] / max(1.0, abs(s["max_drawdown_eur"])), 3
        ),
    }


def _combos() -> list[tuple[int, ...]]:
    out: list[tuple[int, ...]] = []
    for k in range(1, MAX_K + 1):
        out.extend(itertools.combinations(SEARCH_HOURS, k))
    if BASELINE not in out:
        out.append(BASELINE)
    return sorted(set(out), key=lambda h: (len(h), h))


def search_window(candles, *, days: int, end_ms: int) -> dict:
    start_ms = end_ms - days * 86_400_000
    combos = _combos()
    print(f"\n=== {days}d search ({len(combos)} sets) ===", flush=True)
    rows: list[dict] = []
    t0 = time.time()
    with ProcessPoolExecutor(
        max_workers=WORKERS,
        initializer=_init_worker,
        initargs=(candles, start_ms, end_ms),
    ) as ex:
        futs = {ex.submit(_run_hours, h): h for h in combos}
        done = 0
        for fut in as_completed(futs):
            rows.append(fut.result())
            done += 1
            if done % 50 == 0 or done == len(combos):
                print(f"  {done}/{len(combos)} ({time.time()-t0:.0f}s)", flush=True)
    by_key = {tuple(r["hours"]): r for r in rows}
    baseline = by_key[BASELINE]
    better = sorted(
        [r for r in rows if r["total_eur"] > baseline["total_eur"] + 1e-6],
        key=lambda r: (-r["total_eur"], -r["calmar_proxy"]),
    )
    robust = [
        r
        for r in better
        if r["max_drawdown_eur"] >= baseline["max_drawdown_eur"]  # not worse DD
    ]
    return {
        "days": days,
        "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
        "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        "elapsed_sec": round(time.time() - t0, 1),
        "baseline": baseline,
        "n_better_total": len(better),
        "n_better_total_and_dd": len(robust),
        "better_than_baseline_by_total": better,
        "better_total_and_not_worse_dd": robust,
        "top10_by_total": sorted(rows, key=lambda r: -r["total_eur"])[:10],
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    base = live_cfg()
    max_days = max(WINDOWS_DAYS)
    print(f"refresh candles days={max_days}…", flush=True)
    t0 = time.time()
    candles = load_candles(
        ("BTC", *base.universe), days=max_days + 2, end_ms=end_ms, refresh=True
    )
    print(
        f"loaded in {time.time()-t0:.0f}s; BTC bars={len(candles['BTC'])} "
        f"from {datetime.fromtimestamp(candles['BTC'][0][0]/1000, UTC)}",
        flush=True,
    )

    windows = [search_window(candles, days=d, end_ms=end_ms) for d in WINDOWS_DAYS]

    # Cross-window: hour sets that beat baseline on BOTH longer windows.
    beat_sets = []
    for w in windows:
        beat_sets.append({tuple(r["hours"]) for r in w["better_than_baseline_by_total"]})
    common = set.intersection(*beat_sets) if beat_sets else set()
    common_rows = []
    for hrs in sorted(common, key=lambda h: (len(h), h)):
        common_rows.append(
            {
                "hours": list(hrs),
                "by_window": {
                    str(w["days"]): next(
                        r for r in w["better_than_baseline_by_total"] if tuple(r["hours"]) == hrs
                    )
                    for w in windows
                },
            }
        )

    # Also note 12w winners (7,10) etc. on long windows even if not beating.
    watch = [(7, 10), (7, 10, 12), (7, 13), (7, 10, 16), (7, 13, 16)]
    watch_rows = []
    for hrs in watch:
        watch_rows.append(
            {
                "hours": list(hrs),
                "by_window": {
                    str(w["days"]): next(
                        (
                            r
                            for r in w["top10_by_total"]
                            + w["better_than_baseline_by_total"]
                            + [w["baseline"]]
                            if tuple(r["hours"]) == hrs
                        ),
                        None,
                    )
                    # fallback: re-sim not needed if in by_key — pull from full better+baseline+top
                    for w in windows
                },
            }
        )
    # Fix watch lookup properly from each window's all ranked — store baseline+better+top is incomplete.
    # Re-derive from better list OR baseline OR re-query via simulating watch only if missing.
    for item in watch_rows:
        hrs = tuple(item["hours"])
        for w in windows:
            key = str(w["days"])
            found = None
            if hrs == BASELINE:
                found = w["baseline"]
            else:
                for r in w["better_than_baseline_by_total"] + w["top10_by_total"]:
                    if tuple(r["hours"]) == hrs:
                        found = r
                        break
            if found is None:
                # not in top/better — run single
                _init_worker(candles, end_ms - w["days"] * 86_400_000, end_ms)
                found = _run_hours(hrs)
                found["note"] = "below_baseline_or_outside_top10"
            item["by_window"][key] = found

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "search": {
            "hours_grid": list(SEARCH_HOURS),
            "max_k": MAX_K,
            "windows_days": list(WINDOWS_DAYS),
            "criterion": "total_eur > baseline 7/13/16",
        },
        "windows": windows,
        "beats_baseline_on_all_long_windows": common_rows,
        "watchlist_12w_favorites": watch_rows,
        "caveats": [
            "15m close fills; no AlphaI; live knobs otherwise fixed",
            "research only — live config not changed",
            "longer windows share regimes with the 12w sample (not fully independent)",
        ],
        "summary_nl": (
            "Uursets die op 24w én ~52w beter scoren dan live 7/13/16 op totale PnL; "
            "plus hoe 12w-favorieten zich houden verder terug."
        ),
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(OUT),
                "per_window": [
                    {
                        "days": w["days"],
                        "baseline_total": w["baseline"]["total_eur"],
                        "baseline_dd": w["baseline"]["max_drawdown_eur"],
                        "n_better": w["n_better_total"],
                        "n_better_dd_ok": w["n_better_total_and_dd"],
                        "top3": w["better_than_baseline_by_total"][:3],
                    }
                    for w in windows
                ],
                "common_beat_count": len(common_rows),
                "common_hours": [r["hours"] for r in common_rows[:20]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
