"""Event ordering diagnostics — detect gaps, dupes, regressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from bot.market_data.research.schema import ResearchMarketEvent


@dataclass
class OrderingStats:
    events: int = 0
    duplicates: int = 0
    sequence_gaps: int = 0
    timestamp_regressions: int = 0
    out_of_order: int = 0
    missing_sequence: int = 0
    reconnect_boundaries: int = 0
    last_sequence: int | None = None
    last_exchange_ts_ns: int | None = None
    last_mono_ns: int | None = None

    def as_dict(self) -> dict[str, Any]:
        n = max(1, self.events)
        return {
            "events": self.events,
            "duplicates": self.duplicates,
            "sequence_gaps": self.sequence_gaps,
            "timestamp_regressions": self.timestamp_regressions,
            "out_of_order": self.out_of_order,
            "missing_sequence": self.missing_sequence,
            "reconnect_boundaries": self.reconnect_boundaries,
            "duplicate_rate": self.duplicates / n,
            "out_of_order_rate": self.out_of_order / n,
            "sequence_gap_rate": self.sequence_gaps / n,
        }


def sort_key(event: ResearchMarketEvent) -> tuple:
    """Venue ordering: sequence → exchange_ts → local monotonic receive."""
    seq = event.sequence_number if event.sequence_number is not None else -1
    ets = event.exchange_ts_ns if event.exchange_ts_ns is not None else -1
    return (seq >= 0, seq, ets >= 0, ets, event.local_monotonic_ns)


def analyze_ordering(events: Iterable[ResearchMarketEvent]) -> OrderingStats:
    stats = OrderingStats()
    seen_ids: set[str] = set()
    ordered = sorted(events, key=sort_key)
    # Also walk in input receive order for regression detection on raw stream
    raw = list(events)
    for ev in raw:
        stats.events += 1
        if ev.event_id in seen_ids:
            stats.duplicates += 1
        seen_ids.add(ev.event_id)

        if ev.sequence_number is None:
            stats.missing_sequence += 1
        else:
            if stats.last_sequence is not None:
                if ev.sequence_number == stats.last_sequence:
                    stats.duplicates += 1
                elif ev.sequence_number < stats.last_sequence:
                    stats.out_of_order += 1
                elif ev.sequence_number > stats.last_sequence + 1:
                    stats.sequence_gaps += 1
            stats.last_sequence = ev.sequence_number

        if ev.exchange_ts_ns is not None:
            if (
                stats.last_exchange_ts_ns is not None
                and ev.exchange_ts_ns < stats.last_exchange_ts_ns
                and (ev.sequence_number is None or stats.last_sequence is None
                     or ev.sequence_number >= (stats.last_sequence or 0))
            ):
                # Regression only if not explained by out-of-order seq already counted
                stats.timestamp_regressions += 1
            stats.last_exchange_ts_ns = ev.exchange_ts_ns

        if ev.is_snapshot and stats.events > 1:
            stats.reconnect_boundaries += 1

        if stats.last_mono_ns is not None and ev.local_monotonic_ns < stats.last_mono_ns:
            stats.out_of_order += 1
        stats.last_mono_ns = ev.local_monotonic_ns

    _ = ordered  # available for callers via sort_key
    return stats


def sort_events(events: Iterable[ResearchMarketEvent]) -> list[ResearchMarketEvent]:
    return sorted(events, key=sort_key)
