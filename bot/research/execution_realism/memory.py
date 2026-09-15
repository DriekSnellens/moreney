"""Research-runner memory instrumentation. Not used on production hot paths."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


def current_rss_mb() -> float:
    """Current process RSS in MiB via /proc/self/statm (Linux)."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * float(os.sysconf("SC_PAGE_SIZE")) / (1024.0 * 1024.0)
    except Exception:
        import resource

        rss_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return rss_kb / 1024.0


def peak_rss_mb() -> float:
    """Kernel-reported max RSS in MiB."""
    import resource

    rss_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return rss_kb / 1024.0


def python_allocated_mb() -> float | None:
    try:
        import tracemalloc

        if tracemalloc.is_tracing():
            current, _peak = tracemalloc.get_traced_memory()
            return current / (1024.0 * 1024.0)
    except Exception:
        return None
    return None


@dataclass
class MemoryMonitor:
    windows_total: int = 0
    scenarios_total: int = 0
    signals_processed: int = 0
    scenarios_processed: int = 0
    windows_completed: int = 0
    artifacts_written: int = 0
    artifacts_skipped: int = 0
    log_every_seconds: float = 5.0
    _t0: float = field(default_factory=time.perf_counter)
    _last_log_t: float = field(default_factory=time.perf_counter)
    _last_signals: int = 0
    peak_rss: float = 0.0
    records: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self, *, force_log: bool = False) -> dict[str, Any]:
        rss = current_rss_mb()
        peak = max(self.peak_rss, rss, peak_rss_mb())
        self.peak_rss = peak
        now = time.perf_counter()
        elapsed = max(now - self._t0, 1e-9)
        dt = max(now - self._last_log_t, 1e-9)
        d_signals = self.signals_processed - self._last_signals
        rec = {
            "window": f"{self.windows_completed}/{self.windows_total}",
            "scenario": f"{self.scenarios_processed}/{self.scenarios_total}",
            "signals": self.signals_processed,
            "artifacts_written": self.artifacts_written,
            "artifacts_skipped": self.artifacts_skipped,
            "rss_mb": round(rss, 1),
            "peak_rss_mb": round(peak, 1),
            "python_alloc_mb": python_allocated_mb(),
            "signals_per_sec": round(self.signals_processed / elapsed, 1),
            "instant_signals_per_sec": round(d_signals / dt, 1),
            "elapsed_s": round(elapsed, 3),
        }
        if force_log or (now - self._last_log_t) >= self.log_every_seconds:
            self.records.append(rec)
            self._last_log_t = now
            self._last_signals = self.signals_processed
            py = rec["python_alloc_mb"]
            py_s = f"{py:.1f}" if isinstance(py, float) else "n/a"
            print(
                f"MEM window={rec['window']} scenario={rec['scenario']} "
                f"signals={rec['signals']} rss_mb={rec['rss_mb']} "
                f"peak_rss_mb={rec['peak_rss_mb']} python_alloc_mb={py_s} "
                f"signals_per_sec={rec['signals_per_sec']}",
                flush=True,
            )
        return rec
