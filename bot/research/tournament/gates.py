"""Signal statistics and gate helpers."""

from __future__ import annotations

import math
from typing import Sequence

from bot.research.tournament.contract import SignalStats
from bot.research.tournament.criteria import (
    MIN_DEV_OBSERVATIONS,
    MIN_DEV_SIGNALS,
    MIN_OOS_OBSERVATIONS,
    MIN_OOS_SIGNALS,
    MIN_SIGNAL_EFFECT,
    SIGNAL_UNCERTAINTY_Z,
)


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    idx = min(len(ys) - 1, max(0, int(round(p / 100 * (len(ys) - 1)))))
    return ys[idx]


def summarize_forwards(forwards: Sequence[float], *, observations: int) -> SignalStats:
    xs = [float(x) for x in forwards if x is not None and math.isfinite(x)]
    n = len(xs)
    if n == 0:
        return SignalStats(observations=observations, signals=0)
    mean = sum(xs) / n
    median = _pct(xs, 50)
    up = sum(1 for x in xs if x > 0) / n
    down = sum(1 for x in xs if x < 0) / n
    # std / sqrt(n) for CI
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / (n - 1)
        se = math.sqrt(var / n)
    else:
        se = float("inf")
    z = SIGNAL_UNCERTAINTY_Z
    return SignalStats(
        observations=observations,
        signals=n,
        conditional_forward_mean=mean,
        conditional_forward_median=median,
        up_probability=up,
        down_probability=down,
        effect_size=abs(mean),
        ci_low=mean - z * se if math.isfinite(se) else None,
        ci_high=mean + z * se if math.isfinite(se) else None,
        p10=_pct(xs, 10),
        p90=_pct(xs, 90),
    )


def supported_horizons_from_readiness(
    requested: Sequence[int],
    readiness: dict[str, str],
) -> tuple[list[int], list[int], str | None]:
    """Return (supported, unsupported, reason). Do not substitute horizons."""
    supported: list[int] = []
    unsupported: list[int] = []
    reasons: list[str] = []
    for h in requested:
        key = f"LEAD_LAG_{h}MS"
        status = readiness.get(key) or readiness.get(f"{h}ms") or readiness.get(str(h))
        if status in {"READY", "READY_WITH_CAUTION"}:
            supported.append(h)
        else:
            unsupported.append(h)
            reasons.append(f"{h}ms={status or 'NOT_READY'}")
    reason = None
    if unsupported and not supported:
        reason = "all_requested_horizons_unsupported: " + ", ".join(reasons)
    elif unsupported:
        reason = "partial: " + ", ".join(reasons)
    return supported, unsupported, reason


def sample_adequate_dev(stats: SignalStats) -> bool:
    return (
        stats.observations >= MIN_DEV_OBSERVATIONS and stats.signals >= MIN_DEV_SIGNALS
    )


def sample_adequate_oos(stats: SignalStats) -> bool:
    return (
        stats.observations >= MIN_OOS_OBSERVATIONS and stats.signals >= MIN_OOS_SIGNALS
    )


def has_predictive_signal(stats: SignalStats) -> bool:
    if stats.signals < MIN_DEV_SIGNALS:
        return False
    mean = stats.conditional_forward_mean
    if mean is None:
        return False
    if abs(mean) < MIN_SIGNAL_EFFECT:
        return False
    # Require CI not containing zero strongly opposite to signal direction
    if stats.ci_low is None or stats.ci_high is None:
        return abs(mean) >= MIN_SIGNAL_EFFECT * 10
    if mean > 0 and stats.ci_low <= 0:
        return False
    if mean < 0 and stats.ci_high >= 0:
        return False
    return True


def classify_oos(dev: SignalStats, oos: SignalStats) -> str:
    dm = dev.conditional_forward_mean
    om = oos.conditional_forward_mean
    if dm is None or om is None:
        return "DISAPPEARED"
    if dm == 0:
        return "DISAPPEARED" if om == 0 else "REVERSED"
    if (dm > 0) != (om > 0):
        return "REVERSED"
    if abs(om) < abs(dm) * 0.35:
        return "WEAKENED"
    if abs(om) < MIN_SIGNAL_EFFECT:
        return "DISAPPEARED"
    return "CONSISTENT"
