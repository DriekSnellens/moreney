"""Bar frames, indicators and a small long/short trade simulator for the crowd lab.

Everything here is coin-agnostic: strategies are expressed as entry/exit callables
over an aligned bar frame plus generic risk knobs (stop, target, time stop, max
concurrent positions). Costs: Bitvavo spot taker 0.25% + 0.10% slippage per side;
OKX-style perp 0.05% + 0.05% per side plus Binance funding on the open notional.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SPOT_FEE = 0.0025
SPOT_SLIP = 0.0010
PERP_FEE = 0.0005
PERP_SLIP = 0.0005
H8_MS = 8 * 3_600_000
DAY_MS = 86_400_000

Ohlc = Mapping[str, Sequence[Sequence[float]]]
FundingSeries = Mapping[str, Sequence[tuple[int, float]]]


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def hour_of(ms: int) -> int:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).hour


@dataclass
class Bars:
    """OHLCV per base aligned on the BTC bar calendar (``None`` where missing)."""

    ts: list[int]
    open: dict[str, list[float | None]]
    high: dict[str, list[float | None]]
    low: dict[str, list[float | None]]
    close: dict[str, list[float | None]]
    volume: dict[str, list[float | None]]
    _cache: dict[tuple[Any, ...], list[float | None]] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, ohlc: Ohlc, *, anchor: str = "BTC") -> Bars:
        ts = [int(r[0]) for r in ohlc[anchor]]
        pos = {t: i for i, t in enumerate(ts)}
        n = len(ts)
        cols: dict[str, dict[str, list[float | None]]] = {
            k: {} for k in ("open", "high", "low", "close", "volume")
        }
        for base, rows in ohlc.items():
            arrs = {k: [None] * n for k in cols}
            for r in rows:
                i = pos.get(int(r[0]))
                if i is None:
                    continue
                arrs["open"][i] = float(r[1])
                arrs["high"][i] = float(r[2])
                arrs["low"][i] = float(r[3])
                arrs["close"][i] = float(r[4])
                arrs["volume"][i] = float(r[5])
            for k in cols:
                cols[k][base] = arrs[k]
        return cls(ts, cols["open"], cols["high"], cols["low"], cols["close"], cols["volume"])

    @property
    def dates(self) -> list[str]:
        return [day_of(t) for t in self.ts]

    @property
    def bases(self) -> list[str]:
        return sorted(self.close)

    def __len__(self) -> int:
        return len(self.ts)

    def ret(self, base: str, i: int, n: int) -> float | None:
        c = self.close[base]
        if i - n < 0:
            return None
        a, b = c[i - n], c[i]
        return b / a - 1.0 if a and b else None

    def sma(self, base: str, n: int) -> list[float | None]:
        key = ("sma", base, n)
        if key not in self._cache:
            c = self.close[base]
            out: list[float | None] = [None] * len(c)
            acc = 0.0
            cnt = 0
            for i, x in enumerate(c):
                if x is not None:
                    acc += x
                    cnt += 1
                j = i - n
                if j >= 0 and c[j] is not None:
                    acc -= c[j]
                    cnt -= 1
                if cnt == n:
                    out[i] = acc / n
            self._cache[key] = out
        return self._cache[key]

    def ema(self, base: str, n: int) -> list[float | None]:
        key = ("ema", base, n)
        if key not in self._cache:
            c = self.close[base]
            out: list[float | None] = [None] * len(c)
            k = 2.0 / (n + 1)
            prev: float | None = None
            seen = 0
            for i, x in enumerate(c):
                if x is None:
                    continue
                seen += 1
                prev = x if prev is None else x * k + prev * (1 - k)
                if seen >= n:
                    out[i] = prev
            self._cache[key] = out
        return self._cache[key]

    def rsi(self, base: str, n: int = 14) -> list[float | None]:
        """Wilder RSI, the textbook default every retail bot ships with."""
        key = ("rsi", base, n)
        if key not in self._cache:
            c = self.close[base]
            out: list[float | None] = [None] * len(c)
            up = dn = None
            last: float | None = None
            seen = 0
            for i, x in enumerate(c):
                if x is None:
                    continue
                if last is not None:
                    d = x - last
                    g, lo = max(d, 0.0), max(-d, 0.0)
                    if up is None:
                        up, dn = g, lo
                    else:
                        up = (up * (n - 1) + g) / n
                        dn = (dn * (n - 1) + lo) / n
                    seen += 1
                    if seen >= n:
                        out[i] = 100.0 if dn == 0 else 100.0 - 100.0 / (1.0 + up / dn)
                last = x
            self._cache[key] = out
        return self._cache[key]

    def roll_max(self, base: str, n: int, *, field_: str = "high") -> list[float | None]:
        key = ("rmax", base, n, field_)
        if key not in self._cache:
            src = getattr(self, field_)[base]
            out: list[float | None] = [None] * len(src)
            for i in range(len(src)):
                win = [x for x in src[max(0, i - n + 1) : i + 1] if x is not None]
                out[i] = max(win) if len(win) == n else None
            self._cache[key] = out
        return self._cache[key]

    def vol_z(self, base: str, n: int = 30) -> list[float | None]:
        """Volume z-score versus the trailing ``n`` bars (excluding the current one)."""
        key = ("volz", base, n)
        if key not in self._cache:
            v = self.volume[base]
            out: list[float | None] = [None] * len(v)
            for i in range(n, len(v)):
                win = [x for x in v[i - n : i] if x is not None]
                if len(win) < n or v[i] is None:
                    continue
                mu = sum(win) / n
                sd = statistics.pstdev(win)
                out[i] = (v[i] - mu) / sd if sd > 0 else None
            self._cache[key] = out
        return self._cache[key]

    def bars_per_day(self) -> int:
        if len(self.ts) < 2:
            return 1
        step = self.ts[1] - self.ts[0]
        return max(1, int(round(DAY_MS / step)))


# --------------------------------------------------------------------------- funding


class Funding:
    """8h Binance settlement rates; bases without history use the cross-sectional mean."""

    def __init__(self, history: FundingSeries | None):
        self.by_base: dict[str, dict[int, float]] = {}
        for base, rows in (history or {}).items():
            self.by_base[base] = {int(ts) - int(ts) % 1000: float(r) for ts, r in rows}
        self.mean: dict[int, float] = {}
        acc: dict[int, list[float]] = {}
        for rows in self.by_base.values():
            for ts, r in rows.items():
                acc.setdefault(ts, []).append(r)
        self.mean = {ts: sum(v) / len(v) for ts, v in acc.items()}

    def rate(self, base: str, settle_ms: int) -> float:
        rows = self.by_base.get(base)
        if rows and settle_ms in rows:
            return rows[settle_ms]
        return self.mean.get(settle_ms, 0.0)

    def between(self, base: str, t0: int, t1: int) -> float:
        """Sum of settlement rates with ``t0 < settle <= t1``."""
        first = (t0 // H8_MS + 1) * H8_MS
        total = 0.0
        t = first
        while t <= t1:
            total += self.rate(base, t)
            t += H8_MS
        return total


# --------------------------------------------------------------------------- simulator

EntryFn = Callable[[Bars, str, int], bool]
ExitFn = Callable[[Bars, str, int, "Position"], bool]


@dataclass
class Position:
    base: str
    side: int
    i_entry: int
    px_entry: float
    notional: float
    i_exit: int | None = None
    px_exit: float | None = None
    reason: str = ""
    fees: float = 0.0
    funding: float = 0.0

    @property
    def qty(self) -> float:
        return self.notional / self.px_entry

    def mark(self, px: float) -> float:
        return self.side * self.qty * (px - self.px_entry)

    @property
    def pnl(self) -> float:
        assert self.px_exit is not None
        return self.mark(self.px_exit) - self.fees - self.funding


@dataclass
class Strategy:
    name: str
    entry: EntryFn
    side: int = 1
    venue: str = "spot"
    exit: ExitFn | None = None
    stop_pct: float | None = None
    tp_pct: float | None = None
    max_bars: int | None = None
    max_pos: int = 4
    cooldown: int = 0
    universe: Sequence[str] | None = None
    note: str = ""


@dataclass
class Result:
    name: str
    pnl_eur: float
    max_dd_eur: float
    max_dd_pct: float
    n_trades: int
    win_rate: float
    fees_eur: float
    funding_eur: float
    by_year: dict[str, float]
    by_window: dict[str, float]
    positions: list[Position] = field(default_factory=list, repr=False)
    curve: list[float] = field(default_factory=list, repr=False)

    def row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pnl_eur": round(self.pnl_eur, 2),
            "max_dd_eur": round(self.max_dd_eur, 2),
            "max_dd_pct": round(self.max_dd_pct, 4),
            "n_trades": self.n_trades,
            "win_rate": round(self.win_rate, 4),
            "fees_eur": round(self.fees_eur, 2),
            "funding_eur": round(self.funding_eur, 2),
            "by_year": {k: round(v, 2) for k, v in self.by_year.items()},
            "by_window": {k: round(v, 2) for k, v in self.by_window.items()},
        }


def run(
    bars: Bars,
    strat: Strategy,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    funding: Funding | None = None,
    is_end: str = "2025-12-31",
) -> Result:
    """Signal on bar ``i`` close, fill at bar ``i+1`` open; stops/targets intrabar.

    Fixed notional ``book / max_pos`` per position (no compounding) so that every
    strategy is compared on the same EUR 20k of risk capital.
    """
    if strat.side < 0 and strat.venue == "spot":
        raise ValueError("spot cannot short")
    universe = list(strat.universe or bars.bases)
    dates = bars.dates
    n = len(bars)
    i0 = next((i for i, d in enumerate(dates) if d >= start), n)
    i1 = max(i for i, d in enumerate(dates) if d <= end) if any(d <= end for d in dates) else -1
    per_pos = book_eur / strat.max_pos
    open_pos: dict[str, Position] = {}
    closed: list[Position] = []
    last_exit: dict[str, int] = {}
    pending: list[str] = []
    curve: list[float] = []
    realized = 0.0

    def close_pos(p: Position, i: int, px: float, reason: str) -> None:
        nonlocal realized
        px_eff = px * (1 - strat.side * SPOT_SLIP) if strat.venue == "spot" else px
        p.fees += p.notional * (PERP_FEE if strat.venue == "perp" else SPOT_FEE)
        if strat.venue == "perp":
            px_eff = px * (1 - p.side * PERP_SLIP)
            p.funding += (
                p.side * p.notional * funding.between(p.base, bars.ts[p.i_entry], bars.ts[i])
                if funding
                else 0.0
            )
        p.i_exit, p.px_exit, p.reason = i, px_eff, reason
        realized += p.pnl
        closed.append(p)
        del open_pos[p.base]
        last_exit[p.base] = i

    for i in range(i0, i1 + 1):
        # 1) fills queued from the previous close
        for base in pending:
            o = bars.open[base][i]
            if o is None or base in open_pos or len(open_pos) >= strat.max_pos:
                continue
            px = o * (1 + strat.side * (SPOT_SLIP if strat.venue == "spot" else PERP_SLIP))
            p = Position(base, strat.side, i, px, per_pos)
            p.fees += per_pos * (PERP_FEE if strat.venue == "perp" else SPOT_FEE)
            open_pos[base] = p
        pending = []
        # 2) manage open positions on this bar
        for base, p in list(open_pos.items()):
            if p.i_entry == i:
                continue
            hi, lo, c = bars.high[base][i], bars.low[base][i], bars.close[base][i]
            if hi is None or lo is None or c is None:
                continue
            stop_px = tp_px = None
            if strat.stop_pct is not None:
                stop_px = p.px_entry * (1 - p.side * strat.stop_pct)
            if strat.tp_pct is not None:
                tp_px = p.px_entry * (1 + p.side * strat.tp_pct)
            hit_stop = stop_px is not None and (lo <= stop_px if p.side > 0 else hi >= stop_px)
            hit_tp = tp_px is not None and (hi >= tp_px if p.side > 0 else lo <= tp_px)
            if hit_stop:
                close_pos(p, i, stop_px, "stop")
            elif hit_tp:
                close_pos(p, i, tp_px, "target")
            elif strat.max_bars is not None and i - p.i_entry >= strat.max_bars:
                close_pos(p, i, c, "time")
            elif strat.exit is not None and strat.exit(bars, base, i, p):
                close_pos(p, i, c, "signal")
        # 3) new signals on this close
        if len(open_pos) < strat.max_pos:
            for base in universe:
                if base in open_pos or bars.close[base][i] is None:
                    continue
                if i - last_exit.get(base, -(10**9)) < strat.cooldown:
                    continue
                if strat.entry(bars, base, i):
                    pending.append(base)
        # 4) mark
        unreal = sum(
            p.mark(c) - p.fees
            for p in open_pos.values()
            if (c := bars.close[p.base][i]) is not None
        )
        curve.append(realized + unreal)
    for base, p in list(open_pos.items()):
        c = bars.close[base][i1]
        if c is not None:
            close_pos(p, i1, c, "end")
    curve[-1:] = [realized]

    peak = 0.0
    dd = 0.0
    for x in curve:
        peak = max(peak, x)
        dd = max(dd, peak - x)
    by_year: dict[str, float] = {}
    by_window = {"IS": 0.0, "OOS": 0.0}
    for p in closed:
        d = dates[p.i_exit or 0]
        by_year[d[:4]] = by_year.get(d[:4], 0.0) + p.pnl
        by_window["IS" if d <= is_end else "OOS"] += p.pnl
    wins = sum(1 for p in closed if p.pnl > 0)
    return Result(
        name=strat.name,
        pnl_eur=realized,
        max_dd_eur=dd,
        max_dd_pct=dd / book_eur,
        n_trades=len(closed),
        win_rate=wins / len(closed) if closed else 0.0,
        fees_eur=sum(p.fees for p in closed),
        funding_eur=sum(p.funding for p in closed),
        by_year=by_year,
        by_window=by_window,
        positions=closed,
        curve=curve,
    )


# --------------------------------------------------------------------------- event study


@dataclass
class EventStats:
    name: str
    n: int
    horizons: dict[int, dict[str, float]]

    def row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n": self.n,
            "horizons": {
                str(h): {k: round(v, 5) for k, v in s.items()} for h, s in self.horizons.items()
            },
        }


def event_study(
    bars: Bars,
    name: str,
    cond: EntryFn,
    *,
    horizons: Sequence[int],
    start: str,
    end: str,
    universe: Sequence[str] | None = None,
    min_gap: int = 1,
) -> EventStats:
    """Forward close-to-close returns after ``cond`` fires (next-bar open entry)."""
    dates = bars.dates
    universe = list(universe or bars.bases)
    rets: dict[int, list[float]] = {h: [] for h in horizons}
    n_ev = 0
    hmax = max(horizons)
    for base in universe:
        last = -(10**9)
        for i, d in enumerate(dates):
            if d < start or d > end or i + 1 + hmax >= len(bars):
                continue
            if i - last < min_gap or not cond(bars, base, i):
                continue
            o = bars.open[base][i + 1]
            if o is None:
                continue
            last = i
            n_ev += 1
            for h in horizons:
                c = bars.close[base][i + h]
                if c is not None:
                    rets[h].append(c / o - 1.0)
    out: dict[int, dict[str, float]] = {}
    for h, xs in rets.items():
        if not xs:
            continue
        mu = sum(xs) / len(xs)
        sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        out[h] = {
            "mean": mu,
            "median": statistics.median(xs),
            "hit": sum(1 for x in xs if x > 0) / len(xs),
            "t": mu / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0,
        }
    return EventStats(name, n_ev, out)
