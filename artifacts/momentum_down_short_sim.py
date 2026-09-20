#!/usr/bin/env python3
"""Simulate complementary short: ON while momentum desk is down/idle.

User intent: when the long momentum sleeve is idle/weak, run shorts so the
combined desk is rarely flat — ideally always compounding.

Compares on the same 12w core occupancy + daily short book (€20k each):
  1. core_alone          — no short
  2. bear_sma200         — live pack (BTC < SMA200 only)
  3. idle_absolute       — short weakest while core idle (cover on core entry)
  4. idle_excess         — old idle-fill excess-vs-BTC while core idle
  5. always_absolute     — short always (no gate; stress test)

Writes artifacts/momentum_down_short_sim.json
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from artifacts.combined_desk_12w_sim import (
    CORE_BOOK,
    DAYS,
    SHORT_BOOK,
    _day_ms,
    _week_key,
    align_daily,
    build_core_occupancy_full,
    combine_paths,
    core_daily_equity,
    live_core_cfg,
    load_daily_map,
    simulate_short_sleeve,
)
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE
from bot.live.momentum_short_weakest import ShortWeakestConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "momentum_down_short_sim.json"

Gate = Literal["bear_only", "idle_absolute", "idle_excess", "always_absolute", "flat"]


def balanced_pack(**overrides: Any) -> ShortWeakestConfig:
    """Live bear-harvest balanced knobs, then overrides for the gate under test."""
    base = ShortWeakestConfig(
        book_eur=SHORT_BOOK,
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
        require_btc_below_sma200=True,
        only_when_core_idle=False,
        cover_when_core_active=False,
        idle_fill_enabled=False,
        alphai_enabled=False,
        universe=DEFAULT_UNIVERSE,
    )
    return replace(base, **overrides) if overrides else base


def cfg_for_gate(gate: Gate) -> ShortWeakestConfig | None:
    if gate == "flat":
        return None
    if gate == "bear_only":
        # Live: only when BTC < SMA200 (independent of core).
        return balanced_pack()
    if gate == "idle_absolute":
        # User intent: while momentum idle → short absolute weakest; cover when core longs.
        # require_btc_below_sma200=False ⇒ btc_bear_ok always True ⇒ absolute mode entries
        # whenever only_when_core_idle allows.
        return balanced_pack(
            require_btc_below_sma200=False,
            only_when_core_idle=True,
            cover_when_core_active=True,
            idle_fill_enabled=False,
        )
    if gate == "idle_excess":
        # Old idle-fill: excess-vs-BTC only when core idle AND above SMA200.
        return balanced_pack(
            require_btc_below_sma200=True,
            only_when_core_idle=True,
            cover_when_core_active=True,
            idle_fill_enabled=True,
            idle_lookback_days=14,
            idle_excess_floor=-0.025,
            top_n=3,
            rebalance_days=14,
            max_weight=0.15,
            trail_pct=0.18,
            hard_stop_pct=0.12,
            skip_days=0,
            bounce_block_pct=0.0,
            vol_spike_exit=True,
        )
    if gate == "always_absolute":
        return balanced_pack(
            require_btc_below_sma200=False,
            only_when_core_idle=False,
            cover_when_core_active=False,
            idle_fill_enabled=False,
        )
    raise ValueError(gate)


def week_stats(combined_path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    week_last: dict[str, dict[str, float]] = {}
    for row in combined_path:
        wk = _week_key(
            int(datetime.fromisoformat(row["date"]).replace(tzinfo=UTC).timestamp() * 1000)
        )
        week_last[wk] = row
    rows = []
    prev_eq = CORE_BOOK + SHORT_BOOK
    prev_core = CORE_BOOK
    prev_short = SHORT_BOOK
    for wk in sorted(week_last):
        row = week_last[wk]
        c = round(float(row["core_eur"]) - prev_core, 2)
        s = round(float(row["short_eur"]) - prev_short, 2)
        comb = round(float(row["combined_eur"]) - prev_eq, 2)
        rows.append(
            {
                "week": wk,
                "core_pnl_eur": c,
                "short_pnl_eur": s,
                "combined_pnl_eur": comb,
                "core_down": c < 0,
                "short_up_when_core_down": c < 0 and s > 0,
                "both_down": c < 0 and s < 0,
                "combined_green": comb > 0,
            }
        )
        prev_eq = float(row["combined_eur"])
        prev_core = float(row["core_eur"])
        prev_short = float(row["short_eur"])
    return rows


def summarize_variant(
    name: str,
    core_total: float,
    short: dict[str, Any] | None,
    core_path: list[dict[str, Any]],
) -> dict[str, Any]:
    if short is None:
        # flat short book sits in cash
        short_pnl = 0.0
        short_dd = 0.0
        short_path = [
            {
                "date": r["date"],
                "equity_eur": SHORT_BOOK,
                "n_pos": 0,
                "core_idle": not r.get("active"),
            }
            for r in core_path
        ]
        active_days = 0
        idle_days = sum(1 for r in core_path if not r.get("active"))
        trades = 0
        win_rate = None
    else:
        short_pnl = float(short["pnl_eur"])
        short_dd = float(short["max_dd_pct"])
        short_path = short["equity_path_full"]
        active_days = int(short["short_active_days"])
        idle_days = int(short["idle_core_days"])
        trades = int(short["trades"])
        win_rate = short.get("win_rate")

    combined_path = combine_paths(core_path, short_path)
    peak = CORE_BOOK + SHORT_BOOK
    mdd = 0.0
    for row in combined_path:
        v = float(row["combined_eur"])
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    end = float(combined_path[-1]["combined_eur"]) if combined_path else CORE_BOOK + SHORT_BOOK
    comb_pnl = end - (CORE_BOOK + SHORT_BOOK)
    weeks = week_stats(combined_path)
    core_down_weeks = [w for w in weeks if w["core_down"]]
    covered = sum(1 for w in core_down_weeks if w["short_up_when_core_down"])
    both_down = sum(1 for w in weeks if w["both_down"])
    green = sum(1 for w in weeks if w["combined_green"])
    # days: core idle but short also flat (= dead capital)
    both_flat_days = sum(
        1
        for r in combined_path
        if not r.get("core_active") and not r.get("short_n")
    )
    return {
        "name": name,
        "core_pnl_eur": round(core_total, 2),
        "short_pnl_eur": round(short_pnl, 2),
        "combined_pnl_eur": round(comb_pnl, 2),
        "combined_return_pct": round(100 * comb_pnl / (CORE_BOOK + SHORT_BOOK), 2),
        "combined_max_dd_pct": round(100 * mdd / (CORE_BOOK + SHORT_BOOK), 2),
        "short_max_dd_pct": short_dd,
        "short_trades": trades,
        "short_win_rate": win_rate,
        "short_active_days": active_days,
        "core_idle_days": idle_days,
        "both_flat_days": both_flat_days,
        "weeks_total": len(weeks),
        "weeks_combined_green": green,
        "weeks_core_down": len(core_down_weeks),
        "weeks_short_offsets_core_down": covered,
        "weeks_both_down": both_down,
        "beats_core_alone": comb_pnl > core_total,  # vs leaving short book cash
        # Fairer: core alone on €20k vs combined on €40k — also report per-euro
        "pnl_per_eur_deployed": round(comb_pnl / (CORE_BOOK + SHORT_BOOK), 4),
        "core_alone_pnl_per_eur": round(core_total / CORE_BOOK, 4),
        "weeks": weeks,
    }


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
    print(
        f"core pnl={core_total:.2f} trades={core_sum.get('trades')} "
        f"intervals={len(occ.intervals)}",
        flush=True,
    )

    bases = ("BTC", *DEFAULT_UNIVERSE)
    series = load_daily_map(bases, days=DAYS + 220)
    daily_ts, daily_closes = align_daily(series, bases)
    i0 = next(i for i, t in enumerate(daily_ts) if t >= _day_ms(start_ms))
    i1 = len(daily_ts) - 1 - next(
        i for i, t in enumerate(reversed(daily_ts)) if t <= end_ms
    )
    core_path = core_daily_equity(
        core_res,
        daily_ts=daily_ts,
        daily_closes=daily_closes,
        start_ms=start_ms,
        end_ms=end_ms,
        book=CORE_BOOK,
    )

    variants: list[tuple[str, Gate]] = [
        ("core_alone", "flat"),
        ("bear_sma200_live", "bear_only"),
        ("idle_absolute_complement", "idle_absolute"),
        ("idle_excess_old", "idle_excess"),
        ("always_absolute", "always_absolute"),
    ]
    results = []
    for name, gate in variants:
        print(f"… {name}", flush=True)
        scfg = cfg_for_gate(gate)
        if scfg is None:
            results.append(summarize_variant(name, core_total, None, core_path))
            continue
        short = simulate_short_sleeve(
            daily_ts=daily_ts,
            daily_closes=daily_closes,
            i0=i0,
            i1=i1,
            core_occ=occ,
            cfg=scfg,
        )
        print(
            f"   short pnl={short['pnl_eur']} dd={short['max_dd_pct']}% "
            f"active={short['short_active_days']}d",
            flush=True,
        )
        results.append(summarize_variant(name, core_total, short, core_path))

    # Rank by combined PnL, then by offset quality
    ranked = sorted(
        results,
        key=lambda r: (r["combined_pnl_eur"], r["weeks_short_offsets_core_down"]),
        reverse=True,
    )
    verdict = {
        "best_combined": ranked[0]["name"],
        "user_intent": "idle_absolute_complement",
        "user_intent_beats_core_alone_cash": next(
            r["beats_core_alone"] for r in results if r["name"] == "idle_absolute_complement"
        ),
        "user_intent_vs_bear_live_pnl_delta": round(
            next(r["combined_pnl_eur"] for r in results if r["name"] == "idle_absolute_complement")
            - next(r["combined_pnl_eur"] for r in results if r["name"] == "bear_sma200_live"),
            2,
        ),
        "note": (
            "core_alone leaves €20k short book in cash (combined = core + 20k). "
            "beats_core_alone compares combined €40k PnL to core PnL alone — "
            "also check pnl_per_eur_deployed vs core_alone_pnl_per_eur."
        ),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "intent": (
            "While momentum desk is down/idle, run short-weakest so capital "
            "is always working; measure if that beats bear-only / core-alone."
        ),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).date().isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).date().isoformat(),
        },
        "books": {"core_eur": CORE_BOOK, "short_eur": SHORT_BOOK},
        "core": {
            "pnl_total_eur": round(core_total, 2),
            "trades": core_sum.get("trades"),
            "win_rate": core_sum.get("win_rate"),
            "summary": {
                k: core_sum.get(k)
                for k in (
                    "realized_eur",
                    "open_mtm_eur",
                    "max_drawdown_eur",
                    "by_reason",
                )
            },
        },
        "variants": results,
        "ranked_by_combined_pnl": [r["name"] for r in ranked],
        "verdict": verdict,
        "caveats": [
            "Core: 15m close fills; no AlphaI timeline",
            "Short: daily bars; paper synthetic; no funding",
            "Momentum-down ≈ core occupancy idle (live weak_tape often → idle)",
            "idle_absolute = core-idle gate + absolute weakest (no SMA200 wait)",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== VERDICT ===", flush=True)
    for r in ranked:
        print(
            f"{r['name']:28} combined={r['combined_pnl_eur']:>9} "
            f"short={r['short_pnl_eur']:>8} dd={r['combined_max_dd_pct']:>6}% "
            f"offset_weeks={r['weeks_short_offsets_core_down']}/"
            f"{r['weeks_core_down']} both_down={r['weeks_both_down']} "
            f"flat_days={r['both_flat_days']}",
            flush=True,
        )
    print(json.dumps(verdict, indent=2), flush=True)
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
