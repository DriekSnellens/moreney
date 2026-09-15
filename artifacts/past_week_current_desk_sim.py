#!/usr/bin/env python3
"""Replay the last 7 days with the current WR desk + weak-tape survival knobs.

Writes ``artifacts/past_week_current_desk_sim.json``.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.live.momentum_desk import DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).with_suffix(".json")
BOOK = 20_000.0
CLIP = 10_000.0


def live_cfg(**extra) -> DeskConfig:
    knobs = dict(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        clip_eur=CLIP,
        max_positions=3,
        book_eur=BOOK,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        green_min_peak=0.01,
        fade_eta_sec=180.0,
        fade_confirm_sec=20.0,
        fade_smooth_sec=40.0,
        fade_min_peak_eur=15.0,
        fade_min_peak_pct=0.012,
        fade_min_giveback_eur=5.0,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        skip_weekend_entries=True,
        refill_on_exit=True,
        macro_caution_mode="reduce",
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
    )
    knobs.update(extra)
    return DeskConfig().with_overrides(**knobs)


def regime_of(reason: str) -> str:
    r = reason or ""
    for tag in ("strong", "soft", "weak", "firm"):
        if f"regime={tag}" in r:
            return tag
    if "soft_regime" in r:
        return "soft"
    return "firm"


def pack(res, *, label: str, cfg: DeskConfig) -> dict:
    s = res.summary()
    closed: list[dict] = []
    by_reg: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "net_eur": 0.0})
    for t in res.closed:
        er = getattr(t, "entry_reason", "") or ""
        lab = regime_of(er)
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
            "entry_reason": er,
            "regime_label": lab,
        }
        closed.append(row)
        b = by_reg[lab]
        b["n"] += 1
        b["net_eur"] += row["net_eur"]
        if row["net_eur"] > 0:
            b["wins"] += 1
    idle: Counter[str] = Counter()
    soft_n = ok_n = 0
    for d in res.decisions:
        reasons = tuple(getattr(d, "reasons", ()) or ())
        if getattr(d, "regime_ok", False):
            ok_n += 1
            if getattr(d, "soft", False) or any("soft" in str(x) for x in reasons):
                soft_n += 1
        for r in reasons:
            idle[str(r)] += 1
    regime_pnl = {
        k: {
            "n": v["n"],
            "wins": v["wins"],
            "win_rate": round(v["wins"] / v["n"], 3) if v["n"] else None,
            "net_eur": round(v["net_eur"], 2),
            "avg_net_eur": round(v["net_eur"] / v["n"], 2) if v["n"] else None,
        }
        for k, v in sorted(by_reg.items())
    }
    return {
        "label": label,
        "config": {
            "book_eur": cfg.book_eur,
            "clip_eur": cfg.clip_eur,
            "max_positions": cfg.max_positions,
            "decision_hours_utc": list(cfg.decision_hours_utc),
            "trail": [cfg.trail_pct, cfg.trail_tight_after, cfg.trail_tight_pct],
            "hard_stop_pct": cfg.hard_stop_pct,
            "time_exit_hours": cfg.time_exit_hours,
            "min_excess": cfg.min_excess,
            "soft_regime_on_weak_tape": cfg.soft_regime_on_weak_tape,
            "weak_tape_idle_on_double": cfg.weak_tape_idle_on_double,
            "soft_regime_idle_on_macro_caution": cfg.soft_regime_idle_on_macro_caution,
            "day_loss_limit_eur": cfg.day_loss_limit_eur,
            "week_loss_limit_eur": cfg.week_loss_limit_eur,
        },
        "summary": s,
        "return_on_book_pct": round(
            100.0 * float(s.get("total_eur") or 0) / float(cfg.book_eur or BOOK), 3
        ),
        "regime_pnl": regime_pnl,
        "decision_stats": {
            "n": len(res.decisions),
            "regime_ok": ok_n,
            "soft_ok": soft_n,
            "reason_counts": dict(idle.most_common(15)),
        },
        "closed_trades": closed,
        "open_mtm": list(getattr(res, "open_mtm", []) or []),
    }


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    candles = load_candles(("BTC", *DeskConfig().universe), days=14, end_ms=end_ms)
    variants = {
        "current_20k": live_cfg(),
        "legacy_soft_no_idle": live_cfg(
            weak_tape_idle_on_double=False,
            soft_regime_idle_on_macro_caution=False,
        ),
        "hard_block_no_soft": live_cfg(soft_regime_on_weak_tape=False),
        "micro_live_defaults": live_cfg(
            book_eur=4036.0,
            clip_eur=1300.0,
            day_loss_limit_eur=100.0,
            week_loss_limit_eur=250.0,
        ),
    }
    out = {
        "asof": end.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": 7},
        "caveats": [
            "15m bar replay; fills at bar close (no live maker/taker path).",
            "No historical AlphaI timeline — soft+macro idle cannot fire; tape-only.",
            "Fade-ETA exits need dense marks; bar sim mostly trail/stop/time.",
        ],
        "variants": {
            name: pack(simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None), label=name, cfg=cfg)
            for name, cfg in variants.items()
        },
    }
    OUT.write_text(json.dumps(out, indent=2))
    cur = out["variants"]["current_20k"]
    s = cur["summary"]
    print(f"wrote {OUT}")
    print(
        f"current_20k: trades={s.get('trades')} WR={s.get('win_rate')} "
        f"realized={s.get('realized_eur')}€ return={cur['return_on_book_pct']}%"
    )


if __name__ == "__main__":
    main()
