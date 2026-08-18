"""Append-only compact artifacts. No per-event fsync."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from bot.research.shadow_validation.protocol import (
    ACCUMULATOR_FILENAME,
    DEFAULT_RUN_DIR,
    OBSERVATIONS_FILENAME,
    WRITER_BATCH_SIZE,
    WRITER_FLUSH_INTERVAL_S,
)


class CompactObservationWriter:
    """Batched JSONL append. Hot path only enqueues dicts."""

    def __init__(
        self,
        path: Path | str,
        *,
        batch_size: int = WRITER_BATCH_SIZE,
        flush_interval_s: float = WRITER_FLUSH_INTERVAL_S,
    ) -> None:
        self.path = Path(path)
        self.batch_size = int(batch_size)
        self.flush_interval_s = float(flush_interval_s)
        self._buf: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(self, record: Mapping[str, Any]) -> None:
        self._buf.append(dict(record))
        now = time.monotonic()
        if len(self._buf) >= self.batch_size or (now - self._last_flush) >= self.flush_interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            self._last_flush = time.monotonic()
            return
        lines = "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in self._buf)
        self._buf.clear()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(lines)
            # Intentional: no fsync on the hot observation path.
        self._last_flush = time.monotonic()

    @property
    def pending(self) -> int:
        return len(self._buf)


def write_accumulator_snapshot(path: Path | str, payload: Mapping[str, Any]) -> None:
    """Infrequent snapshot. Atomic replace, fsync allowed here."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(dest)


def default_paths(run_dir: Path | str | None = None) -> dict[str, Path]:
    root = Path(run_dir or DEFAULT_RUN_DIR)
    return {
        "run_dir": root,
        "observations": root / OBSERVATIONS_FILENAME,
        "accumulator": root / ACCUMULATOR_FILENAME,
    }
