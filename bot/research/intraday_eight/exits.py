"""Fill a stop, a target, or a close signal after the entry bar.

On a bar that trades both the stop and the target, the stop fills.
A bar that opens through a level fills at the open.
"""

from __future__ import annotations

import numpy as np

Array = np.ndarray


def walk_exit(
    open_: Array,
    high: Array,
    low: Array,
    close: Array,
    start: int,
    stop: float,
    target: float | None,
    last: int,
    side: int,
    signal_exit: Array | None = None,
) -> tuple[int, float, str]:
    """Return ``(fill_index, raw_price, reason)``.

    ``last`` is the flatten bar. Bars before it are watched. ``last`` equal
    to the length of the series means there is no time flatten.
    """
    n = len(open_)
    end = min(last, n)
    for j in range(start, end):
        if side > 0:
            if open_[j] <= stop:
                return j, float(open_[j]), "gap"
            if low[j] <= stop:
                return j, float(stop), "stop"
            if target is not None and open_[j] >= target:
                return j, float(open_[j]), "target"
            if target is not None and high[j] >= target:
                return j, float(target), "target"
        else:
            if open_[j] >= stop:
                return j, float(open_[j]), "gap"
            if high[j] >= stop:
                return j, float(stop), "stop"
            if target is not None and open_[j] <= target:
                return j, float(open_[j]), "target"
            if target is not None and low[j] <= target:
                return j, float(target), "target"
        if signal_exit is not None and j < len(signal_exit) and bool(signal_exit[j]):
            nxt = j + 1
            if nxt < n and nxt <= last:
                return nxt, float(open_[nxt]), "signal"
    if last < n:
        return last, float(open_[last]), "time"
    return n - 1, float(close[n - 1]), "eod"
