"""Fine-grained hot-path profiler for strategy_scan → candidate_creation.

Optional attachment; zero cost when disabled. Records total / count / mean /
p50 / p95 and optional allocation deltas via tracemalloc.
"""

from __future__ import annotations

import time
import tracemalloc
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(
        len(sorted_vals) - 1,
        max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))),
    )
    return sorted_vals[idx]


@dataclass(frozen=True, slots=True)
class SubstageStats:
    name: str
    count: int
    total_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    alloc_bytes: int | None = None
    alloc_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "count": self.count,
            "total_ms": round(self.total_ms, 4),
            "mean_ms": round(self.mean_ms, 6),
            "p50_ms": round(self.p50_ms, 6),
            "p95_ms": round(self.p95_ms, 6),
        }
        if self.alloc_bytes is not None:
            out["alloc_bytes"] = int(self.alloc_bytes)
        if self.alloc_count is not None:
            out["alloc_count"] = int(self.alloc_count)
        return out


class HotPathProfiler:
    """Accumulate per-substage timings (and optional alloc deltas)."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        track_allocs: bool = False,
        window: int = 8192,
        alloc_span_names: frozenset[str] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.track_allocs = bool(track_allocs)
        # Only take tracemalloc snapshots for these spans (default: none).
        # Per-call snapshots on micro-spans are prohibitively expensive.
        self._alloc_span_names = alloc_span_names or frozenset()
        self._window = max(64, int(window))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )
        self._counts: dict[str, int] = defaultdict(int)
        self._totals: dict[str, float] = defaultdict(float)
        self._alloc_bytes: dict[str, int] = defaultdict(int)
        self._alloc_count: dict[str, int] = defaultdict(int)
        self._own_tracemalloc = False

    def ensure_tracemalloc(self) -> None:
        if self.track_allocs and not tracemalloc.is_tracing():
            tracemalloc.start()
            self._own_tracemalloc = True

    def stop_tracemalloc(self) -> None:
        if self._own_tracemalloc and tracemalloc.is_tracing():
            tracemalloc.stop()
            self._own_tracemalloc = False

    def record(
        self,
        name: str,
        elapsed_s: float,
        *,
        alloc_bytes: int = 0,
        alloc_count: int = 0,
    ) -> None:
        if not self.enabled:
            return
        ms = float(elapsed_s) * 1000.0
        self._samples[name].append(ms)
        self._counts[name] += 1
        self._totals[name] += ms
        if alloc_bytes:
            self._alloc_bytes[name] += int(alloc_bytes)
        if alloc_count:
            self._alloc_count[name] += int(alloc_count)

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        snap0 = None
        if (
            self.track_allocs
            and name in self._alloc_span_names
            and tracemalloc.is_tracing()
        ):
            snap0 = tracemalloc.take_snapshot()
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            alloc_b = 0
            alloc_n = 0
            if snap0 is not None:
                snap1 = tracemalloc.take_snapshot()
                stats = snap1.compare_to(snap0, "lineno")
                alloc_b = sum(max(0, s.size_diff) for s in stats)
                alloc_n = sum(max(0, s.count_diff) for s in stats)
            self.record(name, elapsed, alloc_bytes=alloc_b, alloc_count=alloc_n)

    def stats(self, name: str) -> SubstageStats | None:
        vals = list(self._samples.get(name) or ())
        if not vals:
            return None
        ordered = sorted(vals)
        return SubstageStats(
            name=name,
            count=int(self._counts.get(name, len(ordered))),
            total_ms=float(self._totals.get(name, sum(ordered))),
            mean_ms=sum(ordered) / len(ordered),
            p50_ms=_percentile(ordered, 50),
            p95_ms=_percentile(ordered, 95),
            alloc_bytes=self._alloc_bytes.get(name),
            alloc_count=self._alloc_count.get(name),
        )

    def report(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        ranked = sorted(
            [s.as_dict() for s in (self.stats(n) for n in self._samples) if s],
            key=lambda p: float(p.get("total_ms") or 0),
            reverse=True,
        )
        return {
            "enabled": True,
            "track_allocs": self.track_allocs,
            "ranked_by_total_ms": ranked,
            "phases": {r["name"]: r for r in ranked},
        }

    def reset(self) -> None:
        self._samples.clear()
        self._counts.clear()
        self._totals.clear()
        self._alloc_bytes.clear()
        self._alloc_count.clear()
