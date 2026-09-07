"""CLI: ``python -m bot.research.momentum_backtest --days 90``."""

from __future__ import annotations

import argparse
import json
import time

from bot.live.momentum_desk import BAR_MS, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate, walk_forward


def _parse_hours(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split(",") if x.strip() != "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily Momentum Desk walk-forward backtest")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--window-days", type=int, default=14)
    ap.add_argument("--hours", type=_parse_hours, default=(0,))
    ap.add_argument("--clip", type=float, default=500.0)
    ap.add_argument("--trail", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--min-excess", type=float, default=None)
    ap.add_argument("--max-positions", type=int, default=None)
    ap.add_argument("--time-exit-hours", type=float, default=None)
    ap.add_argument("--refresh", action="store_true", help="ignore candle cache")
    ap.add_argument("--trades", action="store_true", help="print every closed trade")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    overrides = {"decision_hours_utc": args.hours, "clip_eur": args.clip}
    for key, val in (
        ("trail_pct", args.trail),
        ("hard_stop_pct", args.stop),
        ("min_excess", args.min_excess),
        ("max_positions", args.max_positions),
        ("time_exit_hours", args.time_exit_hours),
    ):
        if val is not None:
            overrides[key] = val
    cfg = DeskConfig().with_overrides(**overrides)

    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - args.days * 86_400_000
    candles = load_candles(
        ("BTC", *cfg.universe), days=args.days, end_ms=end_ms, refresh=args.refresh
    )

    full = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms)
    windows = walk_forward(
        candles, cfg, start_ms=start_ms, end_ms=end_ms, window_days=args.window_days
    )
    if args.json:
        print(
            json.dumps(
                {
                    "config": {
                        k: (list(v) if isinstance(v, tuple) else v)
                        for k, v in cfg.__dict__.items()
                        if k != "clusters"
                    },
                    "full": full.summary(),
                    "windows": [w.summary() for w in windows],
                    "trades": [t.as_row() for t in full.closed],
                },
                indent=2,
                default=str,
            )
        )
        return

    s = full.summary()
    print(
        f"Window {s['window']}  hours={list(cfg.decision_hours_utc)} clip=€{cfg.clip_eur:.0f} "
        f"trail={cfg.trail_pct:.1%} stop={cfg.hard_stop_pct:.1%} "
        f"time_exit={cfg.time_exit_hours:.0f}h"
    )
    print(
        f"  trades {s['trades']} (open {s['open']})  win {s['win_rate']}  "
        f"realized €{s['realized_eur']:+.2f}  open €{s['open_mtm_eur']:+.2f}  "
        f"total €{s['total_eur']:+.2f}  fees €{s['fees_eur']:.2f}  "
        f"maxDD €{s['max_drawdown_eur']:.2f}"
    )
    print(
        f"  regime ON at {s['regime_on_points']}/{s['decision_points']} decision points; "
        f"by reason: {s['by_reason']}"
    )
    print("\nWalk-forward windows:")
    positive = 0
    for w in windows:
        ws = w.summary()
        positive += ws["total_eur"] > 0
        print(
            f"  {ws['window']}: trades {ws['trades']:3d}  total €{ws['total_eur']:+8.2f}  "
            f"win {ws['win_rate']}  maxDD €{ws['max_drawdown_eur']:.2f}  "
            f"regime {ws['regime_on_points']}/{ws['decision_points']}"
        )
    print(f"  positive windows: {positive}/{len(windows)}")
    if args.trades:
        print("\nTrades:")
        for t in full.closed:
            row = t.as_row()
            print(
                f"  {t.base:5s} {row['opened']} -> {row['closed']}  gross {t.gross_return:+.2%}  "
                f"peak {t.peak_return:+.2%}  net €{t.net_eur:+.2f}  [{t.reason}]"
            )


if __name__ == "__main__":
    main()
