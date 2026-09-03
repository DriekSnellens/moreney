"""Capital intelligence — dynamic reservation and opportunity reserve."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

_ZERO = Decimal("0")
_ONE = Decimal("1")


class CapitalBucket(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DEPLOYED = "DEPLOYED"
    LOCKED = "LOCKED"


@dataclass(frozen=True, slots=True)
class CapitalIntelligenceConfig:
    enabled: bool = True
    min_reserve_pct: Decimal = Decimal("0.05")
    max_reserve_pct: Decimal = Decimal("0.35")
    dead_market_reserve_boost: Decimal = Decimal("0.15")
    burst_reserve_reduction: Decimal = Decimal("0.10")
    low_quality_reserve_boost: Decimal = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class CapitalState:
    available_eur: Decimal
    reserved_eur: Decimal
    deployed_eur: Decimal
    locked_eur: Decimal
    reserve_need_pct: Decimal
    deployable_eur: Decimal
    capital_velocity: Decimal | None
    reasons: tuple[str, ...]


def assess_capital_state(
    *,
    total_budget_eur: Decimal,
    deployed_eur: Decimal,
    locked_eur: Decimal,
    candidate_count: int = 0,
    avg_opportunity_score: Decimal | None = None,
    market_volatility: Decimal | None = None,
    recent_trade_frequency: Decimal | None = None,
    is_dead_market: bool = False,
    is_opportunity_burst: bool = False,
    alphai_macro_active: bool = False,
    alphai_bullish_cluster: bool = False,
    realized_net_per_hour: Decimal | None = None,
    config: CapitalIntelligenceConfig | None = None,
) -> CapitalState:
    """Compute dynamic capital reservation without exceeding budget."""
    cfg = config or CapitalIntelligenceConfig()
    deployed = max(_ZERO, deployed_eur)
    locked = max(_ZERO, locked_eur)
    available = max(_ZERO, total_budget_eur - deployed - locked)

    reserve_pct = cfg.min_reserve_pct
    reasons: list[str] = []

    avg_score = avg_opportunity_score or Decimal("50")
    if avg_score < Decimal("55"):
        reserve_pct += cfg.low_quality_reserve_boost
        reasons.append("low_candidate_quality")
    elif avg_score >= Decimal("75"):
        reserve_pct -= Decimal("0.05")
        reasons.append("high_candidate_quality")

    if is_dead_market:
        reserve_pct += cfg.dead_market_reserve_boost
        reasons.append("dead_market")
    if is_opportunity_burst:
        reserve_pct -= cfg.burst_reserve_reduction
        reasons.append("opportunity_burst")

    # Soft news×regime fusion — never exceeds max_reserve_pct / never forces deploy.
    if alphai_macro_active or (is_dead_market and not alphai_bullish_cluster):
        reserve_pct += Decimal("0.05")
        reasons.append("alphai_defensive_reserve")
    elif alphai_bullish_cluster and is_opportunity_burst:
        reserve_pct -= Decimal("0.03")
        reasons.append("alphai_burst_deploy")

    if market_volatility is not None and market_volatility > Decimal("0.006"):
        reserve_pct += Decimal("0.05")
        reasons.append("elevated_volatility")

    if recent_trade_frequency is not None and recent_trade_frequency > Decimal("0.5"):
        reserve_pct -= Decimal("0.03")
        reasons.append("active_trading")

    reserve_pct = max(cfg.min_reserve_pct, min(cfg.max_reserve_pct, reserve_pct))
    reserved = (total_budget_eur * reserve_pct).quantize(Decimal("0.01"))
    reserved = min(reserved, available)
    deployable = max(_ZERO, available - reserved)

    velocity = realized_net_per_hour
    if velocity is None and deployed > 0 and total_budget_eur > 0:
        velocity = _ZERO

    return CapitalState(
        available_eur=available,
        reserved_eur=reserved,
        deployed_eur=deployed,
        locked_eur=locked,
        reserve_need_pct=reserve_pct,
        deployable_eur=deployable,
        capital_velocity=velocity,
        reasons=tuple(dict.fromkeys(reasons)) or ("default",),
    )


def config_from_settings(settings: Any) -> CapitalIntelligenceConfig:
    return CapitalIntelligenceConfig(
        enabled=bool(getattr(settings, "live_micro_capital_intelligence_enabled", True)),
    )
