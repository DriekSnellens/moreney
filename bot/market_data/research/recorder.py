"""Async buffered append-only research recorder — never blocks trading path."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
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
        }


class ResearchMarketDataRecorder:
    """Buffered JSONL recorder partitioned by date/venue/symbol.

    Parquet is not a dependency; JSONL is the lightweight durable format.
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
    ) -> None:
        self.enabled = enabled
        self._root = Path(path)
        self._max_queue = max_queue
        self._max_depth = max_depth_levels
        self._flush_every = flush_every
        self._queue: deque[ResearchMarketEvent] = deque()
        self._lock = threading.Lock()
        self._stats = RecorderStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._files: dict[str, Path] = {}
        self._session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if self.enabled:
            self._root.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(
                target=self._drain_loop, name="research-md-recorder", daemon=True
            )
            self._thread.start()
            logger.info(
                "RESEARCH_MARKETDATA_RECORDING_ENABLED path=%s session=%s",
                self._root,
                self._session_id,
            )

    @property
    def session_id(self) -> str:
        return self._session_id

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
                time.sleep(0.01)
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
            except Exception as exc:  # noqa: BLE001
                logger.warning("RESEARCH_MD_FINAL_FLUSH_FAILED error=%s", exc)

    def _path_for(self, event: ResearchMarketEvent) -> Path:
        # Prefer exchange day when available; else receive day
        ns = event.exchange_ts_ns if event.exchange_ts_ns is not None else event.received_ts_ns
        day = datetime.fromtimestamp(ns / 1e9, tz=UTC).strftime("%Y%m%d")
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
            "path": str(self._root),
            "session_id": self._session_id,
            "format": "jsonl_partitioned_date_venue_symbol",
            "affects_trading": False,
            **st,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
