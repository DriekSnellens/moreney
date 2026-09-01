"""Cross-venue synchronization windows with timestamp-quality awareness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from bot.market_data.research.schema import ResearchMarketEvent, SyncQuality, TimestampQuality


@dataclass(frozen=True, slots=True)
class VenueSlice:
    venue: str
    event: ResearchMarketEvent | None
    event_ts_ns: int | None
    age_vs_target_ms: float | None
    timestamp_quality: str


@dataclass(frozen=True, slots=True)
class SynchronizedObservation:
    target_ts_ns: int
    tolerance_ms: float
    venues: tuple[VenueSlice, ...]
    sync_quality: str
    usable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_ts_ns": self.target_ts_ns,
            "tolerance_ms": self.tolerance_ms,
            "sync_quality": self.sync_quality,
            "usable": self.usable,
            "venues": [
                {
                    "venue": v.venue,
                    "event_id": v.event.event_id if v.event else None,
                    "event_ts_ns": v.event_ts_ns,
                    "age_vs_target_ms": v.age_vs_target_ms,
                    "timestamp_quality": v.timestamp_quality,
                    "exchange_ts_available": (
                        v.event.exchange_ts_available if v.event else False
                    ),
                }
                for v in self.venues
            ],
        }


def _event_clock_ns(ev: ResearchMarketEvent) -> int | None:
    """Prefer exchange_ts; fall back to received only with degraded quality."""
    if ev.exchange_ts_ns is not None and ev.exchange_ts_available:
        return ev.exchange_ts_ns
    return None  # do not silently use receive as exchange clock for sync


def synchronize_at(
    events_by_venue: dict[str, Sequence[ResearchMarketEvent]],
    *,
    target_ts_ns: int,
    tolerance_ms: float,
    venues: Sequence[str],
) -> SynchronizedObservation:
    """Latest valid observation per venue with clock <= target within tolerance.

    Events without exchange_ts cannot participate as EXACT/GOOD sync members.
    """
    tol_ns = int(tolerance_ms * 1_000_000)
    slices: list[VenueSlice] = []
    qualities: list[str] = []

    for venue in venues:
        series = list(events_by_venue.get(venue) or [])
        best: ResearchMarketEvent | None = None
        best_ts: int | None = None
        for ev in series:
            clock = _event_clock_ns(ev)
            if clock is None:
                continue
            if clock > target_ts_ns:
                continue
            if target_ts_ns - clock > tol_ns:
                continue
            if best_ts is None or clock > best_ts:
                best = ev
                best_ts = clock
        if best is None:
            # Attempt receive-only fallback marked UNSUPPORTED
            for ev in series:
                if ev.received_ts_ns <= target_ts_ns and target_ts_ns - ev.received_ts_ns <= tol_ns:
                    if best is None or ev.received_ts_ns > (best_ts or -1):
                        best = ev
                        best_ts = ev.received_ts_ns
            q = TimestampQuality.UNSUPPORTED.value
            age = (
                (target_ts_ns - best_ts) / 1e6
                if best is not None and best_ts is not None
                else None
            )
            slices.append(
                VenueSlice(
                    venue=venue,
                    event=best,
                    event_ts_ns=best_ts if best and not best.exchange_ts_available else (
                        best.exchange_ts_ns if best else None
                    ),
                    age_vs_target_ms=age,
                    timestamp_quality=q,
                )
            )
            qualities.append(q)
            continue

        age = (target_ts_ns - best_ts) / 1e6 if best_ts is not None else None
        q = best.timestamp_quality
        slices.append(
            VenueSlice(
                venue=venue,
                event=best,
                event_ts_ns=best_ts,
                age_vs_target_ms=age,
                timestamp_quality=q,
            )
        )
        qualities.append(q)

    sync_q, usable = _grade(qualities, slices, tolerance_ms)
    return SynchronizedObservation(
        target_ts_ns=target_ts_ns,
        tolerance_ms=tolerance_ms,
        venues=tuple(slices),
        sync_quality=sync_q,
        usable=usable,
    )


def _grade(
    qualities: list[str],
    slices: list[VenueSlice],
    tolerance_ms: float,
) -> tuple[str, bool]:
    if any(s.event is None for s in slices):
        return SyncQuality.UNSUPPORTED.value, False
    if any(q == TimestampQuality.UNSUPPORTED.value for q in qualities):
        return SyncQuality.UNSUPPORTED.value, False
    ages = [s.age_vs_target_ms for s in slices if s.age_vs_target_ms is not None]
    max_age = max(ages) if ages else tolerance_ms
    if any(q == TimestampQuality.LOW.value for q in qualities):
        return SyncQuality.DEGRADED.value, max_age <= tolerance_ms
    if max_age <= min(5.0, tolerance_ms * 0.1):
        return SyncQuality.EXACT.value, True
    if max_age <= tolerance_ms * 0.5:
        return SyncQuality.GOOD.value, True
    return SyncQuality.DEGRADED.value, True


def sync_coverage_report(
    events: Iterable[ResearchMarketEvent],
    *,
    venues: Sequence[str] = ("binance", "bitvavo", "okx"),
    tolerances_ms: Sequence[float] = (50, 100, 250, 500, 1000),
    sample_step: int = 10,
) -> dict[str, Any]:
    by_venue: dict[str, list[ResearchMarketEvent]] = {v: [] for v in venues}
    for ev in events:
        if ev.venue in by_venue:
            by_venue[ev.venue].append(ev)

    # Targets from binance exchange clocks when available
    targets = [
        e.exchange_ts_ns
        for e in by_venue.get("binance", [])
        if e.exchange_ts_ns is not None and e.exchange_ts_available
    ][:: max(1, sample_step)]

    out: dict[str, Any] = {"targets_sampled": len(targets), "by_tolerance_ms": {}}
    for tol in tolerances_ms:
        usable = 0
        qualities: dict[str, int] = {}
        ages: list[float] = []
        for t in targets:
            obs = synchronize_at(by_venue, target_ts_ns=t, tolerance_ms=float(tol), venues=venues)
            qualities[obs.sync_quality] = qualities.get(obs.sync_quality, 0) + 1
            if obs.usable and obs.sync_quality != SyncQuality.UNSUPPORTED.value:
                usable += 1
            for sl in obs.venues:
                if sl.age_vs_target_ms is not None:
                    ages.append(sl.age_vs_target_ms)
        ages_sorted = sorted(ages)
        def pct(p: float) -> float | None:
            if not ages_sorted:
                return None
            idx = min(len(ages_sorted) - 1, max(0, int(round(p / 100 * (len(ages_sorted) - 1)))))
            return ages_sorted[idx]

        out["by_tolerance_ms"][str(tol)] = {
            "usable_windows": usable,
            "usable_rate": (usable / len(targets)) if targets else 0.0,
            "quality_counts": qualities,
            "median_skew_ms": pct(50),
            "p95_skew_ms": pct(95),
            "p99_skew_ms": pct(99),
        }
    return out
