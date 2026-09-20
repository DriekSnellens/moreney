#!/usr/bin/env python3
"""Validate bear-harvest winners: walk-forward halves + multi-name robustness."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from artifacts.bear_harvest_hunter import (
    DESK_TARGET_PNL,
    BOOK_EUR,
    pick_weakest,
    sim_short_book,
    summarize,
)
from artifacts.bear_market_strategy_sim import UNIVERSE, _align, _sma, load_daily

OUT = Path(__file__).resolve().parent / "bear_harvest_validated.json"

# Candidate packs from hunter (desk-beating, prefer lower DD)
CANDIDATES = [
    {
        "label": "conc1_runner_full",
        "lookback": 15, "top_n": 1, "reb": 30, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.0, "hard": 0.0, "max_weight": 1.0,
        "mode": "absolute", "weight_mode": "equal",
    },
    {
        "label": "conc1_runner_half_budget",
        "lookback": 15, "top_n": 1, "reb": 30, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.0, "hard": 0.0, "max_weight": 0.5,
        "mode": "absolute", "weight_mode": "equal", "deploy": 1.0,
        # max_weight 0.5 with top_n=1 → 50% book deployed
    },
    {
        "label": "top2_runner_mw50",
        "lookback": 15, "top_n": 2, "reb": 30, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.0, "hard": 0.0, "max_weight": 0.5,
        "mode": "absolute", "weight_mode": "equal",
    },
    {
        "label": "top3_runner_mw35",
        "lookback": 15, "top_n": 3, "reb": 30, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.0, "hard": 0.0, "max_weight": 0.35,
        "mode": "absolute", "weight_mode": "equal",
    },
    {
        "label": "top2_soft_trail",
        "lookback": 15, "top_n": 2, "reb": 21, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.20, "hard": 0.15, "max_weight": 0.5,
        "mode": "absolute", "weight_mode": "equal",
    },
    {
        "label": "excess_top2_runner",
        "lookback": 15, "top_n": 2, "reb": 30, "floor": -0.08, "skip": 2,
        "bounce": 0.04, "trail": 0.0, "hard": 0.0, "max_weight": 0.5,
        "mode": "excess", "weight_mode": "equal",
    },
    {
        "label": "live_short_weakest_bear_only",
        "lookback": 15, "top_n": 3, "reb": 14, "floor": -0.08, "skip": 0,
        "bounce": 0.0, "trail": 0.18, "hard": 0.12, "max_weight": 0.15,
        "mode": "absolute", "weight_mode": "equal", "deploy": 1.0,
    },
]


def run_cfg(closes, btc, sma200, i0, i1, cfg: dict):
    def pick(i, c=cfg):
        return pick_weakest(
            closes, btc, i,
            lookback=c["lookback"], top_n=c["top_n"], floor=c["floor"],
            skip=c["skip"], lookback2=0, floor2=-0.06, mode=c["mode"],
            bounce=c["bounce"], below_sma=0, weight_mode=c["weight_mode"],
        )
    eq, tr, w, act = sim_short_book(
        closes, btc, sma200, i0, i1,
        pick_fn=pick,
        rebalance_every=cfg["reb"],
        trail_pct=cfg["trail"],
        hard_stop_pct=cfg["hard"],
        vol_spike=False,
        vol_mult=3.0,
        max_weight=cfg["max_weight"],
        deploy_frac=cfg.get("deploy", 1.0),
        vol_scale=False,
        require_bear=True,
    )
    return summarize(cfg["label"], eq, tr, w, act, cfg), eq


def main() -> None:
    series = load_daily(("BTC", *UNIVERSE), days=430)
    ts, closes = _align(series, ("BTC", *UNIVERSE))
    btc = closes["BTC"]
    sma200 = _sma(btc, 200)
    bear_idx = [i for i, s in enumerate(sma200) if s is not None and btc[i] < s]
    i0, i1 = bear_idx[0], bear_idx[-1]
    mid = i0 + (i1 - i0) // 2

    windows = {
        "full_bear": (i0, i1),
        "bear_first_half": (i0, mid),
        "bear_second_half": (mid + 1, i1),
    }

    rows = []
    for cfg in CANDIDATES:
        entry = {"label": cfg["label"], "params": cfg, "windows": {}}
        for wname, (a, b) in windows.items():
            res, _ = run_cfg(closes, btc, sma200, a, b, cfg)
            entry["windows"][wname] = {
                "start": datetime.fromtimestamp(ts[a] / 1000, UTC).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(ts[b] / 1000, UTC).strftime("%Y-%m-%d"),
                "days": b - a + 1,
                "pnl_eur": res.pnl_eur,
                "return_pct": res.return_pct,
                "max_dd_pct": res.max_dd_pct,
                "calmar": res.calmar,
                "trades": res.trades,
                "win_rate": res.win_rate,
                "beats_desk": res.pnl_eur >= DESK_TARGET_PNL,
            }
        full = entry["windows"]["full_bear"]
        h1 = entry["windows"]["bear_first_half"]
        h2 = entry["windows"]["bear_second_half"]
        entry["robust"] = {
            "both_halves_profit": h1["pnl_eur"] > 0 and h2["pnl_eur"] > 0,
            "full_beats_desk": full["beats_desk"],
            "min_half_pnl": min(h1["pnl_eur"], h2["pnl_eur"]),
            "full_calmar": full["calmar"],
            "score": (
                int(full["beats_desk"]),
                int(h1["pnl_eur"] > 0 and h2["pnl_eur"] > 0),
                full["calmar"],
                full["pnl_eur"],
                full["max_dd_pct"],
            ),
        }
        rows.append(entry)
        print(
            f"{cfg['label']:28} full={full['pnl_eur']:8} ({full['max_dd_pct']}%) "
            f"h1={h1['pnl_eur']:8} h2={h2['pnl_eur']:8} "
            f"both+={entry['robust']['both_halves_profit']} beat={full['beats_desk']}",
            flush=True,
        )

    ranked = sorted(rows, key=lambda r: r["robust"]["score"], reverse=True)
    pick = ranked[0]

    # Track which bases the recommended pack shorts over full bear
    cfg = pick["params"]
    holdings = []
    last = -10**9
    for i in range(i0, i1 + 1):
        if i - last >= cfg["reb"] or last < 0:
            picks = pick_weakest(
                closes, btc, i,
                lookback=cfg["lookback"], top_n=cfg["top_n"], floor=cfg["floor"],
                skip=cfg["skip"], lookback2=0, floor2=-0.06, mode=cfg["mode"],
                bounce=cfg["bounce"], below_sma=0, weight_mode=cfg["weight_mode"],
            )
            holdings.append({
                "date": datetime.fromtimestamp(ts[i] / 1000, UTC).strftime("%Y-%m-%d"),
                "picks": [{"base": b, "w": round(w, 3)} for b, w in picks],
            })
            last = i

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "desk_benchmark_pnl_eur": DESK_TARGET_PNL,
        "bear_window": {
            "start": datetime.fromtimestamp(ts[i0] / 1000, UTC).strftime("%Y-%m-%d"),
            "end": datetime.fromtimestamp(ts[i1] / 1000, UTC).strftime("%Y-%m-%d"),
            "days": i1 - i0 + 1,
            "btc_return_pct": round(100 * (btc[i1] / btc[i0] - 1), 2),
        },
        "recommendation": pick,
        "all_candidates": ranked,
        "rebalance_snapshot": holdings,
        "verdict": {
            "tactic": pick["label"],
            "rule": (
                "When BTC < SMA200: short the single weakest name by 15d return "
                "(skip last 2d, require ≤−8%, bounce-block 4% green), rebalance every 30d, "
                "no trail/hard-stop; size via max_weight (1.0 aggressive / 0.5 conservative)."
            ),
            "vs_momentum_desk": (
                f"Desk ~+€{DESK_TARGET_PNL:.0f} (+32%) in ~12w rising tape. "
                f"This pack {pick['windows']['full_bear']['pnl_eur']} "
                f"({pick['windows']['full_bear']['return_pct']}%) over "
                f"{pick['windows']['full_bear']['days']}d bear with DD "
                f"{pick['windows']['full_bear']['max_dd_pct']}%."
            ),
        },
    }
    OUT.write_text(json.dumps(out, indent=2))
    print("wrote", OUT)
    print("RECOMMEND", pick["label"], pick["windows"]["full_bear"])


if __name__ == "__main__":
    main()
