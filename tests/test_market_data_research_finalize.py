"""Market-data research finalization — acceptance, sessions, fingerprints."""

from __future__ import annotations

import inspect
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bot.core.config import Settings
from bot.market_data.adapters.binance import BinancePublicAdapter
from bot.market_data.adapters.bitvavo import BitvavoPublicAdapter
from bot.core.exchange_types import OrderBookLevel
from bot.market_data.models import MarketDataEvent
from bot.market_data.research.acceptance import (
    PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA,
    evaluate_acceptance,
)
from bot.market_data.research.chrono_split import assert_zero_overlap, chronological_split
from bot.market_data.research.finalize import run_finalization
from bot.market_data.research.integrity import validate_tape
from bot.market_data.research.operational_state import map_acceptance_to_final
from bot.market_data.research.recorder import ResearchMarketDataRecorder
from bot.market_data.research.tape_scan import scan_tape
from bot.market_data.service import MarketDataService


def _live(venue: str = "binance", *, exchange_ts: datetime | None = None, seq: int = 1) -> MarketDataEvent:
    if venue == "bitvavo":
        adapter = BitvavoPublicAdapter(symbols=["BTCEUR"])
        return adapter.book_event(
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("100.1"), amount=Decimal("1"))],
            is_snapshot=True,
            sequence=seq,
            timestamp=None,
            exchange_ts_available=False,
            timestamp_quality="UNSUPPORTED",
        )
    adapter = BinancePublicAdapter(symbols=["BTCEUR"])
    adapter.name = venue
    return adapter.book_event(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("2"))],
        asks=[OrderBookLevel(price=Decimal("100.1"), amount=Decimal("3"))],
        is_snapshot=True,
        sequence=seq,
        timestamp=exchange_ts or datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC),
        exchange_ts_available=True,
        timestamp_quality="MEDIUM",
    )


def test_recorder_disabled_writes_nothing(tmp_path: Path) -> None:
    rec = ResearchMarketDataRecorder(enabled=False, path=str(tmp_path))
    rec.enqueue_live(_live())
    time.sleep(0.05)
    rec.close()
    assert list(tmp_path.rglob("*.jsonl")) == []
    assert rec.snapshot()["RECORDER_ENABLED"] is False


def test_recorder_enabled_session_layout_and_readback(tmp_path: Path) -> None:
    rec = ResearchMarketDataRecorder(
        enabled=True, path=str(tmp_path), max_queue=1000, flush_every=1, flush_interval_ms=5
    )
    for i in range(3):
        rec.enqueue_live(_live(seq=i + 1))
    time.sleep(0.25)
    snap = rec.snapshot()
    rec.close()
    assert snap["RECORDER_RUNNING"] or snap["EVENTS_ENQUEUED"] >= 3
    files = list(tmp_path.rglob("events.jsonl"))
    assert files, "session layout should write events.jsonl"
    assert any("session=" in str(p) for p in files)
    assert any(p.name == "metadata.json" for p in tmp_path.rglob("metadata.json"))
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[0])
    assert row["venue"] == "binance"
    assert row["received_ts_ns"] is not None
    assert row["local_monotonic_ns"] is not None


def test_production_event_enqueues_via_service(tmp_path: Path) -> None:
    settings = Settings(execution_mode="paper", market_data_mode="local")
    object.__setattr__(settings, "research_marketdata_recording_enabled", True)
    object.__setattr__(settings, "research_marketdata_recording_path", str(tmp_path))
    svc = MarketDataService(settings, start_websockets=False)
    import asyncio

    asyncio.run(svc.handle_event(_live(seq=42)))
    time.sleep(0.3)
    status = svc.research_recorder_status()
    svc._research_recorder.close()  # noqa: SLF001
    assert status["enabled"] is True
    assert status["enqueued"] >= 1 or status["written"] >= 1
    assert list(tmp_path.rglob("*.jsonl"))


def test_bitvavo_null_exchange_ts_persisted(tmp_path: Path) -> None:
    rec = ResearchMarketDataRecorder(enabled=True, path=str(tmp_path), flush_every=1)
    rec.enqueue_live(_live("bitvavo", seq=9))
    time.sleep(0.2)
    rec.close()
    row = json.loads(next(tmp_path.rglob("*.jsonl")).read_text().strip().splitlines()[0])
    assert row["exchange_ts_ns"] is None
    assert row["exchange_ts_available"] is False


def test_queue_drops_are_counted(tmp_path: Path) -> None:
    rec = ResearchMarketDataRecorder(enabled=True, path=str(tmp_path), max_queue=2, flush_every=1000)
    # Stop drain briefly by flooding before drain — use lock by filling faster than flush
    for i in range(20):
        rec.enqueue_live(_live(seq=i + 1))
    time.sleep(0.05)
    st = rec.stats
    rec.close()
    assert st.dropped >= 1


def test_deterministic_fingerprint_changes_with_tape(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root, n in ((a, 2), (b, 3)):
        rec = ResearchMarketDataRecorder(enabled=True, path=str(root), flush_every=1)
        for i in range(n):
            rec.enqueue_live(_live(seq=i + 1))
        time.sleep(0.2)
        rec.close()
    fa = scan_tape(a).content_fingerprint
    fb = scan_tape(b).content_fingerprint
    assert fa != fb
    assert scan_tape(a).content_fingerprint == fa


def test_integrity_malformed_and_regression(tmp_path: Path) -> None:
    p = tmp_path / "20260816" / "binance"
    p.mkdir(parents=True)
    f = p / "BTCEUR.jsonl"
    good = {
        "schema_version": "research_md_v1",
        "event_id": "a",
        "venue": "binance",
        "symbol": "BTCEUR",
        "received_ts_ns": 100,
        "local_monotonic_ns": 1,
        "exchange_ts_ns": 100,
        "bid_price": "1",
        "ask_price": "1.1",
    }
    bad = "{not-json"
    regress = dict(good, event_id="b", received_ts_ns=50, exchange_ts_ns=50)
    f.write_text(json.dumps(good) + "\n" + bad + "\n" + json.dumps(regress) + "\n")
    stats = validate_tape(tmp_path)
    assert stats.malformed_json >= 1
    assert stats.out_of_order >= 1 or stats.timestamp_regressions >= 1


def test_chrono_split_zero_overlap() -> None:
    split = chronological_split(
        start_ts_ns=0,
        end_ts_ns=1_000_000_000,
        content_fingerprint="abc",
        dataset_id="ds1",
    )
    assert split["available"] is True
    assert assert_zero_overlap(split) is True
    assert split["development"]["end_ts_ns_exclusive"] == split["freeze_boundary"]["start_ts_ns"]
    assert (
        split["freeze_boundary"]["end_ts_ns_exclusive"]
        == split["untouched_oos"]["start_ts_ns"]
    )


def test_acceptance_no_tape() -> None:
    out = evaluate_acceptance(
        inventory={"total_events": 0, "duration_seconds": None, "events_by_venue": {}, "coverage_by_venue": {}},
        integrity={"observed": 0},
    )
    assert out["final_verdict"] == "NO_REAL_TAPE"


def test_acceptance_deterministic() -> None:
    inv = {
        "total_events": 100_000,
        "duration_seconds": 7200.0,
        "events_by_venue": {"binance": 40_000, "bitvavo": 30_000, "okx": 30_000},
        "coverage_by_venue": {
            "binance": {
                "n": 40000,
                "exchange_ts_pct": 0.8,
                "received_ts_pct": 1.0,
                "monotonic_ts_pct": 1.0,
                "sequence_pct": 1.0,
            },
            "bitvavo": {
                "n": 30000,
                "exchange_ts_pct": 0.0,
                "received_ts_pct": 1.0,
                "monotonic_ts_pct": 1.0,
                "sequence_pct": 0.7,
            },
            "okx": {
                "n": 30000,
                "exchange_ts_pct": 1.0,
                "received_ts_pct": 1.0,
                "monotonic_ts_pct": 1.0,
                "sequence_pct": 1.0,
            },
        },
    }
    integrity = {
        "observed": 100_000,
        "duplicates": 10,
        "sequence_gaps": 10,
        "missing_l1": 1000,
        "with_depth": 50_000,
    }
    a = evaluate_acceptance(inventory=inv, integrity=integrity, sync_by_tolerance={})
    b = evaluate_acceptance(inventory=inv, integrity=integrity, sync_by_tolerance={})
    assert a["final_verdict"] == b["final_verdict"]
    assert a["criteria_version"] == PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA["version"]
    assert a["final_verdict"] in {
        "DATA_READY_FOR_SLOW_HORIZONS",
        "DATA_PARTIALLY_READY",
        "DATA_NOT_READY",
        "DATA_READY_FOR_FAST_HORIZONS",
    }


def test_horizon_uncertainty_rejects_fast() -> None:
    mapped = map_acceptance_to_final(
        has_tape=True,
        recorder_enabled=True,
        write_errors=0,
        events_written_runtime=1,
        slow_ready=True,
        fast_ready=False,
        partial=False,
    )
    assert mapped == "DATA_READY_FOR_SLOW_HORIZONS"


def test_finalize_empty_dir(tmp_path: Path) -> None:
    report = run_finalization(research_path=tmp_path / "empty")
    assert report["FINAL_VERDICT"] == "NO_REAL_TAPE"
    assert report["EVENT_COUNT"] == 0


def test_production_trading_fingerprint_unchanged() -> None:
    import bot.execution.paper_executor as paper_ex
    import bot.strategies.maker_inventory as maker
    import bot.opportunity.economics as economics

    for mod in (paper_ex, maker, economics):
        src = inspect.getsource(mod)
        assert "ResearchMarketEvent" not in src
        assert "PREDECLARED_RESEARCH_ACCEPTANCE_CRITERIA" not in src
