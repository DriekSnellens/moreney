"""Eight locked intraday scans. One position per coin at a time.

A scan that assumes the trade was taken will not emit another signal
for that coin until the trade is over. The book may still skip it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bot.research.intraday_eight.exits import resting_limit, walk_exit
from bot.research.intraday_eight.indicators import (
    adx,
    atr,
    bollinger,
    day_position,
    ema,
    macd,
    prev,
    quote_adv,
    rolling_extreme,
    rsi,
    session_vwap,
    sma,
    supertrend,
)

WARMUP = 250
ADX_TREND = 25.0
ATR_STOP = 1.5
TRAIL_ATR = 3.0
ORB_BARS = 2
SQUEEZE_LOOKBACK = 120
FUNDING_Z = 90
FUNDING_EXTREME = -2.0


@dataclass(frozen=True)
class Trade:
    base: str
    side: int
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    adv: float
    score: float
    reason: str
    funding_sum: float = 0.0
    entry_kind: str = "taker"
    exit_kind: str = "taker"


def _day_bounds(ts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    day = ts // 86_400_000
    cuts = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(ts)]))
    nxt = np.empty(len(ts), dtype=int)
    for start, end in zip(starts, ends, strict=False):
        nxt[start:end] = end
    return day, nxt


def _emit(
    base: str,
    ts: np.ndarray,
    side: int,
    entry: int,
    fill_i: int,
    entry_px: float,
    exit_px: float,
    adv: float,
    score: float,
    reason: str,
    funding_sum: float = 0.0,
    entry_kind: str = "taker",
    exit_kind: str | None = None,
) -> Trade | None:
    if entry_px <= 0 or exit_px <= 0 or not np.isfinite(adv) or adv <= 0:
        return None
    if fill_i < entry:
        return None
    if exit_kind is None:
        exit_kind = "maker" if reason == "target" else "taker"
    return Trade(
        base=base,
        side=side,
        entry_ts=int(ts[entry]),
        exit_ts=int(ts[fill_i]),
        entry_px=float(entry_px),
        exit_px=float(exit_px),
        adv=float(adv),
        score=float(score),
        reason=reason,
        funding_sum=float(funding_sum),
        entry_kind=entry_kind,
        exit_kind=exit_kind,
    )


def vwap_mean_reversion(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """Bid the lower band. Offer the VWAP. Skip strong trends.

    The bid is the band known at the close. It is live on the next bar only,
    so a bar that merely closes below the band is not bought at the next open.
    """
    vwap, std, _day = session_vwap(ts, high, low, close, volume)
    strength = rsi(close)
    trend = adx(high, low, close)
    pos = day_position(ts)
    swing = rolling_extreme(low, 8, highest=False)
    offer = prev(vwap)
    _day, nxt = _day_bounds(ts)
    adv = quote_adv(ts, close, volume)
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        band = float(vwap[i] - 2.0 * std[i])
        entry = i + 1
        tradable = (
            pos[i] >= 8
            and np.isfinite(band)
            and std[i] > 0
            and np.isfinite(strength[i])
            and strength[i] < 30
            and np.isfinite(trend[i])
            and trend[i] <= ADX_TREND
            and np.isfinite(swing[i])
            and float(swing[i]) < band
            and entry < n
            and int(nxt[i]) > entry
        )
        if not tradable:
            i += 1
            continue
        managed = resting_limit(
            open_, high, low, close, entry, entry + 1, int(nxt[entry]),
            band, float(swing[i]), 1, offer, None,
        )
        if managed is None:
            i += 1
            continue
        entry_i, entry_px, fill_i, price, reason = managed
        trade = _emit(
            base, ts, 1, entry_i, fill_i, entry_px, price, float(adv[i]),
            float(vwap[i] - band), reason, entry_kind="maker",
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def ema_pullback(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """After the trend and the RSI turn are closed, bid the EMA 9.

    The next bar has to trade back to that EMA. The stop sits 1.5 ATR under
    the bid. A cross of EMA 9 through EMA 21 sells on the next open.
    """
    fast, slow, anchor = ema(close, 9), ema(close, 21), ema(close, 200)
    strength = rsi(close)
    width = atr(high, low, close)
    was = prev(strength)
    was2 = prev(strength, 2)
    turn_up = (strength > was) & (was <= was2) & (strength < 55)
    turn_dn = (strength < was) & (was >= was2) & (strength > 45)
    fast_was, slow_was = prev(fast), prev(slow)
    lose_long = (fast < slow) & (fast_was >= slow_was)
    lose_short = (fast > slow) & (fast_was <= slow_was)
    adv = quote_adv(ts, close, volume)
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        side = 0
        if close[i] > anchor[i] and fast[i] > slow[i] and bool(turn_up[i]):
            side = 1
        elif close[i] < anchor[i] and fast[i] < slow[i] and bool(turn_dn[i]):
            side = -1
        entry = i + 1
        if side == 0 or not np.isfinite(width[i]) or width[i] <= 0 or entry >= n:
            i += 1
            continue
        limit = float(fast[i])
        stop = float(limit - side * ATR_STOP * width[i])
        if side > 0 and not stop < limit:
            i += 1
            continue
        if side < 0 and not stop > limit:
            i += 1
            continue
        signal = lose_long if side > 0 else lose_short
        managed = resting_limit(
            open_, high, low, close, entry, entry + 1, n,
            limit, stop, side, None, signal,
        )
        if managed is None:
            i += 1
            continue
        entry_i, entry_px, fill_i, price, reason = managed
        trade = _emit(
            base, ts, side, entry_i, fill_i, entry_px, price, float(adv[i]),
            float(abs(close[i] - limit)), reason, entry_kind="maker",
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def _stop_signals(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    adv: np.ndarray,
    width: np.ndarray,
    long: np.ndarray,
    short: np.ndarray,
    exit_long: np.ndarray,
    exit_short: np.ndarray,
) -> list[Trade]:
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        side = 1 if bool(long[i]) else -1 if bool(short[i]) else 0
        entry = i + 1
        if side == 0 or not np.isfinite(width[i]) or width[i] <= 0 or entry >= n:
            i += 1
            continue
        stop = float(open_[entry] - side * ATR_STOP * width[i])
        if side > 0 and not stop < open_[entry]:
            i += 1
            continue
        if side < 0 and not stop > open_[entry]:
            i += 1
            continue
        signal = exit_long if side > 0 else exit_short
        fill_i, price, reason = walk_exit(
            open_, high, low, close, entry, stop, None, n, side, signal
        )
        trade = _emit(
            base, ts, side, entry, fill_i, float(open_[entry]), price, float(adv[i]),
            float(abs(close[i] - open_[entry])), reason,
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def _prior_day_range(
    ts: np.ndarray, high: np.ndarray, low: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    day = ts // 86_400_000
    cuts = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(ts)]))
    prior_high = np.full(len(ts), np.nan)
    prior_low = np.full(len(ts), np.nan)
    yesterday_high = np.nan
    yesterday_low = np.nan
    for start, end in zip(starts, ends, strict=False):
        prior_high[start:end] = yesterday_high
        prior_low[start:end] = yesterday_low
        yesterday_high = float(np.max(high[start:end]))
        yesterday_low = float(np.min(low[start:end]))
    return prior_high, prior_low


def liquidity_sweep(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """A wick through the prior UTC day's high or low that closes back inside."""
    prior_high, prior_low = _prior_day_range(ts, high, low)
    adv = quote_adv(ts, close, volume)
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        swept_low = (
            np.isfinite(prior_low[i])
            and np.isfinite(prior_high[i])
            and low[i] < prior_low[i]
            and close[i] > prior_low[i]
            and close[i] < prior_high[i]
        )
        swept_high = (
            np.isfinite(prior_low[i])
            and np.isfinite(prior_high[i])
            and high[i] > prior_high[i]
            and close[i] < prior_high[i]
            and close[i] > prior_low[i]
        )
        entry = i + 1
        if entry >= n or (swept_low and swept_high) or not (swept_low or swept_high):
            i += 1
            continue
        side = 1 if swept_low else -1
        stop = float(low[i] if side > 0 else high[i])
        target = float(prior_high[i] if side > 0 else prior_low[i])
        entry_px = float(open_[entry])
        if side > 0 and not stop < entry_px < target:
            i += 1
            continue
        if side < 0 and not target < entry_px < stop:
            i += 1
            continue
        fill_i, price, reason = walk_exit(
            open_, high, low, close, entry, stop, target, min(n, entry + 96), side, None
        )
        trade = _emit(
            base, ts, side, entry, fill_i, entry_px, price, float(adv[i]),
            float(abs(entry_px - stop)), reason,
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def opening_range_breakout(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """Break of the first 30 minutes of the UTC day, with a volume filter."""
    width = atr(high, low, close)
    average = sma(volume, 20)
    adv = quote_adv(ts, close, volume)
    _day, nxt = _day_bounds(ts)
    pos = day_position(ts)
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        if int(pos[i]) != 0:
            i += 1
            continue
        end = int(nxt[i])
        if end - i <= ORB_BARS:
            i = max(end, i + 1)
            continue
        range_high = float(max(high[i], high[i + 1]))
        range_low = float(min(low[i], low[i + 1]))
        taken = False
        k = i + ORB_BARS
        while k < end - 1:
            vol_ok = np.isfinite(average[k]) and volume[k] > average[k]
            side = 0
            if vol_ok and close[k] > range_high:
                side = 1
            elif vol_ok and close[k] < range_low:
                side = -1
            entry = k + 1
            if side == 0 or not np.isfinite(width[k]) or width[k] <= 0 or entry >= end:
                k += 1
                continue
            stop = float(open_[entry] - side * ATR_STOP * width[k])
            fill_i, price, reason = walk_exit(
                open_, high, low, close, entry, stop, None, end, side, None
            )
            trade = _emit(
                base, ts, side, entry, fill_i, float(open_[entry]), price, float(adv[k]),
                float(abs(close[k] - (range_high if side > 0 else range_low))), reason,
            )
            if trade is not None:
                out.append(trade)
                taken = True
                i = fill_i + 1
                break
            k += 1
        if not taken:
            i = end
    return out


def bollinger_squeeze(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """Expansion out of a 120-bar bandwidth low, with volume and RSI."""
    mid, upper, lower, width = bollinger(close)
    lowest = rolling_extreme(width, SQUEEZE_LOOKBACK, highest=False)
    is_low = np.isfinite(width) & np.isfinite(lowest) & (width <= lowest)
    recent = np.convolve(is_low.astype(float), np.ones(10), mode="full")[: len(close)] > 0
    expanding = width > prev(width)
    average = sma(volume, 20)
    strength = rsi(close)
    vol_ok = np.isfinite(average) & (volume > average)
    long = recent & expanding & vol_ok & (close > upper) & (strength > 50)
    short = recent & expanding & vol_ok & (close < lower) & (strength < 50)
    exit_long = close < mid
    exit_short = close > mid
    return _stop_signals(
        base, ts, open_, high, low, close, quote_adv(ts, close, volume),
        atr(high, low, close), long, short, exit_long, exit_short,
    )


def rsi_macd(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """RSI above 50, a bullish MACD cross, and above-average volume."""
    line, signal = macd(close)
    cross_up = (line > signal) & (prev(line) <= prev(signal))
    cross_dn = (line < signal) & (prev(line) >= prev(signal))
    strength = rsi(close)
    average = sma(volume, 20)
    width = atr(high, low, close)
    adv = quote_adv(ts, close, volume)
    long = cross_up & (strength > 50) & np.isfinite(average) & (volume > average)
    return _trail_signals(
        base, ts, open_, high, low, close, adv, width, long, None, cross_dn, None
    )


def supertrend_adx(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> list[Trade]:
    """Supertrend flip while ADX is above 25. The line is the stop."""
    direction, line = supertrend(high, low, close)
    trend = adx(high, low, close)
    adv = quote_adv(ts, close, volume)
    was = prev(direction)
    flip_up = (direction > 0) & (was < 0) & (trend > ADX_TREND)
    flip_dn = (direction < 0) & (was > 0) & (trend > ADX_TREND)
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        side = 1 if bool(flip_up[i]) else -1 if bool(flip_dn[i]) else 0
        entry = i + 1
        if side == 0 or entry >= n or not np.isfinite(line[i]):
            i += 1
            continue
        fill_i, price, reason = _supertrend_exit(
            open_, high, low, close, direction, line, entry, side
        )
        trade = _emit(
            base, ts, side, entry, fill_i, float(open_[entry]), price, float(adv[i]),
            float(trend[i]), reason,
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def _supertrend_exit(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: np.ndarray,
    line: np.ndarray,
    entry: int,
    side: int,
) -> tuple[int, float, str]:
    n = len(close)
    for j in range(entry, n):
        level = float(line[j - 1])
        if not np.isfinite(level):
            continue
        if side > 0:
            if open_[j] <= level:
                return j, float(open_[j]), "gap"
            if low[j] <= level:
                return j, level, "stop"
            if direction[j] < 0 and j + 1 < n:
                return j + 1, float(open_[j + 1]), "signal"
        else:
            if open_[j] >= level:
                return j, float(open_[j]), "gap"
            if high[j] >= level:
                return j, level, "stop"
            if direction[j] > 0 and j + 1 < n:
                return j + 1, float(open_[j + 1]), "signal"
    return n - 1, float(close[n - 1]), "eod"


def _trail_signals(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    adv: np.ndarray,
    width: np.ndarray,
    long: np.ndarray | None,
    short: np.ndarray | None,
    exit_long: np.ndarray | None,
    exit_short: np.ndarray | None,
    funding_at: np.ndarray | None = None,
    funding_rate: np.ndarray | None = None,
) -> list[Trade]:
    out: list[Trade] = []
    i = WARMUP
    n = len(close)
    while i < n - 1:
        side = 0
        if long is not None and bool(long[i]):
            side = 1
        elif short is not None and bool(short[i]):
            side = -1
        entry = i + 1
        if side == 0 or entry >= n or not np.isfinite(width[i]):
            i += 1
            continue
        fill_i, price, reason = _trail_exit(
            open_, high, low, close, width, entry, side,
            exit_long if side > 0 else exit_short,
        )
        fund = 0.0
        if funding_at is not None and funding_rate is not None:
            fund = _funding_between(funding_at, funding_rate, int(ts[entry]), int(ts[fill_i]))
        trade = _emit(
            base, ts, side, entry, fill_i, float(open_[entry]), price, float(adv[i]),
            float(abs(width[i])), reason, fund,
        )
        if trade is None:
            i += 1
            continue
        out.append(trade)
        i = fill_i + 1
    return out


def _trail_exit(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    width: np.ndarray,
    entry: int,
    side: int,
    signal_exit: np.ndarray | None,
) -> tuple[int, float, str]:
    n = len(close)
    peak = float(close[entry - 1])
    trough = peak
    for j in range(entry, n):
        span = float(width[j - 1]) if np.isfinite(width[j - 1]) else np.nan
        if side > 0 and np.isfinite(span):
            level = peak - TRAIL_ATR * span
            if open_[j] <= level:
                return j, float(open_[j]), "gap"
            if low[j] <= level:
                return j, level, "stop"
        elif side < 0 and np.isfinite(span):
            level = trough + TRAIL_ATR * span
            if open_[j] >= level:
                return j, float(open_[j]), "gap"
            if high[j] >= level:
                return j, level, "stop"
        if signal_exit is not None and bool(signal_exit[j]) and j + 1 < n:
            return j + 1, float(open_[j + 1]), "signal"
        peak = max(peak, float(close[j]))
        trough = min(trough, float(close[j]))
    return n - 1, float(close[n - 1]), "eod"


def funding_momentum(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    fund_ts: np.ndarray,
    fund_rate: np.ndarray,
) -> list[Trade]:
    """Extreme negative funding only after price has turned up. Not a blind fade."""
    if len(fund_rate) < FUNDING_Z:
        return []
    z = np.full(len(fund_rate), np.nan)
    for i in range(FUNDING_Z - 1, len(fund_rate)):
        window = fund_rate[i - FUNDING_Z + 1 : i + 1]
        scale = float(np.std(window))
        if scale > 0:
            z[i] = (float(fund_rate[i]) - float(np.mean(window))) / scale
    idx = np.searchsorted(fund_ts, ts, side="right") - 1
    mapped = np.full(len(ts), np.nan)
    ok = idx >= 0
    mapped[ok] = z[np.clip(idx[ok], 0, len(z) - 1)]
    slow = ema(close, 21)
    strength = rsi(close)
    long = (mapped < FUNDING_EXTREME) & (close > slow) & (strength > 50)
    calm = mapped > 0
    return _trail_signals(
        base, ts, open_, high, low, close, quote_adv(ts, close, volume),
        atr(high, low, close), long, None, calm, None, fund_ts, fund_rate,
    )


def _funding_between(fund_ts: np.ndarray, fund_rate: np.ndarray, start: int, end: int) -> float:
    lo = int(np.searchsorted(fund_ts, start, side="right"))
    hi = int(np.searchsorted(fund_ts, end, side="right"))
    if hi <= lo:
        return 0.0
    return float(np.sum(fund_rate[lo:hi]))


SCANNERS = {
    "vwap_mr": vwap_mean_reversion,
    "ema_pullback": ema_pullback,
    "sweep": liquidity_sweep,
    "orb": opening_range_breakout,
    "squeeze": bollinger_squeeze,
    "rsi_macd": rsi_macd,
    "supertrend": supertrend_adx,
}
