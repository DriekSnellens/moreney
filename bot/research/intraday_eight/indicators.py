"""Causal indicators on 15-minute bars. The current bar is included."""

from __future__ import annotations

from collections import deque

import numpy as np

Array = np.ndarray


def wilder(values: Array, n: int) -> Array:
    out = np.full(len(values), np.nan)
    if n <= 0 or len(values) < n:
        return out
    out[n - 1] = float(np.mean(values[:n]))
    for i in range(n, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def ema(values: Array, n: int) -> Array:
    out = np.full(len(values), np.nan)
    if n <= 0 or len(values) == 0:
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def prev(values: Array, k: int = 1) -> Array:
    """Shift forward. The first ``k`` bars are NaN, so nothing wraps around."""
    out = np.full(len(values), np.nan)
    if 0 < k < len(values):
        out[k:] = values[:-k]
    return out


def sma(values: Array, n: int) -> Array:
    out = np.full(len(values), np.nan)
    if n <= 0 or len(values) < n:
        return out
    acc = np.cumsum(values)
    out[n - 1 :] = (acc[n - 1 :] - np.concatenate(([0.0], acc[:-n]))) / n
    return out


def rolling_extreme(values: Array, n: int, *, highest: bool) -> Array:
    """Extreme of the window ending at this bar, inclusive."""
    out = np.full(len(values), np.nan)
    if n <= 0:
        return out
    window: deque[int] = deque()
    for i, value in enumerate(values):
        number = float(value)
        while window and window[0] <= i - n:
            window.popleft()
        if np.isfinite(number):
            if highest:
                while window and values[window[-1]] <= number:
                    window.pop()
            else:
                while window and values[window[-1]] >= number:
                    window.pop()
            window.append(i)
        if i >= n - 1 and window:
            out[i] = values[window[0]]
    return out


def shift_extreme(values: Array, n: int, *, highest: bool) -> Array:
    """Extreme of the previous ``n`` bars, excluding the current bar."""
    rolled = rolling_extreme(values, n, highest=highest)
    out = np.full(len(values), np.nan)
    if n < len(values):
        out[n:] = rolled[n - 1 : -1]
    return out


def true_range(high: Array, low: Array, close: Array) -> Array:
    prev = np.concatenate(([close[0]], close[:-1]))
    return np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))


def atr(high: Array, low: Array, close: Array, n: int = 14) -> Array:
    return wilder(true_range(high, low, close), n)


def rsi(close: Array, n: int = 14) -> Array:
    delta = np.diff(close, prepend=close[0])
    delta[0] = 0.0
    gain = wilder(np.clip(delta, 0.0, None), n)
    loss = wilder(np.clip(-delta, 0.0, None), n)
    out = np.full(len(close), np.nan)
    ok = np.isfinite(gain) & np.isfinite(loss)
    calm = ok & (loss == 0)
    out[calm] = 100.0
    both = ok & (loss > 0)
    rs = gain[both] / loss[both]
    out[both] = 100.0 - 100.0 / (1.0 + rs)
    return out


def macd(close: Array, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Array, Array]:
    line = ema(close, fast) - ema(close, slow)
    return line, ema(np.where(np.isfinite(line), line, 0.0), signal)


def adx(high: Array, low: Array, close: Array, n: int = 14) -> Array:
    up = np.diff(high, prepend=high[0])
    down = -np.diff(low, prepend=low[0])
    up[0] = 0.0
    down[0] = 0.0
    plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
    tr = wilder(true_range(high, low, close), n)
    plus = 100.0 * wilder(plus_dm, n) / np.where(tr > 0, tr, np.nan)
    minus = 100.0 * wilder(minus_dm, n) / np.where(tr > 0, tr, np.nan)
    denom = plus + minus
    dx = 100.0 * np.abs(plus - minus) / np.where(denom > 0, denom, np.nan)
    dx = np.where(np.isfinite(dx), dx, 0.0)
    return wilder(dx, n)


def supertrend(
    high: Array, low: Array, close: Array, n: int = 10, mult: float = 3.0
) -> tuple[Array, Array]:
    width = atr(high, low, close, n)
    basic_up = (high + low) / 2.0 + mult * width
    basic_dn = (high + low) / 2.0 - mult * width
    final_up = basic_up.copy()
    final_dn = basic_dn.copy()
    direction = np.ones(len(close))
    for i in range(1, len(close)):
        if not np.isfinite(basic_up[i]) or not np.isfinite(basic_dn[i]):
            final_up[i] = final_up[i - 1]
            final_dn[i] = final_dn[i - 1]
            direction[i] = direction[i - 1]
            continue
        up_ready = not np.isfinite(final_up[i - 1])
        if up_ready or basic_up[i] < final_up[i - 1] or close[i - 1] > final_up[i - 1]:
            final_up[i] = basic_up[i]
        else:
            final_up[i] = final_up[i - 1]
        dn_ready = not np.isfinite(final_dn[i - 1])
        if dn_ready or basic_dn[i] > final_dn[i - 1] or close[i - 1] < final_dn[i - 1]:
            final_dn[i] = basic_dn[i]
        else:
            final_dn[i] = final_dn[i - 1]
        if direction[i - 1] > 0:
            direction[i] = -1.0 if close[i] < final_dn[i] else 1.0
        else:
            direction[i] = 1.0 if close[i] > final_up[i] else -1.0
    line = np.where(direction > 0, final_dn, final_up)
    return direction, line


def bollinger(close: Array, n: int = 20, mult: float = 2.0) -> tuple[Array, Array, Array, Array]:
    mid = sma(close, n)
    sq = sma(close * close, n)
    var = np.maximum(sq - mid * mid, 0.0)
    std = np.sqrt(var)
    upper = mid + mult * std
    lower = mid - mult * std
    width = np.divide(upper - lower, mid, out=np.full(len(close), np.nan), where=mid > 0)
    return mid, upper, lower, width


def session_vwap(
    ts: Array, high: Array, low: Array, close: Array, volume: Array
) -> tuple[Array, Array, Array]:
    """UTC-day VWAP and the running standard deviation of typical price."""
    typical = (high + low + close) / 3.0
    day = ts // 86_400_000
    vwap = np.full(len(close), np.nan)
    std = np.full(len(close), np.nan)
    cuts = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(close)]))
    for start, end in zip(starts, ends, strict=False):
        vol = volume[start:end]
        price = typical[start:end]
        cum_v = np.cumsum(vol)
        cum_pv = np.cumsum(price * vol)
        slot = cum_v > 0
        vwap[start:end][slot] = cum_pv[slot] / cum_v[slot]
        count = np.arange(1, end - start + 1)
        mean = np.cumsum(price) / count
        var = np.cumsum(price * price) / count - mean * mean
        std[start:end] = np.sqrt(np.maximum(var, 0.0))
    return vwap, std, day


def day_position(ts: Array) -> Array:
    day = ts // 86_400_000
    index = np.arange(len(ts))
    change = np.empty(len(ts), dtype=bool)
    change[0] = True
    change[1:] = day[1:] != day[:-1]
    start = np.maximum.accumulate(np.where(change, index, 0))
    return index - start


def quote_adv(close: Array, volume: Array, bars: int = 20 * 96) -> Array:
    quote = close * volume
    rolled = sma(quote, bars)
    out = np.full(len(close), np.nan)
    out[1:] = rolled[:-1]
    return out


def median_daily_quote(ts: Array, close: Array, volume: Array) -> float:
    if len(ts) == 0:
        return 0.0
    day = ts // 86_400_000
    quote = close * volume
    cuts = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate(([0], cuts))
    sums = np.add.reduceat(quote, starts)
    return float(np.median(sums))
