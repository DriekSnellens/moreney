"""Predeclared horizons and latency grids — never tune on OOS PnL."""

from __future__ import annotations

from typing import Any

from bot.opportunity.lead_lag.states import DataQuality


# Fixed research grids (do not optimize after seeing results).
HORIZON_MS_GRID: tuple[int, ...] = (50, 100, 250, 500, 1000, 2000, 5000)
LATENCY_MS_GRID: tuple[int, ...] = (0, 50, 100, 250, 500, 1000)


def classify_horizon_support(
    horizon_ms: int,
    *,
    data_quality: str,
    min_resolution_ms: float | None,
    has_synchronized_tape: bool,
) -> dict[str, Any]:
    """Mark horizons unsupported when data cannot honestly support them."""
    if not has_synchronized_tape:
        return {
            "horizon_ms": horizon_ms,
            "support": "UNSUPPORTED_BY_DATA",
            "reason": "No synchronized multi-venue book tape.",
        }
    if data_quality in {DataQuality.UNSUPPORTED.value, DataQuality.LOW.value}:
        if horizon_ms < 500:
            return {
                "horizon_ms": horizon_ms,
                "support": "UNSUPPORTED_BY_DATA",
                "reason": f"data_quality={data_quality} cannot support {horizon_ms}ms.",
            }
    if min_resolution_ms is not None and horizon_ms < min_resolution_ms:
        return {
            "horizon_ms": horizon_ms,
            "support": "UNSUPPORTED_BY_DATA",
            "reason": f"horizon {horizon_ms}ms < data resolution {min_resolution_ms}ms.",
        }
    return {
        "horizon_ms": horizon_ms,
        "support": "SUPPORTED" if data_quality == DataQuality.HIGH.value else "PARTIALLY_SUPPORTED",
        "reason": "",
    }


def horizon_support_table(
    *,
    data_quality: str,
    min_resolution_ms: float | None,
    has_synchronized_tape: bool,
) -> list[dict[str, Any]]:
    return [
        classify_horizon_support(
            h,
            data_quality=data_quality,
            min_resolution_ms=min_resolution_ms,
            has_synchronized_tape=has_synchronized_tape,
        )
        for h in HORIZON_MS_GRID
    ]
