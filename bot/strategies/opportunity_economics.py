"""Opportunity economics: capital velocity, venue ranking, MFE analytics.

Deterministic core — no exchange I/O, no wall-clock in pure functions.
Uses existing ProfitabilityResult and EntryQualityAssessment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from bot.core.enums import EntryQualityRecommendation, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.strategies.entry_quality import (
    EntryQualityAssessment,
    EntryQualityConfig,
    apply_size_multiplier,
    evaluate_entry_quality,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HOUR = Decimal("3600")


@dataclass(frozen=True, slots=True)
class CapitalEfficiencyConfig:
    enabled: bool = True
    min_expected_net_profit_per_hour: Decimal = Decimal("0.05")
    default_hold_seconds: Decimal = Decimal("1800")
    min_hold_seconds: Decimal = Decimal("300")
    max_hold_seconds: Decimal = Decimal("7200")
    extension_moderate_pct: Decimal = Decimal("0.012")
    extension_max_pct: Decimal = Decimal("0.025")
    reduced_efficiency_multiplier: Decimal = Decimal("0.75")
    small_efficiency_multiplier: Decimal = Decimal("0.50")
    min_efficiency_score: Decimal = Decimal("0.00001")


@dataclass(frozen=True, slots=True)
class VenueEconomicsConfig:
    enabled: bool = True
    default_fill_probability: Decimal = Decimal("0.85")


@dataclass(frozen=True, slots=True)
class CapitalEfficiencyAssessment:
    expected_net_profit_eur: Decimal
    expected_hold_seconds: Decimal | None
    expected_net_profit_per_hour: Decimal | None
    capital_required_eur: Decimal
    capital_efficiency_per_capital_hour: Decimal | None
    capital_efficiency_score: Decimal
    recommended_size_multiplier: Decimal
    recommendation: EntryQualityRecommendation
    reject_reason: str = ""


@dataclass(frozen=True, slots=True)
class VenueEconomicsScore:
    venue: str
    expected_net_profit_eur: Decimal
    fill_probability: Decimal
    economic_score: Decimal


@dataclass(frozen=True, slots=True)
class OpportunityEconomics:
    """Unified economic view of one candidate."""

    entry_quality: EntryQualityAssessment | None
    capital_efficiency: CapitalEfficiencyAssessment | None
    venue: str | None
    venue_scores: tuple[VenueEconomicsScore, ...]
    combined_multiplier: Decimal
    recommendation: EntryQualityRecommendation
    reject_reason: str = ""


@dataclass(frozen=True, slots=True)
class MFERecord:
    """Closed-trade MFE analytics (price-based NET proxy)."""

    entry_price: Decimal
    exit_price: Decimal
    mfe_price: Decimal
    mae_price: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal
    realized_net_pct: Decimal
    mfe_capture_ratio: Decimal | None
    holding_seconds: Decimal | None


@dataclass
class EconomicDiagnostics:
    """Session counters for profit-efficiency observability."""

    entry_quality: Any = field(default_factory=lambda: _EntryQualityCounters())
    capital_efficiency_candidates: int = 0
    capital_efficiency_reduced: int = 0
    capital_efficiency_rejected: int = 0
    venue_bitvavo_selected: int = 0
    venue_okx_selected: int = 0
    mfe_capture_samples: int = 0
    adaptive_trail_harvest: int = 0
    adaptive_trail_hold: int = 0
    _sum_mfe_capture: Decimal = _ZERO
    _sum_hold_seconds: Decimal = _ZERO
    _sum_net_eur_per_hour: Decimal = _ZERO
    _net_eur_per_hour_samples: int = 0
    _sum_realized_net_eur: Decimal = _ZERO
    _capital_deployed_eur: Decimal = _ZERO
    _capital_locked_eur: Decimal = _ZERO

    def record_mfe(self, record: MFERecord) -> None:
        self.mfe_capture_samples += 1
        if record.mfe_capture_ratio is not None:
            self._sum_mfe_capture += record.mfe_capture_ratio
        if record.holding_seconds is not None:
            self._sum_hold_seconds += record.holding_seconds

    def record_net_per_hour(self, eur_per_hour: Decimal) -> None:
        if eur_per_hour <= 0:
            return
        self._net_eur_per_hour_samples += 1
        self._sum_net_eur_per_hour += eur_per_hour

    def snapshot(self, *, entry_quality_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        eq = entry_quality_extra or {}
        mfe_n = self.mfe_capture_samples or 0
        hr_n = self._net_eur_per_hour_samples or 0
        hold_n = mfe_n or 1
        util_pct = None
        if self._capital_deployed_eur > 0:
            util_pct = (
                (self._capital_deployed_eur - self._capital_locked_eur)
                / self._capital_deployed_eur
                * Decimal("100")
            ).quantize(Decimal("0.1"))
        out = {
            "capital_efficiency_candidates": self.capital_efficiency_candidates,
            "capital_efficiency_reduced": self.capital_efficiency_reduced,
            "capital_efficiency_rejected": self.capital_efficiency_rejected,
            "venue_bitvavo_selected": self.venue_bitvavo_selected,
            "venue_okx_selected": self.venue_okx_selected,
            "mfe_capture_samples": self.mfe_capture_samples,
            "adaptive_trail_harvest": self.adaptive_trail_harvest,
            "adaptive_trail_hold": self.adaptive_trail_hold,
            "average_mfe_capture_ratio": (
                str((self._sum_mfe_capture / mfe_n).quantize(Decimal("0.0001")))
                if mfe_n
                else None
            ),
            "average_hold_seconds": (
                str((self._sum_hold_seconds / hold_n).quantize(Decimal("0.1")))
                if self._sum_hold_seconds > 0
                else None
            ),
            "average_hold_minutes": (
                str((self._sum_hold_seconds / hold_n / Decimal("60")).quantize(Decimal("0.1")))
                if self._sum_hold_seconds > 0
                else None
            ),
            "net_eur_per_hour": (
                str((self._sum_net_eur_per_hour / hr_n).quantize(Decimal("0.0001")))
                if hr_n
                else None
            ),
            "realized_net_eur_session": str(
                self._sum_realized_net_eur.quantize(Decimal("0.01"))
            ),
            "capital_deployed_eur": str(
                self._capital_deployed_eur.quantize(Decimal("0.01"))
            ),
            "capital_locked_eur": str(
                self._capital_locked_eur.quantize(Decimal("0.01"))
            ),
            "capital_utilization_pct": str(util_pct) if util_pct is not None else None,
        }
        out.update(eq)
        return out


@dataclass
class _EntryQualityCounters:
    """Placeholder when entry quality diagnostics live elsewhere."""

    pass


def config_capital_efficiency_from_settings(settings: Any) -> CapitalEfficiencyConfig:
    return CapitalEfficiencyConfig(
        enabled=bool(getattr(settings, "live_micro_capital_efficiency_enabled", True)),
        min_expected_net_profit_per_hour=Decimal(
            str(getattr(settings, "live_micro_min_expected_net_profit_per_hour", 0.05))
        ),
        default_hold_seconds=Decimal(
            str(getattr(settings, "live_micro_expected_hold_seconds", 1800))
        ),
        min_hold_seconds=Decimal(
            str(getattr(settings, "live_micro_min_expected_hold_seconds", 300))
        ),
        max_hold_seconds=Decimal(
            str(getattr(settings, "live_micro_max_expected_hold_seconds", 7200))
        ),
        extension_moderate_pct=Decimal(
            str(getattr(settings, "live_micro_entry_extension_moderate_pct", 0.012))
        ),
        extension_max_pct=Decimal(
            str(getattr(settings, "live_micro_entry_extension_max_pct", 0.025))
        ),
        reduced_efficiency_multiplier=Decimal(
            str(getattr(settings, "live_micro_capital_efficiency_reduced_multiplier", 0.75))
        ),
        small_efficiency_multiplier=Decimal(
            str(getattr(settings, "live_micro_capital_efficiency_small_multiplier", 0.50))
        ),
    )


def config_venue_economics_from_settings(settings: Any) -> VenueEconomicsConfig:
    return VenueEconomicsConfig(
        enabled=bool(getattr(settings, "live_micro_venue_economic_ranking_enabled", True)),
        default_fill_probability=Decimal(
            str(getattr(settings, "live_micro_venue_default_fill_probability", 0.85))
        ),
    )


def estimate_expected_hold_seconds(
    *,
    config: CapitalEfficiencyConfig,
    entry_quality: EntryQualityAssessment | None,
) -> Decimal | None:
    """Expected holding duration from entry shape (no wall-clock)."""
    base = config.default_hold_seconds
    if entry_quality is None:
        return base
    hold = base
    cont = entry_quality.trend_continuity
    ext = entry_quality.extension_pct
    if cont is not None and cont >= Decimal("0.75"):
        hold = min(config.max_hold_seconds, hold * Decimal("1.35"))
    if ext is not None and ext >= config.extension_max_pct:
        hold = max(config.min_hold_seconds, hold * Decimal("0.55"))
    elif ext is not None and ext >= config.extension_moderate_pct:
        hold = max(config.min_hold_seconds, hold * Decimal("0.75"))
    return max(config.min_hold_seconds, min(config.max_hold_seconds, hold))


def assess_capital_efficiency(
    *,
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    entry_quality: EntryQualityAssessment | None = None,
    config: CapitalEfficiencyConfig | None = None,
) -> CapitalEfficiencyAssessment:
    cfg = config or CapitalEfficiencyConfig()
    side = str(
        opportunity.side.value if hasattr(opportunity.side, "value") else opportunity.side
    ).lower()
    if not cfg.enabled or side not in {"buy", "long"}:
        net = profitability.net_profit_usd
        cap = opportunity.quantity * opportunity.entry_price
        return CapitalEfficiencyAssessment(
            expected_net_profit_eur=net,
            expected_hold_seconds=None,
            expected_net_profit_per_hour=None,
            capital_required_eur=cap,
            capital_efficiency_per_capital_hour=None,
            capital_efficiency_score=_ONE,
            recommended_size_multiplier=_ONE,
            recommendation=EntryQualityRecommendation.NORMAL_SIZE,
        )

    expected_net = profitability.net_profit_usd
    if expected_net <= 0:
        meta_net = (opportunity.metadata or {}).get("net_profit_eur")
        if meta_net is not None:
            try:
                expected_net = Decimal(str(meta_net))
            except Exception:  # noqa: BLE001
                expected_net = _ZERO

    capital = opportunity.quantity * opportunity.entry_price
    hold = estimate_expected_hold_seconds(config=cfg, entry_quality=entry_quality)
    eur_per_hour: Decimal | None = None
    cap_hour: Decimal | None = None
    if hold is not None and hold > 0:
        hours = hold / _HOUR
        eur_per_hour = expected_net / hours
        if capital > 0:
            cap_hour = expected_net / (capital * hours)

    score = eur_per_hour if eur_per_hour is not None else expected_net
    recommendation = EntryQualityRecommendation.NORMAL_SIZE
    mult = _ONE
    reason = ""

    if eur_per_hour is not None and eur_per_hour < cfg.min_expected_net_profit_per_hour:
        if eur_per_hour < cfg.min_expected_net_profit_per_hour * Decimal("0.5"):
            recommendation = EntryQualityRecommendation.REJECT
            mult = _ZERO
            reason = "capital_efficiency_low"
        else:
            recommendation = EntryQualityRecommendation.REDUCED_SIZE
            mult = cfg.small_efficiency_multiplier
            reason = "capital_efficiency_marginal"
    elif eur_per_hour is not None and eur_per_hour < cfg.min_expected_net_profit_per_hour * Decimal(
        "1.5"
    ):
        recommendation = EntryQualityRecommendation.REDUCED_SIZE
        mult = cfg.reduced_efficiency_multiplier
        reason = "capital_efficiency_below_target"

    return CapitalEfficiencyAssessment(
        expected_net_profit_eur=expected_net,
        expected_hold_seconds=hold,
        expected_net_profit_per_hour=eur_per_hour,
        capital_required_eur=capital,
        capital_efficiency_per_capital_hour=cap_hour,
        capital_efficiency_score=score,
        recommended_size_multiplier=min(_ONE, mult),
        recommendation=recommendation,
        reject_reason=reason,
    )


def venue_economic_score(
    *,
    venue: str,
    expected_net_profit_eur: Decimal,
    fill_probability: Decimal,
) -> VenueEconomicsScore:
    prob = max(_ZERO, min(_ONE, fill_probability))
    return VenueEconomicsScore(
        venue=venue.strip().lower(),
        expected_net_profit_eur=expected_net_profit_eur,
        fill_probability=prob,
        economic_score=expected_net_profit_eur * prob,
    )


def rank_venue_for_opportunity(
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    *,
    config: VenueEconomicsConfig | None = None,
    venue_net: dict[str, Decimal] | None = None,
) -> tuple[str | None, tuple[VenueEconomicsScore, ...]]:
    """Pick best venue when multiple NET estimates exist."""
    cfg = config or VenueEconomicsConfig()
    meta = opportunity.metadata or {}
    if not cfg.enabled:
        v = str(meta.get("buy_exchange") or meta.get("venue") or "")
        return (v or None, ())

    scores: list[VenueEconomicsScore] = []
    if venue_net:
        for venue, net in venue_net.items():
            scores.append(
                venue_economic_score(
                    venue=venue,
                    expected_net_profit_eur=net,
                    fill_probability=cfg.default_fill_probability,
                )
            )
    else:
        net = profitability.net_profit_usd
        meta_net = meta.get("net_profit_eur")
        if meta_net is not None:
            try:
                net = Decimal(str(meta_net))
            except Exception:  # noqa: BLE001
                pass
        venue = str(meta.get("buy_exchange") or meta.get("venue") or "")
        if venue:
            scores.append(
                venue_economic_score(
                    venue=venue,
                    expected_net_profit_eur=net,
                    fill_probability=cfg.default_fill_probability,
                )
            )

    if not scores:
        return None, ()
    scores.sort(key=lambda s: s.economic_score, reverse=True)
    return scores[0].venue, tuple(scores)


def select_best_buy_opportunities(
    opportunities: Sequence[TradeOpportunity],
    *,
    config: VenueEconomicsConfig | None = None,
) -> list[TradeOpportunity]:
    """When same symbol has multiple buy venues, keep highest NET metadata."""
    cfg = config or VenueEconomicsConfig()
    if not cfg.enabled:
        return list(opportunities)
    best: dict[str, TradeOpportunity] = {}
    order: list[str] = []
    for opp in opportunities:
        side = str(
            opp.side.value if hasattr(opp.side, "value") else opp.side
        ).lower()
        if side not in {"buy", "long"}:
            continue
        sym = opp.symbol.upper()
        net = _ZERO
        meta = opp.metadata or {}
        raw = meta.get("net_profit_eur")
        if raw is not None:
            try:
                net = Decimal(str(raw))
            except Exception:  # noqa: BLE001
                net = _ZERO
        prev = best.get(sym)
        if prev is None:
            best[sym] = opp
            order.append(sym)
            continue
        prev_net = Decimal(str((prev.metadata or {}).get("net_profit_eur") or 0))
        if net > prev_net:
            best[sym] = opp
    if not best:
        return list(opportunities)
    kept = set(best.keys())
    out: list[TradeOpportunity] = []
    seen_buy_syms: set[str] = set()
    for opp in opportunities:
        side = str(
            opp.side.value if hasattr(opp.side, "value") else opp.side
        ).lower()
        if side in {"buy", "long"}:
            sym = opp.symbol.upper()
            if sym in kept:
                if sym in seen_buy_syms:
                    continue
                seen_buy_syms.add(sym)
                out.append(best[sym])
            else:
                out.append(opp)
        else:
            out.append(opp)
    return out


def assess_opportunity_economics(
    *,
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    marks: Sequence[Decimal] | None = None,
    entry_config: EntryQualityConfig | None = None,
    capital_config: CapitalEfficiencyConfig | None = None,
    venue_config: VenueEconomicsConfig | None = None,
) -> OpportunityEconomics:
    eq_cfg = entry_config or EntryQualityConfig()
    eq: EntryQualityAssessment | None = None
    if eq_cfg.enabled:
        eq = evaluate_entry_quality(
            opportunity=opportunity,
            profitability=profitability,
            marks=marks,
            config=eq_cfg,
        )
    ce = assess_capital_efficiency(
        opportunity=opportunity,
        profitability=profitability,
        entry_quality=eq,
        config=capital_config,
    )
    venue, scores = rank_venue_for_opportunity(
        opportunity, profitability, config=venue_config
    )
    mult = _ONE
    rec = EntryQualityRecommendation.NORMAL_SIZE
    reason = ""
    if eq is not None:
        mult = min(mult, eq.recommended_size_multiplier)
        if eq.recommendation == EntryQualityRecommendation.REJECT:
            rec = EntryQualityRecommendation.REJECT
            reason = eq.reject_reason or "entry_quality"
    if ce.recommendation == EntryQualityRecommendation.REJECT:
        rec = EntryQualityRecommendation.REJECT
        reason = ce.reject_reason or reason or "capital_efficiency"
    elif (
        rec != EntryQualityRecommendation.REJECT
        and ce.recommendation == EntryQualityRecommendation.REDUCED_SIZE
    ):
        rec = EntryQualityRecommendation.REDUCED_SIZE
        mult = min(mult, ce.recommended_size_multiplier)
    elif eq is not None and eq.recommendation == EntryQualityRecommendation.REDUCED_SIZE:
        rec = EntryQualityRecommendation.REDUCED_SIZE
        mult = min(mult, eq.recommended_size_multiplier)

    mult = min(_ONE, max(_ZERO, mult))
    if rec == EntryQualityRecommendation.REJECT:
        mult = _ZERO

    return OpportunityEconomics(
        entry_quality=eq,
        capital_efficiency=ce,
        venue=venue,
        venue_scores=scores,
        combined_multiplier=mult,
        recommendation=rec,
        reject_reason=reason,
    )


def combine_size_multipliers(*multipliers: Decimal) -> Decimal:
    """Downward-only combined multiplier."""
    out = _ONE
    for m in multipliers:
        out = min(out, min(_ONE, max(_ZERO, m)))
    return out


def apply_economics_multiplier(
    quantity: Decimal,
    economics: OpportunityEconomics,
) -> Decimal:
    return apply_size_multiplier(quantity, economics.combined_multiplier)


def compute_mfe_record(
    *,
    entry_price: Decimal,
    exit_price: Decimal,
    mfe_price: Decimal,
    mae_price: Decimal,
    cost_basis: Decimal,
    realized_net_eur: Decimal,
    notional: Decimal,
    holding_seconds: Decimal | None,
) -> MFERecord:
    """Price-based MFE/MAE with NET capture proxy."""
    if entry_price <= 0:
        entry_price = cost_basis if cost_basis > 0 else exit_price
    mfe_pct = (
        (mfe_price - entry_price) / entry_price if entry_price > 0 else _ZERO
    )
    mae_pct = (
        (mae_price - entry_price) / entry_price if entry_price > 0 else _ZERO
    )
    realized_pct = realized_net_eur / notional if notional > 0 else _ZERO
    max_realizable = mfe_pct - (cost_basis - entry_price) / entry_price if entry_price > 0 else mfe_pct
    if max_realizable <= 0:
        capture: Decimal | None = None
    else:
        capture = max(_ZERO, min(_ONE, realized_pct / max_realizable))
    return MFERecord(
        entry_price=entry_price,
        exit_price=exit_price,
        mfe_price=mfe_price,
        mae_price=mae_price,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        realized_net_pct=realized_pct,
        mfe_capture_ratio=capture,
        holding_seconds=holding_seconds,
    )


def underwater_recovery_metrics(
    *,
    mark: Decimal,
    break_even: Decimal,
    notional_eur: Decimal,
    age_seconds: Decimal | None,
    expected_hold_seconds: Decimal | None = None,
) -> dict[str, str | None]:
    """Analytics for underwater bags — no exit action."""
    if break_even <= 0 or mark <= 0:
        return {}
    underwater_pct = (mark - break_even) / break_even
    dist_be = break_even - mark if mark < break_even else _ZERO
    est_recovery = None
    if (
        expected_hold_seconds is not None
        and underwater_pct < 0
        and expected_hold_seconds > 0
    ):
        est_recovery = str(
            (expected_hold_seconds * abs(underwater_pct) / Decimal("0.01")).quantize(
                Decimal("1")
            )
        )
    opp_cost = None
    if age_seconds is not None and notional_eur > 0:
        hours = age_seconds / _HOUR
        if hours > 0:
            opp_cost = str((notional_eur * hours * Decimal("0.0001")).quantize(Decimal("0.01")))
    return {
        "underwater_pct": str(underwater_pct.quantize(Decimal("0.0001"))),
        "capital_locked_eur": str(notional_eur.quantize(Decimal("0.01"))),
        "age_seconds": str(int(age_seconds)) if age_seconds is not None else None,
        "distance_to_break_even_eur": str(dist_be.quantize(Decimal("0.01"))),
        "estimated_recovery_seconds": est_recovery,
        "capital_opportunity_cost_eur": opp_cost,
    }


@dataclass
class CapitalAllocator:
    """Rank opportunities by NET edge + capital efficiency (downward-only sizing)."""

    capital_config: CapitalEfficiencyConfig
    venue_config: VenueEconomicsConfig
    entry_config: EntryQualityConfig | None = None

    def assess(
        self,
        *,
        opportunity: TradeOpportunity,
        profitability: ProfitabilityResult,
        marks: Sequence[Decimal] | None = None,
    ) -> OpportunityEconomics:
        return assess_opportunity_economics(
            opportunity=opportunity,
            profitability=profitability,
            marks=marks,
            entry_config=self.entry_config,
            capital_config=self.capital_config,
            venue_config=self.venue_config,
        )

    def rank_opportunities(
        self, opportunities: Sequence[TradeOpportunity]
    ) -> list[TradeOpportunity]:
        return select_best_buy_opportunities(
            opportunities, config=self.venue_config
        )


def compute_net_eur_per_capital_hour(
    *,
    realized_net_eur: Decimal,
    capital_deployed_eur: Decimal,
    elapsed_seconds: Decimal,
) -> Decimal | None:
    """Primary KPI: realized NET / (capital × time).

    Returns None when denominator would be zero or unreliable.
    """
    if capital_deployed_eur <= 0 or elapsed_seconds <= 0:
        return None
    hours = elapsed_seconds / _HOUR
    cap_hours = capital_deployed_eur * hours
    if cap_hours <= 0:
        return None
    return realized_net_eur / cap_hours


def compute_net_eur_per_hour(
    *,
    realized_net_eur: Decimal,
    elapsed_seconds: Decimal,
) -> Decimal | None:
    """Operator-friendly NET EUR/hour."""
    if elapsed_seconds <= 0:
        return None
    return realized_net_eur / (elapsed_seconds / _HOUR)


def adaptive_trail_should_hold(
    *,
    symbol: str,
    marks: Sequence[Decimal],
    extension_pct: Decimal | None,
    continuity: Decimal | None,
    headroom_pct: Decimal | None,
    enabled: bool = True,
) -> bool:
    """Strong trend → hold longer (never below BE — caller enforces)."""
    if not enabled:
        return False
    if continuity is not None and continuity >= Decimal("0.7"):
        if extension_pct is None or extension_pct < Decimal("0.02"):
            if headroom_pct is None or headroom_pct > Decimal("0.003"):
                if len(marks) >= 3:
                    return marks[-1] >= marks[0]
    return False
