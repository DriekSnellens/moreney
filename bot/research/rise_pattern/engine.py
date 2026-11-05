"""Signals that are known before a coin rises.

The Bitvavo 15-minute candles have no separate buy and sell prints. Buy
pressure is the euro turnover of bars that closed up, divided by the
turnover of bars that closed up or down. A doji is left out. The window
ends on the bar before the entry, so the entry bar itself is unread.

Primary window is 2 hours. The 24-hour window is the neighbour and is
reported too. Buy is a share of at least 65%. The neighbours are 60% and
70%, with sell the mirror (35%, 40%, 30%). Market volume is the same
ratio as the peak study: all-coin euro turnover over 2 hours, divided by
that tape's own prior day, in the bins under 1×, 1–2× and from 2×.

A clock fires every 8 hours. The long buys that open and sells the open
4 hours or 24 hours later, taker 0.25% each side. The path has to be
gap-free for 24 hours, and the prior day has to average at least €20,000
of quote per bar. A riser is a path whose high gets 3% above the entry.
The 6% neighbour is reported in the descriptive table only.

AlphaI coin and market votes are the majority in the 24 hours before the
entry. A headline at the entry is invisible. The full pattern is buy
pressure, market volume from 2×, a bullish coin and a bullish market.
Every row below is locked before the tape runs. Nothing is armed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from bot.research.small_peak.engine import (
    BAR_MS,
    MIN_QUOTE,
    NULL_STRIDE,
    QUOTE_BARS,
    net_long,
    volume_bin,
    volume_ratio,
)

FLOW_2H = 8
FLOW_24H = 96
HOLD_4H = 16
HOLD_24H = 96
BUY_SHARE = 0.65
BUY_NEIGHBOURS = (0.60, 0.70)
RISE_3 = 0.03
RISE_6 = 0.06


@dataclass(frozen=True)
class Clock:
    ms: np.ndarray
    share_2h: np.ndarray
    share_24h: np.ndarray
    market: np.ndarray
    rise_4h: np.ndarray
    rise_24h: np.ndarray
    net_4h: np.ndarray
    net_24h: np.ndarray

    def __len__(self) -> int:
        return int(len(self.ms))


def slice_clock(clock: Clock, mask: np.ndarray) -> Clock:
    """Keep the rows where ``mask`` is true."""
    return Clock(
        clock.ms[mask],
        clock.share_2h[mask],
        clock.share_24h[mask],
        clock.market[mask],
        clock.rise_4h[mask],
        clock.rise_24h[mask],
        clock.net_4h[mask],
        clock.net_24h[mask],
    )


def empty_clock() -> Clock:
    blank = np.asarray([], dtype=np.float64)
    return Clock(
        np.asarray([], dtype=np.int64),
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
    )


def concat_clocks(parts: list[Clock]) -> Clock:
    if not parts:
        return empty_clock()
    return Clock(
        np.concatenate([part.ms for part in parts]),
        np.concatenate([part.share_2h for part in parts]),
        np.concatenate([part.share_24h for part in parts]),
        np.concatenate([part.market for part in parts]),
        np.concatenate([part.rise_4h for part in parts]),
        np.concatenate([part.rise_24h for part in parts]),
        np.concatenate([part.net_4h for part in parts]),
        np.concatenate([part.net_24h for part in parts]),
    )


def buy_share(
    open_: np.ndarray,
    close: np.ndarray,
    quote: np.ndarray,
    end: np.ndarray,
    *,
    width: int,
) -> np.ndarray:
    """Up-bar euro turnover over up-bar plus down-bar turnover.

    ``end`` is inclusive. Bars after ``end`` are unread. A window with no
    up or down turnover is NaN.
    """
    ends = np.asarray(end, dtype=np.int64)
    out = np.full(ends.shape, np.nan, dtype=np.float64)
    if len(ends) == 0:
        return out
    sign = np.sign(close - open_)
    up = np.where(sign > 0.0, quote, 0.0)
    down = np.where(sign < 0.0, quote, 0.0)
    up_c = np.empty(len(quote) + 1, dtype=np.float64)
    down_c = np.empty(len(quote) + 1, dtype=np.float64)
    up_c[0] = 0.0
    down_c[0] = 0.0
    np.cumsum(up, out=up_c[1:])
    np.cumsum(down, out=down_c[1:])
    start = ends + 1 - width
    ok = (start >= 0) & (ends < len(quote))
    up_sum = up_c[ends[ok] + 1] - up_c[start[ok]]
    down_sum = down_c[ends[ok] + 1] - down_c[start[ok]]
    total = up_sum + down_sum
    good = total > 0.0
    filled = out[ok]
    filled[good] = up_sum[good] / total[good]
    out[ok] = filled
    return out


def flow_names(share: np.ndarray, *, buy: float = BUY_SHARE) -> np.ndarray:
    """Buy, sell, or between. Sell is the mirror of ``buy``."""
    values = np.asarray(share, dtype=np.float64)
    names = np.full(values.shape, "tussen", dtype=object)
    finite = np.isfinite(values)
    names[~finite] = "onbekend"
    names[finite & (values >= buy)] = "koop"
    names[finite & (values <= 1.0 - buy)] = "verkoop"
    return names


def measure_clock(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    market: np.ndarray,
    *,
    start_ms: int,
) -> Clock:
    """One coin's 8-hour clock. ``market`` is the all-coin quote by slot."""
    n = len(ts)
    stop = n - HOLD_24H
    if stop <= QUOTE_BARS:
        return empty_clock()
    entries = np.arange(QUOTE_BARS, stop, NULL_STRIDE)
    gap_free = ts[entries + HOLD_24H] - ts[entries] == HOLD_24H * BAR_MS
    entries = entries[gap_free]
    if len(entries) == 0:
        return empty_clock()
    end = entries - 1
    quote = close * volume
    means = sliding_window_view(quote, QUOTE_BARS)[end - (QUOTE_BARS - 1)].mean(axis=1)
    keep = (means >= MIN_QUOTE) & (open_[entries] > 0.0)
    entries = entries[keep]
    end = entries - 1
    if len(entries) == 0:
        return empty_clock()
    entry_px = open_[entries]
    return Clock(
        ms=ts[entries],
        share_2h=buy_share(open_, close, quote, end, width=FLOW_2H),
        share_24h=buy_share(open_, close, quote, end, width=FLOW_24H),
        market=_market_at(ts, end, market, start_ms),
        rise_4h=_forward_high(high, entries, HOLD_4H) / entry_px - 1.0,
        rise_24h=_forward_high(high, entries, HOLD_24H) / entry_px - 1.0,
        net_4h=net_long(entry_px, open_[entries + HOLD_4H]),
        net_24h=net_long(entry_px, open_[entries + HOLD_24H]),
    )


def _forward_high(high: np.ndarray, entries: np.ndarray, horizon: int) -> np.ndarray:
    """Max high of the ``horizon`` bars that start at the entry, entry included."""
    view = sliding_window_view(high, horizon)
    return view[entries].max(axis=1)


def _market_at(ts: np.ndarray, end: np.ndarray, market: np.ndarray, start_ms: int) -> np.ndarray:
    slot = (ts[end] - start_ms) // BAR_MS
    inside = (slot >= 0) & (slot < len(market))
    out = np.full(len(end), np.nan, dtype=np.float64)
    if inside.any():
        out[inside] = volume_ratio(market, slot[inside])
    return out


def trade_stats(rise: np.ndarray, net: np.ndarray, *, level: float = RISE_3) -> dict[str, float]:
    """How often the high clears ``level``, and what the long earned."""
    if len(rise) == 0:
        return {"n": 0.0}
    return {
        "n": float(len(rise)),
        "p_rise": float(np.mean(rise >= level - 1e-15)),
        "mean_net": float(np.mean(net)),
        "median_net": float(np.median(net)),
    }


def signal_rate(mask: np.ndarray, risers: np.ndarray) -> tuple[float, float]:
    """Share of risers with the signal, and share of the rest with it."""
    if len(mask) == 0 or not risers.any() or risers.all():
        return float("nan"), float("nan")
    return float(np.mean(mask[risers])), float(np.mean(mask[~risers]))


def market_bin_names(ratio: np.ndarray) -> np.ndarray:
    return np.array([volume_bin(float(value)) for value in ratio], dtype=object)
