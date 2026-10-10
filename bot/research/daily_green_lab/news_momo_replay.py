"""Replay the chase-capped daily leader against the armed continuous pack.

Signal on the close, buy the next open. This is the daily analogue of the
live news/momentum band (do not enter a day that already closed above +12%).
"""

from __future__ import annotations

from pathlib import Path

from bot.research.daily_green_lab.engine import Spec, _alphai_daily, _load_dir, simulate


def _spec(pick: str, *, btc: bool, hard: float) -> Spec:
    return Spec(
        name=pick,
        pick=pick,
        trail_pct=0.10,
        time_max_days=3,
        hard_stop_pct=hard,
        require_btc_sma=btc,
        excess_floor=0.0,
        lookback=1,
        force_daily=not btc,
    )


def run(*, candle_dir: str = "data/ignition_expand_candles", book: float = 2_000.0) -> None:
    ohlc = _load_dir(Path(candle_dir))
    alphai = _alphai_daily(Path("data/research/alphai_sessions_merged.json"))
    windows = {
        "IS": ("2024-06-01", "2025-12-31"),
        "OOS": ("2026-01-01", "2026-10-10"),
        "full": ("2024-06-01", "2026-10-10"),
    }
    packs = (
        ("top_day btc0 hs3", "top_day", False, 0.03),
        ("top_day btc1 hs5", "top_day", True, 0.05),
        ("day_cap12 btc0 hs3", "day_cap12", False, 0.03),
        ("day_cap12 btc1 hs5", "day_cap12", True, 0.05),
    )
    for title, pick, btc, hard in packs:
        print(f"\n== {title} book €{book:.0f}")
        for label, (start, end) in windows.items():
            row = simulate(
                ohlc,
                _spec(pick, btc=btc, hard=hard),
                start=start,
                end=end,
                book=book,
                alphai_by_day=alphai,
            )
            if not row.get("ok"):
                print(label, row.get("reason"))
                continue
            print(
                f"{label:4} pnl={row['pnl_eur']:+.0f} "
                f"avg/day={row['avg_day_pnl']:+.2f} "
                f"green={row['pct_days_green']:.0%} "
                f"in={row['pct_days_in_market']:.0%} "
                f"dd={row['max_dd_pct']:.0%} "
                f"trades={row['n_trades']} "
                f"best={row['best_day']:+.0f} worst={row['worst_day']:+.0f}"
            )


if __name__ == "__main__":
    run()
