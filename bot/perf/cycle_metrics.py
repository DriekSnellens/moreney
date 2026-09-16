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
    total_ms: float = 0.0
    pct_of_cycle: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "count": self.count,
            "total_ms": round(self.total_ms, 3),
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "max_ms": round(self.max_ms, 3),
        }
        if self.pct_of_cycle is not None:
            out["pct_of_cycle"] = round(self.pct_of_cycle, 2)
        return out


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


class CycleLatencyTracker:
    """Collect phase latencies; summarize mean / p50 / p95 / p99 / count."""

    def __init__(self, *, enabled: bool = False, window: int = 512) -> None:
        self.enabled = bool(enabled)
        self._window = max(16, int(window))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )
        self._counts: dict[str, int] = defaultdict(int)
        self._totals: dict[str, float] = defaultdict(float)

    def record(self, name: str, elapsed_s: float) -> None:
        if not self.enabled:
            return
        ms = float(elapsed_s) * 1000.0
        self._samples[name].append(ms)
        self._counts[name] += 1
        self._totals[name] += ms

    def span(self, name: str) -> "_Span":
        return _Span(self, name)

    def stats(
        self, name: str, *, cycle_total_ms: float | None = None
    ) -> LatencyStats | None:
        vals = list(self._samples.get(name) or ())
        if not vals:
            return None
        ordered = sorted(vals)
        total = float(self._totals.get(name, sum(ordered)))
        pct = None
        if cycle_total_ms is not None and cycle_total_ms > 0:
            pct = 100.0 * total / cycle_total_ms
        return LatencyStats(
            name=name,
            count=int(self._counts.get(name, len(ordered))),
            mean_ms=sum(ordered) / len(ordered),
            p50_ms=_percentile(ordered, 50),
            p95_ms=_percentile(ordered, 95),
            p99_ms=_percentile(ordered, 99),
            max_ms=ordered[-1],
            total_ms=total,
            pct_of_cycle=pct,
        )

    def report(self, *, cycle_phase: str = "total_cycle") -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        cycle_total = float(self._totals.get(cycle_phase, 0.0)) or None
        names = sorted(self._samples.keys())
        phases = {
            name: (
                self.stats(name, cycle_total_ms=cycle_total).as_dict()
                if self.stats(name) is not None
                else None
            )
            for name in names
        }
        ranked = sorted(
            [p for p in phases.values() if p],
            key=lambda p: float(p.get("total_ms") or 0),
            reverse=True,
        )
        return {
            "enabled": True,
            "window": self._window,
            "cycle_total_ms": round(cycle_total or 0.0, 3),
            "phases": phases,
            "ranked_by_total_ms": ranked,
        }

    def reset(self) -> None:
        self._samples.clear()
        self._counts.clear()
        self._totals.clear()


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
