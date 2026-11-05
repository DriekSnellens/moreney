"""Identify a 50% rise before it happens.

The entry is the next Bitvavo daily open. Everything used as a signal
is known at the close of the day before that open. A rise is the high
over the next seven daily bars. The order sells when that high reaches
+30%, +50% or +100% above the entry. A bar that opens through the level
fills at the open. Otherwise it fills at the level. If the level is
still untouched, the sell is the open seven days after the entry. The
primary level is +50%. The other two are neighbours and are reported.

Signals, locked before the tape:

* buy pressure — up-bar euro share of that day's 15-minute bars at least
  65%, and the mirror at most 35%, when the day has at least eight bars
* coin volume — that day's euro turnover against the prior seven days,
  in the bins under 1×, 1–2× and from 2×
* market volume — the same ratio for every coin on the daily tape
* taker buy and taker sell — Binance taker share at least 55%
* the day already closed +10%, and the neighbour +20%
* the close is within 5% of the high of the last 20 days
* a small peak of the primary shape confirmed on that day

A name whose closes stay inside 0.5% for seven days is a peg and is
left out. Two universes: a day with at least €10,000 of quote, and a
day with at least €1,000,000. The wet fill uses a €5,000 order and the
signal day's quote as the turnover. Costs are the Bitvavo taker on both
sides. Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from bot.research.capital_flow.engine import is_peg, wet_net
from bot.research.small_peak.engine import (
    PRIMARY,
    net_long,
    prepare,
    select_peaks,
)

DAY_MS = 86_400_000
HOLD_DAYS = 7
HISTORY = 7
HIGH_DAYS = 20
MIN_BARS = 8
QUOTE_BROAD = 10_000.0
QUOTE_LIQUID = 1_000_000.0
BUY_SHARE = 0.65
SELL_SHARE = 0.35
TAKER_CUT = 0.55
UP_MILD = 0.10
UP_STRONG = 0.20
NEAR_HIGH = 0.95
TARGETS = (0.30, 0.50, 1.00)

COLUMNS = (
    "entry_ms",
    "quote",
    "hit30",
    "hit50",
    "hit100",
    "hit50_1d",
    "net30",
    "net50",
    "net100",
    "wet50",
    "koop",
    "verkoop",
    "vol_lo",
    "vol_mid",
    "vol_hi",
    "mkt_lo",
    "mkt_mid",
    "mkt_hi",
    "taker_buy",
    "taker_sell",
    "up10",
    "up20",
    "near_high",
    "peak",
    "coin_bull",
    "mkt_bull",
)


def empty_book() -> dict[str, np.ndarray]:
    blank = np.asarray([], dtype=np.float64)
    book = {name: blank.copy() for name in COLUMNS}
    book["base"] = np.asarray([], dtype=object)
    return book


def concat_books(parts: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    parts = [part for part in parts if len(part["entry_ms"])]
    if not parts:
        return empty_book()
    book = {name: np.concatenate([part[name] for part in parts]) for name in COLUMNS}
    book["base"] = np.concatenate([part["base"] for part in parts])
    return book


def limit_or_open(
    open_: np.ndarray,
    high: np.ndarray,
    entry: int,
    window: int,
    target: float,
) -> tuple[float, float]:
    """Fill and whether the level was hit. The exit open is ``window`` days later."""
    level = float(open_[entry]) * (1.0 + target)
    last = entry + window
    for bar in range(entry, last):
        if float(high[bar]) < level:
            continue
        opened = float(open_[bar])
        fill = opened if opened >= level else level
        return fill, 1.0
    return float(open_[last]), 0.0


def intraday_flags(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> tuple[dict[int, float], set[int]]:
    """Buy-pressure by UTC day, and days on which a small peak was confirmed."""
    shares: dict[int, float] = {}
    start = 0
    n = len(ts)
    while start < n:
        day = int(ts[start] - ts[start] % DAY_MS)
        stop = start + 1
        while stop < n and int(ts[stop] - ts[stop] % DAY_MS) == day:
            stop += 1
        if stop - start >= MIN_BARS:
            quote = close[start:stop] * volume[start:stop]
            sign = np.sign(close[start:stop] - open_[start:stop])
            up = float(quote[sign > 0.0].sum())
            down = float(quote[sign < 0.0].sum())
            total = up + down
            if total > 0.0:
                shares[day] = up / total
        start = stop
    peaks: set[int] = set()
    prepared = prepare(high, low, close, volume)
    if prepared is not None:
        idx, rise, pullback = prepared
        for peak_i in select_peaks(idx, rise, pullback, PRIMARY):
            confirm = int(peak_i) + 1
            if confirm < n:
                stamp = int(ts[confirm])
                peaks.add(stamp - stamp % DAY_MS)
    return shares, peaks


def _volume_flags(ratio: float) -> tuple[float, float, float]:
    if not math.isfinite(ratio) or ratio < 0.0:
        return math.nan, math.nan, math.nan
    if ratio < 1.0:
        return 1.0, 0.0, 0.0
    if ratio < 2.0:
        return 0.0, 1.0, 0.0
    return 0.0, 0.0, 1.0


def _flow_flags(share: float) -> tuple[float, float]:
    if not math.isfinite(share):
        return math.nan, math.nan
    return (1.0 if share >= BUY_SHARE else 0.0), (1.0 if share <= SELL_SHARE else 0.0)


def _taker_flags(share: float) -> tuple[float, float]:
    """``share`` is taker-sell. Buy is the mirror, sell share at most 45%."""
    if not math.isfinite(share):
        return math.nan, math.nan
    return (1.0 if share <= 1.0 - TAKER_CUT else 0.0), (1.0 if share >= TAKER_CUT else 0.0)


def coin_rows(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    close: np.ndarray,
    quote: np.ndarray,
    *,
    buy: Mapping[int, float],
    peaks: set[int],
    taker: Mapping[int, float],
    market: Mapping[int, float],
    has_15m: bool,
) -> dict[str, np.ndarray]:
    """One coin. Rows are signal days whose next seven days are on the tape."""
    rows: dict[str, list[float]] = {name: [] for name in COLUMNS}
    bases: list[str] = []
    n = len(ts)
    last = n - (HOLD_DAYS + 1)
    for i in range(HISTORY, last):
        if int(ts[i + HOLD_DAYS + 1]) - int(ts[i]) != (HOLD_DAYS + 1) * DAY_MS:
            continue
        day_quote = float(quote[i])
        entry_px = float(open_[i + 1])
        if day_quote < QUOTE_BROAD or entry_px <= 0.0 or float(open_[i]) <= 0.0:
            continue
        if is_peg(close[i - (HISTORY - 1) : i + 1]):
            continue
        entry = i + 1
        fill30, hit30 = limit_or_open(open_, high, entry, HOLD_DAYS, TARGETS[0])
        fill50, hit50 = limit_or_open(open_, high, entry, HOLD_DAYS, TARGETS[1])
        fill100, hit100 = limit_or_open(open_, high, entry, HOLD_DAYS, TARGETS[2])
        day_stamp = int(ts[i])
        baseline = float(np.mean(quote[i - HISTORY : i]))
        ratio = day_quote / baseline if baseline > 0.0 else math.nan
        vol_lo, vol_mid, vol_hi = _volume_flags(ratio)
        mkt_lo, mkt_mid, mkt_hi = _volume_flags(float(market.get(day_stamp, math.nan)))
        if has_15m:
            koop, verkoop = _flow_flags(float(buy.get(day_stamp, math.nan)))
            peak = 1.0 if day_stamp in peaks else 0.0
        else:
            koop, verkoop, peak = math.nan, math.nan, math.nan
        taker_buy, taker_sell = _taker_flags(float(taker.get(day_stamp, math.nan)))
        day_ret = float(close[i]) / float(open_[i]) - 1.0
        near = math.nan
        if i >= HIGH_DAYS - 1:
            span = float(high[i - (HIGH_DAYS - 1) : i + 1].max())
            near = 1.0 if span > 0.0 and float(close[i]) / span >= NEAR_HIGH else 0.0
        values = {
            "entry_ms": float(ts[entry]),
            "quote": day_quote,
            "hit30": hit30,
            "hit50": hit50,
            "hit100": hit100,
            "hit50_1d": 1.0 if float(high[entry]) / entry_px - 1.0 >= TARGETS[1] else 0.0,
            "net30": net_long(entry_px, fill30),
            "net50": net_long(entry_px, fill50),
            "net100": net_long(entry_px, fill100),
            "wet50": wet_net(entry_px, fill50, day_quote),
            "koop": koop,
            "verkoop": verkoop,
            "vol_lo": vol_lo,
            "vol_mid": vol_mid,
            "vol_hi": vol_hi,
            "mkt_lo": mkt_lo,
            "mkt_mid": mkt_mid,
            "mkt_hi": mkt_hi,
            "taker_buy": taker_buy,
            "taker_sell": taker_sell,
            "up10": 1.0 if day_ret >= UP_MILD else 0.0,
            "up20": 1.0 if day_ret >= UP_STRONG else 0.0,
            "near_high": near,
            "peak": peak,
            "coin_bull": math.nan,
            "mkt_bull": math.nan,
        }
        for name in COLUMNS:
            rows[name].append(values[name])
        bases.append(base)
    book = {name: np.asarray(rows[name], dtype=np.float64) for name in COLUMNS}
    book["base"] = np.asarray(bases, dtype=object)
    return book


def market_ratios(totals: Mapping[int, float]) -> dict[int, float]:
    """Today's all-coin quote divided by the mean of the prior seven days."""
    if not totals:
        return {}
    stamps = np.asarray(sorted(totals), dtype=np.int64)
    values = np.asarray([totals[int(stamp)] for stamp in stamps], dtype=np.float64)
    out: dict[int, float] = {}
    for i in range(HISTORY, len(stamps)):
        if int(stamps[i]) - int(stamps[i - HISTORY]) != HISTORY * DAY_MS:
            continue
        baseline = float(values[i - HISTORY : i].mean())
        if baseline > 0.0:
            out[int(stamps[i])] = float(values[i]) / baseline
    return out


def universe(book: dict[str, np.ndarray], *, liquid: bool) -> np.ndarray:
    floor = QUOTE_LIQUID if liquid else QUOTE_BROAD
    return book["quote"] >= floor


def signal_mask(book: dict[str, np.ndarray], name: str | None, rows: np.ndarray) -> np.ndarray:
    """Rows where the signal fired. ``None`` is every row in ``rows``."""
    if name is None:
        return rows
    values = book[name]
    return rows & np.isfinite(values) & (values == 1.0)


def known_mask(book: dict[str, np.ndarray], name: str | None, rows: np.ndarray) -> np.ndarray:
    if name is None:
        return rows
    return rows & np.isfinite(book[name])


def describe(
    book: dict[str, np.ndarray],
    rows: np.ndarray,
    signal: str | None,
    *,
    hit: str = "hit50",
    net: str = "net50",
) -> dict[str, float]:
    """Precision, recall and the order that sells at the hit level."""
    fired = signal_mask(book, signal, rows)
    known = known_mask(book, signal, rows)
    n = int(fired.sum())
    events = known & (book[hit] == 1.0)
    n_events = int(events.sum())
    if n == 0:
        precision = math.nan
        mean = math.nan
        median = math.nan
        wet = math.nan
    else:
        precision = float(book[hit][fired].mean())
        taken = book[net][fired]
        mean = float(taken.mean())
        median = float(np.median(taken))
        wet = float(book["wet50"][fired].mean()) if net == "net50" else math.nan
    recall = float(book[signal][events].mean()) if signal is not None and n_events else math.nan
    if signal is None and n_events:
        recall = 1.0
    return {
        "n": float(n),
        "precision": precision,
        "recall": recall,
        "mean": mean,
        "median": median,
        "wet": wet,
        "events": float(n_events),
    }


def hit_rate(book: dict[str, np.ndarray], rows: np.ndarray, signal: str | None, hit: str) -> float:
    fired = signal_mask(book, signal, rows)
    if not fired.any():
        return math.nan
    return float(book[hit][fired].mean())
