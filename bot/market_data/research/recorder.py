"""Async buffered append-only research recorder — never blocks trading path."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.market_data.models import MarketDataEvent
from bot.market_data.research.convert import from_live_event
from bot.market_data.research.schema import ResearchMarketEvent

logger = logging.getLogger(__name__)


@dataclass
class RecorderStats:
    enqueued: int = 0
    written: int = 0
    dropped: int = 0
    write_errors: int = 0
    queue_depth: int = 0
    last_write_ns: int | None = None
    complete: bool = True  # False if any drops

    def as_dict(self) -> dict[str, Any]:
        return {
            "enqueued": self.enqueued,
            "written": self.written,
            "dropped": self.dropped,
            "write_errors": self.write_errors,
            "queue_depth": self.queue_depth,
            "last_write_ns": self.last_write_ns,
            "complete": self.complete and self.dropped == 0,
            "backpressure": self.queue_depth > 0,
            "EVENTS_ENQUEUED": self.enqueued,
            "EVENTS_WRITTEN": self.written,
            "EVENTS_DROPPED": self.dropped,
            "WRITE_ERRORS": self.write_errors,
            "QUEUE_DEPTH": self.queue_depth,
            "LAST_WRITE_TIMESTAMP": self.last_write_ns,
        }


class ResearchMarketDataRecorder:
    """Buffered JSONL recorder partitioned by date/venue/symbol/session.

    New sessions use:
      date=YYYY-MM-DD/venue=<v>/symbol=<s>/session=<id>/events.jsonl

    Legacy layout (YYYYMMDD/<venue>/<SYMBOL>.jsonl) remains readable by scanners.
    Trading path only enqueues; a background thread drains to disk.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        path: str = "./data/research_marketdata",
        max_queue: int = 50_000,
        max_depth_levels: int = 10,
        flush_every: int = 64,
        flush_interval_ms: int = 50,
        use_session_layout: bool = True,
    ) -> None:
        self.enabled = enabled
        self._root = Path(path)
        self._max_queue = max_queue
        self._max_depth = max_depth_levels
        self._flush_every = flush_every
        self._flush_interval_s = max(0.001, flush_interval_ms / 1000.0)
        self._use_session_layout = use_session_layout
        self._queue: deque[ResearchMarketEvent] = deque()
        self._lock = threading.Lock()
        self._stats = RecorderStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._files: dict[str, Path] = {}
        self._meta_written: set[str] = set()
        self._session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self._started_at_ns = time.time_ns()
        if self.enabled:
            self._root.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(
                target=self._drain_loop, name="research-md-recorder", daemon=True
            )
            self._thread.start()
            logger.info(
                "RESEARCH_MARKETDATA_RECORDING_ENABLED path=%s session=%s layout=%s",
                self._root,
                self._session_id,
                "session" if self._use_session_layout else "legacy",
            )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def running(self) -> bool:
        return bool(self.enabled and self._thread is not None and self._thread.is_alive())

    @property
    def stats(self) -> RecorderStats:
        with self._lock:
            self._stats.queue_depth = len(self._queue)
            return self._stats

    def enqueue_live(self, event: MarketDataEvent) -> None:
        """Non-blocking enqueue from hot path."""
        if not self.enabled:
            return
        research = from_live_event(event, max_depth_levels=self._max_depth)
        if research is None:
            return
        self.enqueue(research)

    def enqueue(self, event: ResearchMarketEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            if len(self._queue) >= self._max_queue:
                self._stats.dropped += 1
                self._stats.complete = False
                return
            self._queue.append(event)
            self._stats.enqueued += 1
            self._stats.queue_depth = len(self._queue)

    async def record_live(self, event: MarketDataEvent) -> None:
        """Async-compatible wrapper — never awaits disk."""
        self.enqueue_live(event)
        await asyncio.sleep(0)

    def _drain_loop(self) -> None:
        buf: list[ResearchMarketEvent] = []
        while not self._stop.is_set():
            with self._lock:
                while self._queue and len(buf) < self._flush_every:
                    buf.append(self._queue.popleft())
                self._stats.queue_depth = len(self._queue)
            if not buf:
                time.sleep(self._flush_interval_s)
                continue
            try:
                self._write_batch(buf)
                with self._lock:
                    self._stats.written += len(buf)
                    self._stats.last_write_ns = time.time_ns()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._stats.write_errors += 1
                    self._stats.complete = False
                logger.warning("RESEARCH_MD_WRITE_FAILED error=%s", exc)
            buf.clear()
        # Final flush
        with self._lock:
            while self._queue:
                buf.append(self._queue.popleft())
        if buf:
            try:
                self._write_batch(buf)
                with self._lock:
                    self._stats.written += len(buf)
                    self._stats.last_write_ns = time.time_ns()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._stats.write_errors += 1
                logger.warning("RESEARCH_MD_FINAL_FLUSH_FAILED error=%s", exc)

    def _path_for(self, event: ResearchMarketEvent) -> Path:
        ns = event.exchange_ts_ns if event.exchange_ts_ns is not None else event.received_ts_ns
        day_dt = datetime.fromtimestamp(ns / 1e9, tz=UTC)
        if self._use_session_layout:
            day = day_dt.strftime("%Y-%m-%d")
            key = f"date={day}/venue={event.venue}/symbol={event.symbol}/session={self._session_id}"
            path = self._files.get(key)
            if path is None:
                directory = (
                    self._root
                    / f"date={day}"
                    / f"venue={event.venue}"
                    / f"symbol={event.symbol}"
                    / f"session={self._session_id}"
                )
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / "events.jsonl"
                self._files[key] = path
                if key not in self._meta_written:
                    meta = {
                        "session_id": self._session_id,
                        "venue": event.venue,
                        "symbol": event.symbol,
                        "date": day,
                        "started_at_ns": self._started_at_ns,
                        "schema_note": "restart creates new session boundary",
                    }
                    (directory / "metadata.json").write_text(
                        json.dumps(meta, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    self._meta_written.add(key)
            return path

        day = day_dt.strftime("%Y%m%d")
        key = f"{day}/{event.venue}/{event.symbol}"
        path = self._files.get(key)
        if path is None:
            directory = self._root / day / event.venue
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{event.symbol}.jsonl"
            self._files[key] = path
        return path

    def _write_batch(self, events: list[ResearchMarketEvent]) -> None:
        by_path: dict[Path, list[str]] = {}
        for ev in events:
            p = self._path_for(ev)
            by_path.setdefault(p, []).append(json.dumps(ev.as_dict(), separators=(",", ":")))
        for path, lines in by_path.items():
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")

    def snapshot(self) -> dict[str, Any]:
        st = self.stats.as_dict()
        return {
            "enabled": self.enabled,
            "RECORDER_ENABLED": self.enabled,
            "path": str(self._root),
            "RECORDER_OUTPUT_DIR": str(self._root),
            "RECORDER_RUNNING": self.running,
            "session_id": self._session_id,
            "QUEUE_SIZE": self._max_queue,
            "flush_every": self._flush_every,
            "flush_interval_ms": int(self._flush_interval_s * 1000),
            "format": (
                "jsonl_date_venue_symbol_session"
                if self._use_session_layout
                else "jsonl_partitioned_date_venue_symbol"
            ),
            "affects_trading": False,
            **st,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
