"""Convert live MarketDataEvent → ResearchMarketEvent without inventing clocks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.market_data.models import MarketDataEvent
from bot.market_data.research import SCHEMA_VERSION
from bot.market_data.research.schema import (
    DepthLevel,
    ResearchMarketEvent,
    TimestampQuality,
    datetime_to_ns,
    new_event_id,
    now_received_ns,
)
from bot.market_data.research.venue_audit import VENUE_CAPABILITIES, quality_for_venue

_ZERO = Decimal("0")


def _levels(raw: list[Any], *, max_levels: int = 10) -> tuple[DepthLevel, ...]:
    out: list[DepthLevel] = []
    for lvl in raw[:max_levels]:
        price = getattr(lvl, "price", None)
        amount = getattr(lvl, "amount", None)
        if price is None:
            continue
        out.append(DepthLevel(price=Decimal(str(price)), quantity=Decimal(str(amount or 0))))
    return tuple(out)


def from_live_event(
    event: MarketDataEvent,
    *,
    max_depth_levels: int = 10,
    connection_id: str | None = None,
    stale_age_ms: float = 2000.0,
) -> ResearchMarketEvent | None:
    """Build research event. exchange_ts_ns is null when venue did not provide one."""
    if event.event_type in {"heartbeat", "control", "error"}:
        return None

    venue = event.exchange.lower()
    cap = VENUE_CAPABILITIES.get(venue) or {}
    venue_claims_exchange_ts = bool(cap.get("exchange_timestamp_available"))

    # Explicit metadata from adapter overrides capability table.
    meta = dict(event.metadata or {})
    if event.book_update is not None:
        meta.update(event.book_update.metadata or {})
    exchange_ts_flag = meta.get("exchange_ts_available")
    if exchange_ts_flag is None:
        # Bitvavo / coinbase: never treat local stamp as exchange_ts
        if venue in {"bitvavo", "coinbase"}:
            exchange_ts_available = False
        else:
            exchange_ts_available = venue_claims_exchange_ts and event.timestamp is not None
    else:
        exchange_ts_available = bool(exchange_ts_flag)

    received_ns = datetime_to_ns(event.received_at)
    mono_ns = now_received_ns()[1]
    if received_ns is None:
        received_ns, mono_ns = now_received_ns()

    exchange_ts_ns: int | None = None
    if exchange_ts_available:
        exchange_ts_ns = datetime_to_ns(event.timestamp)
        if exchange_ts_ns is None:
            exchange_ts_available = False

    quality = (
        quality_for_venue(venue)
        if exchange_ts_available
        else TimestampQuality.UNSUPPORTED.value
    )
    if meta.get("timestamp_quality"):
        quality = str(meta["timestamp_quality"])

    receive_latency_ms = None
    if exchange_ts_ns is not None and received_ns is not None:
        receive_latency_ms = (received_ns - exchange_ts_ns) / 1_000_000.0

    bid = ask = bid_sz = ask_sz = None
    bid_levels: tuple[DepthLevel, ...] = ()
    ask_levels: tuple[DepthLevel, ...] = ()
    is_snapshot = False
    prev_seq = None

    if event.book_update is not None:
        bu = event.book_update
        is_snapshot = bool(bu.is_snapshot)
        prev_seq = bu.prev_sequence
        bid_levels = _levels(list(bu.bids), max_levels=max_depth_levels)
        ask_levels = _levels(list(bu.asks), max_levels=max_depth_levels)
        if bid_levels:
            bid, bid_sz = bid_levels[0].price, bid_levels[0].quantity
        if ask_levels:
            ask, ask_sz = ask_levels[0].price, ask_levels[0].quantity
    elif event.tick is not None:
        t = event.tick
        bid, ask = t.bid, t.ask
        bid_sz, ask_sz = t.bid_size, t.ask_size

    crossed = bool(bid is not None and ask is not None and bid > ask)
    locked = bool(bid is not None and ask is not None and bid == ask)

    book_age_ms = None
    if exchange_ts_ns is not None:
        book_age_ms = max(0.0, (received_ns - exchange_ts_ns) / 1_000_000.0)
    stale = bool(book_age_ms is not None and book_age_ms > stale_age_ms)

    notes: list[str] = []
    if not exchange_ts_available:
        notes.append("exchange_ts_ns_null_no_invention")

    return ResearchMarketEvent(
        schema_version=SCHEMA_VERSION,
        event_id=new_event_id(),
        venue=venue,
        symbol=event.symbol.upper().replace("-", "").replace("/", ""),
        channel=str(event.event_type),
        exchange_ts_ns=exchange_ts_ns,
        received_ts_ns=int(received_ns),
        local_monotonic_ns=int(mono_ns),
        sequence_number=event.sequence,
        bid_price=bid,
        bid_size=bid_sz,
        ask_price=ask,
        ask_size=ask_sz,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        source="websocket",
        connection_id=connection_id,
        book_age_ms=book_age_ms,
        receive_latency_ms=receive_latency_ms,
        crossed_book=crossed,
        locked_book=locked,
        stale=stale,
        timestamp_quality=quality,
        exchange_ts_available=exchange_ts_available,
        prev_sequence=prev_seq,
        is_snapshot=is_snapshot,
        notes=tuple(notes),
    )
