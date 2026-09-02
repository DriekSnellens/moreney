"""Central Opportunity Optimization Engine for live micro.

Deterministic, replay-safe: no exchange I/O, no wall-clock in pure functions.
Composes entry quality, capital velocity, venue economics, spread/timing,
volatility regime, and portfolio-level capital allocation.

Pipeline position:
  Strategy → TradeOpportunity → Profitability (NET) → OpportunityEngine → Risk → Execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence

from bot.core.enums import EntryQualityRecommendation, FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, ProfitabilityResult, TradeOpportunity
from bot.strategies.entry_quality import (
    EntryQualityAssessment,
    EntryQualityConfig,
    apply_size_multiplier,
    compute_extension_over_window,
    compute_headroom_pct,
    compute_trend_continuity,
    evaluate_entry_quality,
)
from bot.strategies.opportunity_economics import (
    CapitalEfficiencyAssessment,
    CapitalEfficiencyConfig,
    VenueEconomicsConfig,
    assess_capital_efficiency,
    rank_venue_for_opportunity,
    select_best_buy_opportunities,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HOUR = Decimal("3600")
_SCORE_FLOOR = Decimal("0.05")


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class OpportunityDecision(str, Enum):
    HIGH_QUALITY = "HIGH_QUALITY"
    REDUCED = "REDUCED"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class OpportunityEngineConfig:
    enabled: bool = True
    headroom_enabled: bool = True
    extension_enabled: bool = True
    volatility_regime_enabled: bool = True
    capital_efficiency_enabled: bool = True
    venue_economic_ranking_enabled: bool = True
    spread_liquidity_enabled: bool = True
    timing_enabled: bool = True
    coin_learning_enabled: bool = True
    adaptive_exit_enabled: bool = True
    min_opportunity_score: Decimal = Decimal("55")
    reduced_opportunity_score: Decimal = Decimal("70")
    high_quality_score: Decimal = Decimal("80")
    excellent_size_multiplier: Decimal = Decimal("1.0")
    good_size_multiplier: Decimal = Decimal("0.75")
    medium_size_multiplier: Decimal = Decimal("0.50")
    max_spread_pct: Decimal = Decimal("0.008")
    min_liquidity_score: Decimal = Decimal("0.25")
    extreme_volatility_size_cap: Decimal = Decimal("0.50")
    high_volatility_size_cap: Decimal = Decimal("0.75")
    default_maker_fill_probability: Decimal = Decimal("0.65")
    default_taker_fill_probability: Decimal = Decimal("0.92")
    weight_net_edge: Decimal = Decimal("0.22")
    weight_capital_efficiency: Decimal = Decimal("0.18")
    weight_headroom: Decimal = Decimal("0.18")
    weight_momentum: Decimal = Decimal("0.10")
    weight_continuity: Decimal = Decimal("0.08")
    weight_extension: Decimal = Decimal("0.08")
    weight_liquidity: Decimal = Decimal("0.06")
    weight_spread: Decimal = Decimal("0.05")
    weight_timing: Decimal = Decimal("0.05")
    weight_venue: Decimal = Decimal("0.05")
    weight_breakout: Decimal = Decimal("0.05")
    # Phase 2 execution intelligence (off by default; enable via settings).
    regime_engine_enabled: bool = False
    adverse_selection_enabled: bool = False
    outcome_learning_enabled: bool = False
    execution_quality_enabled: bool = False
    weight_regime_fit: Decimal = Decimal("0.08")
    weight_adverse_selection: Decimal = Decimal("0.07")
    adverse_selection_reject_threshold: Decimal = Decimal("0.80")
    stale_data_reject_threshold: Decimal = Decimal("0.15")


@dataclass(frozen=True, slots=True)
class OpportunityAssessment:
    symbol: str
    venue: str | None
    direction: str
    expected_net_profit_eur: Decimal
    expected_net_profit_pct: Decimal
    expected_hold_seconds: Decimal | None
    expected_net_eur_per_hour: Decimal | None
    expected_net_eur_per_capital_hour: Decimal | None
    momentum_score: Decimal
    continuity_score: Decimal | None
    volatility_regime: VolatilityRegime
    extension_pct: Decimal | None
    headroom_pct: Decimal | None
    headroom_score: Decimal
    headroom_5m: Decimal | None = None
    headroom_30m: Decimal | None = None
    headroom_2h: Decimal | None = None
    spread_pct: Decimal | None = None
    liquidity_score: Decimal = _ZERO
    fill_probability: Decimal = _ONE
    venue_score: Decimal = _ZERO
    entry_quality_score: Decimal = _ZERO
    timing_score: Decimal = _ZERO
    breakout_quality_score: Decimal = _ZERO
    capital_required_eur: Decimal = _ZERO
    recommended_size_multiplier: Decimal = _ONE
    opportunity_score: Decimal = _ZERO
    decision: OpportunityDecision = OpportunityDecision.REJECT
    reasons: tuple[str, ...] = ()
    maker_expected_net_eur: Decimal | None = None
    taker_expected_net_eur: Decimal | None = None
    extension_5m: Decimal | None = None
    extension_30m: Decimal | None = None
    extension_2h: Decimal | None = None
    headroom_ratio: Decimal | None = None
    market_regime: str | None = None
    regime_score: Decimal = _ZERO
    regime_fit: Decimal = _ONE
    regime_confidence: Decimal | None = None
    adverse_selection_score: Decimal | None = None
    empirical_multiplier: Decimal = _ONE
    execution_decision: str | None = None
    data_freshness_score: Decimal = _ONE


@dataclass
class CoinVenueStats:
    trade_count: int = 0
    win_count: int = 0
    sum_net_eur: Decimal = _ZERO
    sum_net_per_hour: Decimal = _ZERO
    sum_hold_seconds: Decimal = _ZERO
    sum_mfe_capture: Decimal = _ZERO
    mfe_samples: int = 0
    maker_fills: int = 0
    taker_fills: int = 0

    def record_trade(
        self,
        *,
        net_eur: Decimal,
        hold_seconds: Decimal | None,
        mfe_capture: Decimal | None,
        is_maker: bool,
    ) -> None:
        self.trade_count += 1
        if net_eur > 0:
            self.win_count += 1
        self.sum_net_eur += net_eur
        if hold_seconds is not None and hold_seconds > 0:
            self.sum_hold_seconds += hold_seconds
            self.sum_net_per_hour += net_eur / (hold_seconds / _HOUR)
        if mfe_capture is not None:
            self.mfe_samples += 1
            self.sum_mfe_capture += mfe_capture
        if is_maker:
            self.maker_fills += 1
        else:
            self.taker_fills += 1

    def snapshot(self) -> dict[str, str | None]:
        n = self.trade_count or 0
        hr_n = n or 1
        mfe_n = self.mfe_samples or 0
        return {
            "trade_count": str(n),
            "win_rate": str((Decimal(self.win_count) / Decimal(n)).quantize(Decimal("0.01")))
            if n
            else None,
            "average_net_eur": str((self.sum_net_eur / n).quantize(Decimal("0.01"))) if n else None,
            "average_net_eur_per_hour": str(
                (self.sum_net_per_hour / hr_n).quantize(Decimal("0.0001"))
            )
            if self.sum_net_per_hour > 0
            else None,
            "average_hold_seconds": str(
                (self.sum_hold_seconds / hr_n).quantize(Decimal("0.1"))
            )
            if self.sum_hold_seconds > 0
            else None,
            "average_mfe_capture": str(
                (self.sum_mfe_capture / mfe_n).quantize(Decimal("0.0001"))
            )
            if mfe_n
            else None,
            "maker_fill_rate": str(
                (Decimal(self.maker_fills) / Decimal(n)).quantize(Decimal("0.01"))
            )
            if n
            else None,
        }


@dataclass
class OpportunityEngineDiagnostics:
    candidates: int = 0
    high_quality: int = 0
    reduced: int = 0
    rejected: int = 0
    headroom_reject: int = 0
    extension_reject: int = 0
    continuity_reject: int = 0
    volatility_reject: int = 0
    spread_reject: int = 0
    liquidity_reject: int = 0
    timing_reject: int = 0
    venue_bitvavo_selected: int = 0
    venue_okx_selected: int = 0
    capital_allocator_selected: int = 0
    capital_allocator_skipped: int = 0
    mfe_samples: int = 0
    adaptive_trail_hold: int = 0
    adaptive_trail_harvest: int = 0
    regime_reject: int = 0
    adverse_selection_reject: int = 0
    stale_data_reject: int = 0
    execution_wait: int = 0
    execution_reject: int = 0
    _sum_mfe_capture: Decimal = _ZERO
    _sum_score: Decimal = _ZERO
    _best: OpportunityAssessment | None = None
    coin_stats: dict[str, CoinVenueStats] = field(default_factory=dict)
    venue_stats: dict[str, CoinVenueStats] = field(default_factory=dict)

    def record(self, assessment: OpportunityAssessment) -> None:
        self.candidates += 1
        if assessment.decision == OpportunityDecision.HIGH_QUALITY:
            self.high_quality += 1
        elif assessment.decision == OpportunityDecision.REDUCED:
            self.reduced += 1
        else:
            self.rejected += 1
        for reason in assessment.reasons:
            if "headroom" in reason:
                self.headroom_reject += 1
            if "extension" in reason:
                self.extension_reject += 1
            if "continuity" in reason:
                self.continuity_reject += 1
            if "volatility" in reason:
                self.volatility_reject += 1
            if "spread" in reason:
                self.spread_reject += 1
            if "liquidity" in reason:
                self.liquidity_reject += 1
            if "timing" in reason:
                self.timing_reject += 1
            if "regime" in reason or "dead_market" in reason:
                self.regime_reject += 1
            if "adverse" in reason:
                self.adverse_selection_reject += 1
            if "stale" in reason:
                self.stale_data_reject += 1
            if "execution_wait" in reason:
                self.execution_wait += 1
            if "execution_reject" in reason:
                self.execution_reject += 1
        if assessment.venue == "bitvavo":
            self.venue_bitvavo_selected += 1
        elif assessment.venue == "okx":
            self.venue_okx_selected += 1
        if assessment.opportunity_score > 0:
            self._sum_score += assessment.opportunity_score
        if self._best is None or assessment.opportunity_score > self._best.opportunity_score:
            self._best = assessment

    def snapshot(self, *, economic_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        n = self.candidates or 0
        best = self._best
        out: dict[str, Any] = {
            "opportunity_candidates": self.candidates,
            "opportunity_high_quality": self.high_quality,
            "opportunity_reduced": self.reduced,
            "opportunity_rejected": self.rejected,
            "headroom_reject": self.headroom_reject,
            "extension_reject": self.extension_reject,
            "continuity_reject": self.continuity_reject,
            "volatility_reject": self.volatility_reject,
            "spread_reject": self.spread_reject,
            "liquidity_reject": self.liquidity_reject,
            "timing_reject": self.timing_reject,
            "venue_bitvavo_selected": self.venue_bitvavo_selected,
            "venue_okx_selected": self.venue_okx_selected,
            "capital_allocator_selected": self.capital_allocator_selected,
            "capital_allocator_skipped": self.capital_allocator_skipped,
            "mfe_samples": self.mfe_samples,
            "adaptive_trail_hold": self.adaptive_trail_hold,
            "adaptive_trail_harvest": self.adaptive_trail_harvest,
            "average_opportunity_score": str(
                (self._sum_score / n).quantize(Decimal("0.1"))
            )
            if n
            else None,
            "best_opportunity_symbol": best.symbol if best else None,
            "best_opportunity_venue": best.venue if best else None,
            "best_opportunity_score": str(best.opportunity_score.quantize(Decimal("0.1")))
            if best
            else None,
            "best_opportunity_net_eur": str(
                best.expected_net_profit_eur.quantize(Decimal("0.01"))
            )
            if best
            else None,
            "best_opportunity_net_eur_per_hour": (
                str(best.expected_net_eur_per_hour.quantize(Decimal("0.0001")))
                if best and best.expected_net_eur_per_hour is not None
                else None
            ),
            "best_opportunity_headroom_pct": (
                str(best.headroom_pct.quantize(Decimal("0.0001")))
                if best and best.headroom_pct is not None
                else None
            ),
            "best_opportunity_extension_pct": (
                str(best.extension_pct.quantize(Decimal("0.0001")))
                if best and best.extension_pct is not None
                else None
            ),
            "best_opportunity_hold_minutes": (
                str((best.expected_hold_seconds / Decimal("60")).quantize(Decimal("0.1")))
                if best and best.expected_hold_seconds is not None
                else None
            ),
            "venue_economics_bitvavo": self.venue_stats.get("bitvavo", CoinVenueStats()).snapshot(),
            "venue_economics_okx": self.venue_stats.get("okx", CoinVenueStats()).snapshot(),
            "regime_reject": self.regime_reject,
            "adverse_selection_reject": self.adverse_selection_reject,
            "stale_data_reject": self.stale_data_reject,
            "execution_wait": self.execution_wait,
            "execution_reject": self.execution_reject,
        }
        if economic_extra:
            out.update(economic_extra)
        return out


def config_from_settings(settings: Any) -> OpportunityEngineConfig:
    return OpportunityEngineConfig(
        enabled=bool(getattr(settings, "live_micro_opportunity_engine_enabled", True)),
        headroom_enabled=bool(getattr(settings, "live_micro_entry_headroom_enabled", True)),
        extension_enabled=bool(
            getattr(settings, "live_micro_extension_enabled", True)
        ),
        volatility_regime_enabled=bool(
            getattr(settings, "live_micro_volatility_regime_enabled", True)
        ),
        capital_efficiency_enabled=bool(
            getattr(settings, "live_micro_capital_efficiency_enabled", True)
        ),
        venue_economic_ranking_enabled=bool(
            getattr(settings, "live_micro_venue_economic_ranking_enabled", True)
        ),
        spread_liquidity_enabled=bool(
            getattr(settings, "live_micro_spread_liquidity_enabled", True)
        ),
        timing_enabled=bool(getattr(settings, "live_micro_timing_enabled", True)),
        coin_learning_enabled=bool(
            getattr(settings, "live_micro_coin_learning_enabled", True)
        ),
        adaptive_exit_enabled=bool(
            getattr(settings, "live_micro_adaptive_exit_enabled", True)
        ),
        min_opportunity_score=Decimal(
            str(getattr(settings, "live_micro_opportunity_min_score", 55))
        ),
        reduced_opportunity_score=Decimal(
            str(getattr(settings, "live_micro_opportunity_reduced_score", 70))
        ),
        high_quality_score=Decimal(
            str(getattr(settings, "live_micro_opportunity_high_quality_score", 80))
        ),
        max_spread_pct=Decimal(str(getattr(settings, "live_micro_max_spread_pct", 0.008))),
        min_liquidity_score=Decimal(
            str(getattr(settings, "live_micro_min_liquidity_score", 0.25))
        ),
        default_maker_fill_probability=Decimal(
            str(getattr(settings, "live_micro_maker_fill_probability", 0.65))
        ),
        default_taker_fill_probability=Decimal(
            str(getattr(settings, "live_micro_taker_fill_probability", 0.92))
        ),
        weight_net_edge=Decimal(
            str(getattr(settings, "live_micro_opp_weight_net_edge", 0.22))
        ),
        weight_capital_efficiency=Decimal(
            str(getattr(settings, "live_micro_opp_weight_capital_efficiency", 0.18))
        ),
        weight_headroom=Decimal(
            str(getattr(settings, "live_micro_opp_weight_headroom", 0.18))
        ),
        weight_momentum=Decimal(
            str(getattr(settings, "live_micro_opp_weight_momentum", 0.10))
        ),
        weight_continuity=Decimal(
            str(getattr(settings, "live_micro_opp_weight_continuity", 0.08))
        ),
        weight_extension=Decimal(
            str(getattr(settings, "live_micro_opp_weight_extension", 0.08))
        ),
        weight_liquidity=Decimal(
            str(getattr(settings, "live_micro_opp_weight_liquidity", 0.06))
        ),
        weight_spread=Decimal(
            str(getattr(settings, "live_micro_opp_weight_spread", 0.05))
        ),
        weight_timing=Decimal(
            str(getattr(settings, "live_micro_opp_weight_timing", 0.05))
        ),
        weight_venue=Decimal(
            str(getattr(settings, "live_micro_opp_weight_venue", 0.05))
        ),
        weight_breakout=Decimal(
            str(getattr(settings, "live_micro_opp_weight_breakout", 0.05))
        ),
        regime_engine_enabled=bool(
            getattr(settings, "live_micro_regime_engine_enabled", False)
            and getattr(settings, "live_micro_regime_scoring_enabled", False)
        ),
        adverse_selection_enabled=bool(
            getattr(settings, "live_micro_adverse_selection_enabled", False)
        ),
        outcome_learning_enabled=bool(
            getattr(settings, "live_micro_outcome_learning_enabled", False)
        ),
        execution_quality_enabled=bool(
            getattr(settings, "live_micro_execution_quality_enabled", False)
        ),
    )


def _clamp01(value: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, value))


def _weighted_score(factors: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Robust weighted sum with floor — avoids single zero factor killing score."""
    total_w = sum(w for _, w in factors)
    if total_w <= 0:
        return _ZERO
    acc = _ZERO
    for val, w in factors:
        v = max(_SCORE_FLOOR, _clamp01(val))
        acc += v * w
    raw = acc / total_w
    return (raw * _HUNDRED).quantize(Decimal("0.1"))


_HUNDRED = Decimal("100")


def classify_volatility_regime(
    marks: Sequence[Decimal],
    *,
    enabled: bool = True,
) -> VolatilityRegime:
    if not enabled or len(marks) < 5:
        return VolatilityRegime.NORMAL
    steps: list[Decimal] = []
    prev = marks[0]
    for cur in marks[1:]:
        if prev > 0:
            steps.append(abs((cur - prev) / prev))
        prev = cur
    if not steps:
        return VolatilityRegime.NORMAL
    avg = sum(steps, _ZERO) / Decimal(len(steps))
    mx = max(steps)
    if mx >= Decimal("0.015") or avg >= Decimal("0.006"):
        return VolatilityRegime.EXTREME
    if mx >= Decimal("0.008") or avg >= Decimal("0.0035"):
        return VolatilityRegime.HIGH
    if avg <= Decimal("0.0008"):
        return VolatilityRegime.LOW
    return VolatilityRegime.NORMAL


def _spread_from_snapshot(snapshot: MarketSnapshot | None) -> Decimal | None:
    if snapshot is None:
        return None
    mid = snapshot.mid
    if mid <= 0:
        return None
    return snapshot.spread / mid


def _liquidity_from_snapshot(snapshot: MarketSnapshot | None) -> Decimal:
    if snapshot is None:
        return Decimal("0.5")
    vol = snapshot.volume_24h
    if vol is None or vol <= 0:
        book = snapshot.order_book
        if book is not None:
            bid_depth = sum((lvl.quantity for lvl in book.bids[:5]), _ZERO)
            ask_depth = sum((lvl.quantity for lvl in book.asks[:5]), _ZERO)
            depth = bid_depth + ask_depth
            if depth > 0:
                return _clamp01(depth / Decimal("100"))
        return Decimal("0.35")
    return _clamp01(vol / Decimal("1000000"))


def _timing_score(
    marks: Sequence[Decimal],
    *,
    extension_pct: Decimal | None,
    continuity: Decimal | None,
    enabled: bool,
) -> Decimal:
    if not enabled or len(marks) < 4:
        return Decimal("0.5")
    last = marks[-1]
    prev = marks[-2]
    if prev <= 0:
        return Decimal("0.5")
    step = (last - prev) / prev
    ext = extension_pct or _ZERO
    cont = continuity or Decimal("0.5")
    if ext >= Decimal("0.025") and step > Decimal("0.003"):
        return Decimal("0.15")
    if ext >= Decimal("0.015") and step > Decimal("0.002"):
        return Decimal("0.35")
    if cont >= Decimal("0.7") and ext < Decimal("0.012") and step >= _ZERO:
        return Decimal("0.85")
    if step < _ZERO and cont >= Decimal("0.55") and ext < Decimal("0.02"):
        return Decimal("0.75")
    return Decimal("0.55")


def _breakout_quality(
    *,
    continuity: Decimal | None,
    extension_pct: Decimal | None,
    liquidity_score: Decimal,
    spread_pct: Decimal | None,
    marks: Sequence[Decimal],
) -> Decimal:
    cont = continuity or Decimal("0.4")
    ext = extension_pct or _ZERO
    spread_penalty = _ZERO
    if spread_pct is not None and spread_pct > Decimal("0.004"):
        spread_penalty = min(_ONE, spread_pct / Decimal("0.01"))
    spike_penalty = _ZERO
    if len(marks) >= 3 and marks[-3] > 0:
        jump = (marks[-1] - marks[-3]) / marks[-3]
        if jump > Decimal("0.02") and cont < Decimal("0.5"):
            spike_penalty = Decimal("0.6")
    base = cont * liquidity_score
    if ext > Decimal("0.03"):
        base *= Decimal("0.5")
    base *= _ONE - spread_penalty * Decimal("0.4")
    base *= _ONE - spike_penalty
    return _clamp01(base)


def _headroom_windows(
    marks: Sequence[Decimal],
    *,
    current_price: Decimal,
    samples_5m: int,
    samples_30m: int,
    samples_2h: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    h5 = compute_headroom_pct(marks, lookback=samples_5m, current_price=current_price)
    h30 = compute_headroom_pct(marks, lookback=samples_30m, current_price=current_price)
    h2h = compute_headroom_pct(marks, lookback=samples_2h, current_price=current_price)
    return h5, h30, h2h


def _maker_taker_net(
    profitability: ProfitabilityResult,
    *,
    maker_fill: Decimal,
    taker_fill: Decimal,
    is_maker: bool,
) -> tuple[Decimal | None, Decimal | None, Decimal]:
    net = profitability.net_profit_usd
    maker_net = net * maker_fill
    taker_net = net * taker_fill
    fill_prob = maker_fill if is_maker else taker_fill
    return maker_net, taker_net, fill_prob


def evaluate(
    *,
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    marks: Sequence[Decimal] | None = None,
    snapshot: MarketSnapshot | None = None,
    entry_config: EntryQualityConfig | None = None,
    capital_config: CapitalEfficiencyConfig | None = None,
    venue_config: VenueEconomicsConfig | None = None,
    engine_config: OpportunityEngineConfig | None = None,
    candidate_count: int = 0,
    avg_opportunity_score: Decimal | None = None,
    outcome_store: Any = None,
    learning_config: Any = None,
) -> OpportunityAssessment:
    """Evaluate one candidate — deterministic, no I/O."""
    cfg = engine_config or OpportunityEngineConfig()
    eq_cfg = entry_config or EntryQualityConfig()
    cap_cfg = capital_config or CapitalEfficiencyConfig(enabled=cfg.capital_efficiency_enabled)
    ven_cfg = venue_config or VenueEconomicsConfig(
        enabled=cfg.venue_economic_ranking_enabled
    )
    mark_series = list(marks or [])
    side = str(
        opportunity.side.value if hasattr(opportunity.side, "value") else opportunity.side
    ).lower()
    meta = opportunity.metadata or {}
    venue = str(meta.get("buy_exchange") or meta.get("venue") or "")
    notional = opportunity.quantity * opportunity.entry_price
    net_eur = profitability.net_profit_usd
    net_pct = profitability.net_return
    reasons: list[str] = []

    if meta.get("alphai_inventory_build"):
        cap = notional if notional > 0 else Decimal("55")
        return OpportunityAssessment(
            symbol=opportunity.symbol,
            venue=venue or None,
            direction=str(
                opportunity.side.value
                if hasattr(opportunity.side, "value")
                else opportunity.side
            ).lower(),
            expected_net_profit_eur=net_eur,
            expected_net_profit_pct=net_pct,
            expected_hold_seconds=Decimal("3600"),
            expected_net_eur_per_hour=net_eur,
            expected_net_eur_per_capital_hour=net_eur / cap if cap > 0 else _ZERO,
            momentum_score=Decimal("0.8"),
            continuity_score=Decimal("0.7"),
            volatility_regime=VolatilityRegime.NORMAL,
            extension_pct=None,
            headroom_pct=Decimal("0.01"),
            headroom_score=Decimal("0.8"),
            capital_required_eur=cap,
            recommended_size_multiplier=_ONE,
            opportunity_score=Decimal("95"),
            decision=OpportunityDecision.HIGH_QUALITY,
            reasons=("alphai_inventory_build",),
            fill_probability=Decimal("0.35"),
        )

    eq: EntryQualityAssessment | None = None
    if eq_cfg.enabled and cfg.headroom_enabled:
        eq = evaluate_entry_quality(
            opportunity=opportunity,
            profitability=profitability,
            marks=mark_series,
            config=eq_cfg,
        )

    ce: CapitalEfficiencyAssessment = assess_capital_efficiency(
        opportunity=opportunity,
        profitability=profitability,
        entry_quality=eq,
        config=cap_cfg,
    )

    best_venue, venue_scores = rank_venue_for_opportunity(
        opportunity, profitability, config=ven_cfg
    )
    if best_venue:
        venue = best_venue

    vol_regime = classify_volatility_regime(
        mark_series, enabled=cfg.volatility_regime_enabled
    )
    spread_pct = _spread_from_snapshot(snapshot or opportunity.market)
    liquidity = _liquidity_from_snapshot(snapshot or opportunity.market)

    h5 = h30 = h2h = None
    if mark_series and opportunity.entry_price > 0:
        h5, h30, h2h = _headroom_windows(
            mark_series,
            current_price=opportunity.entry_price,
            samples_5m=eq_cfg.extension_samples_5m if eq else 5,
            samples_30m=eq_cfg.extension_samples_30m if eq else 30,
            samples_2h=eq_cfg.extension_samples_2h if eq else 120,
        )

    ext_5m = compute_extension_over_window(mark_series, eq_cfg.extension_samples_5m) if mark_series else None
    ext_30m = compute_extension_over_window(mark_series, eq_cfg.extension_samples_30m) if mark_series else None
    ext_2h = compute_extension_over_window(mark_series, eq_cfg.extension_samples_2h) if mark_series else None

    continuity = eq.trend_continuity if eq else compute_trend_continuity(mark_series)
    extension = eq.extension_pct if eq else ext_30m
    headroom = eq.headroom_pct if eq else h30
    headroom_score = eq.headroom_score if eq else Decimal("0.5")
    momentum = eq.momentum_score if eq else Decimal("0.5")
    entry_score = eq.score if eq else Decimal("50")

    headroom_ratio: Decimal | None = None
    required = eq.required_move_pct if eq else net_pct
    if headroom is not None and required and required > 0:
        headroom_ratio = headroom / required

    is_maker = opportunity.entry_fee_role == FeeRole.MAKER or str(
        meta.get("post_only") or meta.get("maker") or ""
    ).lower() in {"1", "true", "yes"}
    maker_net, taker_net, fill_prob = _maker_taker_net(
        profitability,
        maker_fill=cfg.default_maker_fill_probability,
        taker_fill=cfg.default_taker_fill_probability,
        is_maker=is_maker,
    )
    expected_net = net_eur * fill_prob

    timing = _timing_score(
        mark_series,
        extension_pct=extension,
        continuity=continuity,
        enabled=cfg.timing_enabled,
    )
    breakout = _breakout_quality(
        continuity=continuity,
        extension_pct=extension,
        liquidity_score=liquidity,
        spread_pct=spread_pct,
        marks=mark_series,
    )

    net_edge_score = _clamp01(expected_net / Decimal("2")) if expected_net > 0 else _ZERO
    cap_eff_score = _ZERO
    if ce.expected_net_profit_per_hour is not None and ce.expected_net_profit_per_hour > 0:
        cap_eff_score = _clamp01(ce.expected_net_profit_per_hour / Decimal("1"))
    hr_score = _clamp01(headroom_score)
    if headroom_ratio is not None:
        hr_score = _clamp01(min(_ONE, headroom_ratio / Decimal("3")))
    ext_score = eq.extension_score if eq else _ONE
    if extension is not None and eq is None:
        if extension >= eq_cfg.extension_extreme_pct:
            ext_score = _ZERO
        elif extension >= eq_cfg.extension_max_pct:
            ext_score = Decimal("0.3")
        elif extension >= eq_cfg.extension_moderate_pct:
            ext_score = Decimal("0.6")
    spread_score = _ONE
    if spread_pct is not None:
        if spread_pct > cfg.max_spread_pct:
            spread_score = _ZERO
            reasons.append("spread_reject")
        else:
            spread_score = _clamp01(_ONE - spread_pct / cfg.max_spread_pct)

    venue_score = _ZERO
    if venue_scores:
        venue_score = _clamp01(venue_scores[0].economic_score / max(_ONE, expected_net))

    # Phase 2: market regime, adverse selection, learning, execution quality
    from bot.intelligence.adverse_selection import assess_adverse_selection, config_from_settings as adverse_cfg_from
    from bot.intelligence.execution_quality import assess_execution, classify_urgency
    from bot.intelligence.market_regime_engine import (
        MarketRegime,
        classify_market_regime,
        config_from_settings as regime_cfg_from,
        regime_fit_for_strategy,
    )
    from bot.intelligence.outcome_learning import empirical_multiplier

    snap = snapshot or opportunity.market
    regime_cfg = regime_cfg_from(None)
    regime_assessment = None
    if cfg.regime_engine_enabled:
        regime_assessment = classify_market_regime(
            marks=mark_series,
            snapshot=snap,
            config=regime_cfg,
            candidate_count=candidate_count,
            avg_opportunity_score=avg_opportunity_score,
        )

    regime_fit = _ONE
    regime_score = Decimal("0.5")
    data_fresh = _ONE
    market_regime_str: str | None = None
    regime_conf: Decimal | None = None

    if regime_assessment is not None:
        market_regime_str = regime_assessment.regime.value
        regime_conf = regime_assessment.confidence
        data_fresh = regime_assessment.data_freshness_score
        regime_fit = regime_fit_for_strategy(
            strategy=opportunity.strategy_name,
            regime=regime_assessment.regime,
        )
        regime_score = _clamp01(regime_fit * regime_assessment.confidence)
        if regime_assessment.regime == MarketRegime.DEAD_MARKET:
            reasons.append("dead_market")
        if data_fresh < cfg.stale_data_reject_threshold:
            reasons.append("stale_market_data")

    adv = None
    adverse_score: Decimal | None = None
    if cfg.adverse_selection_enabled:
        adv = assess_adverse_selection(
            snapshot=snap,
            marks=mark_series,
            side=opportunity.side,
            order_price=opportunity.entry_price,
        )
        adverse_score = adv.adverse_selection_score
        if adverse_score >= cfg.adverse_selection_reject_threshold:
            reasons.append("adverse_selection_high")

    empirical_mult = _ONE
    if cfg.outcome_learning_enabled and outcome_store is not None and regime_assessment is not None:
        bucket = outcome_store.bucket(
            symbol=opportunity.symbol,
            venue=venue or "unknown",
            strategy=opportunity.strategy_name,
            regime=regime_assessment.regime.value,
        )
        empirical_mult = empirical_multiplier(bucket=bucket, config=learning_config)

    adverse_penalty = _ONE
    if adverse_score is not None:
        adverse_penalty = _ONE - adverse_score * cfg.weight_adverse_selection

    exec_decision_str: str | None = None
    if cfg.execution_quality_enabled and maker_net is not None and taker_net is not None:
        urgency = classify_urgency(extension_pct=extension)
        exec_assess = assess_execution(
            maker_net_eur=maker_net,
            taker_net_eur=taker_net,
            adverse=adv if cfg.adverse_selection_enabled else None,
            regime=regime_assessment,
            spread_pct=spread_pct,
            urgency=urgency,
        )
        exec_decision_str = exec_assess.decision.value
        if exec_assess.decision.value == "REJECT":
            reasons.append("execution_reject")
        elif exec_assess.decision.value == "WAIT":
            reasons.append("execution_wait")

    opp_score = _weighted_score(
        [
            (net_edge_score, cfg.weight_net_edge),
            (cap_eff_score, cfg.weight_capital_efficiency),
            (hr_score, cfg.weight_headroom),
            (momentum, cfg.weight_momentum),
            (continuity or Decimal("0.5"), cfg.weight_continuity),
            (ext_score, cfg.weight_extension),
            (liquidity, cfg.weight_liquidity),
            (spread_score, cfg.weight_spread),
            (timing, cfg.weight_timing),
            (venue_score, cfg.weight_venue),
            (breakout, cfg.weight_breakout),
            (regime_score, cfg.weight_regime_fit),
            (adverse_penalty, cfg.weight_adverse_selection),
        ]
    )
    opp_score = (opp_score * empirical_mult * data_fresh).quantize(Decimal("0.1"))

    mult = _ONE
    if eq is not None:
        mult = min(mult, eq.recommended_size_multiplier)
    mult = min(mult, ce.recommended_size_multiplier)

    if vol_regime == VolatilityRegime.EXTREME:
        mult = min(mult, cfg.extreme_volatility_size_cap)
        opp_score = min(opp_score, cfg.reduced_opportunity_score)
        reasons.append("volatility_extreme")
    elif vol_regime == VolatilityRegime.HIGH:
        mult = min(mult, cfg.high_volatility_size_cap)

    if cfg.spread_liquidity_enabled and liquidity < cfg.min_liquidity_score:
        opp_score = min(opp_score, cfg.reduced_opportunity_score)
        mult = min(mult, cfg.medium_size_multiplier)
        reasons.append("liquidity_low")

    if cfg.timing_enabled and timing < Decimal("0.3"):
        mult = min(mult, cfg.medium_size_multiplier)
        reasons.append("timing_late_spike")

    if regime_assessment is not None and regime_assessment.regime == MarketRegime.DEAD_MARKET:
        mult = min(mult, cfg.medium_size_multiplier)
        opp_score = min(opp_score, cfg.reduced_opportunity_score)

    if adverse_score is not None and adverse_score >= cfg.adverse_selection_reject_threshold:
        mult = min(mult, cfg.medium_size_multiplier)
        opp_score = min(opp_score, cfg.reduced_opportunity_score)

    if data_fresh < cfg.stale_data_reject_threshold:
        opp_score = min(opp_score, cfg.min_opportunity_score - Decimal("1"))
        mult = _ZERO

    decision = OpportunityDecision.HIGH_QUALITY
    if data_fresh < cfg.stale_data_reject_threshold:
        decision = OpportunityDecision.REJECT
        reasons.append("stale_data_reject")
    elif eq is not None and eq.recommendation == EntryQualityRecommendation.REJECT:
        decision = OpportunityDecision.REJECT
        reasons.append(eq.reject_reason or "entry_quality")
    elif ce.recommendation == EntryQualityRecommendation.REJECT:
        decision = OpportunityDecision.REJECT
        reasons.append(ce.reject_reason or "capital_efficiency")
    elif opp_score < cfg.min_opportunity_score:
        decision = OpportunityDecision.REJECT
        reasons.append("opportunity_score_low")
    elif opp_score < cfg.reduced_opportunity_score:
        decision = OpportunityDecision.REDUCED
        mult = min(mult, cfg.medium_size_multiplier)
    elif opp_score < cfg.high_quality_score:
        decision = OpportunityDecision.REDUCED
        mult = min(mult, cfg.good_size_multiplier)
    else:
        mult = min(mult, cfg.excellent_size_multiplier)

    mult = min(_ONE, max(_ZERO, mult))
    if decision == OpportunityDecision.REJECT:
        mult = _ZERO

    return OpportunityAssessment(
        symbol=opportunity.symbol,
        venue=venue or None,
        direction=side,
        expected_net_profit_eur=expected_net,
        expected_net_profit_pct=net_pct,
        expected_hold_seconds=ce.expected_hold_seconds,
        expected_net_eur_per_hour=ce.expected_net_profit_per_hour,
        expected_net_eur_per_capital_hour=ce.capital_efficiency_per_capital_hour,
        momentum_score=momentum,
        continuity_score=continuity,
        volatility_regime=vol_regime,
        extension_pct=extension,
        headroom_pct=headroom,
        headroom_score=headroom_score,
        headroom_5m=h5,
        headroom_30m=h30,
        headroom_2h=h2h,
        spread_pct=spread_pct,
        liquidity_score=liquidity,
        fill_probability=fill_prob,
        venue_score=venue_score,
        entry_quality_score=entry_score,
        timing_score=timing,
        breakout_quality_score=breakout,
        capital_required_eur=notional,
        recommended_size_multiplier=mult,
        opportunity_score=opp_score,
        decision=decision,
        reasons=tuple(dict.fromkeys(reasons)),
        maker_expected_net_eur=maker_net,
        taker_expected_net_eur=taker_net,
        extension_5m=ext_5m,
        extension_30m=ext_30m,
        extension_2h=ext_2h,
        headroom_ratio=headroom_ratio,
        market_regime=market_regime_str,
        regime_score=regime_score,
        regime_fit=regime_fit,
        regime_confidence=regime_conf,
        adverse_selection_score=adverse_score,
        empirical_multiplier=empirical_mult,
        execution_decision=exec_decision_str,
        data_freshness_score=data_fresh,
    )


def apply_assessment_to_opportunity(
    opportunity: TradeOpportunity,
    assessment: OpportunityAssessment,
) -> TradeOpportunity | None:
    """Apply downward sizing + metadata; return None on reject."""
    if assessment.decision == OpportunityDecision.REJECT:
        return None
    new_qty = apply_size_multiplier(
        opportunity.quantity, assessment.recommended_size_multiplier
    )
    if new_qty <= 0:
        return None
    meta = dict(opportunity.metadata or {})
    meta.update(
        {
            "opportunity_score": str(assessment.opportunity_score),
            "opportunity_decision": assessment.decision.value,
            "opportunity_reject_reasons": ",".join(assessment.reasons),
            "entry_quality_score": str(assessment.entry_quality_score),
            "entry_quality_multiplier": str(assessment.recommended_size_multiplier),
            "entry_quality_recommendation": (
                EntryQualityRecommendation.NORMAL_SIZE.value
                if assessment.decision == OpportunityDecision.HIGH_QUALITY
                else EntryQualityRecommendation.REDUCED_SIZE.value
            ),
            "headroom_pct": (
                str(assessment.headroom_pct) if assessment.headroom_pct is not None else None
            ),
            "extension_pct": (
                str(assessment.extension_pct) if assessment.extension_pct is not None else None
            ),
            "trend_continuity": (
                str(assessment.continuity_score)
                if assessment.continuity_score is not None
                else None
            ),
            "expected_net_profit_per_hour": (
                str(assessment.expected_net_eur_per_hour)
                if assessment.expected_net_eur_per_hour is not None
                else None
            ),
            "capital_efficiency_per_capital_hour": (
                str(assessment.expected_net_eur_per_capital_hour)
                if assessment.expected_net_eur_per_capital_hour is not None
                else None
            ),
            "expected_hold_seconds": (
                str(assessment.expected_hold_seconds)
                if assessment.expected_hold_seconds is not None
                else None
            ),
            "volatility_regime": assessment.volatility_regime.value,
            "timing_score": str(assessment.timing_score),
            "breakout_quality_score": str(assessment.breakout_quality_score),
        }
    )
    if assessment.venue and not meta.get("buy_exchange"):
        meta["buy_exchange"] = assessment.venue
        meta["venue"] = assessment.venue
    return opportunity.model_copy(update={"quantity": new_qty, "metadata": meta})


def rank_opportunities(
    assessments: Sequence[OpportunityAssessment],
) -> list[OpportunityAssessment]:
    """Sort by opportunity score, then NET EUR/hour, then NET EUR."""
    return sorted(
        assessments,
        key=lambda a: (
            a.opportunity_score,
            a.expected_net_eur_per_hour or _ZERO,
            a.expected_net_profit_eur,
        ),
        reverse=True,
    )


def allocate_portfolio(
    assessments: Sequence[OpportunityAssessment],
    *,
    available_capital_eur: Decimal,
    corr_groups: dict[str, frozenset[str]] | None = None,
    max_per_corr_group: int = 2,
) -> tuple[list[OpportunityAssessment], list[OpportunityAssessment]]:
    """Greedy capital allocation with correlation awareness.

    Returns (selected, skipped). Never increases size — selection only.
    """
    ranked = rank_opportunities(
        [a for a in assessments if a.decision != OpportunityDecision.REJECT]
    )
    selected: list[OpportunityAssessment] = []
    skipped: list[OpportunityAssessment] = []
    budget = available_capital_eur
    corr_counts: dict[str, int] = {}
    groups = corr_groups or {}

    def _base(sym: str) -> str:
        s = sym.upper()
        for quote in ("EUR", "USDT", "USDC", "USD"):
            if s.endswith(quote):
                return s[: -len(quote)]
        return s

    def _corr_key(base: str) -> str:
        for key, members in groups.items():
            if base in members:
                return key
        return base

    for assessment in ranked:
        cap = assessment.capital_required_eur * assessment.recommended_size_multiplier
        base = _base(assessment.symbol)
        ck = _corr_key(base)
        alphai_deploy = "alphai_inventory_build" in (assessment.reasons or ())
        if not alphai_deploy and corr_counts.get(ck, 0) >= max_per_corr_group:
            skipped.append(assessment)
            continue
        if cap > budget and budget > 0:
            skipped.append(assessment)
            continue
        if cap <= 0:
            skipped.append(assessment)
            continue
        selected.append(assessment)
        budget -= cap
        corr_counts[ck] = corr_counts.get(ck, 0) + 1

    for assessment in assessments:
        if assessment.decision == OpportunityDecision.REJECT:
            skipped.append(assessment)

    return selected, skipped


def dedupe_venues(
    opportunities: Sequence[TradeOpportunity],
    *,
    venue_config: VenueEconomicsConfig | None = None,
) -> list[TradeOpportunity]:
    return select_best_buy_opportunities(
        list(opportunities), config=venue_config or VenueEconomicsConfig()
    )
