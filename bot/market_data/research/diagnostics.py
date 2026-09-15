"""Latency / clock diagnostics — never infer fake latency without exchange_ts."""

from __future__ import annotations

from typing import Any, Sequence

from bot.market_data.research.schema import ResearchMarketEvent


def _pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def latency_report(events: Sequence[ResearchMarketEvent]) -> dict[str, Any]:
    by_venue: dict[str, list[ResearchMarketEvent]] = {}
    for e in events:
        by_venue.setdefault(e.venue, []).append(e)

    out: dict[str, Any] = {}
    for venue, xs in sorted(by_venue.items()):
        lats = [
            float(e.receive_latency_ms)
            for e in xs
            if e.receive_latency_ms is not None and e.exchange_ts_available
        ]
        lats_sorted = sorted(lats)
        neg = sum(1 for x in lats if x < 0)
        regressions = 0
        last = None
        for e in xs:
            if e.exchange_ts_ns is None:
                continue
            if last is not None and e.exchange_ts_ns < last:
                regressions += 1
            last = e.exchange_ts_ns
        out[venue] = {
            "n": len(xs),
            "exchange_ts_coverage": (
                sum(1 for e in xs if e.exchange_ts_available) / max(1, len(xs))
            ),
            "receive_ts_coverage": 1.0,
            "sequence_coverage": (
                sum(1 for e in xs if e.sequence_number is not None) / max(1, len(xs))
            ),
            "latency_sample_n": len(lats),
            "median_receive_latency_ms": _pct(lats_sorted, 50),
            "p50_ms": _pct(lats_sorted, 50),
            "p95_ms": _pct(lats_sorted, 95),
            "p99_ms": _pct(lats_sorted, 99),
            "max_ms": (max(lats_sorted) if lats_sorted else None),
            "negative_latency_count": neg,
            "timestamp_regression_count": regressions,
            "note": (
                "Latency only defined when exchange_ts exists; "
                "Bitvavo reports null latency (UNSUPPORTED), not invented."
            ),
        }
    return out
