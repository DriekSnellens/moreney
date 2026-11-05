"""Recognition points that were still open after the earlier passes.

The small peak is already measured: within 24 hours price trades back
through it more often than through a plain recent high, and holding to
the close does not pay the taker. This pass prices the order that the
shape itself suggests, and records the path up to that sale.

The order sells when the high reaches the peak. A bar that opens through
the level fills at the open. Otherwise it fills at the level. If the
level is still untouched at the horizon, the sell is the horizon open.
The horizon is 4 hours and 24 hours. The low of the fill bar counts in
the dip, because a 15-minute bar does not say whether the low printed
first. Bars after the sell do not. The highest high over the whole 24
hours is kept as a description of the path, separate from the fill.

The null is the same clock as before: every 8 hours, the level is the
high of the prior 2 hours, same liquidity floor, no peak required.
Distance bins are locked: entry already through the level, 0–1% under,
1–2% under, and from 2% under. A higher low means the confirmation bar's
low is above the midpoint of the impulse. Coin and market volume use the
bins already locked: under 1×, 1–2×, from 2×. One joint row is a higher
low and coin volume under 1×.

The other open point is the 24-hour buy-pressure pop. Up-bar euro
turnover of at least 65% over the prior day touched +3% more often, and
the close gave it back. The scalp sells at +1% or at +2%, or at 4 hours
and at 24 hours if price never gets there. Buy, sell and the whole clock
are all reported.

Costs are the Bitvavo taker, 0.25% a side. Nothing is armed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from bot.research.rise_pattern.engine import FLOW_24H, buy_share
from bot.research.small_peak.engine import (
    BAR_MS,
    IMPULSE_BARS,
    MIN_QUOTE,
    NULL_STRIDE,
    PEAK_BARS,
    PRIMARY,
    QUOTE_BARS,
    gap_free,
    net_long,
    prepare,
    select_peaks,
    volume_ratio,
)

HOLD_4 = 16
HOLD_24 = 96
SCALP_LEVELS = (0.01, 0.02)


@dataclass(frozen=True)
class PathMark:
    entry_ms: int
    base: str
    kind: str
    discount: float
    higher_low: bool
    coin_vol: float
    market_vol: float
    touch_4: float
    touch_24: float
    bars_24: float
    mae_4: float
    mae_24: float
    mfe_24: float
    reclaim_4: float
    reclaim_24: float
    hold_4: float
    hold_24: float
    coin: str = ""
    market: str = ""


@dataclass(frozen=True)
class ScalpBook:
    entry_ms: np.ndarray
    share: np.ndarray
    hold_4: np.ndarray
    hold_24: np.ndarray
    up1_4: np.ndarray
    up1_24: np.ndarray
    up2_4: np.ndarray
    up2_24: np.ndarray
    hit1_4: np.ndarray
    hit1_24: np.ndarray
    hit2_4: np.ndarray
    hit2_24: np.ndarray

    def __len__(self) -> int:
        return int(len(self.entry_ms))


def empty_scalps() -> ScalpBook:
    blank = np.asarray([], dtype=np.float64)
    return ScalpBook(
        np.asarray([], dtype=np.int64),
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
        blank,
    )


def concat_scalps(parts: list[ScalpBook]) -> ScalpBook:
    if not parts:
        return empty_scalps()
    return ScalpBook(
        np.concatenate([part.entry_ms for part in parts]),
        np.concatenate([part.share for part in parts]),
        np.concatenate([part.hold_4 for part in parts]),
        np.concatenate([part.hold_24 for part in parts]),
        np.concatenate([part.up1_4 for part in parts]),
        np.concatenate([part.up1_24 for part in parts]),
        np.concatenate([part.up2_4 for part in parts]),
        np.concatenate([part.up2_24 for part in parts]),
        np.concatenate([part.hit1_4 for part in parts]),
        np.concatenate([part.hit1_24 for part in parts]),
        np.concatenate([part.hit2_4 for part in parts]),
        np.concatenate([part.hit2_24 for part in parts]),
    )


def discount_bin(discount: float) -> str:
    """Locked distance from the entry open up to the level being sold."""
    if not math.isfinite(discount):
        return "onbekend"
    if discount < 0.0:
        return "entry boven het niveau"
    if discount < 0.01:
        return "0–1% eronder"
    if discount < 0.02:
        return "1–2% eronder"
    return "vanaf 2% eronder"


def is_higher_low(low: np.ndarray, peak_i: int, peak_high: float) -> bool:
    """Confirmation low sits above the midpoint of the impulse into the peak."""
    impulse = float(low[peak_i - IMPULSE_BARS : peak_i].min())
    if not math.isfinite(impulse) or peak_high <= impulse:
        return False
    mid = impulse + 0.5 * (peak_high - impulse)
    return float(low[peak_i + 1]) > mid


def walk_level(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    entry: int,
    horizon: int,
    level: float,
) -> tuple[float, float, float, float]:
    """Fill, touched (1 or 0), bars until the fill, dip until the fill.

    Bars after the sell are unread. The fill bar's low counts.
    """
    entry_px = float(open_[entry])
    last = entry + horizon
    worst = entry_px
    touched = 0.0
    bars = math.nan
    fill = float(open_[last])
    for bar in range(entry, last):
        hi = float(high[bar])
        lo = float(low[bar])
        if lo < worst:
            worst = lo
        if hi < level:
            continue
        opened = float(open_[bar])
        fill = opened if opened >= level else level
        touched = 1.0
        bars = float(bar - entry)
        break
    return fill, touched, bars, worst / entry_px - 1.0


def path_mfe(high: np.ndarray, entry: int, horizon: int, entry_px: float) -> float:
    """Highest high over the hold, including bars after a limit would have sold."""
    peak = float(high[entry : entry + horizon].max())
    return peak / entry_px - 1.0


def _market_ratios(stamps: np.ndarray, market: np.ndarray, start_ms: int) -> np.ndarray:
    out = np.full(len(stamps), np.nan, dtype=np.float64)
    if len(market) == 0 or len(stamps) == 0:
        return out
    slot = (stamps.astype(np.int64) - start_ms) // BAR_MS
    inside = (slot >= 0) & (slot < len(market))
    if inside.any():
        out[inside] = volume_ratio(market, slot[inside])
    return out


def _mark_at(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    entry: int,
    level: float,
    *,
    base: str,
    kind: str,
    higher_low: bool,
    coin_vol: float,
    market_vol: float,
) -> PathMark | None:
    if not gap_free(ts, entry, HOLD_24):
        return None
    entry_px = float(open_[entry])
    if entry_px <= 0.0 or level <= 0.0:
        return None
    fill_4, touch_4, _bars_4, mae_4 = walk_level(open_, high, low, entry, HOLD_4, level)
    fill_24, touch_24, bars_24, mae_24 = walk_level(open_, high, low, entry, HOLD_24, level)
    return PathMark(
        entry_ms=int(ts[entry]),
        base=base,
        kind=kind,
        discount=level / entry_px - 1.0,
        higher_low=higher_low,
        coin_vol=coin_vol,
        market_vol=market_vol,
        touch_4=touch_4,
        touch_24=touch_24,
        bars_24=bars_24,
        mae_4=mae_4,
        mae_24=mae_24,
        mfe_24=path_mfe(high, entry, HOLD_24, entry_px),
        reclaim_4=net_long(entry_px, fill_4),
        reclaim_24=net_long(entry_px, fill_24),
        hold_4=net_long(entry_px, float(open_[entry + HOLD_4])),
        hold_24=net_long(entry_px, float(open_[entry + HOLD_24])),
    )


def _null_entries(n: int) -> np.ndarray:
    stop = n - HOLD_24
    if stop <= QUOTE_BARS:
        return np.asarray([], dtype=np.int64)
    return np.arange(QUOTE_BARS, stop, NULL_STRIDE)


def scan_paths(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    market: np.ndarray,
    *,
    start_ms: int,
) -> list[PathMark]:
    """Primary peaks and the no-peak clock, each with the level-sell path."""
    marks: list[PathMark] = []
    quote = close * volume
    prepared = prepare(high, low, close, volume)
    if prepared is not None:
        idx, rise, pullback = prepared
        chosen = select_peaks(idx, rise, pullback, PRIMARY)
        if len(chosen):
            coin_vol = volume_ratio(quote, chosen)
            tape_vol = _market_ratios(ts[chosen], market, start_ms)
            for offset, peak_i in enumerate(chosen):
                i_peak = int(peak_i)
                entry = i_peak + 2
                level = float(high[i_peak])
                mark = _mark_at(
                    ts,
                    open_,
                    high,
                    low,
                    entry,
                    level,
                    base=base,
                    kind="piek",
                    higher_low=is_higher_low(low, i_peak, level),
                    coin_vol=float(coin_vol[offset]),
                    market_vol=float(tape_vol[offset]),
                )
                if mark is not None:
                    marks.append(mark)
    entries = _null_entries(len(ts))
    if len(entries) == 0:
        return marks
    keep = ts[entries + HOLD_24] - ts[entries] == HOLD_24 * BAR_MS
    entries = entries[keep]
    if len(entries) == 0:
        return marks
    end = entries - 1
    means = sliding_window_view(quote, QUOTE_BARS)[end - (QUOTE_BARS - 1)].mean(axis=1)
    level = sliding_window_view(high, PEAK_BARS)[entries - PEAK_BARS].max(axis=1)
    keep = (means >= MIN_QUOTE) & (open_[entries] > 0.0) & (level > 0.0)
    entries = entries[keep]
    level = level[keep]
    if len(entries) == 0:
        return marks
    coin_vol = volume_ratio(quote, entries - 1)
    tape_vol = _market_ratios(ts[entries - 1], market, start_ms)
    for entry, lvl, vol, tape in zip(entries, level, coin_vol, tape_vol, strict=True):
        i_entry = int(entry)
        mark = _mark_at(
            ts,
            open_,
            high,
            low,
            i_entry,
            float(lvl),
            base=base,
            kind="null",
            higher_low=False,
            coin_vol=float(vol),
            market_vol=float(tape),
        )
        if mark is not None:
            marks.append(mark)
    return marks


def _scalp_column(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    entries: np.ndarray,
    horizon: int,
    target: float,
) -> tuple[np.ndarray, np.ndarray]:
    nets = np.empty(len(entries), dtype=np.float64)
    hits = np.empty(len(entries), dtype=np.float64)
    for pos, entry in enumerate(entries):
        i_entry = int(entry)
        level = float(open_[i_entry]) * (1.0 + target)
        fill, touched, _bars, _mae = walk_level(open_, high, low, i_entry, horizon, level)
        nets[pos] = net_long(float(open_[i_entry]), fill)
        hits[pos] = touched
    return nets, hits


def scan_scalps(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> ScalpBook:
    """8-hour clock. The signal is the prior day's up-bar share."""
    entries = _null_entries(len(ts))
    if len(entries) == 0:
        return empty_scalps()
    keep = ts[entries + HOLD_24] - ts[entries] == HOLD_24 * BAR_MS
    entries = entries[keep]
    if len(entries) == 0:
        return empty_scalps()
    quote = close * volume
    end = entries - 1
    means = sliding_window_view(quote, QUOTE_BARS)[end - (QUOTE_BARS - 1)].mean(axis=1)
    keep = (means >= MIN_QUOTE) & (open_[entries] > 0.0)
    entries = entries[keep]
    if len(entries) == 0:
        return empty_scalps()
    share = buy_share(open_, close, quote, entries - 1, width=FLOW_24H)
    entry_px = open_[entries]
    up1_4, hit1_4 = _scalp_column(open_, high, low, entries, HOLD_4, SCALP_LEVELS[0])
    up1_24, hit1_24 = _scalp_column(open_, high, low, entries, HOLD_24, SCALP_LEVELS[0])
    up2_4, hit2_4 = _scalp_column(open_, high, low, entries, HOLD_4, SCALP_LEVELS[1])
    up2_24, hit2_24 = _scalp_column(open_, high, low, entries, HOLD_24, SCALP_LEVELS[1])
    return ScalpBook(
        entry_ms=ts[entries],
        share=share,
        hold_4=net_long(entry_px, open_[entries + HOLD_4]),
        hold_24=net_long(entry_px, open_[entries + HOLD_24]),
        up1_4=up1_4,
        up1_24=up1_24,
        up2_4=up2_4,
        up2_24=up2_24,
        hit1_4=hit1_4,
        hit1_24=hit1_24,
        hit2_4=hit2_4,
        hit2_24=hit2_24,
    )


def order_stats(nets: np.ndarray) -> dict[str, float]:
    if len(nets) == 0:
        return {"n": 0.0}
    return {
        "n": float(len(nets)),
        "mean": float(np.mean(nets)),
        "median": float(np.median(nets)),
    }


def path_stats(marks: list[PathMark]) -> dict[str, float]:
    """Touch, speed, dip, path high, distance, and both sells."""
    if not marks:
        return {"n": 0.0}
    touch = np.asarray([mark.touch_24 for mark in marks], dtype=np.float64)
    bars = np.asarray([mark.bars_24 for mark in marks], dtype=np.float64)
    reached = bars[np.isfinite(bars)]
    return {
        "n": float(len(marks)),
        "touch": float(np.mean(touch)),
        "bars": float(np.median(reached)) if len(reached) else math.nan,
        "mae": float(np.median([mark.mae_24 for mark in marks])),
        "mfe": float(np.median([mark.mfe_24 for mark in marks])),
        "discount": float(np.median([mark.discount for mark in marks])),
        "reclaim_mean": float(np.mean([mark.reclaim_24 for mark in marks])),
        "reclaim_median": float(np.median([mark.reclaim_24 for mark in marks])),
        "hold_mean": float(np.mean([mark.hold_24 for mark in marks])),
        "reclaim_4_mean": float(np.mean([mark.reclaim_4 for mark in marks])),
    }
