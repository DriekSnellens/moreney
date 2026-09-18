#!/usr/bin/env python3
"""12-week bar replay of the live momentum desk knobs (Sep 2026 pack).

Uses Bitvavo 15m candles (cache + refresh). No AlphaI timeline — tape +
chase/soft/macro knobs only (matches live requires_alphai_pick=false).
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import BAR_MS, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "live_cfg_12w_sim.json"
DAYS = 84  # 12 weeks


def live_cfg() -> DeskConfig:
    """Mirror live-micro desk knobs as of the entry-chase-gate pack."""
    return DeskConfig().with_overrides(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        refill_on_exit=True,
        clip_eur=20_000.0,
        book_eur=20_000.0,
        max_positions=1,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.09,
        chase_near_high=0.008,
        trail_pct=0.05,
        trail_tight_after=0.0,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        early_stop_pct=0.0,
        early_stop_until_peak=0.0,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        green_min_peak=0.01,
        fade_eta_sec=0.0,
        fade_confirm_sec=20.0,
        fade_smooth_sec=40.0,
        fade_min_peak_eur=15.0,
        fade_min_peak_pct=0.012,
        fade_min_giveback_eur=5.0,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        skip_weekend_entries=True,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        macro_caution_mode="reduce",
        macro_caution_requires_alphai_pick=False,
        requires_alphai_pick=False,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
        exit_on_touch=False,
    )


def _week_key(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def main() -> None:
    cfg = live_cfg()
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    print(
        f"loading candles days={DAYS} "
        f"{datetime.fromtimestamp(start_ms/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(end_ms/1000, UTC).date()}",
        flush=True,
    )
    candles = load_candles(
        ("BTC", *cfg.universe), days=DAYS + 2, end_ms=end_ms, refresh=True
    )
    print("simulating…", flush=True)
    res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    s = res.summary()

    by_reason: dict[str, dict] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    by_base: dict[str, dict] = defaultdict(lambda: {"n": 0, "net_eur": 0.0, "wins": 0})
    by_week: dict[str, dict] = defaultdict(lambda: {"n": 0, "net_eur": 0.0, "wins": 0})
    trades = []
    for t in res.closed:
        row = {
            "base": t.base,
            "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
            "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
            "hold_h": round((t.closed_ms - t.opened_ms) / 3_600_000, 2),
            "notional_eur": round(t.notional_eur, 2),
            "gross_pct": round(100 * t.gross_return, 2),
            "peak_pct": round(100 * t.peak_return, 2),
            "net_eur": round(t.net_eur, 2),
            "reason": t.reason,
            "entry_reason": t.entry_reason,
        }
        trades.append(row)
        br = by_reason[t.reason]
        br["n"] += 1
        br["net_eur"] += t.net_eur
        bb = by_base[t.base]
        bb["n"] += 1
        bb["net_eur"] += t.net_eur
        if t.net_eur > 0:
            bb["wins"] += 1
        wk = _week_key(t.closed_ms)
        bw = by_week[wk]
        bw["n"] += 1
        bw["net_eur"] += t.net_eur
        if t.net_eur > 0:
            bw["wins"] += 1

    open_mtm = round(sum(float(x.get("mtm_eur") or x.get("net_eur") or 0) for x in res.open_mtm), 2)
    # open_mtm structure may use different keys — fall back to summary
    if "open_mtm_eur" in s:
        open_mtm = s["open_mtm_eur"]

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "label": "live_cfg_12w",
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "caveats": [
            "15m close fills; no live maker path / AlphaI timeline",
            "book_eur=20000 clip=20000 max_positions=1 fixed5 trail chase 9%/0.8%",
            "requires_alphai_pick=false (tape + chase gate)",
        ],
        "config": {
            k: (list(v) if isinstance(v, tuple) else v)
            for k, v in cfg.__dict__.items()
            if k not in {"clusters", "universe"}
        },
        "summary": s,
        "open_mtm_eur": open_mtm,
        "total_with_open_eur": round(float(s.get("total_eur") or 0) + float(open_mtm or 0), 2),
        "by_reason": {
            k: {"n": v["n"], "net_eur": round(v["net_eur"], 2)}
            for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]["net_eur"])
        },
        "by_base": {
            k: {
                "n": v["n"],
                "wins": v["wins"],
                "win_rate": round(v["wins"] / v["n"], 3) if v["n"] else None,
                "net_eur": round(v["net_eur"], 2),
            }
            for k, v in sorted(by_base.items(), key=lambda kv: -kv[1]["net_eur"])
        },
        "by_week": {
            k: {
                "n": v["n"],
                "wins": v["wins"],
                "net_eur": round(v["net_eur"], 2),
            }
            for k, v in sorted(by_week.items())
        },
        "trades": trades,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "summary": s, "open_mtm_eur": open_mtm}, indent=2))


if __name__ == "__main__":
    main()
