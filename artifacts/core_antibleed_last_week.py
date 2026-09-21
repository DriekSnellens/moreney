#!/usr/bin/env python3
"""Past-week core replay: live knobs vs proposed anti-bleed pack.

No AlphaI timeline (same caveat as other sims). Writes
artifacts/core_antibleed_last_week.json
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from artifacts.combined_desk_12w_sim import live_core_cfg
from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "core_antibleed_last_week.json"


def pack_summary(res) -> dict[str, Any]:
    s = res.summary()
    closed = []
    for t in res.closed:
        closed.append(
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                "hold_h": round((t.closed_ms - t.opened_ms) / 3_600_000, 2),
                "notional": round(t.notional_eur, 2),
                "net_eur": round(t.net_eur, 2),
                "reason": t.reason,
                "entry_reason": getattr(t, "entry_reason", None),
            }
        )
    return {
        "summary": s,
        "pnl_total_eur": round(
            float(s.get("realized_eur") or 0) + float(s.get("open_mtm_eur") or 0), 2
        ),
        "trades": s.get("trades"),
        "win_rate": s.get("win_rate"),
        "max_drawdown_eur": s.get("max_drawdown_eur"),
        "by_reason": s.get("by_reason"),
        "closed_trades": closed,
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - 7 * 86_400_000
    print(
        f"window {_iso(start_ms)} → {_iso(end_ms)}",
        flush=True,
    )

    base = live_core_cfg()
    # Proposed anti-bleed levers (non-AlphaI), stacked.
    bleed = base.with_overrides(
        soft_regime_clip_mult=0.35,  # was 0.5
        weak_clip_mult=0.40,  # was 0.7
        hard_stop_pct=0.022,  # was 0.03 — cut losers earlier
        hard_stop_eur=220.0,  # was 300
        day_loss_limit_eur=400.0,  # was 750
        week_loss_limit_eur=1_000.0,  # was 2000
        min_excess=0.03,  # was 0.025 — slightly pickier
        entry_fee_buffer_mult=7.0,  # was 6
        soft_regime_fee_buffer_mult=8.0,  # was 6
        time_exit_hours=24.0,  # was 36 — less bleed on dead trades
        max_chase_ret_24h=0.06,  # was 0.09
        # idle flags already True on live; keep explicit
        soft_regime_on_weak_tape=True,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
    )

    # Also: aggressive idle-first variant (same stops, much smaller weak/soft)
    idle_heavy = bleed.with_overrides(
        soft_regime_clip_mult=0.25,
        weak_clip_mult=0.25,
        day_loss_limit_eur=300.0,
        week_loss_limit_eur=700.0,
    )

    candles = load_candles(
        ("BTC", *base.universe), days=10, end_ms=end_ms, refresh=False
    )

    variants = {
        "live_baseline": base,
        "antibleed": bleed,
        "antibleed_idle_heavy": idle_heavy,
    }
    out_vars = {}
    for name, cfg in variants.items():
        print(f"sim {name}…", flush=True)
        res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
        out_vars[name] = {
            "knobs": {
                "soft_regime_clip_mult": cfg.soft_regime_clip_mult,
                "weak_clip_mult": cfg.weak_clip_mult,
                "hard_stop_pct": cfg.hard_stop_pct,
                "hard_stop_eur": cfg.hard_stop_eur,
                "day_loss_limit_eur": cfg.day_loss_limit_eur,
                "week_loss_limit_eur": cfg.week_loss_limit_eur,
                "min_excess": cfg.min_excess,
                "entry_fee_buffer_mult": cfg.entry_fee_buffer_mult,
                "soft_regime_fee_buffer_mult": cfg.soft_regime_fee_buffer_mult,
                "time_exit_hours": cfg.time_exit_hours,
                "max_chase_ret_24h": cfg.max_chase_ret_24h,
                "book_eur": cfg.book_eur,
                "clip_eur": cfg.clip_eur,
            },
            **pack_summary(res),
        }
        s = out_vars[name]
        print(
            f"  pnl={s['pnl_total_eur']} trades={s['trades']} wr={s['win_rate']} "
            f"dd={s['max_drawdown_eur']} by={s['by_reason']}",
            flush=True,
        )

    base_pnl = out_vars["live_baseline"]["pnl_total_eur"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "start": _iso(start_ms),
            "end": _iso(end_ms),
            "days": 7,
        },
        "caveats": [
            "No AlphaI timeline — live week can differ when picks/avoid/macro fire",
            "15m close fills; no maker path",
            "Anti-bleed = smaller soft/weak clips, tighter stops/limits, pickier entry",
        ],
        "variants": out_vars,
        "deltas_vs_live": {
            name: {
                "pnl_delta_eur": round(v["pnl_total_eur"] - base_pnl, 2),
                "trades_delta": int(v["trades"] or 0)
                - int(out_vars["live_baseline"]["trades"] or 0),
            }
            for name, v in out_vars.items()
            if name != "live_baseline"
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
