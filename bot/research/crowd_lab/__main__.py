"""Crowd lab: trade against the growing stack of look-alike (AI-built) bots?

    .venv/bin/python -m bot.research.crowd_lab

Replays (a) the default strategies those bots ship with and (b) second-order plays
that try to earn from their footprints, on Bitvavo 4h and 1d bars with fees,
slippage and Binance funding. EUR 20k book, fixed notional per position, no
compounding. Writes artifacts/crowd_lab.json; see RESULTS.md for the reading.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.crowd_lab.engine import (
    Bars,
    EventStats,
    Funding,
    Result,
    event_study,
    hour_of,
    run,
)
from bot.research.crowd_lab.strategies import (
    FundingPercentile,
    cascade_btc,
    cascade_hardest_hit,
    crossed_up,
    crowd_ema_cross,
    crowd_golden_cross,
    crowd_rsi,
    flush_fade,
    funding_extreme,
    grid_bot,
    liquid,
    ma_cross_trade,
    pump_fade,
    rsi_bounce_fade,
    rsi_oversold_hold,
    rsi_reclaim,
    stop_run_buy,
    utc_slot,
    weekday_hold,
)
from bot.research.factor_scoreboard.external import funding_history
from bot.research.unusual_volume_lab.data import CACHE_1D, CACHE_4H, load_cached

DESK = ("BTC", *DEFAULT_UNIVERSE)
YEARS = ("2024", "2025", "2026")


def _line(r: Result) -> str:
    ys = " ".join(f"{r.by_year.get(y, 0.0):8.0f}" for y in YEARS)
    return (
        f"{r.name:44s} {ys} | {r.pnl_eur:8.0f} dd {r.max_dd_pct * 100:5.1f}% "
        f"n {r.n_trades:4d} wr {r.win_rate * 100:4.0f}% fee {r.fees_eur:6.0f} "
        f"fund {r.funding_eur:6.0f} | IS {r.by_window['IS']:8.0f} OOS {r.by_window['OOS']:7.0f}"
    )


def _ev_line(e: EventStats) -> str:
    cells = " ".join(
        f"h{h}: {s['mean'] * 100:+.2f}%/{s['median'] * 100:+.2f}% "
        f"hit {s['hit'] * 100:3.0f}% t {s['t']:+.1f}"
        for h, s in e.horizons.items()
    )
    return f"{e.name:34s} n {e.n:5d} | {cells}"


def main() -> None:
    p = argparse.ArgumentParser(description="Crowd lab")
    p.add_argument("--start-4h", default="2024-07-01")
    p.add_argument("--start-1d", default="2024-03-16")
    p.add_argument("--end", default=None)
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--out", default="artifacts/crowd_lab.json")
    args = p.parse_args()

    bars4 = Bars.build(load_cached(CACHE_4H, DESK))
    all_1d = load_cached(CACHE_1D, [q.stem for q in CACHE_1D.glob("*.json")])
    bars1 = Bars.build(all_1d)
    end4 = args.end or bars4.dates[-2]
    end1 = args.end or bars1.dates[-1]
    fund_hist = funding_history(["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE"])
    funding = Funding(fund_hist)
    fp = FundingPercentile.build(fund_hist)
    print(f"4h bars {bars4.dates[0]}..{bars4.dates[-1]} ({len(bars4)}), {args.start_4h}..{end4}")
    print(f"1d bars {bars1.dates[0]}..{bars1.dates[-1]} ({len(bars1)}, {len(bars1.bases)} markets)")

    out: dict[str, Any] = {"book_eur": args.book, "strategies": [], "events": [], "grid": []}

    def go(bars: Bars, strat, start: str, end: str) -> Result:
        r = run(bars, strat, start=start, end=end, book_eur=args.book, funding=funding)
        print(_line(r))
        out["strategies"].append({**r.row(), "note": strat.note})
        return r

    print("\n== what the crowd's default bots earn (desk universe unless noted) ==")
    for s in (crowd_rsi(6), crowd_ema_cross(6), crowd_ema_cross(6, 20, 50)):
        s.universe = list(DESK)
        go(bars4, s, args.start_4h, end4)
    for s in (crowd_rsi(1), crowd_ema_cross(1, 20, 50)):
        s.universe = list(DESK)
        go(bars1, s, args.start_1d, end1)
    go(bars1, crowd_golden_cross(), args.start_1d, end1)
    for base in ("BTC", "ETH", "SOL"):
        g = grid_bot(bars4, base, start=args.start_4h, end=end4)
        ys = " ".join(f"{g.by_year.get(y, 0.0):8.0f}" for y in YEARS)
        print(
            f"{g.name:44s} {ys} | {g.pnl_eur:8.0f} dd {g.max_dd_eur / args.book * 100:5.1f}% "
            f"fills {g.fills:4d} resets {g.resets:3d} fee {g.fees_eur:6.0f}"
        )
        out["grid"].append(g.__dict__)

    print("\n== second-order: forced flushes (4h) ==")
    desk_alts = list(DEFAULT_UNIVERSE)
    for drop, hold in ((0.04, 6), (0.04, 18), (0.06, 6), (0.06, 18)):
        for gate in (False, True):
            go(
                bars4,
                flush_fade(
                    6,
                    drop=drop,
                    vol_z=2.0,
                    hold_bars=hold,
                    stop=0.06,
                    trend_gate=gate,
                    universe=desk_alts,
                ),
                args.start_4h,
                end4,
            )
    go(
        bars4,
        flush_fade(
            6,
            drop=0.03,
            vol_z=2.0,
            hold_bars=6,
            stop=0.04,
            trend_gate=False,
            universe=["BTC"],
            tag="_btc",
        ),
        args.start_4h,
        end4,
    )
    go(
        bars4,
        flush_fade(
            6,
            drop=0.03,
            vol_z=2.0,
            hold_bars=18,
            stop=0.04,
            trend_gate=False,
            universe=["BTC"],
            tag="_btc",
        ),
        args.start_4h,
        end4,
    )

    print("\n== flush fade plateau (alts, 4h): drop x hold x volume-z around d6/h18 ==")
    for drop in (0.05, 0.06, 0.07, 0.08):
        for hold in (12, 18, 24, 36):
            go(
                bars4,
                flush_fade(
                    6,
                    drop=drop,
                    vol_z=2.0,
                    hold_bars=hold,
                    stop=0.06,
                    trend_gate=False,
                    universe=desk_alts,
                ),
                args.start_4h,
                end4,
            )
    for z in (1.0, 1.5, 3.0):
        go(
            bars4,
            flush_fade(
                6, drop=0.06, vol_z=z, hold_bars=18, stop=0.06, trend_gate=False, universe=desk_alts
            ),
            args.start_4h,
            end4,
        )
    for stop in (0.04, 0.10):
        go(
            bars4,
            flush_fade(
                6,
                drop=0.06,
                vol_z=2.0,
                hold_bars=18,
                stop=stop,
                trend_gate=False,
                universe=desk_alts,
                tag=f"_s{int(stop * 100)}",
            ),
            args.start_4h,
            end4,
        )

    print("\n== second-order: the RSI crowd ==")
    for s in (
        rsi_bounce_fade(6),
        rsi_bounce_fade(6, hold_bars=18, stop=0.05),
        rsi_reclaim(6),
        rsi_oversold_hold(6, hold_bars=18),
        rsi_oversold_hold(6, hold_bars=18, venue="perp"),
        rsi_oversold_hold(6, hold_bars=42, venue="perp"),
    ):
        s.universe = list(DESK)
        go(bars4, s, args.start_4h, end4)
    s = rsi_reclaim(1)
    s.universe = list(DESK)
    go(bars1, s, args.start_1d, end1)

    print("\n== second-order: MA-cross crowd on the desk (perp both ways) ==")
    for s in (
        ma_cross_trade(fast=50, slow=200, side=1, hold_days=20, stop=0.15, venue="spot"),
        ma_cross_trade(fast=50, slow=200, side=-1, hold_days=20, stop=0.15, venue="perp"),
        ma_cross_trade(fast=20, slow=50, side=-1, hold_days=10, stop=0.10, venue="perp"),
    ):
        s.universe = list(DESK)
        go(bars1, s, args.start_1d, end1)

    print("\n== second-order: stop runs, pumps, cascades (1d, liquid EUR markets) ==")
    go(bars1, stop_run_buy(), args.start_1d, end1)
    go(bars1, stop_run_buy(band_lo=0.08, band_hi=0.12, hold_days=5), args.start_1d, end1)
    s = pump_fade()
    s.universe = list(DEFAULT_UNIVERSE)
    go(bars1, s, args.start_1d, end1)
    s = pump_fade(jump=0.10, vol_z=2.0, hold_days=2)
    s.universe = list(DEFAULT_UNIVERSE)
    go(bars1, s, args.start_1d, end1)
    for md in (0.05, 0.08):
        go(bars1, cascade_hardest_hit(median_drop=md), args.start_1d, end1)
        go(bars1, cascade_btc(median_drop=md), args.start_1d, end1)

    print("\n== second-order: crowded funding (6 perps with Binance history) ==")
    for side in (-1, 1):
        for hold in (6, 18):
            go(
                bars4,
                funding_extreme(fp, side=side, tail=0.10, hold_bars=hold, bpd=6),
                args.start_4h,
                end4,
            )
    s = funding_extreme(fp, side=1, tail=0.10, hold_bars=18, bpd=6)
    s.name = "funding_top10_long_h18_6bpd"
    s.entry = lambda bars, base, i: (q := fp.at(base, bars.ts[i])) is not None and q >= 0.9
    go(bars4, s, args.start_4h, end4)

    print("\n== clocks: one-bar holds are fee-dominated; see the gross slot table below ==")
    go(bars4, utc_slot(0, side=1, universe=DESK), args.start_4h, end4)
    go(bars1, weekday_hold(0, universe=DESK), args.start_1d, end1)

    print("\n== event studies (next-open entry, close-to-close forward return) ==")

    def rsi_lt30(bars: Bars, base: str, i: int) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r < 30

    def rsi_gt70(bars: Bars, base: str, i: int) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r > 70

    def golden(bars: Bars, base: str, i: int) -> bool:
        return liquid(bars, base, i) and crossed_up(bars.sma(base, 50), bars.sma(base, 200), i)

    def death(bars: Bars, base: str, i: int) -> bool:
        return liquid(bars, base, i) and crossed_up(bars.sma(base, 200), bars.sma(base, 50), i)

    def any_bar(_bars: Bars, _base: str, _i: int) -> bool:
        return True

    def ftop(bars: Bars, base: str, i: int) -> bool:
        q = fp.at(base, bars.ts[i])
        return q is not None and q >= 0.9

    def fbot(bars: Bars, base: str, i: int) -> bool:
        q = fp.at(base, bars.ts[i])
        return q is not None and q <= 0.1

    evs = [
        event_study(
            bars4,
            "4h_any_bar",
            any_bar,
            horizons=(6, 18, 42),
            start=args.start_4h,
            end=end4,
            universe=DESK,
        ),
        event_study(
            bars4,
            "4h_rsi14<30",
            rsi_lt30,
            horizons=(6, 18, 42),
            start=args.start_4h,
            end=end4,
            universe=DESK,
            min_gap=6,
        ),
        event_study(
            bars4,
            "4h_rsi14>70",
            rsi_gt70,
            horizons=(6, 18, 42),
            start=args.start_4h,
            end=end4,
            universe=DESK,
            min_gap=6,
        ),
        event_study(
            bars4,
            "4h_funding_top10",
            ftop,
            horizons=(6, 18),
            start=args.start_4h,
            end=end4,
            universe=sorted(fp.pct),
            min_gap=6,
        ),
        event_study(
            bars4,
            "4h_funding_bottom10",
            fbot,
            horizons=(6, 18),
            start=args.start_4h,
            end=end4,
            universe=sorted(fp.pct),
            min_gap=6,
        ),
        event_study(
            bars1, "1d_any_day_liquid", liquid, horizons=(5, 10, 20), start=args.start_1d, end=end1
        ),
        event_study(
            bars1, "1d_golden_cross", golden, horizons=(5, 10, 20), start=args.start_1d, end=end1
        ),
        event_study(
            bars1, "1d_death_cross", death, horizons=(5, 10, 20), start=args.start_1d, end=end1
        ),
    ]
    for e in evs:
        print(_ev_line(e))
        out["events"].append(e.row())

    print("\n== gross mean return per 4h slot (desk) and per weekday (desk) ==")

    def slot(h: int):
        return lambda bars, _b, i: i + 1 < len(bars) and hour_of(bars.ts[i + 1]) == h

    def wday(w: int):
        return lambda bars, _b, i: (
            i + 1 < len(bars) and datetime.fromtimestamp(bars.ts[i + 1] / 1000, UTC).weekday() == w
        )

    for h in range(0, 24, 4):
        e = event_study(
            bars4,
            f"slot_{h:02d}utc",
            slot(h),
            horizons=(1,),
            start=args.start_4h,
            end=end4,
            universe=DESK,
        )
        print(_ev_line(e))
        out["events"].append(e.row())
    for w in range(7):
        e = event_study(
            bars1,
            f"weekday_{w}",
            wday(w),
            horizons=(1,),
            start=args.start_1d,
            end=end1,
            universe=DESK,
        )
        print(_ev_line(e))
        out["events"].append(e.row())

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
