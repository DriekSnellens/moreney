"""OOS verdict engine — criteria frozen in code, never tuned after seeing results."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.strategy_lab.types import Scorecard

# ---------------------------------------------------------------------------
# FROZEN acceptance criteria (do not edit after first OOS run without bumping
# criteria_version and discarding prior OOS_PROMISING / OOS_ROBUST claims).
# ---------------------------------------------------------------------------
CRITERIA_VERSION = "strategy_lab_verdict_v1"

MIN_OOS_COMPLETED = 30
MIN_OOS_INDEPENDENT_EVENTS = 20
MIN_DEV_COMPLETED = 20
MIN_OOS_NET_EUR = Decimal("0")
MIN_OOS_NET_PER_CAPITAL_SECOND = Decimal("0")
MAX_OOS_DRAWDOWN_EUR = Decimal("50")
MIN_WIN_RATE_FOR_ROBUST = 0.45
MAX_PARTICIPATION_FOR_NO_EDGE_CLAIM = 0.02  # trading almost never ≠ edge


def verdict_for_scorecard(
    *,
    development: Scorecard,
    oos: Scorecard | None,
) -> str:
    """Assign a lab verdict from frozen criteria. Never looks at live PnL."""
    if development.opportunities < 5 and (oos is None or oos.opportunities < 5):
        return "INSUFFICIENT_DATA"
    if development.completed < MIN_DEV_COMPLETED and (
        oos is None or oos.completed < MIN_OOS_COMPLETED
    ):
        if development.accepted == 0 and (oos is None or oos.accepted == 0):
            return "INSUFFICIENT_DATA"
        if development.completed < 5:
            return "INSUFFICIENT_DATA"

    # Negative after costs on development with meaningful sample
    if development.completed >= 10 and development.realized_net_eur < 0:
        if oos is None:
            return "EDGE_NEGATIVE_AFTER_COSTS"
        if oos.completed >= 5 and oos.realized_net_eur < 0:
            return "EDGE_NEGATIVE_AFTER_COSTS"

    if oos is None:
        if development.realized_net_eur > 0 and development.completed >= MIN_DEV_COMPLETED:
            return "IN_SAMPLE_ONLY"
        if development.realized_net_eur <= 0:
            return "NO_EDGE"
        return "INSUFFICIENT_DATA"

    if oos.completed < MIN_OOS_COMPLETED or oos.independent_events < MIN_OOS_INDEPENDENT_EVENTS:
        if development.realized_net_eur > 0:
            return "IN_SAMPLE_ONLY"
        return "INSUFFICIENT_DATA"

    if oos.realized_net_eur < MIN_OOS_NET_EUR:
        return "EDGE_NEGATIVE_AFTER_COSTS"

    # Unstable: OOS sign flips vs development or severe drawdown
    if development.realized_net_eur > 0 and oos.realized_net_eur <= 0:
        return "OOS_UNSTABLE"
    if oos.max_drawdown_eur > MAX_OOS_DRAWDOWN_EUR:
        return "OOS_UNSTABLE"

    # Participation trap: almost never trading with tiny positive sample
    if (
        oos.participation_rate < MAX_PARTICIPATION_FOR_NO_EDGE_CLAIM
        and oos.completed < MIN_OOS_COMPLETED
    ):
        return "INSUFFICIENT_DATA"

    velocity_ok = (
        oos.capital_velocity is not None
        and oos.capital_velocity >= MIN_OOS_NET_PER_CAPITAL_SECOND
    )
    if (
        oos.realized_net_eur > MIN_OOS_NET_EUR
        and velocity_ok
        and oos.win_rate >= MIN_WIN_RATE_FOR_ROBUST
        and oos.max_drawdown_eur <= MAX_OOS_DRAWDOWN_EUR
        and oos.independent_events >= MIN_OOS_INDEPENDENT_EVENTS * 2
    ):
        return "OOS_ROBUST"

    if oos.realized_net_eur > MIN_OOS_NET_EUR and velocity_ok:
        return "OOS_PROMISING"

    if development.realized_net_eur > 0 and oos.realized_net_eur > 0:
        return "OOS_PROMISING"

    return "NO_EDGE"


def criteria_manifest() -> dict[str, Any]:
    return {
        "criteria_version": CRITERIA_VERSION,
        "min_oos_completed": MIN_OOS_COMPLETED,
        "min_oos_independent_events": MIN_OOS_INDEPENDENT_EVENTS,
        "min_dev_completed": MIN_DEV_COMPLETED,
        "min_oos_net_eur": str(MIN_OOS_NET_EUR),
        "min_oos_net_per_capital_second": str(MIN_OOS_NET_PER_CAPITAL_SECOND),
        "max_oos_drawdown_eur": str(MAX_OOS_DRAWDOWN_EUR),
        "min_win_rate_for_robust": MIN_WIN_RATE_FOR_ROBUST,
        "note": "Frozen before OOS inspection. Bump criteria_version to change.",
    }
