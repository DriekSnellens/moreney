#!/usr/bin/env python3
"""12w A/B: live decision hours (7/13/16) vs no hour filter (every 15m weekday)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import BAR_MS
from bot.research.momentum_backtest.engine import load_candles, simulate

from artifacts.live_cfg_12w_sim import live_cfg

OUT = Path(__file__).resolve().parent / "live_cfg_12w_hours_ab.json"
DAYS = 84


def pack(res, *, label: str, cfg) -> dict:
    s = res.summary()
    trades = [
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
    ]
    return {
        "label": label,
        "decision": {
            "hours_utc": list(cfg.decision_hours_utc),
            "decision_interval_sec": cfg.decision_interval_sec,
            "decision_every_bar": cfg.decision_every_bar,
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
        "trades": trades,
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    base = live_cfg()
    print(
        f"candles {datetime.fromtimestamp(start_ms/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(end_ms/1000, UTC).date()}",
        flush=True,
    )
    candles = load_candles(
        ("BTC", *base.universe), days=DAYS + 2, end_ms=end_ms, refresh=False
    )

    variants = {
        "hours_7_13_16": base.with_overrides(
            decision_hours_utc=(7, 13, 16),
            decision_interval_sec=0.0,
            decision_every_bar=False,
        ),
        # Same weekday skip, but enter on any 15m bar (no 7/13/16 filter).
        "every_15m_weekday": base.with_overrides(
            decision_hours_utc=(7, 13, 16),  # ignored when interval > 0
            decision_interval_sec=900.0,
            decision_every_bar=False,
        ),
        # Also: every clock hour Mon–Fri (middle ground).
        "every_hour_weekday": base.with_overrides(
            decision_hours_utc=tuple(range(24)),
            decision_interval_sec=0.0,
            decision_every_bar=False,
        ),
    }

    results = {}
    for name, cfg in variants.items():
        print(f"sim {name}…", flush=True)
        res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
        results[name] = pack(res, label=name, cfg=cfg)
        s = results[name]["summary"]
        print(
            f"  trades={s['trades']} wr={s['win_rate']} "
            f"realized={s['realized_eur']} total={s['total_eur']} "
            f"dd={s['max_drawdown_eur']}",
            flush=True,
        )

    h = results["hours_7_13_16"]["summary"]
    e = results["every_15m_weekday"]["summary"]
    o = results["every_hour_weekday"]["summary"]
    verdict = {
        "hours_vs_every_15m": {
            "delta_realized_eur": round(h["realized_eur"] - e["realized_eur"], 2),
            "delta_total_eur": round(h["total_eur"] - e["total_eur"], 2),
            "delta_dd_eur": round(h["max_drawdown_eur"] - e["max_drawdown_eur"], 2),
            "delta_trades": h["trades"] - e["trades"],
        },
        "hours_vs_every_hour": {
            "delta_realized_eur": round(h["realized_eur"] - o["realized_eur"], 2),
            "delta_total_eur": round(h["total_eur"] - o["total_eur"], 2),
            "delta_dd_eur": round(h["max_drawdown_eur"] - o["max_drawdown_eur"], 2),
            "delta_trades": h["trades"] - o["trades"],
        },
        "winner_by_total": max(
            results,
            key=lambda k: results[k]["summary"]["total_eur"],
        ),
        "winner_by_calmar_proxy": max(
            results,
            key=lambda k: (
                results[k]["summary"]["total_eur"]
                / max(1.0, abs(results[k]["summary"]["max_drawdown_eur"]))
            ),
        ),
        "summary_nl": (
            "Vaste uren 7/13/16 vs elke 15m (weekdag) vs elk uur (weekdag) op dezelfde "
            "12w live-knobs. Positieve delta_realized = uren winnen op gesloten PnL."
        ),
    }

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "caveats": [
            "15m close fills; no AlphaI timeline",
            "every_15m_weekday uses decision_interval_sec=900 (weekday filter kept)",
            "exits still run every bar in all variants",
        ],
        "variants": results,
        "verdict": verdict,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "verdict": verdict}, indent=2))


if __name__ == "__main__":
    main()
