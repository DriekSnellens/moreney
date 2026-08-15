"""Ring-buffer latency histograms for the paper / hydrate hot path.

Disabled by default. When enabled, records cheap monotonic timestamps only —
no per-event logging on the normal path.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LatencyStats:
    name: str
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "max_ms": round(self.max_ms, 3),
        }


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


class CycleLatencyTracker:
    """Collect phase latencies; summarize mean / p50 / p95 / p99 / count."""

    def __init__(self, *, enabled: bool = False, window: int = 512) -> None:
        self.enabled = bool(enabled)
        self._window = max(16, int(window))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )
        self._counts: dict[str, int] = defaultdict(int)

    def record(self, name: str, elapsed_s: float) -> None:
        if not self.enabled:
            return
        ms = float(elapsed_s) * 1000.0
        self._samples[name].append(ms)
        self._counts[name] += 1

    def span(self, name: str) -> "_Span":
        return _Span(self, name)

    def stats(self, name: str) -> LatencyStats | None:
        vals = list(self._samples.get(name) or ())
        if not vals:
            return None
        ordered = sorted(vals)
        total = sum(ordered)
        return LatencyStats(
            name=name,
            count=int(self._counts.get(name, len(ordered))),
            mean_ms=total / len(ordered),
            p50_ms=_percentile(ordered, 50),
            p95_ms=_percentile(ordered, 95),
            p99_ms=_percentile(ordered, 99),
            max_ms=ordered[-1],
        )

    def report(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        names = sorted(self._samples.keys())
        return {
            "enabled": True,
            "window": self._window,
            "phases": {
                name: (self.stats(name).as_dict() if self.stats(name) else None)
                for name in names
            },
        }

    def reset(self) -> None:
        self._samples.clear()
        self._counts.clear()


class _Span:
    __slots__ = ("_tracker", "_name", "_t0")

    def __init__(self, tracker: CycleLatencyTracker, name: str) -> None:
        self._tracker = tracker
        self._name = name
        self._t0 = 0.0

    def __enter__(self) -> "_Span":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._tracker.record(self._name, time.perf_counter() - self._t0)
