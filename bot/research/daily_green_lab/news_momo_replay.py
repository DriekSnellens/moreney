"""Replay the chase-capped daily leader against the armed continuous pack.

Signal on the close, buy the next open. This is the daily analogue of the
live news/momentum band (do not enter a day that already closed above +12%).
"""

from __future__ import annotations

from pathlib import Path

from bot.research.daily_green_lab.engine import Spec, _alphai_daily, _by_date, _load_dir, simulate


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


def oracle(
    *,
    candle_dir: str = "data/ignition_expand_candles",
    start: str = "2024-06-01",
    end: str = "2026-10-02",
    min_qvol: float = 50_000.0,
    max_bar: float = 3.0,
) -> None:
    """Hindsight ceiling: best liquid name, bought at that day's open.

    A causal rule cannot beat this. ``max_bar`` drops broken prints whose
    close or high is more than 3× the open.
    """
    ohlc = _load_dir(Path(candle_dir))
    by = {b: _by_date(rows) for b, rows in ohlc.items()}
    btc_days = sorted(by.get("BTC") or [])
    days = [d for d in btc_days if start <= d <= end]
    closes: list[float] = []
    highs: list[float] = []
    for day in days:
        best_c: tuple[float, str] | None = None
        best_h: tuple[float, str] | None = None
        for base, bars in by.items():
            if base == "BTC" or day not in bars:
                continue
            bar = bars[day]
            o, h, c, vol = bar[1], bar[2], bar[4], bar[5]
            if o <= 0 or h <= 0 or c <= 0:
                continue
            if c / o - 1.0 > max_bar or h / o - 1.0 > max_bar:
                continue
            if vol * c < min_qvol:
                continue
            rc, rh = c / o - 1.0, h / o - 1.0
            if best_c is None or rc > best_c[0]:
                best_c = (rc, base)
            if best_h is None or rh > best_h[0]:
                best_h = (rh, base)
        if best_c:
            closes.append(best_c[0])
        if best_h:
            highs.append(best_h[0])

    def _line(label: str, xs: list[float]) -> None:
        n = len(xs) or 1
        ordered = sorted(xs)
        avg = sum(xs) / n
        print(
            f"{label}: n={len(xs)} avg={avg * 100:.2f}% "
            f"med={ordered[len(xs) // 2] * 100:.2f}% "
            f">=30%={sum(x >= 0.30 for x in xs) / n:.1%} "
            f">=10%={sum(x >= 0.10 for x in xs) / n:.1%}"
        )

    print(f"oracle {start}->{end} qv>={min_qvol:.0f} cap={max_bar:.0%}")
    _line("close/open", closes)
    _line("high/open", highs)


if __name__ == "__main__":
    run()
    oracle()
