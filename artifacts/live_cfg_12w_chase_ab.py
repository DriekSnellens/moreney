#!/usr/bin/env python3
"""12w A/B: late-chase gate on (9%/0.8%) vs off under live desk knobs."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate
from artifacts.live_cfg_12w_sim import live_cfg

OUT = Path(__file__).resolve().parent / "live_cfg_12w_chase_ab.json"
DAYS = 84


def pack(res, *, label: str, cfg) -> dict:
    s = res.summary()
    return {
        "label": label,
        "chase": {
            "max_chase_ret_24h": cfg.max_chase_ret_24h,
            "chase_near_high": cfg.chase_near_high,
        },
        "summary": s,
        "open_positions": [
            {
                "base": m.get("base"),
                "opened": m.get("opened"),
                "net_eur": m.get("net_eur"),
                "gross_return": m.get("gross_return"),
                "peak_return": m.get("peak_return"),
            }
            for m in res.open_mtm
        ],
        "trades": [
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                "net_eur": round(t.net_eur, 2),
                "reason": t.reason,
                "peak_pct": round(100 * t.peak_return, 2),
                "entry_reason": t.entry_reason,
            }
            for t in res.closed
        ],
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    base = live_cfg()
    candles = load_candles(
        ("BTC", *base.universe), days=DAYS + 2, end_ms=end_ms, refresh=False
    )
    variants = {
        "chase_on_9pct": base.with_overrides(
            max_chase_ret_24h=0.09, chase_near_high=0.008
        ),
        "chase_off": base.with_overrides(max_chase_ret_24h=0.0, chase_near_high=0.008),
    }
    results = {
        name: pack(
            simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None),
            label=name,
            cfg=cfg,
        )
        for name, cfg in variants.items()
    }
    on, off = results["chase_on_9pct"]["summary"], results["chase_off"]["summary"]
    on_keys = {(t["base"], t["opened"]) for t in results["chase_on_9pct"]["trades"]}
    off_keys = {(t["base"], t["opened"]) for t in results["chase_off"]["trades"]}
    only_off = [
        t
        for t in results["chase_off"]["trades"]
        if (t["base"], t["opened"]) not in on_keys
    ]
    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "variants": results,
        "only_in_chase_off_trades": only_off,
        "verdict": {
            "chase_on_minus_off": {
                "delta_realized_eur": round(on["realized_eur"] - off["realized_eur"], 2),
                "delta_total_eur": round(on["total_eur"] - off["total_eur"], 2),
                "delta_dd_eur": round(on["max_drawdown_eur"] - off["max_drawdown_eur"], 2),
                "delta_trades": on["trades"] - off["trades"],
            },
            "only_in_chase_off_net_eur": round(sum(t["net_eur"] for t in only_off), 2),
            "winner_by_total": (
                "chase_on_9pct" if on["total_eur"] >= off["total_eur"] else "chase_off"
            ),
        },
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "verdict": out["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
