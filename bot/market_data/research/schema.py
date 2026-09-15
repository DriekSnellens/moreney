"""Versioned immutable research market-data event schema."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any


class TimestampQuality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNSUPPORTED = "UNSUPPORTED"


class SyncQuality(StrEnum):
    EXACT = "EXACT"
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal

    def as_dict(self) -> dict[str, str]:
        return {"price": str(self.price), "quantity": str(self.quantity)}


@dataclass(frozen=True, slots=True)
class ResearchMarketEvent:
    """Immutable research event — dual clocks, never invent exchange_ts."""

    schema_version: str
    event_id: str
    venue: str
    symbol: str
    channel: str  # book_snapshot | book_update | tick
    exchange_ts_ns: int | None  # null if venue did not provide
    received_ts_ns: int
    local_monotonic_ns: int
    sequence_number: int | None
    bid_price: Decimal | None
    bid_size: Decimal | None
    ask_price: Decimal | None
    ask_size: Decimal | None
    bid_levels: tuple[DepthLevel, ...] = ()
    ask_levels: tuple[DepthLevel, ...] = ()
    source: str = "websocket"
    connection_id: str | None = None
    book_age_ms: float | None = None
    receive_latency_ms: float | None = None
    crossed_book: bool = False
    locked_book: bool = False
    stale: bool = False
    timestamp_quality: str = TimestampQuality.UNSUPPORTED.value
    exchange_ts_available: bool = False
    prev_sequence: int | None = None
    is_snapshot: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = str(v)
        d["bid_levels"] = [lvl.as_dict() if hasattr(lvl, "as_dict") else lvl for lvl in self.bid_levels]
        d["ask_levels"] = [lvl.as_dict() if hasattr(lvl, "as_dict") else lvl for lvl in self.ask_levels]
        # asdict already converted DepthLevel to dict with Decimal — normalize
        def _norm_levels(levels: list[Any]) -> list[dict[str, str]]:
            out = []
            for lvl in levels:
                if isinstance(lvl, dict):
                    out.append(
                        {
                            "price": str(lvl.get("price")),
                            "quantity": str(lvl.get("quantity") or lvl.get("amount") or 0),
                        }
                    )
                else:
                    out.append(lvl.as_dict())
            return out

        d["bid_levels"] = _norm_levels(list(d.get("bid_levels") or []))
        d["ask_levels"] = _norm_levels(list(d.get("ask_levels") or []))
        d["notes"] = list(self.notes)
        return d


def datetime_to_ns(dt: Any) -> int | None:
    if dt is None:
        return None
    try:
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:
        return None


def now_received_ns() -> tuple[int, int]:
    return time.time_ns(), time.monotonic_ns()


def new_event_id() -> str:
    return str(uuid.uuid4())
