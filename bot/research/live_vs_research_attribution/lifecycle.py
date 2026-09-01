"""Canonical opportunity lifecycle stages."""

from __future__ import annotations

from enum import StrEnum


class LifecycleStage(StrEnum):
    MARKET_OBSERVED = "MARKET_OBSERVED"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    PROFITABILITY_EVALUATED = "PROFITABILITY_EVALUATED"
    GOE_EVALUATED = "GOE_EVALUATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    NO_FILL = "NO_FILL"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_MANAGED = "POSITION_MANAGED"
    EXIT_ORDER = "EXIT_ORDER"
    EXIT_FILL = "EXIT_FILL"
    ROUND_TRIP_REALIZED = "ROUND_TRIP_REALIZED"


class MatchClass(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    PROBABLE_MATCH = "PROBABLE_MATCH"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    NO_MATCH = "NO_MATCH"


LIFECYCLE_ORDER: tuple[LifecycleStage, ...] = (
    LifecycleStage.MARKET_OBSERVED,
    LifecycleStage.SIGNAL_CREATED,
    LifecycleStage.PROFITABILITY_EVALUATED,
    LifecycleStage.GOE_EVALUATED,
    LifecycleStage.RISK_EVALUATED,
    LifecycleStage.APPROVED,
    LifecycleStage.ORDER_SUBMITTED,
    LifecycleStage.FULL_FILL,
    LifecycleStage.POSITION_OPEN,
    LifecycleStage.POSITION_MANAGED,
    LifecycleStage.EXIT_FILL,
    LifecycleStage.ROUND_TRIP_REALIZED,
)
