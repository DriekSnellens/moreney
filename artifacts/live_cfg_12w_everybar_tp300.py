#!/usr/bin/env python3
"""12w research: no hour slots + take-profit at +€300 net.

Uses 15m bars (sim resolution — not true 1-minute marks). Live unchanged.
Compares against live hours 7/13/16 without the €300 TP.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    Position,
    RiskLedger,
    evaluate_exit,
    net_pnl_eur,
)
from bot.research.momentum_backtest.engine import (
    BacktestResult,
    ClosedTrade,
    _try_entries,
    load_candles,
    simulate,
)
from artifacts.live_cfg_12w_sim import live_cfg

OUT = Path(__file__).resolve().parent / "live_cfg_12w_everybar_tp300.json"
DAYS = 84
TP_EUR = 300.0


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def simulate_with_tp(
    candles_by_base,
    cfg,
    *,
    start_ms: int,
    end_ms: int,
    tp_eur: float,
) -> BacktestResult:
    """Same as engine.simulate, plus exit when unrealized net ≥ tp_eur."""
    idx = {b: {int(r[0]): r for r in rows} for b, rows in candles_by_base.items()}
    ledger = RiskLedger(
        day_loss_limit_eur=cfg.day_loss_limit_eur,
        week_loss_limit_eur=cfg.week_loss_limit_eur,
        pause_hours=cfg.pause_hours_after_week_limit,
    )
    res = BacktestResult(start_ms=start_ms, end_ms=end_ms, cfg=cfg)
    positions: list[Position] = []
    t = start_ms // BAR_MS * BAR_MS
    view: AlphaIView | None = None
    while t <= end_ms:
        closed_ts = t - BAR_MS
        for pos in list(positions):
            bar = idx.get(pos.base, {}).get(closed_ts)
            if bar is None:
                continue
            close = float(bar[4])
            # Update peak via evaluate_exit path: call evaluate_exit which mutates peak.
            decision = evaluate_exit(pos, bar, cfg, alphai=view)
            net = net_pnl_eur(pos, close, None, cfg)
            reason = None
            exit_price = close
            if net >= tp_eur:
                reason = "take_profit_300"
                exit_price = close
            elif decision is not None:
                reason = decision.reason
                exit_price = (
                    decision.price if decision.price is not None else close
                )
                net = net_pnl_eur(pos, exit_price, None, cfg)
            if reason is None:
                continue
            res.closed.append(
                ClosedTrade(
                    base=pos.base,
                    opened_ms=pos.opened_ms,
                    closed_ms=t,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    notional_eur=pos.notional_eur,
                    gross_return=(exit_price / pos.entry_price - 1.0)
                    if pos.entry_price
                    else 0.0,
                    peak_return=pos.peak / pos.entry_price - 1.0
                    if pos.entry_price
                    else 0.0,
                    net_eur=net,
                    reason=reason,
                    entry_reason=pos.entry_reason,
                )
            )
            ledger.note_close(net, t)
            positions.remove(pos)
            if bool(getattr(cfg, "refill_on_exit", False)):
                _try_entries(
                    res,
                    ledger,
                    positions,
                    candles_by_base,
                    cfg,
                    view,
                    t,
                    log_decision=False,
                )
        # Entries: every 15m on weekdays (interval), no 7/13/16 filter.
        from bot.live.momentum_desk import is_decision_time

        if is_decision_time(t, cfg):
            _try_entries(
                res,
                ledger,
                positions,
                candles_by_base,
                cfg,
                view,
                t,
                log_decision=True,
            )
        t += BAR_MS

    for pos in positions:
        rows = [r for r in (candles_by_base.get(pos.base) or []) if int(r[0]) < end_ms]
        last = rows[-1] if rows else None
        px = float(last[4]) if last else pos.entry_price
        res.open_mtm.append(
            {
                "base": pos.base,
                "opened": _fmt(pos.opened_ms),
                "entry_price": pos.entry_price,
                "mark": px,
                "gross_return": round(pos.gross_return(px), 4),
                "peak_return": round(pos.peak / pos.entry_price - 1.0, 4)
                if pos.entry_price
                else 0.0,
                "net_eur": round(net_pnl_eur(pos, px, None, cfg), 2),
            }
        )
    return res


def pack(res: BacktestResult, *, label: str) -> dict:
    s = res.summary()
    by_reason: dict[str, dict] = {}
    for t in res.closed:
        b = by_reason.setdefault(t.reason, {"n": 0, "net_eur": 0.0})
        b["n"] += 1
        b["net_eur"] += t.net_eur
    return {
        "label": label,
        "summary": s,
        "by_reason": {
            k: {"n": v["n"], "net_eur": round(v["net_eur"], 2)}
            for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1]["net_eur"])
        },
        "open_positions": list(res.open_mtm),
        "trades_sample": [
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                "net_eur": round(t.net_eur, 2),
                "reason": t.reason,
                "peak_pct": round(100 * t.peak_return, 2),
            }
            for t in res.closed[:15]
        ]
        + (
            [
                {
                    "base": t.base,
                    "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                    "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                    "net_eur": round(t.net_eur, 2),
                    "reason": t.reason,
                    "peak_pct": round(100 * t.peak_return, 2),
                }
                for t in res.closed[-5:]
            ]
            if len(res.closed) > 20
            else []
        ),
    }


def main() -> None:
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    base = live_cfg()
    print("load candles…", flush=True)
    candles = load_candles(
        ("BTC", *base.universe), days=DAYS + 2, end_ms=end_ms, refresh=False
    )

    live_hours = base.with_overrides(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        decision_every_bar=False,
    )
    every_15m = base.with_overrides(
        decision_hours_utc=(7, 13, 16),  # ignored when interval>0
        decision_interval_sec=900.0,
        decision_every_bar=False,
    )

    print("sim A: live hours, no TP…", flush=True)
    a = simulate(candles, live_hours, start_ms=start_ms, end_ms=end_ms, alphai=None)
    print("sim B: every 15m weekday, no TP…", flush=True)
    b = simulate(candles, every_15m, start_ms=start_ms, end_ms=end_ms, alphai=None)
    print("sim C: every 15m weekday + TP €300…", flush=True)
    c = simulate_with_tp(
        candles, every_15m, start_ms=start_ms, end_ms=end_ms, tp_eur=TP_EUR
    )

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "caveats": [
            "15m bar resolution — not true 1-minute checks (no 1m universe history here)",
            "take_profit at unrealized net ≥ €300 on bar close",
            "hard stop / trail / time-exit still apply if they fire first",
            "no AlphaI timeline; live config not changed",
        ],
        "variants": {
            "live_hours_7_13_16": pack(a, label="live_hours_7_13_16"),
            "every_15m_weekday": pack(b, label="every_15m_weekday"),
            "every_15m_weekday_tp300": pack(c, label="every_15m_weekday_tp300"),
        },
        "verdict": {
            "tp300_minus_live_hours": {
                "delta_realized_eur": round(
                    c.summary()["realized_eur"] - a.summary()["realized_eur"], 2
                ),
                "delta_total_eur": round(
                    c.summary()["total_eur"] - a.summary()["total_eur"], 2
                ),
                "delta_dd_eur": round(
                    c.summary()["max_drawdown_eur"] - a.summary()["max_drawdown_eur"], 2
                ),
                "delta_trades": c.summary()["trades"] - a.summary()["trades"],
            },
            "tp300_minus_every_15m": {
                "delta_realized_eur": round(
                    c.summary()["realized_eur"] - b.summary()["realized_eur"], 2
                ),
                "delta_total_eur": round(
                    c.summary()["total_eur"] - b.summary()["total_eur"], 2
                ),
                "delta_dd_eur": round(
                    c.summary()["max_drawdown_eur"] - b.summary()["max_drawdown_eur"], 2
                ),
            },
        },
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "summaries": {
        k: v["summary"] for k, v in out["variants"].items()
    }, "verdict": out["verdict"]}, indent=2))


if __name__ == "__main__":
    main()
