"""Causal 4h replay of the unusual-volume momentum system.

Pipeline per signal (daily unusual volume on completed bar D):
  confluence  on the first ``confirm_bars`` 4h closes after D: close > EMA20 > EMA50
              and MACD line > signal (optionally a fresh cross within ``macd_fresh``)
  entry       next 4h open after the confirming close, taker fee + impact
  exits       RSI14 >= ``rsi_exit`` (sell next open), swing-low trail (close below the
              highest higher-low since entry), hard stop, time stop, optional scale-out
              at fixed gains. One fixed-size slot per position, ``max_positions`` slots.
No per-coin branches.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bot.research.unusual_volume_lab.scanner import day_of

H4_MS = 4 * 3_600_000
DAY_MS = 86_400_000
TAKER = 0.0025
IMPACT_K = 0.02
IMPACT_CAP = 0.03


@dataclass(frozen=True)
class SystemSpec:
    name: str = "base"
    confirm_bars: int = 6  # 4h bars after the signal day in which confluence may fire
    ema_fast: int = 20
    ema_slow: int = 50
    require_ema: bool = True
    require_macd: bool = True
    macd_fresh: int = 0  # >0: MACD cross must be within this many bars
    rsi_n: int = 14
    rsi_exit: float = 75.0  # 0 = off
    trail_mode: str = "swing"  # swing | pct | off
    trail_pct: float = 0.10
    swing_bars: int = 3  # pivot low = low lower than ``swing_bars`` bars either side
    hard_stop: float = 0.08
    time_bars: int = 30  # 30 x 4h = 5 days
    scale_out: tuple[tuple[float, float], ...] = ()  # ((gain, frac), ...)
    book_eur: float = 2_000.0
    max_positions: int = 1
    min_day_qvol_eur: float = 50_000.0


def ema(xs: Sequence[float], n: int) -> list[float]:
    out: list[float] = []
    k = 2.0 / (n + 1.0)
    e = None
    for x in xs:
        e = x if e is None else (x - e) * k + e
        out.append(e)
    return out


def macd(
    xs: Sequence[float], fast: int = 12, slow: int = 26, sig: int = 9
) -> tuple[list[float], list[float]]:
    ef = ema(xs, fast)
    es = ema(xs, slow)
    line = [a - b for a, b in zip(ef, es, strict=True)]
    return line, ema(line, sig)


def rsi(xs: Sequence[float], n: int = 14) -> list[float]:
    out = [50.0] * len(xs)
    if len(xs) < n + 1:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


@dataclass
class Indicators:
    ts: list[int]
    o: list[float]
    h: list[float]
    lo: list[float]
    c: list[float]
    v: list[float]
    ema_f: list[float]
    ema_s: list[float]
    macd_l: list[float]
    macd_s: list[float]
    rsi: list[float]


def build(rows: Sequence[Sequence[float]], spec: SystemSpec) -> Indicators:
    ts = [int(r[0]) for r in rows]
    o = [float(r[1]) for r in rows]
    h = [float(r[2]) for r in rows]
    lo = [float(r[3]) for r in rows]
    c = [float(r[4]) for r in rows]
    v = [float(r[5]) for r in rows]
    ml, ms = macd(c)
    return Indicators(
        ts, o, h, lo, c, v, ema(c, spec.ema_fast), ema(c, spec.ema_slow), ml, ms, rsi(c, spec.rsi_n)
    )


def _impact(notional: float, adv_eur: float) -> float:
    if adv_eur <= 0:
        return IMPACT_CAP
    return min(IMPACT_CAP, IMPACT_K * notional / adv_eur)


@dataclass
class Pos:
    base: str
    entry_i: int
    entry_px: float
    qty: float
    cost_eur: float
    peak: float
    swing_floor: float
    sold: list[float] = field(default_factory=list)
    scaled: int = 0
    realized: float = 0.0


def _pivot_lows(ind: Indicators, start: int, end: int, k: int) -> list[float]:
    """Pivot lows confirmed by ``k`` bars on each side, inside [start, end]."""
    out: list[float] = []
    for i in range(max(start, k), min(end, len(ind.lo) - 1) - k + 1):
        low = ind.lo[i]
        if all(low < ind.lo[j] for j in range(i - k, i)) and all(
            low <= ind.lo[j] for j in range(i + 1, i + k + 1)
        ):
            out.append(low)
    return out


def run_system(
    daily: Mapping[str, Sequence[Sequence[float]]],
    h4: Mapping[str, Sequence[Sequence[float]]],
    signals: Sequence[Mapping[str, Any]],
    spec: SystemSpec,
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    ind_cache: dict[str, Indicators] = {}
    adv: dict[str, dict[str, float]] = {}
    for base, rows in daily.items():
        d: dict[str, float] = {}
        for i in range(len(rows)):
            seg = rows[max(0, i - 20) : i]
            d[day_of(rows[i][0])] = (
                statistics.fmean([float(r[5]) * float(r[4]) for r in seg]) if seg else 0.0
            )
        adv[base] = d

    # Candidate entries: (entry_bar_ts, base, entry_i) found causally per signal.
    cands: list[tuple[int, str, int, float]] = []
    for s in signals:
        if s["day"] < start or s["day"] > end:
            continue
        base = s["base"]
        rows = h4.get(base)
        if not rows or len(rows) < spec.ema_slow + 30:
            continue
        ind = ind_cache.get(base)
        if ind is None:
            ind = build(rows, spec)
            ind_cache[base] = ind
        if "ts" in s and int(s.get("bar_ms", DAY_MS)) < DAY_MS:
            bar_end_ms = int(s["ts"]) + int(s["bar_ms"])
        else:
            bar_end_ms = (
                int(datetime.strptime(s["day"], "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
                + DAY_MS
            )
        j0 = bisect.bisect_left(ind.ts, bar_end_ms)
        for j in range(j0, min(j0 + spec.confirm_bars, len(ind.ts) - 1)):
            if j < spec.ema_slow:
                continue
            ok = True
            if spec.require_ema:
                ok = ind.c[j] > ind.ema_f[j] > ind.ema_s[j]
            if ok and spec.require_macd:
                ok = ind.macd_l[j] > ind.macd_s[j]
                if ok and spec.macd_fresh > 0:
                    ok = any(
                        ind.macd_l[m] <= ind.macd_s[m]
                        for m in range(max(0, j - spec.macd_fresh), j)
                    )
            if ok:
                cands.append((ind.ts[j + 1], base, j + 1, float(s["vol_mult"])))
                break
    # Same-bar ties go to the strongest volume anomaly.
    cands.sort(key=lambda x: (x[0], -x[3]))

    # Event loop over 4h bar timestamps.
    all_ts = sorted({t for ind in ind_cache.values() for t in ind.ts})
    open_pos: dict[str, Pos] = {}
    trades: list[dict[str, Any]] = []
    equity_curve: list[tuple[int, float]] = []
    realized = 0.0
    ci = 0
    for t in all_ts:
        # 1) manage exits on bars that closed at t (bar index with ts == t is the bar opening at t;
        #    the close of the previous bar is what we act on at this open).
        for base in list(open_pos):
            p = open_pos[base]
            ind = ind_cache[base]
            k = bisect.bisect_left(ind.ts, t)
            if k >= len(ind.ts) or ind.ts[k] != t or k <= p.entry_i:
                continue
            prev = k - 1  # bar that just closed
            close = ind.c[prev]
            p.peak = max(p.peak, ind.h[prev])
            piv = _pivot_lows(ind, p.entry_i, prev, spec.swing_bars)
            if piv:
                p.swing_floor = max(p.swing_floor, max(piv))
            reason = ""
            if spec.hard_stop > 0 and close <= p.entry_px * (1 - spec.hard_stop):
                reason = "hard_stop"
            elif spec.rsi_exit > 0 and ind.rsi[prev] >= spec.rsi_exit:
                reason = "rsi"
            elif spec.trail_mode == "swing" and p.swing_floor > 0 and close < p.swing_floor:
                reason = "swing_trail"
            elif spec.trail_mode == "pct" and close <= p.peak * (1 - spec.trail_pct):
                reason = "pct_trail"
            elif spec.time_bars > 0 and prev - p.entry_i >= spec.time_bars:
                reason = "time"
            frac = 1.0
            if not reason and spec.scale_out and p.scaled < len(spec.scale_out):
                gain, f = spec.scale_out[p.scaled]
                if close >= p.entry_px * (1 + gain):
                    reason = f"scale_{p.scaled + 1}"
                    frac = f
                    p.scaled += 1
            if not reason:
                continue
            px = ind.o[k]
            qty = p.qty * frac
            notional = qty * px
            slip = _impact(notional, adv[base].get(day_of(t), 0.0))
            proceeds = notional * (1 - slip) * (1 - TAKER)
            cost_part = p.cost_eur * frac
            pnl = proceeds - cost_part
            p.qty -= qty
            p.cost_eur -= cost_part
            p.realized += pnl
            realized += pnl
            if frac >= 0.999 or p.qty * px < 5.0:
                trades.append(
                    {
                        "base": base,
                        "entry_ts": ind.ts[p.entry_i],
                        "exit_ts": t,
                        "entry_px": p.entry_px,
                        "exit_px": px,
                        "pnl_eur": round(p.realized, 2),
                        "reason": reason,
                        "bars": prev - p.entry_i,
                        "peak_gain": p.peak / p.entry_px - 1.0,
                    }
                )
                del open_pos[base]
        # 2) new entries at this open
        while ci < len(cands) and cands[ci][0] <= t:
            ets, base, ei, _vm = cands[ci]
            ci += 1
            if ets != t or base in open_pos or len(open_pos) >= spec.max_positions:
                continue
            ind = ind_cache[base]
            px = ind.o[ei]
            if px <= 0:
                continue
            day_adv = adv[base].get(day_of(t), 0.0)
            slip = _impact(spec.book_eur, day_adv)
            fill = px * (1 + slip)
            cost = spec.book_eur * (1 + TAKER)
            qty = spec.book_eur / fill
            open_pos[base] = Pos(base, ei, fill, qty, cost, fill, 0.0)
        # 3) mark
        mark = realized
        for base, p in open_pos.items():
            ind = ind_cache[base]
            k = bisect.bisect_left(ind.ts, t)
            px = ind.c[k - 1] if 0 < k <= len(ind.c) else p.entry_px
            mark += p.qty * px - p.cost_eur
        equity_curve.append((t, mark))

    # Flatten whatever is still open at the last close so the PnL is complete.
    for base, p in list(open_pos.items()):
        ind = ind_cache[base]
        px = ind.c[-1]
        notional = p.qty * px
        proceeds = (
            notional * (1 - _impact(notional, adv[base].get(day_of(ind.ts[-1]), 0.0))) * (1 - TAKER)
        )
        pnl = proceeds - p.cost_eur
        p.realized += pnl
        realized += pnl
        trades.append(
            {
                "base": base,
                "entry_ts": ind.ts[p.entry_i],
                "exit_ts": ind.ts[-1],
                "entry_px": p.entry_px,
                "exit_px": px,
                "pnl_eur": round(p.realized, 2),
                "reason": "end",
                "bars": len(ind.ts) - 1 - p.entry_i,
                "peak_gain": p.peak / p.entry_px - 1.0,
            }
        )
        del open_pos[base]

    peak = 0.0
    max_dd = 0.0
    for _, e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    wins = [x for x in trades if x["pnl_eur"] > 0]
    reasons: dict[str, int] = {}
    for x in trades:
        reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1
    n_days = max(1, (all_ts[-1] - all_ts[0]) // DAY_MS) if all_ts else 1
    return {
        "name": spec.name,
        "n_candidates": len(cands),
        "n_trades": len(trades),
        "pnl_eur": round(realized, 2),
        "max_dd_eur": round(max_dd, 2),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_pnl": statistics.fmean([x["pnl_eur"] for x in trades]) if trades else 0.0,
        "median_pnl": statistics.median([x["pnl_eur"] for x in trades]) if trades else 0.0,
        "avg_bars": statistics.fmean([x["bars"] for x in trades]) if trades else 0.0,
        "reasons": reasons,
        "pnl_per_day": realized / n_days,
        "trades": trades,
    }
