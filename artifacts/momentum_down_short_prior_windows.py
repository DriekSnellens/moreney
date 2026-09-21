#!/usr/bin/env python3
"""Prior-window check: idle-complement short packs on weeks before last 12w.

Runs successive 12-week slices ending just before the recent window, plus the
bear window (2025-10-06 → 2026-06-30). For each window: core desk replay +
idle-complement short with (a) robust rec from last-12w opt, (b) prior balanced,
(c) cash short, (d) quick robust re-opt on that window.

Writes artifacts/momentum_down_short_prior_windows.json
"""

from __future__ import annotations

import itertools
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from artifacts.combined_desk_12w_sim import (
    CORE_BOOK,
    SHORT_BOOK,
    _day_ms,
    align_daily,
    build_core_occupancy_full,
    core_daily_equity,
    live_core_cfg,
    load_daily_map,
    simulate_short_sleeve,
)
from artifacts.momentum_down_short_opt import idle_cfg
from artifacts.momentum_down_short_sim import summarize_variant
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "momentum_down_short_prior_windows.json"

# Robust rec from last-12w optimize (trades≥4, active≥15, DD>-8%)
ROBUST_REC = dict(
    lookback_days=15,
    top_n=1,
    rebalance_days=7,
    mom_floor=-0.08,
    skip_days=2,
    bounce_block_pct=0.0,
    trail_pct=0.0,
    hard_stop_pct=0.0,
    max_weight=0.35,
    deploy_frac=0.75,
    vol_spike_exit=False,
)
BALANCED = dict(
    lookback_days=15,
    top_n=1,
    rebalance_days=30,
    mom_floor=-0.08,
    skip_days=2,
    bounce_block_pct=0.04,
    trail_pct=0.0,
    hard_stop_pct=0.0,
    max_weight=0.5,
    deploy_frac=1.0,
    vol_spike_exit=False,
)
DUAL_WIN = dict(
    lookback_days=21,
    top_n=1,
    rebalance_days=7,
    mom_floor=-0.08,
    skip_days=2,
    bounce_block_pct=0.04,
    trail_pct=0.12,
    hard_stop_pct=0.12,
    max_weight=0.15,
    deploy_frac=1.0,
    vol_spike_exit=True,
)


def _ms(date_s: str, end_of_day: bool = False) -> int:
    dt = datetime.fromisoformat(date_s).replace(tzinfo=UTC)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def quick_robust_opt(
    daily_ts, daily_closes, i0, i1, occ
) -> dict[str, Any] | None:
    """Smaller grid than full opt — enough to see if prior weeks prefer different knobs."""
    focused = list(
        itertools.product(
            (15, 21),
            (1, 2),
            (3, 7, 14),
            (-0.08, -0.12),
            (0, 2),
            (0.0, 0.04),
            (0.0, 0.12),
            (0.0, 0.12),
            (0.15, 0.35, 0.5),
            (0.75, 1.0),
            (False, True),
        )
    )
    best = None
    best_score = None
    for lb, top_n, reb, floor, skip, bounce, trail, hard, mw, dep, vs in focused:
        params = dict(
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
        )
        short = simulate_short_sleeve(
            daily_ts=daily_ts,
            daily_closes=daily_closes,
            i0=i0,
            i1=i1,
            core_occ=occ,
            cfg=idle_cfg(**params),
        )
        pnl = float(short["pnl_eur"])
        dd = float(short["max_dd_pct"])
        trades = int(short["trades"])
        active = int(short["short_active_days"])
        if trades < 4 or active < 15 or pnl <= 0 or dd <= -12:
            continue
        calmar = pnl / (abs(dd) * SHORT_BOOK / 100.0) if abs(dd) > 1e-9 else 0.0
        score = (calmar, pnl, dd)
        if best_score is None or score > best_score:
            best_score = score
            best = {
                "params": params,
                "pnl_eur": pnl,
                "max_dd_pct": dd,
                "calmar": round(calmar, 4),
                "trades": trades,
                "short_active_days": active,
                "win_rate": short.get("win_rate"),
            }
    return best


def run_window(
    *,
    label: str,
    start_ms: int,
    end_ms: int,
    daily_ts: list[int],
    daily_closes: dict[str, list[float]],
    do_local_opt: bool,
) -> dict[str, Any]:
    days = max(1, int((end_ms - start_ms) / 86_400_000) + 1)
    print(
        f"\n=== {label} {_date(start_ms)} → {_date(end_ms)} ({days}d) ===",
        flush=True,
    )
    cfg = live_core_cfg()
    # Load enough 15m history for the window (+ warmup buffer)
    candles = load_candles(
        ("BTC", *cfg.universe),
        days=days + 5,
        end_ms=end_ms,
        refresh=False,
    )
    print("  simulating core…", flush=True)
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    core_sum = core_res.summary()
    occ = build_core_occupancy_full(core_res, end_ms)
    core_total = float(core_sum.get("realized_eur") or 0) + float(
        core_sum.get("open_mtm_eur") or 0
    )
    print(
        f"  core pnl={core_total:.2f} trades={core_sum.get('trades')} "
        f"intervals={len(occ.intervals)}",
        flush=True,
    )

    i0 = next(i for i, t in enumerate(daily_ts) if t >= _day_ms(start_ms))
    # last daily bar on/before end
    i1 = len(daily_ts) - 1
    for i, t in enumerate(daily_ts):
        if t <= end_ms:
            i1 = i
        else:
            break

    core_path = core_daily_equity(
        core_res,
        daily_ts=daily_ts,
        daily_closes=daily_closes,
        start_ms=start_ms,
        end_ms=end_ms,
        book=CORE_BOOK,
    )

    packs: list[tuple[str, dict[str, Any] | None]] = [
        ("core_alone", None),
        ("robust_rec_from_last12w", ROBUST_REC),
        ("prior_balanced", BALANCED),
        ("dual_window_winner", DUAL_WIN),
    ]

    local_opt = None
    if do_local_opt:
        print("  local robust opt…", flush=True)
        local_opt = quick_robust_opt(daily_ts, daily_closes, i0, i1, occ)
        if local_opt:
            packs.append(("local_robust_opt", local_opt["params"]))
            print(
                f"  local opt pnl={local_opt['pnl_eur']} dd={local_opt['max_dd_pct']}% "
                f"{local_opt['params']}",
                flush=True,
            )
        else:
            print("  local opt: no robust profitable pack", flush=True)

    variants = []
    for name, params in packs:
        if params is None:
            row = summarize_variant(name, core_total, None, core_path)
        else:
            short = simulate_short_sleeve(
                daily_ts=daily_ts,
                daily_closes=daily_closes,
                i0=i0,
                i1=i1,
                core_occ=occ,
                cfg=idle_cfg(**params),
            )
            row = summarize_variant(name, core_total, short, core_path)
            print(
                f"  {name}: short={row['short_pnl_eur']} dd={row['short_max_dd_pct']}% "
                f"comb={row['combined_pnl_eur']} offset="
                f"{row['weeks_short_offsets_core_down']}/{row['weeks_core_down']}",
                flush=True,
            )
        variants.append(row)

    ranked = sorted(variants, key=lambda r: r["combined_pnl_eur"], reverse=True)
    return {
        "label": label,
        "start": _date(start_ms),
        "end": _date(end_ms),
        "days": days,
        "core_pnl_eur": round(core_total, 2),
        "core_trades": core_sum.get("trades"),
        "core_win_rate": core_sum.get("win_rate"),
        "local_robust_opt": local_opt,
        "variants": variants,
        "best_combined": ranked[0]["name"],
        "ranked_by_combined_pnl": [r["name"] for r in ranked],
    }


def main() -> None:
    # Anchor: recent 12w ended ~2026-09-20; prior slices end at that start.
    recent_end = datetime(2026, 9, 20, tzinfo=UTC)
    recent_start = recent_end - timedelta(days=84)

    windows: list[tuple[str, datetime, datetime, bool]] = [
        (
            "last_12w",
            recent_start,
            recent_end,
            False,  # already optimized elsewhere
        ),
        (
            "prior_12w",
            recent_start - timedelta(days=84),
            recent_start,
            True,
        ),
        (
            "prior2_12w",
            recent_start - timedelta(days=168),
            recent_start - timedelta(days=84),
            True,
        ),
        (
            "bear_oct25_jun26",
            datetime(2025, 10, 6, tzinfo=UTC),
            datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
            True,
        ),
    ]

    print("loading daily closes (shared)…", flush=True)
    bases = ("BTC", *DEFAULT_UNIVERSE)
    # Need history back through Oct 2025 + SMA warmup
    series = load_daily_map(bases, days=400)
    daily_ts, daily_closes = align_daily(series, bases)

    results = []
    for label, start_dt, end_dt, do_opt in windows:
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000) // BAR_MS * BAR_MS
        results.append(
            run_window(
                label=label,
                start_ms=start_ms,
                end_ms=end_ms,
                daily_ts=daily_ts,
                daily_closes=daily_closes,
                do_local_opt=do_opt,
            )
        )

    # Cross-window table for the last12w robust rec
    cross = []
    for w in results:
        rec = next(
            (v for v in w["variants"] if v["name"] == "robust_rec_from_last12w"),
            None,
        )
        alone = next(v for v in w["variants"] if v["name"] == "core_alone")
        cross.append(
            {
                "window": w["label"],
                "start": w["start"],
                "end": w["end"],
                "core_pnl_eur": w["core_pnl_eur"],
                "short_pnl_eur": rec["short_pnl_eur"] if rec else None,
                "short_max_dd_pct": rec["short_max_dd_pct"] if rec else None,
                "combined_pnl_eur": rec["combined_pnl_eur"] if rec else None,
                "vs_core_alone_combined": (
                    round(rec["combined_pnl_eur"] - alone["combined_pnl_eur"], 2)
                    if rec
                    else None
                ),
                "weeks_short_offsets_core_down": (
                    f"{rec['weeks_short_offsets_core_down']}/{rec['weeks_core_down']}"
                    if rec
                    else None
                ),
                "weeks_both_down": rec["weeks_both_down"] if rec else None,
                "weeks_combined_green": (
                    f"{rec['weeks_combined_green']}/{rec['weeks_total']}"
                    if rec
                    else None
                ),
                "local_opt_short_pnl": (
                    w["local_robust_opt"]["pnl_eur"] if w.get("local_robust_opt") else None
                ),
                "local_opt_params": (
                    w["local_robust_opt"]["params"] if w.get("local_robust_opt") else None
                ),
            }
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "intent": (
            "Same idle-complement short A/B on weeks before the last 12w, "
            "plus OOS check of the last-12w robust recommendation."
        ),
        "packs": {
            "robust_rec_from_last12w": ROBUST_REC,
            "prior_balanced": BALANCED,
            "dual_window_winner": DUAL_WIN,
        },
        "cross_window_robust_rec": cross,
        "windows": results,
        "caveats": [
            "Each 12w core replay is independent (no carry of open positions across windows)",
            "Local opt is in-sample per window; robust_rec_from_last12w is OOS on prior windows",
            "Bear window is long (~9m); 15m core sim is slower",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== CROSS-WINDOW (robust rec from last 12w) ===", flush=True)
    for row in cross:
        print(
            f"{row['window']:20} core={row['core_pnl_eur']:>9} "
            f"short={row['short_pnl_eur']:>8} dd={row['short_max_dd_pct']}% "
            f"Δvs_cash={row['vs_core_alone_combined']:>8} "
            f"offset={row['weeks_short_offsets_core_down']} "
            f"both_down={row['weeks_both_down']} green={row['weeks_combined_green']} "
            f"local_opt_short={row['local_opt_short_pnl']}",
            flush=True,
        )
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
