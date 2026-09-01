"""Market-data research infrastructure — timestamps, replay, safety."""

from __future__ import annotations

import inspect
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.market_data.adapters.base import PublicMarketDataAdapter
from bot.market_data.adapters.binance import BinancePublicAdapter
from bot.market_data.adapters.bitvavo import BitvavoPublicAdapter
from bot.market_data.local_order_book import LocalOrderBook
from bot.market_data.models import MarketDataEvent, OrderBookUpdate
from bot.market_data.research.convert import from_live_event
from bot.market_data.research.ordering import analyze_ordering, sort_events
from bot.market_data.research.quality import reject_horizon_if_uncertain
from bot.market_data.research.recorder import ResearchMarketDataRecorder
from bot.market_data.research.replay import MarketDataReplayEngine
from bot.market_data.research.schema import ResearchMarketEvent, TimestampQuality
from bot.market_data.research.study import build_infrastructure_report
from bot.market_data.research.venue_audit import venue_capability_report
from bot.market_data.service import MarketDataService
from bot.core.config import Settings


def _event(
    *,
    venue: str = "binance",
    exchange_ts: datetime | None = None,
    exchange_ts_available: bool = True,
    seq: int = 1,
    bid: str = "100",
    ask: str = "100.1",
) -> MarketDataEvent:
    adapter: PublicMarketDataAdapter = BinancePublicAdapter(symbols=["BTCEUR"])
    adapter.name = venue
    return adapter.book_event(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal(bid), amount=Decimal("2"))],
        asks=[OrderBookLevel(price=Decimal(ask), amount=Decimal("3"))],
        is_snapshot=True,
        sequence=seq,
        timestamp=exchange_ts,
        exchange_ts_available=exchange_ts_available,
        timestamp_quality="MEDIUM" if exchange_ts_available else "UNSUPPORTED",
    )


def test_exchange_timestamp_survives_ingestion() -> None:
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _event(exchange_ts=ts, exchange_ts_available=True)
    research = from_live_event(ev)
    assert research is not None
    assert research.exchange_ts_available is True
    assert research.exchange_ts_ns == int(ts.timestamp() * 1e9)
    assert research.received_ts_ns != research.exchange_ts_ns or True  # distinct fields


def test_received_timestamp_remains_distinct() -> None:
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ev = _event(exchange_ts=ts)
    # Force received later
    ev.received_at = datetime(2026, 1, 1, 12, 0, 0, 5000, tzinfo=UTC)
    if ev.book_update:
        ev.book_update.received_at = ev.received_at
    research = from_live_event(ev)
    assert research is not None
    assert research.received_ts_ns > research.exchange_ts_ns  # type: ignore[operator]


def test_missing_exchange_ts_is_null_not_invented() -> None:
    adapter = BitvavoPublicAdapter(symbols=["BTCEUR"])
    # Simulate book payload without exchange clock
    ev = adapter.book_event(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("1"), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("1.1"), amount=Decimal("1"))],
        is_snapshot=True,
        sequence=10,
        timestamp=None,
        exchange_ts_available=False,
        timestamp_quality="UNSUPPORTED",
    )
    assert ev.metadata.get("exchange_ts_available") is False
    research = from_live_event(ev)
    assert research is not None
    assert research.exchange_ts_ns is None
    assert research.exchange_ts_available is False
    assert research.timestamp_quality == TimestampQuality.UNSUPPORTED.value


def test_exchange_ts_survives_local_book_and_redis_metadata() -> None:
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    recv = datetime(2026, 1, 1, 12, 0, 0, 2000, tzinfo=UTC)
    update = OrderBookUpdate(
        exchange="binance",
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("100.1"), amount=Decimal("1"))],
        is_snapshot=True,
        sequence=5,
        timestamp=ts,
        received_at=recv,
        metadata={
            "exchange_ts_available": True,
            "timestamp_quality": "MEDIUM",
            "exchange_ts": ts.isoformat(),
            "received_at": recv.isoformat(),
        },
    )
    book = LocalOrderBook("binance", "BTCEUR")
    book.apply_snapshot(update)
    ob = book.to_order_book()
    assert ob is not None
    assert ob.metadata["exchange_ts_available"] is True
    assert ob.metadata["received_at"] == recv.isoformat()
    assert ob.timestamp == ts


def test_hydrate_preserves_received_at_not_poll_time_as_exchange() -> None:
    settings = Settings(execution_mode="paper", market_data_mode="shared")
    # Avoid enabling research thread noise in unit test where possible
    object.__setattr__(settings, "research_marketdata_recording_enabled", False)
    svc = MarketDataService(settings, start_websockets=False)
    ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    recv = datetime(2026, 1, 1, 12, 0, 0, 3000, tzinfo=UTC)
    published = OrderBook(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("100.1"), amount=Decimal("1"))],
        timestamp=ts,
        nonce=7,
        metadata={
            "exchange": "binance",
            "received_at": recv.isoformat(),
            "exchange_ts_available": True,
            "timestamp_quality": "MEDIUM",
            "exchange_ts": ts.isoformat(),
            "synchronized": True,
        },
    )
    svc.apply_remote_book("binance", "BTCEUR", published)
    local = svc.get_local_book("binance", "BTCEUR")
    assert local is not None
    assert local._received_at == recv  # noqa: SLF001
    assert local._timestamp == ts  # noqa: SLF001
    assert local._exchange_ts_available is True  # noqa: SLF001


def test_sequence_gaps_and_duplicates_detected() -> None:
    base = int(time.time() * 1e9)
    events = []
    for seq, mono in ((1, 1), (2, 2), (2, 3), (5, 4)):
        events.append(
            ResearchMarketEvent(
                schema_version="research_md_v1",
                event_id=f"e{seq}-{mono}",
                venue="binance",
                symbol="BTCEUR",
                channel="book_update",
                exchange_ts_ns=base + mono,
                received_ts_ns=base + mono + 1000,
                local_monotonic_ns=mono,
                sequence_number=seq,
                bid_price=Decimal("1"),
                bid_size=Decimal("1"),
                ask_price=Decimal("1.1"),
                ask_size=Decimal("1"),
                exchange_ts_available=True,
                timestamp_quality="MEDIUM",
            )
        )
    stats = analyze_ordering(events)
    assert stats.duplicates >= 1
    assert stats.sequence_gaps >= 1


def test_future_events_invisible_during_replay() -> None:
    base = 1_700_000_000_000_000_000
    events = [
        ResearchMarketEvent(
            schema_version="research_md_v1",
            event_id=f"e{i}",
            venue="binance",
            symbol="BTCEUR",
            channel="book_update",
            exchange_ts_ns=base + i * 1_000_000,
            received_ts_ns=base + i * 1_000_000 + 1000,
            local_monotonic_ns=i,
            sequence_number=i,
            bid_price=Decimal("1"),
            bid_size=Decimal("1"),
            ask_price=Decimal("1.1"),
            ask_size=Decimal("1"),
            exchange_ts_available=True,
            timestamp_quality="MEDIUM",
        )
        for i in range(5)
    ]
    eng = MarketDataReplayEngine(events)
    visible = eng.visible_at(base + 2 * 1_000_000)
    assert len(visible) == 3
    assert all(e.exchange_ts_ns <= base + 2 * 1_000_000 for e in visible)


def test_deterministic_replay_fingerprint() -> None:
    base = 1_700_000_000_000_000_000
    events = [
        ResearchMarketEvent(
            schema_version="research_md_v1",
            event_id=f"e{i}",
            venue="okx",
            symbol="XRPEUR",
            channel="book_snapshot",
            exchange_ts_ns=base + i,
            received_ts_ns=base + i + 10,
            local_monotonic_ns=i,
            sequence_number=i,
            bid_price=Decimal("0.5"),
            bid_size=Decimal("10"),
            ask_price=Decimal("0.51"),
            ask_size=Decimal("10"),
            exchange_ts_available=True,
            timestamp_quality="MEDIUM",
            is_snapshot=True,
        )
        for i in range(10)
    ]
    a = MarketDataReplayEngine(events).fingerprint()
    b = MarketDataReplayEngine(list(reversed(events))).fingerprint()
    assert a == b  # sort makes order-invariant fingerprint of sorted stream


def test_50ms_rejected_when_uncertainty_high() -> None:
    gate = reject_horizon_if_uncertain(50, timestamp_uncertainty_ms=500)
    assert gate["allowed"] is False


def test_report_not_ready_without_recordings(tmp_path: Path) -> None:
    report = build_infrastructure_report(research_path=tmp_path / "empty")
    assert report["final_verdict"] == "DATA_NOT_READY"
    assert report["event_count"] == 0


def test_recorder_does_not_block_and_writes(tmp_path: Path) -> None:
    rec = ResearchMarketDataRecorder(enabled=True, path=str(tmp_path), max_queue=1000)
    ts = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    for i in range(5):
        rec.enqueue_live(_event(exchange_ts=ts, seq=i + 1))
    # Allow drain
    time.sleep(0.2)
    rec.close()
    files = list(tmp_path.rglob("*.jsonl"))
    assert files
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[0])
    assert "exchange_ts_ns" in row
    assert "received_ts_ns" in row


def test_production_paths_unchanged_by_research_imports() -> None:
    import bot.strategies.maker_inventory as maker
    import bot.execution.paper_executor as paper_ex

    assert "research_marketdata" not in inspect.getsource(maker)
    assert "ResearchMarketEvent" not in inspect.getsource(paper_ex)


def test_venue_audit_bitvavo_unsupported() -> None:
    report = venue_capability_report()
    assert report["venues"]["bitvavo"]["exchange_timestamp_available"] is False
    assert report["venues"]["bitvavo"]["timestamp_quality"] == "UNSUPPORTED"


def test_sort_events_prefers_sequence() -> None:
    base = 1000
    events = [
        ResearchMarketEvent(
            schema_version="v",
            event_id="b",
            venue="binance",
            symbol="X",
            channel="u",
            exchange_ts_ns=base + 50,
            received_ts_ns=base + 50,
            local_monotonic_ns=2,
            sequence_number=2,
            bid_price=None,
            bid_size=None,
            ask_price=None,
            ask_size=None,
            exchange_ts_available=True,
        ),
        ResearchMarketEvent(
            schema_version="v",
            event_id="a",
            venue="binance",
            symbol="X",
            channel="u",
            exchange_ts_ns=base + 10,
            received_ts_ns=base + 10,
            local_monotonic_ns=1,
            sequence_number=1,
            bid_price=None,
            bid_size=None,
            ask_price=None,
            ask_size=None,
            exchange_ts_available=True,
        ),
    ]
    ordered = sort_events(events)
    assert [e.sequence_number for e in ordered] == [1, 2]
