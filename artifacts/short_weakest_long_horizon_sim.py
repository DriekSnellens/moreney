#!/usr/bin/env python3
"""Long-horizon short-weakest replay: live idle-fill vs dual-window optimum.

Loads ~900d Bitvavo daily EUR candles, warms SMA200, then simulates from the
first day SMA200 is available through the cache tip. Research-only.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import BOOK_EUR, UNIVERSE, _align, load_daily
from artifacts.short_weakest_tactics_research import (
    TacticExtras,
    base_cfg,
    core_occ_from_combined,
    simulate_tactics,
)

OUT = Path(__file__).resolve().parent / "short_weakest_long_horizon_sim.json"
DAYS_LOAD = 900


def _date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%d")


def configs() -> list[tuple[str, Any, TacticExtras]]:
    live = (
        "live_idle_fill",
        base_cfg(),
        TacticExtras(name="live_idle_fill"),
    )
    opt = (
        "optimum_low_dd",
        base_cfg(
            lookback_days=21,
            trail_pct=0.12,
            hard_stop_pct=0.12,
            max_weight=0.10,
            deploy_frac=0.75,
            vol_spike_exit=True,
            idle_fill_enabled=False,
            cover_when_core_active=False,
            only_when_core_idle=False,
        ),
        TacticExtras(
            name="optimum_low_dd",
            skip_days=2,
            lookback2=30,
            mom_floor2=-0.06,
            bounce_block=0.04,
        ),
    )
    opt_hi = (
        "optimum_higher_pnl",
        base_cfg(
            lookback_days=21,
            trail_pct=0.12,
            hard_stop_pct=0.12,
            max_weight=0.15,
            deploy_frac=1.0,
            vol_spike_exit=True,
            idle_fill_enabled=False,
            cover_when_core_active=False,
            only_when_core_idle=False,
        ),
        TacticExtras(
            name="optimum_higher_pnl",
            skip_days=2,
            lookback2=30,
            mom_floor2=-0.06,
            bounce_block=0.04,
        ),
    )
    bear_only_live_knobs = (
        "live_knobs_idle_off",
        base_cfg(idle_fill_enabled=False, cover_when_core_active=False, only_when_core_idle=False),
        TacticExtras(name="live_knobs_idle_off"),
    )
    return [live, opt, opt_hi, bear_only_live_knobs]


def slice_by_dates(ts: list[int], start: str, end: str) -> tuple[int, int]:
    i0 = next(i for i, t in enumerate(ts) if _date(t) >= start)
    i1 = next(i for i in range(len(ts) - 1, -1, -1) if _date(ts[i]) <= end)
    return i0, i1


def run_window(
    name: str,
    ts: list[int],
    closes: dict[str, list[float]],
    i0: int,
    i1: int,
    *,
    use_core: bool,
) -> dict[str, Any]:
    core = core_occ_from_combined() if use_core else None
    # core_occ only covers last 12w; outside that range active_at is False → idle.
    out: dict[str, Any] = {
        "window": name,
        "start": _date(ts[i0]),
        "end": _date(ts[i1]),
        "days": i1 - i0 + 1,
        "variants": {},
    }
    for label, cfg, extras in configs():
        # Live variant: couple core only on recent window when idle_fill on
        occ = None
        if use_core and (cfg.idle_fill_enabled or cfg.only_when_core_idle):
            occ = core
        cfg_run = cfg
        if not use_core:
            cfg_run = replace(cfg, only_when_core_idle=False, cover_when_core_active=False)
        res = simulate_tactics(
            daily_ts=ts,
            daily_closes=closes,
            i0=i0,
            i1=i1,
            core_occ=occ,
            cfg=cfg_run,
            extras=extras,
        )
        out["variants"][label] = res
    return out


def main() -> None:
    print(f"loading {DAYS_LOAD}d candles…", flush=True)
    series = load_daily(("BTC", *UNIVERSE), days=DAYS_LOAD)
    ts, closes = _align(series, ("BTC", *UNIVERSE))
    print(f"aligned {len(ts)} days {_date(ts[0])} → {_date(ts[-1])}", flush=True)

    # First index with SMA200 available
    i_sma = 199
    if len(ts) <= i_sma + 30:
        raise SystemExit("not enough history for SMA200 + sample")

    windows = [
        ("full_after_sma200", _date(ts[i_sma]), _date(ts[-1]), False),
        ("bear_oct25_jun26", "2025-10-06", "2026-06-30", False),
        ("post_bear_jul_sep26", "2026-07-01", _date(ts[-1]), True),
        ("last_12w", "2026-06-28", _date(ts[-1]), True),
        ("last_26w", _date(ts[max(i_sma, len(ts) - 182)]), _date(ts[-1]), False),
    ]

    results = []
    for wname, start, end, use_core in windows:
        # clamp start to available
        if start < _date(ts[0]):
            start = _date(ts[0])
        if start < _date(ts[i_sma]) and wname.startswith("full"):
            start = _date(ts[i_sma])
        try:
            i0, i1 = slice_by_dates(ts, start, end)
        except StopIteration:
            print(f"skip {wname}: no overlap", flush=True)
            continue
        if i1 <= i0:
            continue
        print(f"sim {wname} { _date(ts[i0])}→{_date(ts[i1])} ({i1-i0+1}d)…", flush=True)
        results.append(run_window(wname, ts, closes, i0, i1, use_core=use_core))

    # Convenience table
    table = []
    for block in results:
        row = {"window": block["window"], "start": block["start"], "end": block["end"], "days": block["days"]}
        for label, res in block["variants"].items():
            row[f"{label}_pnl"] = res["pnl_eur"]
            row[f"{label}_dd_pct"] = res["max_dd_pct"]
            row[f"{label}_calmar"] = res["calmar"]
            row[f"{label}_trades"] = res["trades"]
        table.append(row)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK_EUR,
        "history": {"start": _date(ts[0]), "end": _date(ts[-1]), "bars": len(ts), "sma200_ready": _date(ts[i_sma])},
        "caveats": [
            "Daily Bitvavo closes; paper synthetic shorts; no funding; AlphaI off",
            "Core occupancy only available for last ~12w (from combined_desk_12w_sim); earlier idle-fill assumes core always idle",
            "Optimum = dual-window research winner (idle off, lb21, skip2, dual30, bounce4%, mw10%, dep75%)",
        ],
        "windows": results,
        "table": table,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print("wrote", OUT, flush=True)
    for row in table:
        print(
            f"{row['window']:22} {row['start']}→{row['end']} "
            f"live={row['live_idle_fill_pnl']:>9} ({row['live_idle_fill_dd_pct']}%)  "
            f"opt={row['optimum_low_dd_pnl']:>9} ({row['optimum_low_dd_dd_pct']}%)  "
            f"hi={row['optimum_higher_pnl_pnl']:>9} ({row['optimum_higher_pnl_dd_pct']}%)",
            flush=True,
        )


if __name__ == "__main__":
    main()
