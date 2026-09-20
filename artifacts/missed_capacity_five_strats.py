#!/usr/bin/env python3
"""Five complementary architectures vs the live 15m momentum desk.

Hunt structurally profitable sleeves that capture *missed* capacity:
different horizon, different trigger, different asset (BTC vs alts),
different session. Bitvavo EUR books, fee-aware, no per-coin hardcodes.

Architectures (literature + our desk gaps):
  1. btc_sma200_voltarget     — time-series momentum / SMA200 long-flat (trend research)
  2. winners_weekly_regime    — cross-section winners-only, weekly, BTC>SMA200 (SSRN CS mom)
  3. dip_in_uptrend           — buy washed-out names still above SMA50 (Keel-style MR + trend)
  4. donchian_breakout        — 20d channel breakout, 10d exit (classic turtles / Keel)
  5. london_session_drive     — 15m 07–10 UTC session-high continuation (desk hours miss Asia/late)

Writes artifacts/missed_capacity_five_strats.json
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import (
    BOOK_EUR,
    FEE_RT,
    UNIVERSE,
    Series,
    _align,
    _max_drawdown,
    _rsi,
    _sma,
    load_daily,
)
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE
from bot.research.momentum_backtest.engine import load_candles

OUT = Path(__file__).resolve().parent / "missed_capacity_five_strats.json"
ALT = DEFAULT_UNIVERSE
ALL = ("BTC", *ALT)

WINDOWS = [
    ("last_12w", "2026-06-28", "2026-09-20"),
    ("prior_12w", "2026-04-05", "2026-06-28"),
    ("prior2_12w", "2026-01-11", "2026-04-05"),
    ("bear_to_now", "2025-10-06", "2026-09-20"),
]


def _idx(ts: list[int], date_s: str) -> int:
    target = int(datetime.fromisoformat(date_s).replace(tzinfo=UTC).timestamp() * 1000)
    for i, t in enumerate(ts):
        if t >= target:
            return i
    return len(ts) - 1


def _stats(eq: list[float], trades: int, wins: int, book: float = BOOK_EUR) -> dict[str, Any]:
    if not eq:
        return {"pnl_eur": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0, "trades": 0, "win_rate": None, "calmar": 0.0}
    pnl = eq[-1] - book
    mdd = _max_drawdown(eq)
    dd_pct = 100.0 * mdd / book
    calmar = (pnl / abs(mdd)) if abs(mdd) > 1 else (99.0 if pnl > 0 else 0.0)
    return {
        "pnl_eur": round(pnl, 2),
        "return_pct": round(100.0 * pnl / book, 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(dd_pct, 2),
        "calmar": round(calmar, 3),
        "trades": trades,
        "wins": wins,
        "win_rate": round(wins / trades, 3) if trades else None,
        "end_equity_eur": round(eq[-1], 2),
    }


def _atr(h: list[float], l: list[float], c: list[float], i: int, n: int = 14) -> float:
    if i < 1:
        return 0.02 * c[i]
    start = max(1, i - n + 1)
    trs = []
    for j in range(start, i + 1):
        trs.append(max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])))
    return sum(trs) / len(trs) if trs else 0.02 * c[i]


def _mom(c: list[float], i: int, lb: int, skip: int = 0) -> float | None:
    end = i - skip
    start = end - lb
    if start < 0 or end <= start:
        return None
    a, b = c[start], c[end]
    if a <= 0 or b <= 0:
        return None
    return b / a - 1.0


# ── generic long book (reserved notional) ──────────────────────────────────
class Book:
    def __init__(self, cash: float = BOOK_EUR) -> None:
        self.cash = cash
        self.pos: dict[str, tuple[float, float, float]] = {}  # notional, entry, peak_px
        self.trades = 0
        self.wins = 0
        self.eq: list[float] = []

    def mark(self, px: dict[str, float]) -> float:
        u = 0.0
        nsum = 0.0
        for b, (n, e, _) in self.pos.items():
            p = px.get(b, e)
            u += n * (p / e - 1.0)
            nsum += n
        return self.cash + nsum + u

    def snapshot(self, px: dict[str, float]) -> None:
        self.eq.append(self.mark(px))

    def close(self, b: str, price: float) -> float:
        n, e, _ = self.pos.pop(b)
        ret = price / e - 1.0
        net = n * ret - n * FEE_RT
        self.cash += n + net
        self.trades += 1
        self.wins += int(net > 0)
        return net

    def open(self, b: str, price: float, notional: float) -> bool:
        if b in self.pos or notional < 50 or notional > self.cash or price <= 0:
            return False
        fee = notional * (FEE_RT / 2)
        self.cash -= fee + notional
        self.pos[b] = (notional * (1.0 - FEE_RT / 2), price, price)
        return True

    def bump_peaks(self, highs: dict[str, float]) -> None:
        for b in list(self.pos):
            n, e, peak = self.pos[b]
            self.pos[b] = (n, e, max(peak, highs.get(b, peak)))


# ── 1. BTC SMA200 vol-target ───────────────────────────────────────────────
def sim_btc_sma(
    ts, closes, highs, lows, i0, i1, *, sma_n=200, vol_n=20, target_vol=0.40, max_w=1.0
) -> Book:
    btc = closes["BTC"]
    sma = _sma(btc, sma_n)
    book = Book()
    for i in range(i0, i1 + 1):
        px = {"BTC": btc[i]}
        book.bump_peaks({"BTC": highs["BTC"][i]})
        if i - vol_n < 1:
            book.snapshot(px)
            continue
        rets = [btc[j] / btc[j - 1] - 1.0 for j in range(i - vol_n + 1, i + 1) if btc[j - 1] > 0]
        vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 * math.sqrt(365) if rets else 0.4
        w = min(max_w, target_vol / max(vol, 0.08))
        want_long = sma[i] is not None and btc[i] > sma[i]
        held = "BTC" in book.pos
        if held and not want_long:
            book.close("BTC", btc[i])
        elif want_long:
            target_n = book.mark(px) * w
            if not held:
                book.open("BTC", btc[i], min(target_n, book.cash * 0.99))
            else:
                n, e, peak = book.pos["BTC"]
                if abs(n - target_n) / max(n, 1) > 0.25 and book.cash + n > target_n:
                    book.close("BTC", btc[i])
                    book.open("BTC", btc[i], min(target_n, book.cash * 0.99))
        book.snapshot(px)
    if "BTC" in book.pos:
        book.close("BTC", btc[i1])
        book.eq[-1] = book.cash
    return book


# ── 2. Winners-only weekly CS momentum ─────────────────────────────────────
def sim_winners_weekly(
    ts, closes, highs, lows, i0, i1, *, lb=21, skip=2, top_n=2, reb=7, w_each=0.4, sma_n=200
) -> Book:
    sma = _sma(closes["BTC"], sma_n)
    book = Book()
    last = -10**9
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT if b in closes}
        px["BTC"] = closes["BTC"][i]
        book.bump_peaks({b: highs[b][i] for b in book.pos if b in highs})
        bear = sma[i] is None or closes["BTC"][i] < sma[i]
        due = i - last >= reb or last < 0
        if due or (bear and book.pos):
            for b in list(book.pos):
                book.close(b, px.get(b, book.pos[b][1]))
            last = i
            if not bear:
                ranked = []
                for b in ALT:
                    m = _mom(closes[b], i, lb, skip)
                    if m is None:
                        continue
                    ranked.append((m, b))
                ranked.sort(reverse=True)
                eq = book.mark(px)
                for _, b in ranked[:top_n]:
                    book.open(b, px[b], min(eq * w_each, book.cash * 0.95))
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


# ── 3. Dip in uptrend (mean-reversion, trend-gated) ────────────────────────
def sim_dip_uptrend(
    ts, closes, highs, lows, i0, i1, *, drop=-0.04, rsi_max=45, sma_n=20, hold_days=6, stop=0.06, take=0.08, max_pos=2, w=0.4, btc_sma=50
) -> Book:
    sma_own = {b: _sma(closes[b], sma_n) for b in ALT}
    rsi = {b: _rsi(closes[b], 14) for b in ALT}
    sma_btc = _sma(closes["BTC"], btc_sma)
    book = Book()
    opened_i: dict[str, int] = {}
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        book.bump_peaks({b: highs[b][i] for b in book.pos if b in highs})
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        for b in list(book.pos):
            n, e, peak = book.pos[b]
            ret = px[b] / e - 1.0
            age = i - opened_i.get(b, i)
            reason = None
            if ret <= -stop:
                reason = "stop"
            elif ret >= take:
                reason = "take"
            elif age >= hold_days:
                reason = "time"
            if reason:
                book.close(b, px[b])
                opened_i.pop(b, None)
        if btc_ok and len(book.pos) < max_pos:
            cands = []
            for b in ALT:
                if b in book.pos or i < 5:
                    continue
                if sma_own[b][i] is None or px[b] < sma_own[b][i]:
                    continue
                r3 = px[b] / closes[b][i - 3] - 1.0
                rs = rsi[b][i]
                if r3 <= drop and (rs is None or rs <= rsi_max):
                    cands.append((r3, b))
            cands.sort()
            eq = book.mark(px)
            for _, b in cands:
                if len(book.pos) >= max_pos:
                    break
                if book.open(b, px[b], min(eq * w, book.cash * 0.95)):
                    opened_i[b] = i
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


def sim_residual_fade(
    ts, closes, highs, lows, i0, i1, *, excess=-0.035, hold=2, stop=0.05, take=0.05, max_pos=3, w=0.3
) -> Book:
    """1–3d leftover vs BTC: buy alts that lagged BTC hard, still not crashing 21d."""
    book = Book()
    opened_i: dict[str, int] = {}
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        if i < 2:
            book.snapshot(px)
            continue
        btc_1 = closes["BTC"][i] / closes["BTC"][i - 1] - 1.0
        for b in list(book.pos):
            ret = px[b] / book.pos[b][1] - 1.0
            age = i - opened_i.get(b, i)
            if ret <= -stop or ret >= take or age >= hold:
                book.close(b, px[b])
                opened_i.pop(b, None)
        if len(book.pos) < max_pos:
            cands = []
            for b in ALT:
                if b in book.pos:
                    continue
                r1 = px[b] / closes[b][i - 1] - 1.0
                ex = r1 - btc_1
                m21 = _mom(closes[b], i, 21, 0)
                if ex <= excess and (m21 is None or m21 > -0.25):
                    cands.append((ex, b))
            cands.sort()
            eq = book.mark(px)
            for _, b in cands:
                if len(book.pos) >= max_pos:
                    break
                if book.open(b, px[b], min(eq * w, book.cash * 0.95)):
                    opened_i[b] = i
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


def sim_breadth_thrust(
    ts, closes, highs, lows, i0, i1, *, thr=0.65, hold=4, w=0.25, top_n=4, stop=0.08
) -> Book:
    """When ≥thr of alts are up on the day, ride the strongest names for a few days."""
    book = Book()
    opened_i: dict[str, int] = {}
    last_entry = -10**9
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        if i < 1:
            book.snapshot(px)
            continue
        for b in list(book.pos):
            ret = px[b] / book.pos[b][1] - 1.0
            age = i - opened_i.get(b, i)
            if ret <= -stop or age >= hold:
                book.close(b, px[b])
                opened_i.pop(b, None)
        up = []
        for b in ALT:
            r = px[b] / closes[b][i - 1] - 1.0
            if r > 0:
                up.append((r, b))
        breadth = len(up) / max(len(ALT), 1)
        if breadth >= thr and i - last_entry >= hold and len(book.pos) == 0:
            up.sort(reverse=True)
            eq = book.mark(px)
            for _, b in up[:top_n]:
                if book.open(b, px[b], min(eq * w, book.cash * 0.95)):
                    opened_i[b] = i
            last_entry = i
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


# ── 4. Donchian 20/10 breakout ─────────────────────────────────────────────
def sim_donchian(
    ts, closes, highs, lows, i0, i1, *, ch=20, exit_n=10, max_pos=2, w=0.4, btc_sma=50
) -> Book:
    sma_btc = _sma(closes["BTC"], btc_sma)
    book = Book()
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        book.bump_peaks({b: highs[b][i] for b in book.pos if b in highs})
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        for b in list(book.pos):
            if i >= exit_n:
                ll = min(lows[b][i - exit_n : i])  # prior N lows, not including today
                if lows[b][i] <= ll:
                    book.close(b, px[b])
        if btc_ok and len(book.pos) < max_pos and i >= ch:
            cands = []
            for b in ALT:
                if b in book.pos:
                    continue
                hh = max(highs[b][i - ch : i])
                if highs[b][i] > hh:
                    mom = _mom(closes[b], i, ch, 0) or 0.0
                    cands.append((mom, b))
            cands.sort(reverse=True)
            eq = book.mark(px)
            for _, b in cands:
                if len(book.pos) >= max_pos:
                    break
                book.open(b, px[b], min(eq * w, book.cash * 0.95))
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


def sim_invvol_basket(
    ts, closes, highs, lows, i0, i1, *, lookback=63, vol_n=20, top_n=5, reb=7, sma_n=200, deploy=0.9
) -> Book:
    """Inverse-vol risk basket of strongest intermediate-horizon names, BTC-regime gated."""
    sma = _sma(closes["BTC"], sma_n)
    book = Book()
    last = -10**9
    for i in range(i0, i1 + 1):
        px = {b: closes[b][i] for b in ALT}
        px["BTC"] = closes["BTC"][i]
        bear = sma[i] is None or closes["BTC"][i] < sma[i]
        due = i - last >= reb or last < 0
        if due or (bear and book.pos):
            for b in list(book.pos):
                book.close(b, px.get(b, book.pos[b][1]))
            last = i
            if not bear and i >= max(lookback, vol_n) + 1:
                ranked = []
                vols = {}
                for b in ALT:
                    m = _mom(closes[b], i, lookback, 7)
                    if m is None:
                        continue
                    rets = [
                        closes[b][j] / closes[b][j - 1] - 1.0
                        for j in range(i - vol_n + 1, i + 1)
                        if closes[b][j - 1] > 0
                    ]
                    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 if rets else 0.05
                    vols[b] = max(vol, 0.01)
                    ranked.append((m, b))
                ranked.sort(reverse=True)
                picks = [b for _, b in ranked[:top_n]]
                inv = {b: 1.0 / vols[b] for b in picks}
                s = sum(inv.values()) or 1.0
                eq = book.mark(px)
                for b in picks:
                    book.open(b, px[b], min(eq * deploy * inv[b] / s, book.cash * 0.95))
        book.snapshot(px)
    for b in list(book.pos):
        book.close(b, closes[b][i1])
    if book.eq:
        book.eq[-1] = book.cash
    return book


# ── 5. London session drive (15m) ──────────────────────────────────────────
def sim_london_drive(
    candles: dict[str, list[list[float]]],
    *,
    start_ms: int,
    end_ms: int,
    trail=0.025,
    hard=0.02,
    max_hold_h=6.0,
    session_start=7,
    session_end=10,
    min_breadth=0.45,
) -> Book:
    """Long session-high break after 07 UTC if BTC morning tape is up and breadth ok."""
    by_ts: dict[int, dict[str, list[float]]] = defaultdict(dict)
    for b, rows in candles.items():
        for r in rows:
            by_ts[int(r[0])][b] = r
    stamps = sorted(t for t in by_ts if start_ms <= t <= end_ms)

    def px_map(t: int) -> dict[str, float]:
        return {b: float(r[4]) for b, r in by_ts[t].items()}

    book = Book()
    opened_ms: dict[str, int] = {}
    session_high: dict[str, float] = {}
    session_open_btc: float | None = None
    fired: set[str] = set()
    last_day = ""
    last_px: dict[str, float] = {}
    for t in stamps:
        dt = datetime.fromtimestamp(t / 1000, UTC)
        day = dt.strftime("%Y-%m-%d")
        hour = dt.hour
        minute = dt.minute
        bars = by_ts[t]
        last_px = px_map(t)
        if day != last_day:
            for b in list(book.pos):
                book.close(b, last_px.get(b, book.pos[b][1]) if last_px else book.pos[b][1])
            opened_ms.clear()
            session_high = {}
            session_open_btc = None
            fired = set()
            last_day = day
        for b, r in bars.items():
            session_high[b] = max(session_high.get(b, float(r[2])), float(r[2]))
        if "BTC" in bars and session_open_btc is None and hour >= session_start:
            session_open_btc = float(bars["BTC"][1])

        for b in list(book.pos):
            r = bars.get(b)
            if not r:
                continue
            n, e, peak = book.pos[b]
            hi, lo, cl = float(r[2]), float(r[3]), float(r[4])
            peak = max(peak, hi)
            book.pos[b] = (n, e, peak)
            age_h = (t - opened_ms[b]) / 3_600_000
            if lo <= e * (1 - hard):
                book.close(b, e * (1 - hard))
                opened_ms.pop(b, None)
            elif lo <= peak * (1 - trail):
                book.close(b, peak * (1 - trail))
                opened_ms.pop(b, None)
            elif age_h >= max_hold_h or hour >= 16:
                book.close(b, cl)
                opened_ms.pop(b, None)

        in_window = session_start <= hour < session_end
        if in_window and session_open_btc and "BTC" in last_px and not book.pos:
            btc_up = last_px["BTC"] / session_open_btc - 1.0
            alts = [b for b in ALT if b in last_px]
            if alts:
                up = sum(1 for b in alts if last_px[b] >= session_high.get(b, last_px[b]) * 0.999)
                # breadth vs session open proxy: close vs first seen
                breadth = up / len(alts)
            else:
                breadth = 0.0
            if btc_up > 0 and breadth >= min_breadth:
                # pick name making a session-high break with strongest morning ret
                scores = []
                for b in ALT:
                    r = bars.get(b)
                    if not r or b in fired:
                        continue
                    hi, cl = float(r[2]), float(r[4])
                    sh = session_high.get(b, hi)
                    # break: high touches session high and close in upper half of bar
                    if hi >= sh and cl >= (float(r[1]) + hi) / 2:
                        scores.append((cl / float(r[1]) - 1.0, b, cl))
                scores.sort(reverse=True)
                if scores:
                    _, b, cl = scores[0]
                    if book.open(b, cl, book.cash * 0.9):
                        opened_ms[b] = t
                        fired.add(b)
        book.snapshot(last_px)
    for b in list(book.pos):
        book.close(b, last_px.get(b, book.pos[b][1]))
    if book.eq:
        book.eq[-1] = book.cash
    return book


def window_stats(book: Book, label: str, wname: str) -> dict[str, Any]:
    st = _stats(book.eq, book.trades, book.wins)
    st["arch"] = label
    st["window"] = wname
    return st


def robust_ok(rows: list[dict[str, Any]]) -> bool:
    """Structurally profitable: last_12w + at least one prior window green, DD not catastrophic."""
    by = {r["window"]: r for r in rows}
    last = by.get("last_12w")
    if not last or last["pnl_eur"] <= 0:
        return False
    priors = [by[k] for k in ("prior_12w", "prior2_12w", "bear_to_now") if k in by]
    other_green = any(p["pnl_eur"] > 0 for p in priors)
    dd_ok = last["max_dd_pct"] > -35
    trades_ok = last["trades"] >= 3
    return other_green and dd_ok and trades_ok


def main() -> None:
    print("loading daily OHLC…", flush=True)
    series = load_daily(ALL, days=430)
    ts, closes = _align(series, ALL)
    highs: dict[str, list[float]] = {}
    lows: dict[str, list[float]] = {}
    for b in ALL:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        highs[b] = [series[b].h[idx[t]] for t in ts]
        lows[b] = [series[b].l[idx[t]] for t in ts]

    slices = {name: (_idx(ts, a), _idx(ts, b)) for name, a, b in WINDOWS}

    # 15m candles for last ~180d (prior2 start)
    print("loading 15m for session drive…", flush=True)
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    candles = load_candles(("BTC", *ALT), days=190, end_ms=end_ms, refresh=False)

    variants: list[tuple[str, str, Any]] = [
        ("btc_sma200_voltarget", "sma200_tv40", lambda i0, i1: sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=200, target_vol=0.40, max_w=1.0)),
        ("btc_sma200_voltarget", "sma200_tv25", lambda i0, i1: sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=200, target_vol=0.25, max_w=0.8)),
        ("btc_sma200_voltarget", "sma50_tv40", lambda i0, i1: sim_btc_sma(ts, closes, highs, lows, i0, i1, sma_n=50, target_vol=0.40, max_w=1.0)),
        ("winners_weekly_regime", "lb21_top2_w40", lambda i0, i1: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=21, top_n=2, reb=7, w_each=0.4)),
        ("winners_weekly_regime", "lb21_top1_w75", lambda i0, i1: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=21, top_n=1, reb=7, w_each=0.75)),
        ("winners_weekly_regime", "lb21_top2_sma50", lambda i0, i1: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=21, top_n=2, reb=7, w_each=0.4, sma_n=50)),
        ("winners_weekly_regime", "lb30_top3_w30", lambda i0, i1: sim_winners_weekly(ts, closes, highs, lows, i0, i1, lb=30, skip=7, top_n=3, reb=7, w_each=0.3)),
        ("dip_in_uptrend", "d04_rsi45_sma20", lambda i0, i1: sim_dip_uptrend(ts, closes, highs, lows, i0, i1)),
        ("dip_in_uptrend", "residual_1d", lambda i0, i1: sim_residual_fade(ts, closes, highs, lows, i0, i1)),
        ("dip_in_uptrend", "residual_deep", lambda i0, i1: sim_residual_fade(ts, closes, highs, lows, i0, i1, excess=-0.05, hold=3, w=0.35, max_pos=2)),
        ("donchian_breakout", "ch20_x10", lambda i0, i1: sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10)),
        ("donchian_breakout", "ch10_x5", lambda i0, i1: sim_donchian(ts, closes, highs, lows, i0, i1, ch=10, exit_n=5, btc_sma=50)),
        ("donchian_breakout", "ch20_x10_sma200", lambda i0, i1: sim_donchian(ts, closes, highs, lows, i0, i1, ch=20, exit_n=10, btc_sma=200)),
        ("breadth_thrust", "br65_h4", lambda i0, i1: sim_breadth_thrust(ts, closes, highs, lows, i0, i1, thr=0.65, hold=4)),
        ("breadth_thrust", "br55_h3", lambda i0, i1: sim_breadth_thrust(ts, closes, highs, lows, i0, i1, thr=0.55, hold=3, top_n=3, w=0.3)),
        ("invvol_winners_basket", "lb63_top5", lambda i0, i1: sim_invvol_basket(ts, closes, highs, lows, i0, i1)),
        ("invvol_winners_basket", "lb21_top4", lambda i0, i1: sim_invvol_basket(ts, closes, highs, lows, i0, i1, lookback=21, top_n=4, deploy=0.8)),
        ("invvol_winners_basket", "lb63_sma50", lambda i0, i1: sim_invvol_basket(ts, closes, highs, lows, i0, i1, sma_n=50, lookback=63, top_n=5)),
    ]

    results: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for arch, tag, fn in variants:
        print(f"… {arch}/{tag}", flush=True)
        for wname, (i0, i1) in slices.items():
            bk = fn(i0, i1)
            row = window_stats(bk, arch, wname)
            row["variant"] = tag
            results[f"{arch}::{tag}"].append(row)
            print(
                f"   {wname:14} pnl={row['pnl_eur']:>9} dd={row['max_dd_pct']:>6}% "
                f"tr={row['trades']:>3} wr={row['win_rate']}",
                flush=True,
            )

    # 15m session — last 12w + prior 12w only (cache length)
    print("… london_session_drive", flush=True)
    sess_windows = [
        ("last_12w", "2026-06-28", "2026-09-20"),
        ("prior_12w", "2026-04-05", "2026-06-28"),
        ("prior2_12w", "2026-03-15", "2026-04-05"),  # truncated if cache shorter
    ]
    sess_cfgs = [
        ("t25_h2_br45", dict(trail=0.025, hard=0.02, min_breadth=0.45)),
        ("t35_h25_br40", dict(trail=0.035, hard=0.025, min_breadth=0.40)),
        ("t20_h15_br50", dict(trail=0.02, hard=0.015, min_breadth=0.50)),
    ]
    for tag, kw in sess_cfgs:
        key = f"london_session_drive::{tag}"
        for wname, a, b in sess_windows:
            s_ms = int(datetime.fromisoformat(a).replace(tzinfo=UTC).timestamp() * 1000)
            e_ms = int(datetime.fromisoformat(b).replace(tzinfo=UTC).timestamp() * 1000)
            bk = sim_london_drive(candles, start_ms=s_ms, end_ms=e_ms, **kw)
            row = window_stats(bk, "london_session_drive", wname)
            row["variant"] = tag
            results[key].append(row)
            print(
                f"   {tag} {wname:14} pnl={row['pnl_eur']:>9} dd={row['max_dd_pct']:>6}% "
                f"tr={row['trades']:>3}",
                flush=True,
            )

    # pick best robust variant per architecture
    by_arch: dict[str, list[str]] = defaultdict(list)
    for key in results:
        arch = key.split("::")[0]
        by_arch[arch].append(key)

    picks = []
    for arch, keys in by_arch.items():
        scored = []
        for k in keys:
            rows = results[k]
            last = next((r for r in rows if r["window"] == "last_12w"), None)
            prior = next((r for r in rows if r["window"] == "prior_12w"), None)
            p2 = next((r for r in rows if r["window"] == "prior2_12w"), None)
            bear = next((r for r in rows if r["window"] == "bear_to_now"), None)
            ok = robust_ok(rows)
            last_pnl = last["pnl_eur"] if last else -1e9
            prior_pnl = prior["pnl_eur"] if prior else 0
            p2_pnl = p2["pnl_eur"] if p2 else 0
            last_dd = last["max_dd_pct"] if last else -99
            calmar = last.get("calmar") or 0
            dd_ok = last_dd > -22
            score = (
                int(ok and dd_ok),
                int(last_pnl > 0),
                int(prior_pnl > 0) + int(p2_pnl > 0),
                round(calmar, 3),
                last_pnl + 0.35 * prior_pnl,
                last_dd,
            )
            scored.append((score, k, rows, ok))
        scored.sort(reverse=True)
        best = scored[0]
        k, rows, ok = best[1], best[2], best[3]
        last = next(r for r in rows if r["window"] == "last_12w")
        picks.append(
            {
                "architecture": arch,
                "variant": k.split("::")[1],
                "structurally_profitable": ok,
                "windows": rows,
                "last_12w_pnl_eur": last["pnl_eur"],
                "last_12w_dd_pct": last["max_dd_pct"],
                "last_12w_trades": last["trades"],
            }
        )

    positive = [p for p in picks if p["structurally_profitable"] or p["last_12w_pnl_eur"] > 0]
    positive.sort(key=lambda p: (p["structurally_profitable"], p["last_12w_pnl_eur"]), reverse=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK_EUR,
        "fee_rt": FEE_RT,
        "universe": list(ALT),
        "intent": (
            "Five complementary sleeves capturing capacity the 15m RS desk misses: "
            "BTC trend, weekly winners, dip-MR, Donchian breakout, London session."
        ),
        "sources": [
            "Keel Hyperliquid: momentum+funding, mean-reversion, channel breakout",
            "SSRN: CS momentum winners-only; losers often reverse",
            "crypto-trend-research: 1d SMA200 long/flat vs buy-hold",
            "Artemis: BTC-regime gated alt factors",
            "Own 12w/prior windows: momentum desk idle vs bleed",
        ],
        "windows": [{"name": n, "start": a, "end": b} for n, a, b in WINDOWS],
        "caveats": [
            "No AlphaI timeline, no maker path, daily close fills (except 15m session)",
            "Spot EUR — no funding carry (that family needs perps)",
            "In-sample variant pick per architecture; structural = last12w + another window green",
        ],
        "architectures": picks,
        "positive_overview": positive,
        "all_variants": {k: v for k, v in results.items()},
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== POSITIVE / PICKS ===", flush=True)
    for p in picks:
        flag = "STRUCT" if p["structurally_profitable"] else "last12w-only" if p["last_12w_pnl_eur"] > 0 else "red"
        print(
            f"{p['architecture']:24} {p['variant']:16} {flag:14} "
            f"12w={p['last_12w_pnl_eur']:>9} dd={p['last_12w_dd_pct']}% n={p['last_12w_trades']}",
            flush=True,
        )
        for r in p["windows"]:
            print(
                f"    {r['window']:14} {r['pnl_eur']:>9}  dd {r['max_dd_pct']:>6}%  "
                f"tr {r['trades']:>3} wr {r['win_rate']}",
                flush=True,
            )
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
