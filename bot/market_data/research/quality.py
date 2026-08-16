"""Data-quality gates and horizon readiness — based only on data, not PnL."""

from __future__ import annotations

from typing import Any, Sequence

from bot.market_data.research.schema import ResearchMarketEvent, TimestampQuality
from bot.opportunity.lead_lag.horizons import HORIZON_MS_GRID


def classify_dataset_quality(
    events: Sequence[ResearchMarketEvent],
    *,
    venues_required: Sequence[str] = ("binance", "bitvavo", "okx"),
) -> dict[str, Any]:
    if not events:
        return {
            "grade": TimestampQuality.UNSUPPORTED.value,
            "reason": "no_events",
            "exchange_ts_coverage": 0.0,
            "receive_ts_coverage": 0.0,
            "sequence_coverage": 0.0,
        }

    n = len(events)
    ex = sum(1 for e in events if e.exchange_ts_available and e.exchange_ts_ns is not None)
    recv = sum(1 for e in events if e.received_ts_ns is not None)
    seq = sum(1 for e in events if e.sequence_number is not None)
    by_venue = {v: [e for e in events if e.venue == v] for v in venues_required}
    missing_venues = [v for v, xs in by_venue.items() if not xs]

    # Bitvavo without exchange_ts caps cross-venue causal quality
    bitvavo = by_venue.get("bitvavo") or []
    bitvavo_ex = sum(1 for e in bitvavo if e.exchange_ts_available)

    ex_cov = ex / n
    if missing_venues:
        grade = TimestampQuality.UNSUPPORTED.value
        reason = f"missing_venues={missing_venues}"
    elif bitvavo and bitvavo_ex == 0:
        grade = TimestampQuality.LOW.value
        reason = "bitvavo_exchange_ts_unsupported"
    elif ex_cov >= 0.9:
        grade = TimestampQuality.HIGH.value
        reason = "high_exchange_ts_coverage"
    elif ex_cov >= 0.5:
        grade = TimestampQuality.MEDIUM.value
        reason = "partial_exchange_ts_coverage"
    else:
        grade = TimestampQuality.LOW.value
        reason = "low_exchange_ts_coverage"

    return {
        "grade": grade,
        "reason": reason,
        "n_events": n,
        "exchange_ts_coverage": ex_cov,
        "receive_ts_coverage": recv / n,
        "sequence_coverage": seq / n,
        "venues_present": [v for v, xs in by_venue.items() if xs],
        "missing_venues": missing_venues,
    }


def horizon_readiness(
    quality_grade: str,
    *,
    sync_usable_rate_by_tol: dict[str, float] | None = None,
) -> dict[str, str]:
    """Map horizons → readiness. Never optimize from strategy PnL."""
    sync = sync_usable_rate_by_tol or {}
    out: dict[str, str] = {}
    for h in HORIZON_MS_GRID:
        key = str(float(h)) if str(float(h)) in sync else str(h)
        rate = sync.get(key, sync.get(str(h), 0.0))

        if quality_grade == TimestampQuality.UNSUPPORTED.value:
            out[f"LEAD_LAG_{h}MS"] = "NOT_READY"
            continue
        if h <= 100:
            if quality_grade == TimestampQuality.HIGH.value and rate >= 0.8:
                out[f"LEAD_LAG_{h}MS"] = "READY_WITH_CAUTION"
            else:
                out[f"LEAD_LAG_{h}MS"] = "NOT_READY"
        elif h <= 250:
            if quality_grade in {TimestampQuality.HIGH.value, TimestampQuality.MEDIUM.value} and rate >= 0.5:
                out[f"LEAD_LAG_{h}MS"] = "READY_WITH_CAUTION"
            elif quality_grade == TimestampQuality.LOW.value:
                out[f"LEAD_LAG_{h}MS"] = "NOT_READY"
            else:
                out[f"LEAD_LAG_{h}MS"] = "NOT_READY"
        else:  # >= 500
            if quality_grade == TimestampQuality.HIGH.value and rate >= 0.5:
                out[f"LEAD_LAG_{h}MS"] = "READY"
            elif quality_grade == TimestampQuality.MEDIUM.value and rate >= 0.3:
                out[f"LEAD_LAG_{h}MS"] = "READY_WITH_CAUTION"
            elif quality_grade == TimestampQuality.LOW.value and h >= 1000:
                out[f"LEAD_LAG_{h}MS"] = "READY_WITH_CAUTION"
            else:
                out[f"LEAD_LAG_{h}MS"] = "NOT_READY"
    return out


def reject_horizon_if_uncertain(
    horizon_ms: int,
    *,
    timestamp_uncertainty_ms: float,
) -> dict[str, Any]:
    """50ms research rejected when uncertainty exceeds tolerance."""
    if timestamp_uncertainty_ms > horizon_ms:
        return {
            "horizon_ms": horizon_ms,
            "allowed": False,
            "reason": (
                f"timestamp_uncertainty_ms={timestamp_uncertainty_ms} "
                f"> horizon_ms={horizon_ms}"
            ),
        }
    return {"horizon_ms": horizon_ms, "allowed": True, "reason": ""}
