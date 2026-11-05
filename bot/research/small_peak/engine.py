"""Recognise a small peak and measure how far price shoots through it.

The shape was fixed before the tape was read. A peak is small when the
rise into it is a few percent, not the move itself. It is confirmed only
once the next bar has closed back off that high, so the high is already
known. Nothing in the detector reads a later bar.

Primary shape, on 15-minute bars:

* the high is the highest of the last 8 bars (2 hours)
* the next bar does not take that high out
* the rise from the lowest low of the prior 16 bars (4 hours) is between
  1.5% and 6%
* the confirmation close is 0.4% to 2.5% under the high, and has given
  back at most 70% of that rise
* the close is still above the low the rise started from
* mean quote volume of the last day is at least €20,000
* the next signal on that coin waits 16 bars

Two neighbour shapes use the same windows and only change the rise band,
to 1–8% and to 2–4%. All three are reported. A null uses the same entry
clock without a peak: every 32nd bar, measured against the high of the
prior 2 hours.

Doorschieten is how far the later high gets past that peak:
``later high / peak high - 1``. Horizons are 4 hours, 24 hours and 7 days.
A long buys the next open and sells the horizon open, with the Bitvavo
0.25% taker on both sides. One extra order sells when price has gone 3%
through the peak, or at 24 hours if it has not.

News, when it is attached, is the AlphaI majority in the 24 hours before
the entry. Coin labels and the macro feed are separate. A headline at or
after the entry is invisible. No per-coin branch, and nothing is armed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from bot.core.venue_fees import VENUE_TAKER_FEE
from bot.research.alphai_sentiment_accuracy.score import majority_side

FEE = float(VENUE_TAKER_FEE["bitvavo"])
BAR_MS = 15 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
PEAK_BARS = 8
IMPULSE_BARS = 16
COOLDOWN = 16
QUOTE_BARS = 96
MIN_QUOTE = 20_000.0
PB_LO = 0.004
PB_HI = 0.025
PB_FRAC = 0.70
NULL_STRIDE = 32
HORIZONS: tuple[tuple[str, int], ...] = (("4u", 16), ("24u", 96), ("7d", 96 * 7))
SHOTS: tuple[float, ...] = (0.0, 0.01, 0.03, 0.06)
TARGET = 0.03
# Middle of the news archive. Locked as a calendar cut, not fit on a result.
NEWS_CUT = "2026-09-19"


@dataclass(frozen=True)
class PeakSpec:
    name: str
    rise_lo: float
    rise_hi: float


SPECS: tuple[PeakSpec, ...] = (
    PeakSpec("1,5%–6%", 0.015, 0.06),
    PeakSpec("1%–8%", 0.01, 0.08),
    PeakSpec("2%–4%", 0.02, 0.04),
)
PRIMARY = SPECS[0]


@dataclass(frozen=True)
class Peak:
    entry_ms: int
    overshoot: tuple[float, float, float]
    net: tuple[float, float, float]
    target_net: float
    coin: str = ""
    market: str = ""


def net_long(entry: float, fill: float, fee: float = FEE) -> float:
    """Buy at ``entry``, sell at ``fill``, taker on both sides."""
    return (fill * (1.0 - fee)) / (entry * (1.0 + fee)) - 1.0


def _window_stat(values: np.ndarray, width: int, end_index: np.ndarray, how: str) -> np.ndarray:
    """Max or min of ``width`` bars ending at each ``end_index`` (inclusive)."""
    view = sliding_window_view(values, width)
    picked = view[end_index - (width - 1)]
    if how == "max":
        return picked.max(axis=1)
    return picked.min(axis=1)


def prepare(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Shared, causal features. Peak index ``i``, confirmation bar ``i + 1``.

    Returns the peak indexes that stand and are liquid, with rise and pullback.
    """
    n = len(close)
    start = max(IMPULSE_BARS, QUOTE_BARS)
    if n < start + 8:
        return None
    idx = np.arange(start, n - 2)
    peak_high = high[idx]
    prior_high = _window_stat(high, PEAK_BARS, idx, "max")
    prior_low = _window_stat(low, IMPULSE_BARS, idx - 1, "min")
    quote = close * volume
    quote_view = sliding_window_view(quote, QUOTE_BARS)
    quote_mean = quote_view[(idx + 1) - (QUOTE_BARS - 1)].mean(axis=1)
    safe_low = np.where(prior_low > 0.0, prior_low, np.nan)
    rise = peak_high / safe_low - 1.0
    pullback = (peak_high - close[idx + 1]) / np.where(peak_high > 0.0, peak_high, np.nan)
    stands = (peak_high >= prior_high - 1e-12) & (high[idx + 1] < peak_high)
    held = close[idx + 1] > prior_low
    liquid = quote_mean >= MIN_QUOTE
    ok = stands & held & liquid & np.isfinite(rise) & np.isfinite(pullback)
    return idx[ok], rise[ok], pullback[ok]


def select_peaks(
    idx: np.ndarray,
    rise: np.ndarray,
    pullback: np.ndarray,
    spec: PeakSpec,
) -> np.ndarray:
    """Peak indexes of one rise band, with the cooldown applied in time order."""
    keep = (
        (rise >= spec.rise_lo)
        & (rise <= spec.rise_hi)
        & (pullback >= PB_LO)
        & (pullback <= PB_HI)
        & (pullback <= PB_FRAC * rise)
    )
    chosen: list[int] = []
    last = -(10**9)
    for peak_i in idx[keep]:
        i = int(peak_i)
        if i - last < COOLDOWN:
            continue
        chosen.append(i)
        last = i
    return np.asarray(chosen, dtype=np.int64)


def gap_free(ts: np.ndarray, entry: int, horizon: int) -> bool:
    if entry < 0 or entry + horizon >= len(ts):
        return False
    return int(ts[entry + horizon]) - int(ts[entry]) == horizon * BAR_MS


def forward_overshoot(high: np.ndarray, entry: int, horizon: int, peak: float) -> float:
    later = float(high[entry : entry + horizon].max())
    return later / peak - 1.0


def target_net(
    open_: np.ndarray,
    high: np.ndarray,
    entry: int,
    horizon: int,
    peak: float,
    *,
    extra: float = TARGET,
    fee: float = FEE,
) -> float:
    """Sell at the peak plus ``extra``, or at the horizon open."""
    level = peak * (1.0 + extra)
    last = entry + horizon
    for bar in range(entry, last):
        if float(high[bar]) < level:
            continue
        opened = float(open_[bar])
        fill = opened if opened >= level else level
        return net_long(float(open_[entry]), fill, fee)
    return net_long(float(open_[entry]), float(open_[last]), fee)


def measure_peak(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    peak_i: int,
    peak: float,
) -> Peak | None:
    """One confirmed peak. Entry is the open after the confirmation bar."""
    entry = peak_i + 2
    horizon_max = HORIZONS[-1][1]
    if not gap_free(ts, entry, horizon_max):
        return None
    if float(open_[entry]) <= 0.0 or peak <= 0.0:
        return None
    overshoot: list[float] = []
    nets: list[float] = []
    for _name, horizon in HORIZONS:
        overshoot.append(forward_overshoot(high, entry, horizon, peak))
        nets.append(net_long(float(open_[entry]), float(open_[entry + horizon])))
    return Peak(
        entry_ms=int(ts[entry]),
        overshoot=(overshoot[0], overshoot[1], overshoot[2]),
        net=(nets[0], nets[1], nets[2]),
        target_net=target_net(open_, high, entry, HORIZONS[1][1], peak),
    )


def null_indexes(n: int) -> np.ndarray:
    """Entry indexes for the no-peak baseline. The prior 8 highs are known."""
    start = max(QUOTE_BARS, PEAK_BARS) + 1
    stop = n - HORIZONS[-1][1] - 1
    if stop <= start:
        return np.asarray([], dtype=np.int64)
    return np.arange(start, stop, NULL_STRIDE)


def state_of(sides: Sequence[int], *, tie: str) -> str:
    """Bull, bear, or ``tie`` when the two sides balance. Empty is quiet."""
    directional = [int(side) for side in sides if int(side) != 0]
    if not directional:
        return "quiet"
    vote = majority_side(directional)
    if vote is None:
        return tie
    return "bull" if vote > 0 else "bear"


def labels_between(
    stamps: np.ndarray,
    sides: np.ndarray,
    entry_ms: int,
    *,
    lookback_ms: int = DAY_MS,
) -> list[int]:
    """Sides with ``entry_ms - lookback <= published < entry_ms``."""
    if len(stamps) == 0:
        return []
    hi = int(np.searchsorted(stamps, entry_ms, side="left"))
    lo = int(np.searchsorted(stamps, entry_ms - lookback_ms, side="left"))
    return [int(side) for side in sides[lo:hi]]


def null_frame(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """No-peak clock: entry ms, overshoot columns, net columns.

    The reference high is the highest high of the 8 bars before the entry.
    """
    empty_ms = np.asarray([], dtype=np.int64)
    empty = np.zeros((0, len(HORIZONS)))
    entries = null_indexes(len(ts))
    if len(entries) == 0:
        return empty_ms, empty, empty
    horizon = HORIZONS[-1][1]
    span = ts[entries + horizon] - ts[entries]
    keep = span == horizon * BAR_MS
    entries = entries[keep]
    if len(entries) == 0:
        return empty_ms, empty, empty
    ref = _window_stat(high, PEAK_BARS, entries - 1, "max")
    quote = close * volume
    quote_view = sliding_window_view(quote, QUOTE_BARS)
    quote_mean = quote_view[(entries - 1) - (QUOTE_BARS - 1)].mean(axis=1)
    keep = (quote_mean >= MIN_QUOTE) & (ref > 0.0) & (open_[entries] > 0.0)
    entries = entries[keep]
    ref = ref[keep]
    if len(entries) == 0:
        return empty_ms, empty, empty
    overshoot = np.empty((len(entries), len(HORIZONS)))
    nets = np.empty((len(entries), len(HORIZONS)))
    entry_px = open_[entries]
    for col, (_name, bars) in enumerate(HORIZONS):
        later = _window_stat(high, bars, entries + bars - 1, "max")
        overshoot[:, col] = later / ref - 1.0
        fill_px = open_[entries + bars]
        nets[:, col] = (fill_px * (1.0 - FEE)) / (entry_px * (1.0 + FEE)) - 1.0
    return ts[entries], overshoot, nets


def summarize_columns(overshoot: np.ndarray, nets: np.ndarray, col: int) -> dict[str, float]:
    """Same rates as ``summarize``, from the null frame's columns."""
    if len(overshoot) == 0:
        return {"n": 0.0}
    shot = overshoot[:, col]
    held = nets[:, col]
    shot_through = shot[shot >= -1e-15]
    out = {
        "n": float(len(shot)),
        "median_overshoot": float(np.median(shot)),
        "median_if_shot": float(np.median(shot_through)) if len(shot_through) else 0.0,
        "mean_overshoot": float(shot.mean()),
        "mean_net": float(held.mean()),
    }
    for level in SHOTS:
        out[f"p_{level}"] = float(np.mean(shot >= level - 1e-15))
    return out


def summarize(peaks: Sequence[Peak], horizon: int) -> dict[str, float]:
    """Doorschiet rates and the long's net at one horizon. ``horizon`` is 0, 1 or 2."""
    if not peaks:
        return {"n": 0.0}
    overshoot = np.asarray([peak.overshoot[horizon] for peak in peaks], dtype=np.float64)
    nets = np.asarray([peak.net[horizon] for peak in peaks], dtype=np.float64)
    shot_through = overshoot[overshoot >= -1e-15]
    out: dict[str, float] = {
        "n": float(len(peaks)),
        "median_overshoot": float(np.median(overshoot)),
        "median_if_shot": float(np.median(shot_through)) if len(shot_through) else 0.0,
        "mean_overshoot": float(overshoot.mean()),
        "mean_net": float(nets.mean()),
    }
    for shot in SHOTS:
        out[f"p_{shot}"] = float(np.mean(overshoot >= shot - 1e-15))
    if horizon == 1:
        target = np.asarray([peak.target_net for peak in peaks], dtype=np.float64)
        out["target_net"] = float(target.mean())
    return out


def attach_news(
    peaks: Sequence[Peak],
    coin_book: Mapping[str, tuple[np.ndarray, np.ndarray]],
    market: tuple[np.ndarray, np.ndarray],
    bases: Sequence[str],
) -> list[Peak]:
    """Fill coin and market state. ``bases`` aligns with ``peaks``."""
    market_ts, market_side = market
    out: list[Peak] = []
    for peak, base in zip(peaks, bases, strict=True):
        coin_ts, coin_side = coin_book.get(base, (np.asarray([]), np.asarray([])))
        coin = state_of(labels_between(coin_ts, coin_side, peak.entry_ms), tie="quiet")
        tape = state_of(labels_between(market_ts, market_side, peak.entry_ms), tie="mixed")
        out.append(
            Peak(
                peak.entry_ms,
                peak.overshoot,
                peak.net,
                peak.target_net,
                coin=coin,
                market=tape,
            )
        )
    return out
