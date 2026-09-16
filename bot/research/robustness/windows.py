"""Sequential walk-forward windows after the first frozen OOS."""

from __future__ import annotations

from typing import Any

from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_END_NS,
    FIRST_LAB_OOS_START_NS,
    MIN_COMPLETE_WINDOW_SECONDS,
    WINDOW_SECONDS,
)
from bot.research.tournament.tape_index import TapeIndex


def tape_bounds_after(index: TapeIndex, *, after_ns: int) -> tuple[int | None, int | None]:
    start = end = None
    cut = int(after_ns)
    for pts in index.series.values():
        for p in pts:
            if p.ts_ns <= cut:
                continue
            if start is None or p.ts_ns < start:
                start = p.ts_ns
            if end is None or p.ts_ns > end:
                end = p.ts_ns
    return start, end


def sequential_windows(index: TapeIndex) -> dict[str, Any]:
    """W0 = first lab OOS (historical). W1+ = new unseen tape after that end."""
    windows: list[dict[str, Any]] = [
        {
            "WINDOW_ID": "W0_FIRST_OOS",
            "kind": "historical_first_oos",
            "start_ts_ns": FIRST_LAB_OOS_START_NS,
            "end_ts_ns_inclusive": FIRST_LAB_OOS_END_NS,
            "duration_seconds": (FIRST_LAB_OOS_END_NS - FIRST_LAB_OOS_START_NS) / 1e9,
            "complete": True,
        }
    ]
    start, end = tape_bounds_after(index, after_ns=FIRST_LAB_OOS_END_NS)
    if start is None or end is None or end <= start:
        return {
            "windows": windows,
            "new_tape": False,
            "DATA_STATUS": "NO_ADDITIONAL_UNSEEN_TAPE",
            "fresh_start_ts_ns": start,
            "fresh_end_ts_ns": end,
        }
    step = int(WINDOW_SECONDS * 1e9)
    t = int(start)
    i = 1
    while t < int(end):
        w_end = min(int(t + step - 1), int(end))
        dur = (w_end - t) / 1e9
        complete = dur + 1e-9 >= MIN_COMPLETE_WINDOW_SECONDS
        windows.append(
            {
                "WINDOW_ID": f"W{i}",
                "kind": "walk_forward",
                "start_ts_ns": t,
                "end_ts_ns_inclusive": w_end,
                "duration_seconds": dur,
                "complete": complete,
            }
        )
        t = w_end + 1
        i += 1
    return {
        "windows": windows,
        "new_tape": True,
        "DATA_STATUS": "ADDITIONAL_UNSEEN_TAPE",
        "fresh_start_ts_ns": start,
        "fresh_end_ts_ns": end,
        "fresh_duration_seconds": (end - start) / 1e9,
        "n_new_complete": sum(1 for w in windows if w["kind"] == "walk_forward" and w["complete"]),
    }
