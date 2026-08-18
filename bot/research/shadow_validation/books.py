"""Compact L1 snapshots. Never retain full OrderBook copies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class CompactL1:
    venue: str
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    mid: float
    exchange_ts_ms: float | None
    received_ts_ms: float
    exchange_ts_available: bool
    book_age_ms: float

    def as_tuple(self) -> tuple[Any, ...]:
        return (
            self.venue,
            self.symbol,
            self.bid,
            self.ask,
            self.bid_size,
            self.ask_size,
            self.mid,
            self.exchange_ts_ms,
            self.received_ts_ms,
            self.exchange_ts_available,
            self.book_age_ms,
        )


@dataclass(slots=True, frozen=True)
class L1View:
    """Observation of a venue book: OK, MISSING, EMPTY, or INVALID."""

    status: str
    l1: CompactL1 | None

    @property
    def ok(self) -> bool:
        return self.status == "OK" and self.l1 is not None


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_to_ms(text: Any) -> float | None:
    if not text:
        return None
    if isinstance(text, datetime):
        dt = text if text.tzinfo else text.replace(tzinfo=UTC)
        return dt.timestamp() * 1000.0
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def inspect_l1(
    book: Any,
    *,
    venue: str,
    symbol: str,
    now_ms: float,
) -> L1View:
    """Top-of-book only. Distinguishes missing data from a disappeared quote."""
    if book is None:
        return L1View("MISSING", None)
    bids = getattr(book, "bids", None) or ()
    asks = getattr(book, "asks", None) or ()
    if not bids or not asks:
        return L1View("EMPTY", None)
    try:
        bid = float(bids[0].price)
        ask = float(asks[0].price)
        bid_size = float(bids[0].amount)
        ask_size = float(asks[0].amount)
    except (TypeError, ValueError, IndexError, AttributeError):
        return L1View("INVALID", None)
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return L1View("INVALID", None)
    meta = getattr(book, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    exchange_ts_available = bool(meta.get("exchange_ts_available"))
    exchange_ts_ms = _iso_to_ms(meta.get("exchange_ts")) if exchange_ts_available else None
    received_ts_ms = _iso_to_ms(meta.get("received_at"))
    if received_ts_ms is None:
        ts = getattr(book, "timestamp", None)
        received_ts_ms = _iso_to_ms(ts) or now_ms
    age = _f(getattr(book, "age_ms", None))
    if age is None:
        age = max(0.0, now_ms - received_ts_ms)
    mid = (bid + ask) * 0.5
    l1 = CompactL1(
        venue=venue,
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        mid=mid,
        exchange_ts_ms=exchange_ts_ms,
        received_ts_ms=float(received_ts_ms),
        exchange_ts_available=exchange_ts_available,
        book_age_ms=float(age),
    )
    return L1View("OK", l1)


def extract_compact_l1(
    book: Any,
    *,
    venue: str,
    symbol: str,
    now_ms: float,
) -> CompactL1 | None:
    view = inspect_l1(book, venue=venue, symbol=symbol, now_ms=now_ms)
    return view.l1
