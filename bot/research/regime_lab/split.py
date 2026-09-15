"""Fresh chronological split after the forensic period."""

from __future__ import annotations

from typing import Any

from bot.market_data.research.chrono_split import chronological_split
from bot.research.regime_lab.protocol import FORENSIC_OOS_END_NS, MIN_FRESH_SECONDS
from bot.research.tournament.tape_index import TapeIndex


def fresh_bounds(index: TapeIndex) -> tuple[int | None, int | None]:
    """Labeled research starts strictly after forensic OOS end."""
    start = None
    end = None
    cut = int(FORENSIC_OOS_END_NS)
    for pts in index.series.values():
        for p in pts:
            if p.ts_ns <= cut:
                continue
            if start is None or p.ts_ns < start:
                start = p.ts_ns
            if end is None or p.ts_ns > end:
                end = p.ts_ns
    return start, end


def make_fresh_split(index: TapeIndex) -> dict[str, Any]:
    start, end = fresh_bounds(index)
    if start is None or end is None:
        return {
            "available": False,
            "reason": "INSUFFICIENT_FRESH_DATA",
            "DATA_STATUS": "INSUFFICIENT_FRESH_DATA",
            "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        }
    duration_s = (end - start) / 1e9
    if duration_s < MIN_FRESH_SECONDS:
        return {
            "available": False,
            "reason": "INSUFFICIENT_FRESH_DATA",
            "DATA_STATUS": "INSUFFICIENT_FRESH_DATA",
            "fresh_duration_seconds": duration_s,
            "min_fresh_seconds": MIN_FRESH_SECONDS,
            "fresh_start_ts_ns": start,
            "fresh_end_ts_ns": end,
            "forensic_oos_end_ns": FORENSIC_OOS_END_NS,
        }
    split = chronological_split(
        start_ts_ns=start,
        end_ts_ns=end,
        content_fingerprint=index.content_fingerprint,
        dataset_id=index.dataset_id,
    )
    split["DATA_STATUS"] = "FRESH_SPLIT_READY" if split.get("available") else "INSUFFICIENT_FRESH_DATA"
    split["forensic_oos_end_ns"] = FORENSIC_OOS_END_NS
    split["fresh_duration_seconds"] = duration_s
    split["discovery_label"] = "FORENSICS"
    split["labeled_windows_exclude_ts_lte"] = FORENSIC_OOS_END_NS
    return split


def assert_event_after_forensics(ts_ns: int) -> None:
    if int(ts_ns) <= int(FORENSIC_OOS_END_NS):
        raise RuntimeError("forensic timestamp leaked into labeled fresh window")
