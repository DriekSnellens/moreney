"""Timestamp / data-quality audit for lead-lag research."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.opportunity.lead_lag.states import DataQuality


# Venue event-clock honesty based on known adapter behavior.
VENUE_EVENT_CLOCK: dict[str, dict[str, str]] = {
    "binance": {
        "event_ts": "exchange",
        "quality": DataQuality.MEDIUM.value,
        "note": "Depth uses exchange E; book-ticker may default local.",
    },
    "okx": {
        "event_ts": "exchange",
        "quality": DataQuality.MEDIUM.value,
        "note": "Books carry exchange ts when present.",
    },
    "bitvavo": {
        "event_ts": "local_now",
        "quality": DataQuality.LOW.value,
        "note": "Adapter stamps books with datetime.now(UTC) — not exchange event time.",
    },
    "kraken": {
        "event_ts": "mixed",
        "quality": DataQuality.LOW.value,
        "note": "Timestamp parsing mixed / often local.",
    },
    "coinbase": {
        "event_ts": "local_now",
        "quality": DataQuality.LOW.value,
        "note": "Often local stamp.",
    },
    "bybit": {
        "event_ts": "mixed",
        "quality": DataQuality.LOW.value,
        "note": "May fall back to local now.",
    },
}


def audit_timestamps(
    *,
    market_data_dir: Path | str | None = "data/market_data",
    redis_poll_ms: float = 100.0,
    venues: tuple[str, ...] = ("binance", "bitvavo", "okx"),
) -> dict[str, Any]:
    """Classify whether sub-second lead-lag inference is supportable."""
    md = Path(market_data_dir) if market_data_dir else None
    files = list(md.rglob("*.jsonl")) if md and md.exists() else []
    has_tape = any(f.is_file() for f in files)

    venue_audit = {
        v: VENUE_EVENT_CLOCK.get(
            v,
            {
                "event_ts": "unknown",
                "quality": DataQuality.UNSUPPORTED.value,
                "note": "Unknown adapter clock.",
            },
        )
        for v in venues
    }

    qualities = [v["quality"] for v in venue_audit.values()]
    if not has_tape:
        overall = DataQuality.UNSUPPORTED.value
        reason = (
            "No synchronized multi-venue book/tick tape "
            f"(market_data_dir={md}, files={len(files)})."
        )
    elif DataQuality.UNSUPPORTED.value in qualities:
        overall = DataQuality.UNSUPPORTED.value
        reason = "At least one venue lacks usable timestamps."
    elif DataQuality.LOW.value in qualities:
        overall = DataQuality.LOW.value
        reason = "One or more venues use local-now event stamps (e.g. Bitvavo)."
    else:
        overall = DataQuality.MEDIUM.value
        reason = "Tape present; clocks mixed medium quality."

    # Redis hydrate destroys receive skew for shared consumers.
    redis_notes = (
        "Redis holds latest snapshot only; hydrate resets received_at. "
        f"Shared poll ≈ {redis_poll_ms}ms. Prefer publisher-local recording."
    )

    min_resolution_ms: float | None
    if not has_tape:
        min_resolution_ms = None
    else:
        min_resolution_ms = max(redis_poll_ms, 50.0)

    return {
        "overall_quality": overall,
        "reason": reason,
        "has_synchronized_tape": has_tape,
        "market_data_files": len(files),
        "market_data_dir": str(md) if md else None,
        "venues": venue_audit,
        "dual_clock_policy": (
            "Always preserve event_timestamp and local_received_at separately; "
            "never silently substitute one for the other."
        ),
        "redis_notes": redis_notes,
        "min_resolution_ms": min_resolution_ms,
        "exchange_event_timestamps_available": any(
            v.get("event_ts") == "exchange" for v in venue_audit.values()
        ),
        "local_receive_timestamps_available": True,
        "redis_publish_timestamps_available": False,
        "polling_timestamps_available": True,
        "subsecond_lead_lag_supported": has_tape and overall in {
            DataQuality.HIGH.value,
            DataQuality.MEDIUM.value,
        },
    }
