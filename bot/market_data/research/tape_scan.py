"""Streaming tape inventory — no full in-memory load required."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class TapeInventory:
    root: str
    file_count: int = 0
    total_bytes: int = 0
    total_events: int = 0
    first_received_ts_ns: int | None = None
    last_received_ts_ns: int | None = None
    events_by_venue: dict[str, int] = field(default_factory=dict)
    events_by_symbol: dict[str, int] = field(default_factory=dict)
    coverage_by_venue: dict[str, dict[str, float]] = field(default_factory=dict)
    file_checksums: dict[str, str] = field(default_factory=dict)
    content_fingerprint: str = ""
    layout: str = "unknown"  # legacy | session | mixed

    @property
    def duration_seconds(self) -> float | None:
        if self.first_received_ts_ns is None or self.last_received_ts_ns is None:
            return None
        return (self.last_received_ts_ns - self.first_received_ts_ns) / 1e9

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "total_events": self.total_events,
            "first_received_ts_ns": self.first_received_ts_ns,
            "last_received_ts_ns": self.last_received_ts_ns,
            "duration_seconds": self.duration_seconds,
            "events_by_venue": dict(self.events_by_venue),
            "events_by_symbol": dict(self.events_by_symbol),
            "coverage_by_venue": dict(self.coverage_by_venue),
            "file_checksums": dict(self.file_checksums),
            "content_fingerprint": self.content_fingerprint,
            "layout": self.layout,
        }


def _detect_layout(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts
    joined = "/".join(parts)
    if "session=" in joined or "date=" in joined:
        return "session"
    return "legacy"


def iter_raw_events(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield path, json.loads(line)
                except json.JSONDecodeError:
                    continue


def scan_tape(root: Path | str, *, max_events: int | None = None) -> TapeInventory:
    root = Path(root)
    inv = TapeInventory(root=str(root))
    if not root.exists():
        return inv

    layouts: set[str] = set()
    cov_counts: dict[str, dict[str, int]] = {}
    hasher = hashlib.sha256()

    files = sorted(root.rglob("*.jsonl"))
    inv.file_count = len(files)
    for path in files:
        layouts.add(_detect_layout(path, root))
        rel = str(path.relative_to(root))
        size = path.stat().st_size
        inv.total_bytes += size
        file_hash = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                file_hash.update(chunk)
        digest = file_hash.hexdigest()
        inv.file_checksums[rel] = digest
        hasher.update(rel.encode())
        hasher.update(b"|")
        hasher.update(digest.encode())
        hasher.update(b"\n")

    for _path, raw in iter_raw_events(root):
        if max_events is not None and inv.total_events >= max_events:
            break
        inv.total_events += 1
        venue = str(raw.get("venue") or "")
        symbol = str(raw.get("symbol") or "")
        inv.events_by_venue[venue] = inv.events_by_venue.get(venue, 0) + 1
        inv.events_by_symbol[symbol] = inv.events_by_symbol.get(symbol, 0) + 1
        bucket = cov_counts.setdefault(
            venue, {"n": 0, "exchange_ts": 0, "received_ts": 0, "mono_ts": 0, "sequence": 0}
        )
        bucket["n"] += 1
        if raw.get("exchange_ts_ns") is not None:
            bucket["exchange_ts"] += 1
        if raw.get("received_ts_ns") is not None:
            bucket["received_ts"] += 1
            r = int(raw["received_ts_ns"])
            if inv.first_received_ts_ns is None or r < inv.first_received_ts_ns:
                inv.first_received_ts_ns = r
            if inv.last_received_ts_ns is None or r > inv.last_received_ts_ns:
                inv.last_received_ts_ns = r
        if raw.get("local_monotonic_ns") is not None:
            bucket["mono_ts"] += 1
        if raw.get("sequence_number") is not None:
            bucket["sequence"] += 1

    for venue, b in cov_counts.items():
        n = max(1, b["n"])
        inv.coverage_by_venue[venue] = {
            "n": float(b["n"]),
            "exchange_ts_pct": b["exchange_ts"] / n,
            "received_ts_pct": b["received_ts"] / n,
            "monotonic_ts_pct": b["mono_ts"] / n,
            "sequence_pct": b["sequence"] / n,
        }

    hasher.update(str(inv.total_events).encode())
    inv.content_fingerprint = hasher.hexdigest()
    if layouts == {"legacy"}:
        inv.layout = "legacy"
    elif layouts == {"session"}:
        inv.layout = "session"
    elif layouts:
        inv.layout = "mixed"
    return inv


def dataset_id_from_fingerprint(fingerprint: str, *, schema_version: str) -> str:
    return f"mdresearch-{schema_version}-{fingerprint[:16]}"
