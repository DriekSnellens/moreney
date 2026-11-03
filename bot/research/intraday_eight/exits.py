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


def resting_limit(
    open_: Array,
    high: Array,
    low: Array,
    close: Array,
    start: int,
    fill_last: int,
    manage_last: int,
    limit: float,
    stop: float,
    side: int,
    target: Array | None = None,
    signal_exit: Array | None = None,
) -> tuple[int, float, int, float, str] | None:
    """Rest a limit for bars ``[start, fill_last)``. Then hold it to ``manage_last``.

    A long limit fills at the limit, or at the open when the open is already
    through it. The same bar stops out if it also trades the stop. A target
    on that bar fills only when the stop does not.
    """
    n = len(open_)
    fill_end = min(fill_last, n)
    manage_end = min(manage_last, n)
    held: tuple[int, float] | None = None
    for j in range(start, manage_end):
        offer = None if target is None or j >= len(target) else float(target[j])
        if not np.isfinite(offer or 0.0):
            offer = None
        if held is None:
            if j >= fill_end:
                return None
            filled = _limit_fill(open_[j], high[j], low[j], limit, stop, side)
            if filled is None:
                continue
            entry_px, stopped, stop_px = filled
            if stopped:
                return j, entry_px, j, stop_px, "stop"
            if _target_hit(open_[j], high[j], low[j], offer, entry_px, side):
                return j, entry_px, j, _target_px(open_[j], offer, side), "target"
            exited = _signal_exit(open_, signal_exit, j, j, entry_px, manage_last)
            if exited is not None:
                return exited
            held = (j, entry_px)
            continue
        entry_i, entry_px = held
        if _through_stop(open_[j], high[j], low[j], stop, side):
            return entry_i, entry_px, j, _stop_px(open_[j], stop, side), "stop"
        if _target_hit(open_[j], high[j], low[j], offer, entry_px, side):
            return entry_i, entry_px, j, _target_px(open_[j], offer, side), "target"
        exited = _signal_exit(open_, signal_exit, j, entry_i, entry_px, manage_last)
        if exited is not None:
            return exited
    if held is None:
        return None
    if manage_last < n:
        return held[0], held[1], manage_last, float(open_[manage_last]), "time"
    return held[0], held[1], n - 1, float(close[n - 1]), "eod"


def _signal_exit(
    open_: Array,
    signal_exit: Array | None,
    j: int,
    entry_i: int,
    entry_px: float,
    manage_last: int,
) -> tuple[int, float, int, float, str] | None:
    """A cross known at this close sells on the next open."""
    if signal_exit is None or j >= len(signal_exit) or not bool(signal_exit[j]):
        return None
    nxt = j + 1
    if nxt < len(open_) and nxt <= manage_last:
        return entry_i, entry_px, nxt, float(open_[nxt]), "signal"
    return None


def _limit_fill(
    open_: float, high: float, low: float, limit: float, stop: float, side: int
) -> tuple[float, bool, float] | None:
    if side > 0:
        if low > limit and open_ > limit:
            return None
        entry = open_ if open_ < limit else limit
        if low <= stop or entry <= stop:
            return entry, True, open_ if open_ <= stop else stop
        return entry, False, stop
    if high < limit and open_ < limit:
        return None
    entry = open_ if open_ > limit else limit
    if high >= stop or entry >= stop:
        return entry, True, open_ if open_ >= stop else stop
    return entry, False, stop


def _through_stop(open_: float, high: float, low: float, stop: float, side: int) -> bool:
    if side > 0:
        return open_ <= stop or low <= stop
    return open_ >= stop or high >= stop


def _stop_px(open_: float, stop: float, side: int) -> float:
    if side > 0:
        return open_ if open_ <= stop else stop
    return open_ if open_ >= stop else stop


def _target_hit(
    open_: float, high: float, low: float, offer: float | None, entry: float, side: int
) -> bool:
    if offer is None:
        return False
    if side > 0:
        return offer > entry and (open_ >= offer or high >= offer)
    return offer < entry and (open_ <= offer or low <= offer)


def _target_px(open_: float, offer: float | None, side: int) -> float:
    assert offer is not None
    if side > 0:
        return open_ if open_ >= offer else offer
    return open_ if open_ <= offer else offer
