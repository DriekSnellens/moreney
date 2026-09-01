"""Dynamic capital allocation — maximize NET per capital-hour within existing risk limits."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from bot.intelligence.capital_intelligence import (
    CapitalIntelligenceConfig,
    CapitalState,
    assess_capital_state,
)
from bot.strategies.opportunity_engine import (
    OpportunityAssessment,
    OpportunityDecision,
    rank_opportunities,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HOUR = Decimal("3600")
_MIN_SCORE = Decimal("0.001")
_MAX_SCORE = Decimal("10")


class ReserveMode(str, Enum):
    DEFENSIVE = "DEFENSIVE"
    NORMAL = "NORMAL"
    OPPORTUNITY_BURST = "OPPORTUNITY_BURST"


class AllocationDecision(str, Enum):
    GRANTED = "granted"
    REDUCED = "reduced"
    REJECTED = "rejected"
    ZERO = "zero"


@dataclass(frozen=True, slots=True)
class DynamicCapitalAllocatorConfig:
    enabled: bool = False
    shadow_only: bool = True
    allocation_multiplier: Decimal = Decimal("0.25")
    min_capital_opportunity_score: Decimal = Decimal("0.15")
    min_expected_net_per_capital_hour: Decimal = Decimal("0.0001")
    reservation_ttl_seconds: float = 30.0
    defensive_reserve_pct: Decimal = Decimal("0.30")
    normal_reserve_pct: Decimal = Decimal("0.20")
    burst_reserve_pct: Decimal = Decimal("0.12")
    scarcity_low_max: int = 2
    scarcity_medium_max: int = 7
    lock_low_minutes: Decimal = Decimal("3")
    lock_moderate_minutes: Decimal = Decimal("10")
    lock_high_minutes: Decimal = Decimal("30")
    lock_very_high_minutes: Decimal = Decimal("60")
    marginal_decay_per_100_eur: Decimal = Decimal("0.05")
    concentration_penalty_per_100_eur: Decimal = Decimal("0.04")
    min_quality_score: Decimal = Decimal("55")
    high_quality_score: Decimal = Decimal("75")
    min_allocation_eur: Decimal = Decimal("10")


@dataclass(frozen=True, slots=True)
class CandidateConstraints:
    strategy_size_eur: Decimal
    risk_size_eur: Decimal
    venue_limit_eur: Decimal
    symbol_limit_eur: Decimal
    sector_limit_eur: Decimal
    orderbook_depth_eur: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CapitalScoreComponents:
    expected_net: Decimal
    execution_probability: Decimal
    capital_velocity_factor: Decimal
    historical_quality_factor: Decimal
    liquidity_factor: Decimal
    capital_lock_factor: Decimal
    capital_opportunity_score: Decimal
    capital_velocity: Decimal | None
    expected_recycle_rate: Decimal | None
    capital_lock_penalty: Decimal
    marginal_allocation_factor: Decimal
    concentration_penalty: Decimal

    def as_dict(self) -> dict[str, str | None]:
        return {
            "expected_net": str(self.expected_net.quantize(Decimal("0.0001"))),
            "execution_probability": str(self.execution_probability.quantize(Decimal("0.01"))),
            "capital_velocity_factor": str(self.capital_velocity_factor.quantize(Decimal("0.01"))),
            "historical_quality_factor": str(self.historical_quality_factor.quantize(Decimal("0.01"))),
            "liquidity_factor": str(self.liquidity_factor.quantize(Decimal("0.01"))),
            "capital_lock_factor": str(self.capital_lock_factor.quantize(Decimal("0.01"))),
            "capital_opportunity_score": str(self.capital_opportunity_score.quantize(Decimal("0.01"))),
            "capital_velocity": (
                str(self.capital_velocity.quantize(Decimal("0.000001")))
                if self.capital_velocity is not None
                else None
            ),
            "expected_recycle_rate": (
                str(self.expected_recycle_rate.quantize(Decimal("0.01")))
                if self.expected_recycle_rate is not None
                else None
            ),
            "capital_lock_penalty": str(self.capital_lock_penalty.quantize(Decimal("0.01"))),
            "marginal_allocation_factor": str(
                self.marginal_allocation_factor.quantize(Decimal("0.01"))
            ),
            "concentration_penalty": str(self.concentration_penalty.quantize(Decimal("0.01"))),
        }


@dataclass(frozen=True, slots=True)
class AllocationResult:
    symbol: str
    venue: str
    requested_eur: Decimal
    allocated_eur: Decimal
    baseline_eur: Decimal
    decision: AllocationDecision
    reason: str
    capital_score: Decimal
    components: CapitalScoreComponents
    constraints_applied: tuple[str, ...]
    explanation: str
    velocity_label: str = "MEDIUM"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "requested_eur": str(self.requested_eur.quantize(Decimal("0.01"))),
            "allocated_eur": str(self.allocated_eur.quantize(Decimal("0.01"))),
            "baseline_eur": str(self.baseline_eur.quantize(Decimal("0.01"))),
            "decision": self.decision.value,
            "reason": self.reason,
            "capital_score": str(self.capital_score.quantize(Decimal("0.01"))),
            "velocity_label": self.velocity_label,
            "constraints_applied": list(self.constraints_applied),
            "components": self.components.as_dict(),
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class PortfolioAllocationSnapshot:
    total_equity_eur: Decimal
    free_eur: Decimal
    locked_notional_eur: Decimal
    underwater_capital_eur: Decimal
    resting_reserved_eur: Decimal
    reserve_target_eur: Decimal
    reserve_target_pct: Decimal
    deployable_capital_eur: Decimal
    reserve_mode: ReserveMode
    high_quality_count: int
    allocations: tuple[AllocationResult, ...]
    unused_deployable_eur: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_equity_eur": str(self.total_equity_eur.quantize(Decimal("0.01"))),
            "free_eur": str(self.free_eur.quantize(Decimal("0.01"))),
            "locked_notional_eur": str(self.locked_notional_eur.quantize(Decimal("0.01"))),
            "underwater_capital_eur": str(self.underwater_capital_eur.quantize(Decimal("0.01"))),
            "resting_reserved_eur": str(self.resting_reserved_eur.quantize(Decimal("0.01"))),
            "reserve_target_eur": str(self.reserve_target_eur.quantize(Decimal("0.01"))),
            "reserve_target_pct": str(self.reserve_target_pct.quantize(Decimal("0.01"))),
            "deployable_capital_eur": str(self.deployable_capital_eur.quantize(Decimal("0.01"))),
            "reserve_mode": self.reserve_mode.value,
            "high_quality_count": self.high_quality_count,
            "unused_deployable_eur": str(self.unused_deployable_eur.quantize(Decimal("0.01"))),
            "allocations": [a.as_dict() for a in self.allocations],
        }


@dataclass
class CapitalReservation:
    reservation_id: str
    symbol: str
    venue: str
    amount_eur: Decimal
    created_mono: float
    ttl_seconds: float

    def expired(self, now_mono: float) -> bool:
        return now_mono - self.created_mono > self.ttl_seconds


@dataclass
class CapitalReservationStore:
    """Atomic capital reservations with TTL to prevent double-allocation."""

    reservations: dict[str, CapitalReservation] = field(default_factory=dict)

    def purge_expired(self, now_mono: float | None = None) -> Decimal:
        now = now_mono if now_mono is not None else time.monotonic()
        freed = _ZERO
        expired = [rid for rid, r in self.reservations.items() if r.expired(now)]
        for rid in expired:
            freed += self.reservations.pop(rid).amount_eur
        return freed

    def reserved_total(self, now_mono: float | None = None) -> Decimal:
        now = now_mono if now_mono is not None else time.monotonic()
        self.purge_expired(now)
        return sum((r.amount_eur for r in self.reservations.values()), _ZERO)

    def reserve(
        self,
        *,
        symbol: str,
        venue: str,
        amount_eur: Decimal,
        ttl_seconds: float,
    ) -> str:
        rid = str(uuid4())
        self.reservations[rid] = CapitalReservation(
            reservation_id=rid,
            symbol=symbol,
            venue=venue,
            amount_eur=max(_ZERO, amount_eur),
            created_mono=time.monotonic(),
            ttl_seconds=ttl_seconds,
        )
        return rid

    def release(self, reservation_id: str) -> None:
        self.reservations.pop(reservation_id, None)


def _clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, value))


def config_from_settings(settings: Any) -> DynamicCapitalAllocatorConfig:
    return DynamicCapitalAllocatorConfig(
        enabled=bool(getattr(settings, "live_micro_dynamic_capital_enabled", False)),
        shadow_only=bool(getattr(settings, "live_micro_dynamic_capital_shadow", True)),
        allocation_multiplier=Decimal(
            str(getattr(settings, "live_micro_dynamic_capital_multiplier", 0.25))
        ),
        min_capital_opportunity_score=Decimal(
            str(getattr(settings, "live_micro_min_capital_opportunity_score", 0.15))
        ),
        min_expected_net_per_capital_hour=Decimal(
            str(getattr(settings, "live_micro_min_expected_net_per_capital_hour", 0.0001))
        ),
        reservation_ttl_seconds=float(
            getattr(settings, "live_micro_capital_reservation_ttl_seconds", 30.0)
        ),
        defensive_reserve_pct=Decimal(
            str(getattr(settings, "live_micro_defensive_reserve_pct", 0.30))
        ),
        normal_reserve_pct=Decimal(
            str(getattr(settings, "live_micro_normal_reserve_pct", 0.20))
        ),
        burst_reserve_pct=Decimal(
            str(getattr(settings, "live_micro_burst_reserve_pct", 0.12))
        ),
        scarcity_low_max=int(getattr(settings, "live_micro_scarcity_low_max", 2)),
        scarcity_medium_max=int(getattr(settings, "live_micro_scarcity_medium_max", 7)),
        lock_low_minutes=Decimal(str(getattr(settings, "live_micro_lock_low_minutes", 3))),
        lock_moderate_minutes=Decimal(
            str(getattr(settings, "live_micro_lock_moderate_minutes", 10))
        ),
        lock_high_minutes=Decimal(str(getattr(settings, "live_micro_lock_high_minutes", 30))),
        lock_very_high_minutes=Decimal(
            str(getattr(settings, "live_micro_lock_very_high_minutes", 60))
        ),
        marginal_decay_per_100_eur=Decimal(
            str(getattr(settings, "live_micro_marginal_decay_per_100_eur", 0.05))
        ),
        concentration_penalty_per_100_eur=Decimal(
            str(getattr(settings, "live_micro_concentration_penalty_per_100_eur", 0.04))
        ),
    )


def compute_capital_velocity(
    *,
    expected_net: Decimal,
    capital_eur: Decimal,
    expected_hold_seconds: Decimal | None,
) -> Decimal | None:
    """NET / capital / hour — first-class capital velocity metric."""
    if expected_net <= 0 or capital_eur <= 0:
        return None
    if expected_hold_seconds is None or expected_hold_seconds <= 0:
        return None
    hours = expected_hold_seconds / _HOUR
    if hours <= 0:
        return None
    return expected_net / (capital_eur * hours)


def compute_capital_lock_penalty(
    hold_minutes: Decimal,
    config: DynamicCapitalAllocatorConfig,
) -> Decimal:
    """Smooth lock penalty: longer expected lock → lower allocation appetite."""
    if hold_minutes <= config.lock_low_minutes:
        return _ONE
    if hold_minutes >= config.lock_very_high_minutes:
        lo = float(config.lock_high_minutes)
        hi = float(config.lock_very_high_minutes)
        t = _clamp(
            (hold_minutes - config.lock_high_minutes)
            / Decimal(str(max(hi - lo, 1.0))),
            _ZERO,
            _ONE,
        )
        return Decimal(str(max(0.15, 0.35 * (1.0 - float(t)))))
    if hold_minutes >= config.lock_high_minutes:
        lo = float(config.lock_moderate_minutes)
        hi = float(config.lock_high_minutes)
        t = (float(hold_minutes) - lo) / max(hi - lo, 1.0)
        return Decimal(str(max(0.35, 0.65 - 0.30 * t)))
    if hold_minutes >= config.lock_moderate_minutes:
        lo = float(config.lock_low_minutes)
        hi = float(config.lock_moderate_minutes)
        t = (float(hold_minutes) - lo) / max(hi - lo, 1.0)
        return Decimal(str(max(0.65, 0.90 - 0.25 * t)))
    lo = float(config.lock_low_minutes)
    hi = float(config.lock_moderate_minutes)
    t = (float(hold_minutes) - lo) / max(hi - lo, 1.0)
    return Decimal(str(max(0.90, 1.0 - 0.10 * t)))


def compute_expected_recycle_rate(
    *,
    expected_hold_seconds: Decimal | None,
    historical_hold_seconds: Decimal | None,
    execution_probability: Decimal,
    exit_probability: Decimal = Decimal("0.85"),
) -> Decimal | None:
    """Expected capital recycle rate from decision-time information only."""
    hold = expected_hold_seconds
    if historical_hold_seconds is not None and historical_hold_seconds > 0:
        if hold is None or hold <= 0:
            hold = historical_hold_seconds
        else:
            hold = (hold + historical_hold_seconds) / Decimal("2")
    if hold is None or hold <= 0:
        return None
    hours = hold / _HOUR
    if hours <= 0:
        return None
    prob = _clamp(execution_probability * exit_probability, _ZERO, _ONE)
    return prob / hours


def compute_marginal_allocation_factor(
    cumulative_allocated_eur: Decimal,
    config: DynamicCapitalAllocatorConfig,
) -> Decimal:
    """Diminishing returns for marginal capital chunks."""
    if cumulative_allocated_eur <= Decimal("100"):
        return _ONE
    steps = (cumulative_allocated_eur - Decimal("100")) / Decimal("100")
    decay = config.marginal_decay_per_100_eur * steps
    return _clamp(_ONE - decay, Decimal("0.40"), _ONE)


def compute_concentration_penalty(
    existing_symbol_allocation_eur: Decimal,
    config: DynamicCapitalAllocatorConfig,
) -> Decimal:
    if existing_symbol_allocation_eur <= 0:
        return _ONE
    steps = existing_symbol_allocation_eur / Decimal("100")
    penalty = config.concentration_penalty_per_100_eur * steps
    return _clamp(_ONE - penalty, Decimal("0.50"), _ONE)


def compute_capital_velocity_factor(
    capital_velocity: Decimal | None,
    config: DynamicCapitalAllocatorConfig,
) -> Decimal:
    if capital_velocity is None or capital_velocity <= 0:
        return Decimal("0.85")
    ref = config.min_expected_net_per_capital_hour * Decimal("10")
    if ref <= 0:
        ref = Decimal("0.001")
    ratio = capital_velocity / ref
    return _clamp(ratio, Decimal("0.70"), Decimal("1.50"))


def compute_score_components(
    assessment: OpportunityAssessment,
    *,
    config: DynamicCapitalAllocatorConfig,
    historical_quality: Decimal = _ONE,
    existing_symbol_allocation_eur: Decimal = _ZERO,
    cumulative_marginal_eur: Decimal = _ZERO,
) -> CapitalScoreComponents:
    expected_net = max(_ZERO, assessment.expected_net_profit_eur)
    if expected_net <= 0:
        meta_net = assessment.expected_net_profit_eur
        expected_net = max(_ZERO, meta_net)

    exec_prob = _clamp(assessment.fill_probability, Decimal("0.05"), _ONE)
    liquidity = _clamp(
        assessment.liquidity_score if assessment.liquidity_score > 0 else Decimal("0.75"),
        Decimal("0.50"),
        _ONE,
    )
    hist = _clamp(historical_quality, Decimal("0.80"), Decimal("1.20"))

    hold_sec = assessment.expected_hold_seconds
    hold_min = (
        hold_sec / Decimal("60") if hold_sec is not None and hold_sec > 0 else Decimal("30")
    )
    lock_penalty = compute_capital_lock_penalty(hold_min, config)
    lock_factor = _clamp(lock_penalty, Decimal("0.15"), _ONE)

    requested = assessment.capital_required_eur * assessment.recommended_size_multiplier
    velocity = compute_capital_velocity(
        expected_net=expected_net,
        capital_eur=requested if requested > 0 else assessment.capital_required_eur,
        expected_hold_seconds=hold_sec,
    )
    velocity_factor = compute_capital_velocity_factor(velocity, config)

    recycle = compute_expected_recycle_rate(
        expected_hold_seconds=hold_sec,
        historical_hold_seconds=hold_sec,
        execution_probability=exec_prob,
    )

    marginal = compute_marginal_allocation_factor(cumulative_marginal_eur, config)
    concentration = compute_concentration_penalty(existing_symbol_allocation_eur, config)

    raw_score = expected_net * exec_prob * velocity_factor * hist * liquidity / lock_factor
    adjusted = raw_score * marginal * concentration
    score = _clamp(adjusted, _MIN_SCORE, _MAX_SCORE)

    return CapitalScoreComponents(
        expected_net=expected_net,
        execution_probability=exec_prob,
        capital_velocity_factor=velocity_factor,
        historical_quality_factor=hist,
        liquidity_factor=liquidity,
        capital_lock_factor=lock_factor,
        capital_opportunity_score=score,
        capital_velocity=velocity,
        expected_recycle_rate=recycle,
        capital_lock_penalty=lock_penalty,
        marginal_allocation_factor=marginal,
        concentration_penalty=concentration,
    )


def determine_reserve_mode(
    *,
    high_quality_count: int,
    is_dead_market: bool,
    is_opportunity_burst: bool,
    config: DynamicCapitalAllocatorConfig,
) -> ReserveMode:
    if is_dead_market or high_quality_count <= config.scarcity_low_max:
        return ReserveMode.DEFENSIVE
    if is_opportunity_burst and high_quality_count > config.scarcity_medium_max:
        return ReserveMode.OPPORTUNITY_BURST
    return ReserveMode.NORMAL


def reserve_pct_for_mode(mode: ReserveMode, config: DynamicCapitalAllocatorConfig) -> Decimal:
    if mode == ReserveMode.DEFENSIVE:
        return config.defensive_reserve_pct
    if mode == ReserveMode.OPPORTUNITY_BURST:
        return config.burst_reserve_pct
    return config.normal_reserve_pct


def compute_deployable_capital(
    *,
    total_equity_eur: Decimal,
    free_eur: Decimal,
    locked_notional_eur: Decimal,
    underwater_capital_eur: Decimal,
    resting_reserved_eur: Decimal,
    reservation_store: CapitalReservationStore | None = None,
    high_quality_count: int = 0,
    avg_opportunity_score: Decimal | None = None,
    is_dead_market: bool,
    is_opportunity_burst: bool,
    capital_config: CapitalIntelligenceConfig | None = None,
    allocator_config: DynamicCapitalAllocatorConfig | None = None,
) -> tuple[CapitalState, ReserveMode, Decimal, Decimal]:
    """Combine capital intelligence with dynamic reserve modes."""
    cfg = allocator_config or DynamicCapitalAllocatorConfig()
    cap_cfg = capital_config or CapitalIntelligenceConfig()

    deployed = max(_ZERO, locked_notional_eur)
    locked = max(_ZERO, resting_reserved_eur)
    if reservation_store is not None:
        locked += reservation_store.reserved_total()

    available = max(_ZERO, free_eur - underwater_capital_eur)
    cap_state = assess_capital_state(
        total_budget_eur=total_equity_eur,
        deployed_eur=deployed,
        locked_eur=locked,
        candidate_count=high_quality_count,
        avg_opportunity_score=avg_opportunity_score,
        is_dead_market=is_dead_market,
        is_opportunity_burst=is_opportunity_burst,
        config=cap_cfg,
    )

    mode = determine_reserve_mode(
        high_quality_count=high_quality_count,
        is_dead_market=is_dead_market,
        is_opportunity_burst=is_opportunity_burst,
        config=cfg,
    )
    mode_pct = reserve_pct_for_mode(mode, cfg)
    reserve_target = min(
        (total_equity_eur * mode_pct).quantize(Decimal("0.01")),
        available,
    )
    deployable = max(_ZERO, available - reserve_target - cap_state.reserved_eur)
    deployable = min(deployable, cap_state.deployable_eur)
    return cap_state, mode, reserve_target, deployable


def _velocity_label(velocity: Decimal | None) -> str:
    if velocity is None:
        return "LOW"
    if velocity >= Decimal("0.001"):
        return "HIGH"
    if velocity >= Decimal("0.0003"):
        return "MEDIUM"
    return "LOW"


def _base_symbol(sym: str) -> str:
    s = sym.upper()
    for quote in ("EUR", "USDT", "USDC", "USD"):
        if s.endswith(quote):
            return s[: -len(quote)]
    return s


def _corr_key(base: str, corr_groups: dict[str, frozenset[str]] | None) -> str:
    groups = corr_groups or {}
    for key, members in groups.items():
        if base in members:
            return key
    return base


def _explain_allocation(
    *,
    symbol: str,
    venue: str,
    requested: Decimal,
    allocated: Decimal,
    components: CapitalScoreComponents,
    constraints: tuple[str, ...],
    reason: str,
) -> str:
    vel = components.capital_velocity
    vel_str = f"{vel.quantize(Decimal('0.000001'))}" if vel is not None else "n/a"
    hold_min = "n/a"
    lines = [
        "CAPITAL ALLOCATION",
        f"Symbol: {_base_symbol(symbol)}",
        f"Venue: {venue}",
        f"Requested: €{requested.quantize(Decimal('0.01'))}",
        f"Allocated: €{allocated.quantize(Decimal('0.01'))}",
        "Why:",
        f"  NET edge: {components.expected_net.quantize(Decimal('0.0001'))}",
        f"  Execution probability: {components.execution_probability.quantize(Decimal('0.01'))}",
        f"  Capital velocity: {vel_str}",
        f"  Historical quality: {components.historical_quality_factor.quantize(Decimal('0.01'))}",
        f"  Liquidity: {components.liquidity_factor.quantize(Decimal('0.01'))}",
        f"  Lock penalty: {components.capital_lock_penalty.quantize(Decimal('0.01'))}",
        f"Constraints: {', '.join(constraints) if constraints else 'none'}",
        f"Capital score: {components.capital_opportunity_score.quantize(Decimal('0.01'))}",
        f"Reason: {reason}",
    ]
    return "\n".join(lines)


def _max_cap(constraints: CandidateConstraints) -> Decimal:
    caps = [
        constraints.strategy_size_eur,
        constraints.risk_size_eur,
        constraints.venue_limit_eur,
        constraints.symbol_limit_eur,
        constraints.sector_limit_eur,
    ]
    if constraints.orderbook_depth_eur is not None and constraints.orderbook_depth_eur > 0:
        caps.append(constraints.orderbook_depth_eur)
    positive = [c for c in caps if c > 0]
    return min(positive) if positive else _ZERO


def allocate_portfolio_dynamic(
    assessments: Sequence[OpportunityAssessment],
    *,
    deployable_capital_eur: Decimal,
    constraints_for: Any | None = None,
    corr_groups: dict[str, frozenset[str]] | None = None,
    max_per_corr_group: int = 2,
    existing_symbol_allocations: dict[str, Decimal] | None = None,
    existing_venue_allocations: dict[str, Decimal] | None = None,
    existing_sector_allocations: dict[str, Decimal] | None = None,
    historical_quality_for: Any | None = None,
    config: DynamicCapitalAllocatorConfig | None = None,
    use_velocity: bool = True,
    use_concentration: bool = True,
    allocation_multiplier: Decimal | None = None,
) -> tuple[list[tuple[OpportunityAssessment, AllocationResult]], list[OpportunityAssessment]]:
    """Portfolio-level dynamic allocation — never exceeds existing limits."""
    cfg = config or DynamicCapitalAllocatorConfig()
    mult = allocation_multiplier if allocation_multiplier is not None else cfg.allocation_multiplier
    mult = _clamp(mult, _ZERO, _ONE)

    ranked = rank_opportunities(
        [a for a in assessments if a.decision != OpportunityDecision.REJECT]
    )
    sym_alloc = dict(existing_symbol_allocations or {})
    venue_alloc = dict(existing_venue_allocations or {})
    sector_alloc = dict(existing_sector_allocations or {})
    corr_counts: dict[str, int] = {}

    scored: list[tuple[OpportunityAssessment, CapitalScoreComponents, Decimal]] = []
    for assessment in ranked:
        sym = _base_symbol(assessment.symbol)
        existing_sym = sym_alloc.get(sym, _ZERO)
        cumulative = existing_sym
        hist_q = _ONE
        if historical_quality_for is not None:
            try:
                hist_q = Decimal(str(historical_quality_for(assessment)))
            except Exception:  # noqa: BLE001
                hist_q = _ONE
        components = compute_score_components(
            assessment,
            config=cfg,
            historical_quality=hist_q,
            existing_symbol_allocation_eur=existing_sym if use_concentration else _ZERO,
            cumulative_marginal_eur=cumulative,
        )
        if components.capital_opportunity_score < cfg.min_capital_opportunity_score:
            continue
        if use_velocity and components.capital_velocity is not None:
            if components.capital_velocity < cfg.min_expected_net_per_capital_hour:
                penalty = components.capital_velocity / cfg.min_expected_net_per_capital_hour
                components = replace(
                    components,
                    capital_opportunity_score=components.capital_opportunity_score
                    * _clamp(penalty, Decimal("0.10"), _ONE),
                )
        weight = components.capital_opportunity_score
        if use_velocity and components.capital_velocity is not None:
            weight *= _clamp(
                components.capital_velocity * Decimal("1000"),
                Decimal("0.50"),
                Decimal("2.0"),
            )
        scored.append((assessment, components, weight))

    if not scored:
        skipped = list(assessments)
        return [], skipped

    total_weight = sum((w for _, _, w in scored), _ZERO)
    if total_weight <= 0:
        return [], list(assessments)

    budget = deployable_capital_eur
    results: list[tuple[OpportunityAssessment, AllocationResult]] = []
    skipped: list[OpportunityAssessment] = []
    used = _ZERO

    for assessment, components, weight in scored:
        sym = _base_symbol(assessment.symbol)
        venue = (assessment.venue or "bitvavo").lower()
        ck = _corr_key(sym, corr_groups)
        if corr_counts.get(ck, 0) >= max_per_corr_group:
            skipped.append(assessment)
            continue

        requested = assessment.capital_required_eur * assessment.recommended_size_multiplier
        baseline = requested

        if constraints_for is not None:
            try:
                raw_c = constraints_for(assessment)
                if isinstance(raw_c, CandidateConstraints):
                    constraints = raw_c
                else:
                    constraints = CandidateConstraints(**raw_c)
            except Exception:  # noqa: BLE001
                constraints = CandidateConstraints(
                    strategy_size_eur=requested,
                    risk_size_eur=requested,
                    venue_limit_eur=budget,
                    symbol_limit_eur=requested,
                    sector_limit_eur=requested,
                )
        else:
            constraints = CandidateConstraints(
                strategy_size_eur=requested,
                risk_size_eur=requested,
                venue_limit_eur=budget,
                symbol_limit_eur=requested,
                sector_limit_eur=requested,
            )

        max_cap = _max_cap(constraints)
        raw_share = (weight / total_weight) * budget if total_weight > 0 else _ZERO
        raw_alloc = min(raw_share, max_cap, requested, budget - used)
        raw_alloc = raw_alloc * mult

        applied: list[str] = []
        if raw_alloc < requested:
            applied.append("dynamic_sizing")
        if max_cap < requested:
            if max_cap == constraints.symbol_limit_eur:
                applied.append("symbol_cap")
            elif max_cap == constraints.sector_limit_eur:
                applied.append("sector_cap")
            elif max_cap == constraints.venue_limit_eur:
                applied.append("venue_cap")
            elif max_cap == constraints.risk_size_eur:
                applied.append("risk_cap")
            elif max_cap == constraints.strategy_size_eur:
                applied.append("strategy_cap")
            elif constraints.orderbook_depth_eur and max_cap == constraints.orderbook_depth_eur:
                applied.append("orderbook_depth")

        if raw_alloc < cfg.min_allocation_eur:
            result = AllocationResult(
                symbol=assessment.symbol,
                venue=venue,
                requested_eur=requested,
                allocated_eur=_ZERO,
                baseline_eur=baseline,
                decision=AllocationDecision.ZERO,
                reason="capital_velocity"
                if use_velocity
                and components.capital_velocity is not None
                and components.capital_velocity < cfg.min_expected_net_per_capital_hour
                else "insufficient_quality",
                capital_score=components.capital_opportunity_score,
                components=components,
                constraints_applied=tuple(applied),
                explanation=_explain_allocation(
                    symbol=assessment.symbol,
                    venue=venue,
                    requested=requested,
                    allocated=_ZERO,
                    components=components,
                    constraints=tuple(applied),
                    reason="below_min_allocation",
                ),
                velocity_label=_velocity_label(components.capital_velocity),
            )
            skipped.append(assessment)
            results.append((assessment, result))
            continue

        if budget - used < raw_alloc:
            raw_alloc = max(_ZERO, budget - used)
            applied.append("reserve")

        decision = AllocationDecision.GRANTED
        reason = "optimal"
        if raw_alloc < requested * Decimal("0.95"):
            decision = AllocationDecision.REDUCED
            reason = applied[-1] if applied else "reserve"

        if raw_alloc <= 0:
            skipped.append(assessment)
            continue

        result = AllocationResult(
            symbol=assessment.symbol,
            venue=venue,
            requested_eur=requested,
            allocated_eur=raw_alloc.quantize(Decimal("0.01")),
            baseline_eur=baseline,
            decision=decision,
            reason=reason,
            capital_score=components.capital_opportunity_score,
            components=components,
            constraints_applied=tuple(applied),
            explanation=_explain_allocation(
                symbol=assessment.symbol,
                venue=venue,
                requested=requested,
                allocated=raw_alloc,
                components=components,
                constraints=tuple(applied),
                reason=reason,
            ),
            velocity_label=_velocity_label(components.capital_velocity),
        )
        results.append((assessment, result))
        used += raw_alloc
        sym_alloc[sym] = sym_alloc.get(sym, _ZERO) + raw_alloc
        venue_alloc[venue] = venue_alloc.get(venue, _ZERO) + raw_alloc
        sector_alloc[ck] = sector_alloc.get(ck, _ZERO) + raw_alloc
        corr_counts[ck] = corr_counts.get(ck, 0) + 1

    selected_ids = {id(a) for a, _ in results if _.allocated_eur > 0}
    for assessment in assessments:
        if assessment.decision == OpportunityDecision.REJECT:
            skipped.append(assessment)
        elif id(assessment) not in selected_ids and assessment not in skipped:
            skipped.append(assessment)

    selected = [(a, r) for a, r in results if r.allocated_eur > 0]
    return selected, skipped


def apply_dynamic_allocation_to_assessment(
    assessment: OpportunityAssessment,
    allocation: AllocationResult,
) -> OpportunityAssessment:
    """Downward-only size adjustment from dynamic allocation."""
    if allocation.allocated_eur <= 0 or assessment.capital_required_eur <= 0:
        return assessment
    ratio = allocation.allocated_eur / assessment.capital_required_eur
    ratio = min(_ONE, ratio)
    new_mult = min(_ONE, assessment.recommended_size_multiplier * ratio)
    return replace(assessment, recommended_size_multiplier=new_mult)


def run_portfolio_allocation(
    assessments: Sequence[OpportunityAssessment],
    *,
    total_equity_eur: Decimal,
    free_eur: Decimal,
    locked_notional_eur: Decimal = _ZERO,
    underwater_capital_eur: Decimal = _ZERO,
    resting_reserved_eur: Decimal = _ZERO,
    reservation_store: CapitalReservationStore | None = None,
    is_dead_market: bool = False,
    is_opportunity_burst: bool = False,
    corr_groups: dict[str, frozenset[str]] | None = None,
    max_per_corr_group: int = 2,
    constraints_for: Any | None = None,
    historical_quality_for: Any | None = None,
    capital_config: CapitalIntelligenceConfig | None = None,
    config: DynamicCapitalAllocatorConfig | None = None,
    use_velocity: bool = True,
    use_concentration: bool = True,
) -> PortfolioAllocationSnapshot:
    """Full portfolio allocation with reserve computation."""
    cfg = config or DynamicCapitalAllocatorConfig()
    accepted = [
        a
        for a in assessments
        if a.decision != OpportunityDecision.REJECT
        and a.opportunity_score >= cfg.min_quality_score
    ]
    high_q = sum(
        1
        for a in accepted
        if a.opportunity_score >= cfg.high_quality_score
        or a.decision == OpportunityDecision.HIGH_QUALITY
    )
    avg_score = None
    if accepted:
        avg_score = sum((a.opportunity_score for a in accepted), _ZERO) / Decimal(len(accepted))

    _, mode, reserve_target, deployable = compute_deployable_capital(
        total_equity_eur=total_equity_eur,
        free_eur=free_eur,
        locked_notional_eur=locked_notional_eur,
        underwater_capital_eur=underwater_capital_eur,
        resting_reserved_eur=resting_reserved_eur,
        reservation_store=reservation_store,
        high_quality_count=high_q,
        avg_opportunity_score=avg_score,
        is_dead_market=is_dead_market,
        is_opportunity_burst=is_opportunity_burst,
        capital_config=capital_config,
        allocator_config=cfg,
    )

    selected, _skipped = allocate_portfolio_dynamic(
        assessments,
        deployable_capital_eur=deployable,
        constraints_for=constraints_for,
        corr_groups=corr_groups,
        max_per_corr_group=max_per_corr_group,
        historical_quality_for=historical_quality_for,
        config=cfg,
        use_velocity=use_velocity,
        use_concentration=use_concentration,
    )
    alloc_results = tuple(r for _, r in selected)
    used = sum((r.allocated_eur for r in alloc_results), _ZERO)

    reserve_pct = (
        reserve_target / total_equity_eur if total_equity_eur > 0 else _ZERO
    ).quantize(Decimal("0.01"))

    return PortfolioAllocationSnapshot(
        total_equity_eur=total_equity_eur,
        free_eur=free_eur,
        locked_notional_eur=locked_notional_eur,
        underwater_capital_eur=underwater_capital_eur,
        resting_reserved_eur=resting_reserved_eur,
        reserve_target_eur=reserve_target,
        reserve_target_pct=reserve_pct,
        deployable_capital_eur=deployable,
        reserve_mode=mode,
        high_quality_count=high_q,
        allocations=alloc_results,
        unused_deployable_eur=max(_ZERO, deployable - used),
    )
