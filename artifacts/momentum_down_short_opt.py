#!/usr/bin/env python3
"""Optimize short-while-momentum-idle knobs for max PnL / min DD, then A/B sim.

Gate = user intent: short absolute weakest while core idle, cover when core
longs. Sweep literature-style knobs on the same 12w core occupancy used by
momentum_down_short_sim. Score by Calmar (PnL/|DD|) with DD floor, then
re-run the combined desk comparison with the winner.

Writes artifacts/momentum_down_short_opt.json
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.combined_desk_12w_sim import (
    CORE_BOOK,
    DAYS,
    SHORT_BOOK,
    _day_ms,
    align_daily,
    build_core_occupancy_full,
    live_core_cfg,
    load_daily_map,
    simulate_short_sleeve,
)
from artifacts.momentum_down_short_sim import summarize_variant
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE
from bot.live.momentum_short_weakest import ShortWeakestConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "momentum_down_short_opt.json"


def idle_cfg(**kw: Any) -> ShortWeakestConfig:
    base = dict(
        book_eur=SHORT_BOOK,
        require_btc_below_sma200=False,
        only_when_core_idle=True,
        cover_when_core_active=True,
        idle_fill_enabled=False,
        alphai_enabled=False,
        universe=DEFAULT_UNIVERSE,
        lookback_days=15,
        top_n=1,
        rebalance_days=7,
        mom_floor=-0.08,
        skip_days=2,
        bounce_block_pct=0.04,
        trail_pct=0.12,
        hard_stop_pct=0.12,
        max_weight=0.5,
        deploy_frac=1.0,
        vol_spike_exit=True,
    )
    base.update(kw)
    return ShortWeakestConfig(**base)


def score_row(short: dict[str, Any], *, robust: bool = False) -> tuple:
    """Prefer high Calmar, then PnL, then lower |DD|, then more active days."""
    pnl = float(short["pnl_eur"])
    dd = abs(float(short["max_dd_pct"])) or 0.01
    calmar = pnl / (dd * SHORT_BOOK / 100.0) if dd > 0 else (999.0 if pnl > 0 else 0.0)
    dd_ok = int(float(short["max_dd_pct"]) > -20.0)
    profit = int(pnl > 0)
    trades = int(short.get("trades") or 0)
    active = int(short.get("short_active_days") or 0)
    if robust:
        # Drop 1-trade lottery tickets that dominate in-sample Calmar.
        robust_ok = int(trades >= 4 and active >= 15)
        return (
            profit,
            robust_ok,
            dd_ok,
            round(calmar, 4),
            pnl,
            float(short["max_dd_pct"]),
            active,
        )
    return (profit, dd_ok, round(calmar, 4), pnl, float(short["max_dd_pct"]), active)


def sweep(daily_ts, daily_closes, i0, i1, occ) -> list[dict[str, Any]]:
    # Focused research grid covering prior winners + idle complement.
    focused = list(
        itertools.product(
            (15, 21),                    # lb
            (1, 2),                      # top_n
            (3, 7, 14),                  # reb
            (-0.08, -0.12),              # floor
            (0, 2),                      # skip
            (0.0, 0.04),                 # bounce
            (0.0, 0.12, 0.18),           # trail
            (0.0, 0.12, 0.15),           # hard
            (0.15, 0.35, 0.5, 1.0),      # mw
            (0.75, 1.0),                 # deploy
            (False, True),               # vol spike
        )
    )
    # Prior dual-window winner + balanced packs as anchors
    anchors = [
        dict(lookback_days=21, top_n=1, rebalance_days=7, mom_floor=-0.08, skip_days=2,
             bounce_block_pct=0.04, trail_pct=0.12, hard_stop_pct=0.12, max_weight=0.1,
             deploy_frac=0.75, vol_spike_exit=True),
        dict(lookback_days=21, top_n=1, rebalance_days=7, mom_floor=-0.08, skip_days=2,
             bounce_block_pct=0.04, trail_pct=0.12, hard_stop_pct=0.12, max_weight=0.15,
             deploy_frac=1.0, vol_spike_exit=True),
        dict(lookback_days=15, top_n=1, rebalance_days=30, mom_floor=-0.08, skip_days=2,
             bounce_block_pct=0.04, trail_pct=0.0, hard_stop_pct=0.0, max_weight=0.5,
             deploy_frac=1.0, vol_spike_exit=False),
        dict(lookback_days=15, top_n=1, rebalance_days=30, mom_floor=-0.08, skip_days=2,
             bounce_block_pct=0.04, trail_pct=0.0, hard_stop_pct=0.0, max_weight=1.0,
             deploy_frac=1.0, vol_spike_exit=False),
        dict(lookback_days=15, top_n=3, rebalance_days=14, mom_floor=-0.08, skip_days=0,
             bounce_block_pct=0.0, trail_pct=0.18, hard_stop_pct=0.12, max_weight=0.15,
             deploy_frac=1.0, vol_spike_exit=True),
    ]

    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def run_one(params: dict[str, Any], tag: str) -> None:
        key = tuple(sorted(params.items()))
        if key in seen:
            return
        seen.add(key)
        cfg = idle_cfg(**params)
        short = simulate_short_sleeve(
            daily_ts=daily_ts,
            daily_closes=daily_closes,
            i0=i0,
            i1=i1,
            core_occ=occ,
            cfg=cfg,
        )
        pnl = float(short["pnl_eur"])
        dd = float(short["max_dd_pct"])
        calmar = (
            pnl / (abs(dd) * SHORT_BOOK / 100.0)
            if abs(dd) > 1e-9
            else (999.0 if pnl > 0 else 0.0)
        )
        rows.append(
            {
                "tag": tag,
                "params": params,
                "pnl_eur": pnl,
                "return_pct": float(short["return_pct"]),
                "max_dd_pct": dd,
                "max_dd_eur": float(short["max_dd_eur"]),
                "calmar": round(calmar, 4),
                "trades": int(short["trades"]),
                "win_rate": short.get("win_rate"),
                "short_active_days": int(short["short_active_days"]),
                "by_reason": short.get("by_reason"),
                "_score": score_row(short),
            }
        )

    for a in anchors:
        run_one(a, "anchor")

    for (
        lb, top_n, reb, floor, skip, bounce, trail, hard, mw, dep, vs
    ) in focused:
        # Skip nonsense: hard without trail ok; trail 0 + hard 0 = runner
        run_one(
            dict(
                lookback_days=lb,
                top_n=top_n,
                rebalance_days=reb,
                mom_floor=floor,
                skip_days=skip,
                bounce_block_pct=bounce,
                trail_pct=trail,
                hard_stop_pct=hard,
                max_weight=mw,
                deploy_frac=dep,
                vol_spike_exit=vs,
            ),
            "focused",
        )
        if len(rows) % 200 == 0:
            print(f"  swept {len(rows)}…", flush=True)

    rows.sort(key=lambda r: r["_score"], reverse=True)
    for r in rows:
        r.pop("_score", None)
    return rows


def main() -> None:
    cfg = live_core_cfg()
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    print(
        f"CORE 15m {DAYS}d "
        f"{datetime.fromtimestamp(start_ms/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(end_ms/1000, UTC).date()}",
        flush=True,
    )
    candles = load_candles(
        ("BTC", *cfg.universe), days=DAYS + 3, end_ms=end_ms, refresh=False
    )
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    core_sum = core_res.summary()
    occ = build_core_occupancy_full(core_res, end_ms)
    core_total = float(core_sum.get("realized_eur") or 0) + float(
        core_sum.get("open_mtm_eur") or 0
    )
    print(f"core pnl={core_total:.2f}", flush=True)

    bases = ("BTC", *DEFAULT_UNIVERSE)
    series = load_daily_map(bases, days=DAYS + 220)
    daily_ts, daily_closes = align_daily(series, bases)
    i0 = next(i for i, t in enumerate(daily_ts) if t >= _day_ms(start_ms))
    i1 = len(daily_ts) - 1 - next(
        i for i, t in enumerate(reversed(daily_ts)) if t <= end_ms
    )

    print("sweeping idle-complement short knobs…", flush=True)
    ranked = sweep(daily_ts, daily_closes, i0, i1, occ)
    print(f"tested {len(ranked)} configs", flush=True)

    best_calmar = ranked[0]
    # Best PnL among DD > -12%
    dd12 = [r for r in ranked if r["max_dd_pct"] > -12.0 and r["pnl_eur"] > 0]
    best_pnl_dd12 = max(dd12, key=lambda r: r["pnl_eur"]) if dd12 else best_calmar
    # Best PnL among DD > -8%
    dd8 = [r for r in ranked if r["max_dd_pct"] > -8.0 and r["pnl_eur"] > 0]
    best_pnl_dd8 = max(dd8, key=lambda r: r["pnl_eur"]) if dd8 else best_calmar
    # Absolute max PnL (ignore DD)
    best_pnl = max(ranked, key=lambda r: r["pnl_eur"])

    picks = {
        "best_calmar": best_calmar,
        "best_pnl_dd_gt_m12": best_pnl_dd12,
        "best_pnl_dd_gt_m8": best_pnl_dd8,
        "best_pnl_unconstrained": best_pnl,
    }
    print("\n=== OPTIMA ===", flush=True)
    for k, v in picks.items():
        print(
            f"{k}: pnl={v['pnl_eur']} dd={v['max_dd_pct']}% calmar={v['calmar']} "
            f"params={v['params']}",
            flush=True,
        )

    # Combined A/B with optimal packs vs baselines
    from artifacts.combined_desk_12w_sim import core_daily_equity

    core_path = core_daily_equity(
        core_res,
        daily_ts=daily_ts,
        daily_closes=daily_closes,
        start_ms=start_ms,
        end_ms=end_ms,
        book=CORE_BOOK,
    )

    def run_named(name: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if params is None:
            return summarize_variant(name, core_total, None, core_path)
        cfg_s = idle_cfg(**params)
        short = simulate_short_sleeve(
            daily_ts=daily_ts,
            daily_closes=daily_closes,
            i0=i0,
            i1=i1,
            core_occ=occ,
            cfg=cfg_s,
        )
        print(f"  A/B {name}: short={short['pnl_eur']} dd={short['max_dd_pct']}%", flush=True)
        return summarize_variant(name, core_total, short, core_path)

    # Baselines: cash, prior balanced idle, prior dual-window winner as idle
    ab = [
        run_named("core_alone", None),
        run_named("opt_calmar", picks["best_calmar"]["params"]),
        run_named("opt_pnl_dd12", picks["best_pnl_dd_gt_m12"]["params"]),
        run_named("opt_pnl_dd8", picks["best_pnl_dd_gt_m8"]["params"]),
        run_named("opt_pnl_raw", picks["best_pnl_unconstrained"]["params"]),
        run_named(
            "prior_balanced_idle",
            dict(
                lookback_days=15, top_n=1, rebalance_days=30, mom_floor=-0.08,
                skip_days=2, bounce_block_pct=0.04, trail_pct=0.0, hard_stop_pct=0.0,
                max_weight=0.5, deploy_frac=1.0, vol_spike_exit=False,
            ),
        ),
        run_named(
            "prior_dual_window_winner_idle",
            dict(
                lookback_days=21, top_n=1, rebalance_days=7, mom_floor=-0.08,
                skip_days=2, bounce_block_pct=0.04, trail_pct=0.12, hard_stop_pct=0.12,
                max_weight=0.1, deploy_frac=0.75, vol_spike_exit=True,
            ),
        ),
    ]
    ab_ranked = sorted(ab, key=lambda r: r["combined_pnl_eur"], reverse=True)

    # Recommended: prefer dd8 if close to best combined, else dd12, else calmar
    rec = picks["best_pnl_dd_gt_m8"]
    rec_name = "best_pnl_dd_gt_m8"
    ab_rec = next(r for r in ab if r["name"] == "opt_pnl_dd8")
    for cand_key, ab_name in (
        ("best_pnl_dd_gt_m8", "opt_pnl_dd8"),
        ("best_pnl_dd_gt_m12", "opt_pnl_dd12"),
        ("best_calmar", "opt_calmar"),
    ):
        row = next(r for r in ab if r["name"] == ab_name)
        # Prefer higher combined with DD on short still > -12
        if row["short_max_dd_pct"] > -12 and row["combined_pnl_eur"] >= ab_rec["combined_pnl_eur"] - 50:
            rec = picks[cand_key]
            rec_name = cand_key
            ab_rec = row

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "intent": (
            "Optimize short knobs under momentum-idle complement gate "
            "for max PnL / min DD, then compare combined desk."
        ),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).date().isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).date().isoformat(),
        },
        "books": {"core_eur": CORE_BOOK, "short_eur": SHORT_BOOK},
        "core_pnl_eur": round(core_total, 2),
        "n_tested": len(ranked),
        "optima": picks,
        "recommendation": {
            "which": rec_name,
            "params": rec["params"],
            "short": {
                k: rec[k]
                for k in (
                    "pnl_eur",
                    "return_pct",
                    "max_dd_pct",
                    "calmar",
                    "trades",
                    "win_rate",
                    "short_active_days",
                )
            },
            "combined": {
                k: ab_rec[k]
                for k in (
                    "combined_pnl_eur",
                    "combined_return_pct",
                    "combined_max_dd_pct",
                    "weeks_combined_green",
                    "weeks_total",
                    "weeks_short_offsets_core_down",
                    "weeks_core_down",
                    "weeks_both_down",
                )
            },
        },
        "ab_combined": ab,
        "ab_ranked_by_combined_pnl": [r["name"] for r in ab_ranked],
        "top20_short_only": ranked[:20],
        "caveats": [
            "Optimized in-sample on this 12w window (no walk-forward holdout)",
            "Gate fixed: only_when_core_idle + cover_when_core_active, no SMA200",
            "Core: 15m fills; short: daily; no AlphaI/funding",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== A/B COMBINED ===", flush=True)
    for r in ab_ranked:
        print(
            f"{r['name']:32} combined={r['combined_pnl_eur']:>9} "
            f"short={r['short_pnl_eur']:>8} dd={r['combined_max_dd_pct']:>6}% "
            f"green={r['weeks_combined_green']}/{r['weeks_total']} "
            f"offset={r['weeks_short_offsets_core_down']}/{r['weeks_core_down']}",
            flush=True,
        )
    print(f"\nRECOMMENDED ({rec_name}): {json.dumps(rec['params'])}", flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
