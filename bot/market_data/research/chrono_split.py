"""Chronological DEV / FREEZE / OOS split — never shuffle, never overlap."""

from __future__ import annotations

from typing import Any, Sequence


def chronological_split(
    *,
    start_ts_ns: int | None,
    end_ts_ns: int | None,
    content_fingerprint: str,
    dataset_id: str,
    development_fraction: float = 0.60,
    freeze_fraction: float = 0.10,
) -> dict[str, Any]:
    """Split a completed dataset by receive-time span.

    DEVELOPMENT | FREEZE_BOUNDARY | UNTOUCHED_OOS
    OOS must remain untouched by feature discovery / tuning / fitting.
    """
    if start_ts_ns is None or end_ts_ns is None or end_ts_ns <= start_ts_ns:
        return {
            "available": False,
            "reason": "insufficient_time_span",
            "dataset_id": dataset_id,
            "content_fingerprint": content_fingerprint,
        }

    span = end_ts_ns - start_ts_ns
    dev_end = start_ts_ns + int(span * development_fraction)
    freeze_end = start_ts_ns + int(span * (development_fraction + freeze_fraction))
    # Ensure strict non-overlap: [start, dev_end) [dev_end, freeze_end) [freeze_end, end]
    if not (start_ts_ns < dev_end <= freeze_end <= end_ts_ns):
        return {
            "available": False,
            "reason": "degenerate_boundaries",
            "dataset_id": dataset_id,
            "content_fingerprint": content_fingerprint,
        }

    return {
        "available": True,
        "dataset_id": dataset_id,
        "content_fingerprint": content_fingerprint,
        "method": "chronological_receive_ts",
        "shuffled": False,
        "overlap_allowed": False,
        "development": {
            "label": "DEVELOPMENT",
            "start_ts_ns": start_ts_ns,
            "end_ts_ns_exclusive": dev_end,
            "fraction": development_fraction,
        },
        "freeze_boundary": {
            "label": "FREEZE_BOUNDARY",
            "start_ts_ns": dev_end,
            "end_ts_ns_exclusive": freeze_end,
            "fraction": freeze_fraction,
        },
        "untouched_oos": {
            "label": "UNTOUCHED_OOS",
            "start_ts_ns": freeze_end,
            "end_ts_ns_inclusive": end_ts_ns,
            "fraction": round(1.0 - development_fraction - freeze_fraction, 4),
            "untouched_by": [
                "feature_discovery",
                "parameter_tuning",
                "threshold_selection",
                "model_fitting",
            ],
        },
        "zero_overlap": True,
    }


def assert_zero_overlap(split: dict[str, Any]) -> bool:
    if not split.get("available"):
        return False
    d = split["development"]
    f = split["freeze_boundary"]
    o = split["untouched_oos"]
    return (
        d["start_ts_ns"] < d["end_ts_ns_exclusive"]
        == f["start_ts_ns"]
        < f["end_ts_ns_exclusive"]
        == o["start_ts_ns"]
        <= o["end_ts_ns_inclusive"]
    )


def assign_bucket(ts_ns: int, split: dict[str, Any]) -> str | None:
    if not split.get("available"):
        return None
    if ts_ns < split["development"]["end_ts_ns_exclusive"]:
        return "DEVELOPMENT"
    if ts_ns < split["freeze_boundary"]["end_ts_ns_exclusive"]:
        return "FREEZE_BOUNDARY"
    return "UNTOUCHED_OOS"
