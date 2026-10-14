"""Crowd-follow baselines and second-order plays, all coin-agnostic.

"Crowd" strategies replicate what the retail / AI-generated bot stack ships by
default (RSI-14 30/70, EMA 9/21 cross, golden cross, grid, fixed -10% stop).
"Exploit" strategies try to earn from the footprints those bots and leveraged
perps traders leave: forced flushes, failed oversold bounces, stop runs below
round pullbacks, crowded funding, market-wide cascades, clock effects.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from bot.research.crowd_lab.engine import (
    SPOT_FEE,
    SPOT_SLIP,
    Bars,
    FundingSeries,
    Position,
    Strategy,
    hour_of,
)

MIN_DAILY_EUR_VOLUME = 200_000.0
STABLE_VOL_FLOOR = 0.005


def liquid(bars: Bars, base: str, i: int, *, n: int = 30) -> bool:
    """Median trailing EUR turnover >= 200k and not a stablecoin-like series."""
    c, v = bars.close[base], bars.volume[base]
    if i - n < 0:
        return False
    turn = [cc * vv for cc, vv in zip(c[i - n : i], v[i - n : i], strict=True) if cc and vv]
    if len(turn) < n * 0.8 or statistics.median(turn) < MIN_DAILY_EUR_VOLUME:
        return False
    px = [x for x in c[i - n : i] if x]
    return statistics.pstdev(px) / (sum(px) / len(px)) >= STABLE_VOL_FLOOR


def crossed_up(a: Sequence[float | None], b: Sequence[float | None], i: int) -> bool:
    return (
        i >= 1
        and None not in (a[i], b[i], a[i - 1], b[i - 1])
        and a[i - 1] <= b[i - 1]  # type: ignore[operator]
        and a[i] > b[i]  # type: ignore[operator]
    )


def crossed_down(a: Sequence[float | None], b: Sequence[float | None], i: int) -> bool:
    return crossed_up(b, a, i)


# --------------------------------------------------------------------------- crowd baselines


def crowd_rsi(bpd: int) -> Strategy:
    def entry(bars: Bars, base: str, i: int) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r < 30

    def exit_(bars: Bars, base: str, i: int, _p: Position) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r > 70

    return Strategy(
        f"crowd_rsi14_30_70_stop10_{bpd}bpd",
        entry,
        exit=exit_,
        stop_pct=0.10,
        note="buy RSI<30, sell RSI>70, fixed -10% stop (freqtrade-style default)",
    )


def crowd_ema_cross(bpd: int, fast: int = 9, slow: int = 21) -> Strategy:
    def entry(bars: Bars, base: str, i: int) -> bool:
        return crossed_up(bars.ema(base, fast), bars.ema(base, slow), i)

    def exit_(bars: Bars, base: str, i: int, _p: Position) -> bool:
        return crossed_down(bars.ema(base, fast), bars.ema(base, slow), i)

    return Strategy(
        f"crowd_ema{fast}_{slow}_stop10_{bpd}bpd",
        entry,
        exit=exit_,
        stop_pct=0.10,
        note="EMA fast/slow cross long, exit on reverse cross, -10% stop",
    )


def crowd_golden_cross() -> Strategy:
    def entry(bars: Bars, base: str, i: int) -> bool:
        return liquid(bars, base, i) and crossed_up(bars.sma(base, 50), bars.sma(base, 200), i)

    def exit_(bars: Bars, base: str, i: int, _p: Position) -> bool:
        return crossed_down(bars.sma(base, 50), bars.sma(base, 200), i)

    return Strategy(
        "crowd_golden_cross_1d",
        entry,
        exit=exit_,
        max_pos=6,
        note="SMA50 > SMA200 cross long, exit on death cross, liquid EUR markets",
    )


# --------------------------------------------------------------------------- exploits


def flush_fade(
    bpd: int,
    *,
    drop: float,
    vol_z: float,
    hold_bars: int,
    stop: float,
    trend_gate: bool,
    universe: Sequence[str] | None = None,
    tag: str = "",
) -> Strategy:
    """Buy a forced flush (big red bar on abnormal volume), time-boxed exit."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        r = bars.ret(base, i, 1)
        z = bars.vol_z(base, 30 * bpd)[i]
        if r is None or z is None or r > -drop or z < vol_z:
            return False
        if trend_gate:
            s = bars.sma("BTC", 50 * bpd)[i]
            b = bars.close["BTC"][i]
            if s is None or b is None or b < s:
                return False
        return True

    gate = "_btcgate" if trend_gate else ""
    return Strategy(
        f"flush_fade{tag}_d{int(drop * 100)}_z{vol_z:g}_h{hold_bars}{gate}_{bpd}bpd",
        entry,
        max_bars=hold_bars,
        stop_pct=stop,
        max_pos=4,
        universe=universe,
        note="long after bar <= -drop with volume z >= vol_z; exit after hold_bars or stop",
    )


def rsi_bounce_fade(bpd: int, *, hold_bars: int = 6, stop: float = 0.03) -> Strategy:
    """Short the first green bar after an RSI<30 print (the bot bid) if the trend is down."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        rsi = bars.rsi(base, 14)
        c = bars.close[base]
        s = bars.sma(base, 50 * bpd)[i]
        if i < 2 or None in (rsi[i - 1], c[i], c[i - 1], s):
            return False
        return rsi[i - 1] < 30 and c[i] > c[i - 1] and c[i] < s  # type: ignore[operator]

    return Strategy(
        f"rsi_bounce_fade_perp_h{hold_bars}_{bpd}bpd",
        entry,
        side=-1,
        venue="perp",
        max_bars=hold_bars,
        stop_pct=stop,
        tp_pct=stop,
        max_pos=4,
        note="short the green bar after RSI<30 below SMA50; symmetric stop/target, time stop",
    )


def rsi_oversold_hold(bpd: int, *, hold_bars: int, venue: str = "spot") -> Strategy:
    """The crowd's entry (RSI<30) with a plain time exit instead of RSI>70 / -10% stop."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r < 30

    return Strategy(
        f"rsi14_lt30_hold{hold_bars}_{venue}_{bpd}bpd",
        entry,
        venue=venue,
        max_bars=hold_bars,
        max_pos=4,
        cooldown=hold_bars,
        note="long RSI<30, exit after hold_bars; isolates the oversold-bounce entry",
    )


def ma_cross_trade(
    *, fast: int, slow: int, side: int, hold_days: int, stop: float, venue: str
) -> Strategy:
    """Trade the SMA fast/slow cross in the given direction with a time stop."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        f, s = bars.sma(base, fast), bars.sma(base, slow)
        return crossed_up(f, s, i) if side > 0 else crossed_up(s, f, i)

    label = "golden" if side > 0 else "death"
    return Strategy(
        f"{label}_cross_{'long' if side > 0 else 'short'}_{venue}_h{hold_days}_1d",
        entry,
        side=side,
        venue=venue,
        max_bars=hold_days,
        stop_pct=stop,
        max_pos=4,
        note=f"{label} cross SMA{fast}/{slow}, hold {hold_days} days or stop",
    )


def rsi_reclaim(bpd: int) -> Strategy:
    """Long only once RSI reclaims 30 and price takes out the prior bar high."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        rsi = bars.rsi(base, 14)
        c, h = bars.close[base], bars.high[base]
        if i < 2 or None in (rsi[i], rsi[i - 1], c[i], h[i - 1]):
            return False
        return rsi[i - 1] < 30 <= rsi[i] and c[i] > h[i - 1]  # type: ignore[operator]

    def exit_(bars: Bars, base: str, i: int, _p: Position) -> bool:
        r = bars.rsi(base, 14)[i]
        return r is not None and r > 70

    return Strategy(
        f"rsi_reclaim_30_spot_{bpd}bpd",
        entry,
        exit=exit_,
        stop_pct=0.05,
        max_bars=10 * bpd,
        note="second-order RSI: enter after the crowd's oversold buy is confirmed",
    )


def stop_run_buy(*, band_lo: float = 0.10, band_hi: float = 0.16, hold_days: int = 10) -> Strategy:
    """Buy the day after price breaks -10% from the 20-day high while above SMA50.

    The fixed -10% stop is the most common retail/bot default; the break forces
    their exits while the trend filter says the move is still intact.
    """

    def entry(bars: Bars, base: str, i: int) -> bool:
        if not liquid(bars, base, i):
            return False
        c = bars.close[base]
        s = bars.sma(base, 50)[i]
        hi = bars.roll_max(base, 20)
        if i < 1 or None in (c[i], c[i - 1], s, hi[i], hi[i - 1]):
            return False
        off = c[i] / hi[i] - 1.0  # type: ignore[operator]
        prev_off = c[i - 1] / hi[i - 1] - 1.0  # type: ignore[operator]
        return c[i] > s and -band_hi <= off <= -band_lo and prev_off > -band_lo  # type: ignore[operator]

    def exit_(bars: Bars, base: str, i: int, p: Position) -> bool:
        c = bars.close[base][i]
        hi = bars.roll_max(base, 20)[i]
        return c is not None and hi is not None and c >= hi * 0.999

    return Strategy(
        f"stop_run_buy_{int(band_lo * 100)}_{int(band_hi * 100)}_1d",
        entry,
        exit=exit_,
        stop_pct=0.08,
        max_bars=hold_days,
        max_pos=4,
        note="long after a fresh -10% break from the 20d high above SMA50; exit at new high",
    )


def pump_fade(*, jump: float = 0.15, vol_z: float = 3.0, hold_days: int = 3) -> Strategy:
    def entry(bars: Bars, base: str, i: int) -> bool:
        r = bars.ret(base, i, 1)
        z = bars.vol_z(base, 30)[i]
        return r is not None and z is not None and r >= jump and z >= vol_z

    return Strategy(
        f"pump_fade_perp_j{int(jump * 100)}_z{vol_z:g}_h{hold_days}_1d",
        entry,
        side=-1,
        venue="perp",
        max_bars=hold_days,
        stop_pct=0.10,
        max_pos=4,
        note="short the day after a +jump% / volume-spike bar (bot pile-in), 3-day time stop",
    )


# --------------------------------------------------------------------------- funding crowd


@dataclass
class FundingPercentile:
    """Rolling percentile rank of the latest 8h settlement within the trailing window."""

    pct: dict[str, dict[int, float]]

    @classmethod
    def build(cls, history: FundingSeries, *, window: int = 540) -> FundingPercentile:
        out: dict[str, dict[int, float]] = {}
        for base, rows in history.items():
            rates = [float(r) for _, r in rows]
            stamps = [int(t) - int(t) % 1000 for t, _ in rows]
            d: dict[int, float] = {}
            for k in range(window, len(rates)):
                win = rates[k - window : k]
                x = rates[k]
                below = sum(1 for w in win if w < x)
                eq = sum(1 for w in win if w == x)
                d[stamps[k]] = (below + 0.5 * eq) / window
            out[base] = d
        return cls(out)

    def at(self, base: str, ts: int) -> float | None:
        d = self.pct.get(base)
        if not d:
            return None
        settle = ts - ts % (8 * 3_600_000)
        return d.get(settle)


def funding_extreme(
    fp: FundingPercentile, *, side: int, tail: float, hold_bars: int, bpd: int
) -> Strategy:
    def entry(bars: Bars, base: str, i: int) -> bool:
        p = fp.at(base, bars.ts[i])
        if p is None:
            return False
        return p >= 1 - tail if side < 0 else p <= tail

    label = "top" if side < 0 else "bottom"
    return Strategy(
        f"funding_{label}{int(tail * 100)}_{'short' if side < 0 else 'long'}_h{hold_bars}_{bpd}bpd",
        entry,
        side=side,
        venue="perp",
        max_bars=hold_bars,
        stop_pct=0.05,
        max_pos=3,
        cooldown=hold_bars,
        universe=sorted(fp.pct),
        note="perp position against (short) or with (long) the crowded funding tail",
    )


# --------------------------------------------------------------------------- cascades / clocks


def cascade_hardest_hit(
    *, median_drop: float = 0.06, top_n: int = 3, hold_days: int = 2
) -> Strategy:
    """Market-wide flush: buy the hardest-hit liquid names, hold two days."""

    picks: dict[int, set[str]] = {}

    def compute(bars: Bars, i: int) -> set[str]:
        if i in picks:
            return picks[i]
        rets = {
            b: r
            for b in bars.bases
            if b != "BTC" and liquid(bars, b, i) and (r := bars.ret(b, i, 1)) is not None
        }
        chosen: set[str] = set()
        if len(rets) >= 10 and statistics.median(rets.values()) <= -median_drop:
            chosen = {b for b, _ in sorted(rets.items(), key=lambda kv: kv[1])[:top_n]}
        picks[i] = chosen
        return chosen

    def entry(bars: Bars, base: str, i: int) -> bool:
        return base in compute(bars, i)

    return Strategy(
        f"cascade_hardest{top_n}_md{int(median_drop * 100)}_h{hold_days}_1d",
        entry,
        max_bars=hold_days,
        max_pos=top_n,
        note="after a median-alt <= -drop day, long the hardest-hit names for hold_days",
    )


def cascade_btc(*, median_drop: float = 0.06, hold_days: int = 2) -> Strategy:
    """Beta control for the cascade trade: BTC on the same signal days."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        rets = [
            r
            for b in bars.bases
            if b != "BTC" and liquid(bars, b, i) and (r := bars.ret(b, i, 1)) is not None
        ]
        return len(rets) >= 10 and statistics.median(rets) <= -median_drop

    return Strategy(
        f"cascade_btc_md{int(median_drop * 100)}_h{hold_days}_1d",
        entry,
        max_bars=hold_days,
        max_pos=1,
        universe=["BTC"],
        note="beta control: BTC long on the same cascade days",
    )


def utc_slot(hour: int, *, side: int, universe: Sequence[str]) -> Strategy:
    """Hold one 4h bar starting at ``hour`` UTC (bots' daily-close / funding clock)."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        return i + 1 < len(bars) and hour_of(bars.ts[i + 1]) == hour

    return Strategy(
        f"utc{hour:02d}_{'long' if side > 0 else 'short'}_1bar_6bpd",
        entry,
        side=side,
        venue="spot" if side > 0 else "perp",
        max_bars=1,
        max_pos=len(universe),
        universe=list(universe),
        note="one-bar hold of the slot where daily-close bots and funding settle",
    )


def weekday_hold(weekday: int, *, universe: Sequence[str]) -> Strategy:
    """Buy at the open of ``weekday`` (0=Monday), sell at its close."""

    def entry(bars: Bars, base: str, i: int) -> bool:
        if i + 1 >= len(bars):
            return False
        return datetime.fromtimestamp(bars.ts[i + 1] / 1000, UTC).weekday() == weekday

    return Strategy(
        f"weekday{weekday}_long_1d",
        entry,
        max_bars=1,
        max_pos=len(universe),
        universe=list(universe),
        note="calendar herding: hold only that weekday",
    )


# --------------------------------------------------------------------------- grid bot


@dataclass
class GridResult:
    name: str
    pnl_eur: float
    max_dd_eur: float
    fills: int
    resets: int
    fees_eur: float
    by_year: dict[str, float]


def grid_bot(
    bars: Bars,
    base: str,
    *,
    start: str,
    end: str,
    levels: int = 10,
    step: float = 0.01,
    level_eur: float = 1_000.0,
) -> GridResult:
    """Symmetric spot grid: buy each level below the anchor, sell one level up.

    Anchor resets (inventory dumped at market) when price leaves the grid range,
    the behaviour of every retail grid preset when the range breaks.
    """
    dates = bars.dates
    c = bars.close[base]
    pnl = 0.0
    fees = 0.0
    peak = dd = 0.0
    fills = resets = 0
    by_year: dict[str, float] = {}
    anchor: float | None = None
    inv: dict[int, float] = {}  # level index -> qty held
    cost = SPOT_FEE + SPOT_SLIP
    for i, d in enumerate(dates):
        if d < start or d > end or c[i] is None:
            continue
        px = c[i]
        if anchor is None:
            anchor = px
            continue
        lo_edge = anchor * (1 - levels * step)
        hi_edge = anchor * (1 + levels * step)
        if px < lo_edge or px > hi_edge:
            for qty in inv.values():
                proceeds = qty * px * (1 - cost)
                pnl += proceeds - level_eur
                fees += qty * px * cost
                fills += 1
            inv.clear()
            anchor = px
            resets += 1
        else:
            k = int((px / anchor - 1.0) / step)  # signed level index of the current price
            for lv in range(k, 0):  # fill every buy level at/above the current price
                if lv not in inv:
                    buy_px = anchor * (1 + lv * step)
                    inv[lv] = level_eur / (buy_px * (1 + cost))
                    fees += level_eur * cost / (1 + cost)
                    fills += 1
            for lv in [lv for lv in inv if k >= lv + 1]:
                sell_px = anchor * (1 + (lv + 1) * step)
                qty = inv.pop(lv)
                proceeds = qty * sell_px * (1 - cost)
                pnl += proceeds - level_eur
                fees += qty * sell_px * cost
                fills += 1
        mark = pnl + sum(qty * px - level_eur for qty in inv.values())
        by_year[d[:4]] = mark
        peak = max(peak, mark)
        dd = max(dd, peak - mark)
    if anchor is not None and inv and c[i] is not None:
        px = c[i]
        for qty in inv.values():
            pnl += qty * px * (1 - cost) - level_eur
            fees += qty * px * cost
        inv.clear()
    # by_year holds the marked equity at each year end; convert to per-year deltas
    ys = sorted(by_year)
    deltas: dict[str, float] = {}
    prev = 0.0
    for y in ys:
        deltas[y] = by_year[y] - prev
        prev = by_year[y]
    return GridResult(
        f"crowd_grid_{base}_{levels}x{step * 100:g}pct", pnl, dd, fills, resets, fees, deltas
    )


__all__ = [
    "SPOT_SLIP",
    "FundingPercentile",
    "GridResult",
    "cascade_btc",
    "cascade_hardest_hit",
    "crowd_ema_cross",
    "crowd_golden_cross",
    "crowd_rsi",
    "flush_fade",
    "funding_extreme",
    "grid_bot",
    "liquid",
    "ma_cross_trade",
    "pump_fade",
    "rsi_bounce_fade",
    "rsi_oversold_hold",
    "rsi_reclaim",
    "stop_run_buy",
    "utc_slot",
    "weekday_hold",
]
