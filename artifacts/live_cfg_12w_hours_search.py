#!/usr/bin/env python3
"""Search decision-hour sets on 12w live knobs; report sets beating 7/13/16.

Does not change live config — research only.
"""

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

OUT = Path(__file__).resolve().parent / "live_cfg_12w_hours_search.json"
DAYS = 84
BASELINE = (7, 13, 16)
# Liquid daytime + adjacent hours UTC (desk-relevant).
SEARCH_HOURS = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18)
MAX_K = 3
WORKERS = 8

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
        "fees_eur": s["fees_eur"],
        "calmar_proxy": round(
            s["total_eur"] / max(1.0, abs(s["max_drawdown_eur"])), 3
        ),
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    base = live_cfg()
    print(
        f"load candles {datetime.fromtimestamp(start_ms/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(end_ms/1000, UTC).date()}",
        flush=True,
    )
    candles = load_candles(
        ("BTC", *base.universe), days=DAYS + 2, end_ms=end_ms, refresh=False
    )

    combos: list[tuple[int, ...]] = []
    for k in range(1, MAX_K + 1):
        combos.extend(itertools.combinations(SEARCH_HOURS, k))
    # Ensure baseline is included even if outside grid (it is inside).
    if BASELINE not in combos:
        combos.append(BASELINE)
    combos = sorted(set(combos), key=lambda h: (len(h), h))
    print(f"searching {len(combos)} hour-sets, workers={WORKERS}", flush=True)

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
    # "Better" = strictly higher total_eur than live 7/13/16.
    better = [
        r
        for r in rows
        if r["total_eur"] > baseline["total_eur"] + 1e-6
    ]
    better.sort(key=lambda r: (-r["total_eur"], -r["calmar_proxy"], r["hours"]))

    # Also: better calmar with total not much worse (≥95% of baseline total).
    better_calmar = [
        r
        for r in rows
        if r["calmar_proxy"] > baseline["calmar_proxy"] + 1e-9
        and r["total_eur"] >= 0.95 * baseline["total_eur"]
        and tuple(r["hours"]) != BASELINE
    ]
    better_calmar.sort(key=lambda r: (-r["calmar_proxy"], -r["total_eur"]))

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "search": {
            "hours_grid": list(SEARCH_HOURS),
            "max_k": MAX_K,
            "n_combos": len(combos),
            "criterion_better_total": "total_eur > baseline 7/13/16",
        },
        "baseline": baseline,
        "better_than_baseline_by_total": better,
        "better_calmar_near_baseline_total": better_calmar[:20],
        "top10_overall_by_total": sorted(
            rows, key=lambda r: -r["total_eur"]
        )[:10],
        "elapsed_sec": round(time.time() - t0, 1),
        "caveats": [
            "15m close fills; no AlphaI; live knobs otherwise fixed",
            "hour grid 06–18 UTC only; k≤3 slots",
            "research only — live config not changed",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(OUT),
                "baseline_total": baseline["total_eur"],
                "n_better_total": len(better),
                "top_better": better[:15],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
