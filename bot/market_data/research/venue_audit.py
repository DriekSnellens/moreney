"""Venue timestamp capability audit — honest, no fabricated clocks."""

from __future__ import annotations

from typing import Any

from bot.market_data.research.schema import TimestampQuality


VENUE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "binance": {
        "exchange_timestamp_available": True,
        "timestamp_resolution": "ms",
        "sequence_available": True,
        "book_mode": "incremental_diff_plus_partial_snapshot",
        "local_receive_timestamp": True,
        "server_timestamp_field": "E (depthUpdate); partial snap often local",
        "reconnect_behavior": "exponential_backoff; needs snapshot after gap",
        "ordering_guarantees": "sequence U/u on diff streams",
        "timestamp_quality": TimestampQuality.MEDIUM.value,
        "notes": (
            "depthUpdate carries exchange E; @depth20@100ms and bookTicker often local now."
        ),
    },
    "bitvavo": {
        "exchange_timestamp_available": False,
        "timestamp_resolution": None,
        "sequence_available": True,
        "book_mode": "snapshot_and_delta_nonce",
        "local_receive_timestamp": True,
        "server_timestamp_field": None,
        "reconnect_behavior": "exponential_backoff",
        "ordering_guarantees": "nonce sequence",
        "timestamp_quality": TimestampQuality.UNSUPPORTED.value,
        "notes": (
            "Adapter previously stamped books with local now and treated it as exchange_ts. "
            "Research path records exchange_ts_ns=null with timestamp_quality=UNSUPPORTED."
        ),
    },
    "okx": {
        "exchange_timestamp_available": True,
        "timestamp_resolution": "ms",
        "sequence_available": True,
        "book_mode": "snapshot_and_update_seqId",
        "local_receive_timestamp": True,
        "server_timestamp_field": "ts",
        "reconnect_behavior": "exponential_backoff",
        "ordering_guarantees": "seqId when present",
        "timestamp_quality": TimestampQuality.MEDIUM.value,
        "notes": "Books carry exchange ts when present; else local fallback.",
    },
    "kraken": {
        "exchange_timestamp_available": False,
        "timestamp_resolution": "mixed",
        "sequence_available": False,
        "book_mode": "snapshot_like",
        "local_receive_timestamp": True,
        "server_timestamp_field": "optional ISO timestamp",
        "reconnect_behavior": "exponential_backoff",
        "ordering_guarantees": "weak",
        "timestamp_quality": TimestampQuality.LOW.value,
        "notes": "Checksum unused as sequence; timestamps often local.",
    },
    "coinbase": {
        "exchange_timestamp_available": False,
        "timestamp_resolution": None,
        "sequence_available": False,
        "book_mode": "l2_local_stamp",
        "local_receive_timestamp": True,
        "server_timestamp_field": None,
        "reconnect_behavior": "exponential_backoff",
        "ordering_guarantees": "weak",
        "timestamp_quality": TimestampQuality.UNSUPPORTED.value,
        "notes": "L2 stamped local now.",
    },
    "bybit": {
        "exchange_timestamp_available": True,
        "timestamp_resolution": "ms",
        "sequence_available": False,
        "book_mode": "always_snapshot",
        "local_receive_timestamp": True,
        "server_timestamp_field": "ts/cts when present",
        "reconnect_behavior": "exponential_backoff",
        "ordering_guarantees": "weak (no seq)",
        "timestamp_quality": TimestampQuality.LOW.value,
        "notes": "May fall back to local now; sequence not used.",
    },
}


def venue_capability_report(
    venues: tuple[str, ...] = ("binance", "bitvavo", "okx"),
) -> dict[str, Any]:
    return {
        "venues": {v: VENUE_CAPABILITIES.get(v, _unknown(v)) for v in venues},
        "critical_finding": (
            "Bitvavo has no exchange event timestamp in the current feed path; "
            "sub-second cross-venue causal inference involving Bitvavo is UNSUPPORTED "
            "until the venue provides usable event clocks or research uses receive-only "
            "with explicit LOW/UNSUPPORTED quality."
        ),
        "redis_note": (
            "Redis is transport only. Research recorder must run on the publisher "
            "before Redis publish. Hydration must preserve metadata received_at / "
            "exchange_ts flags — never overwrite with poll time as if it were exchange_ts."
        ),
    }


def _unknown(venue: str) -> dict[str, Any]:
    return {
        "exchange_timestamp_available": False,
        "timestamp_quality": TimestampQuality.UNSUPPORTED.value,
        "notes": f"Unknown venue={venue}",
    }


def quality_for_venue(venue: str) -> str:
    cap = VENUE_CAPABILITIES.get(venue.lower())
    if not cap:
        return TimestampQuality.UNSUPPORTED.value
    return str(cap["timestamp_quality"])
