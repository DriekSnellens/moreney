"""Entry quality + headroom assessment for live micro buys.

Deterministic, replay-safe: no exchange I/O, no wall-clock, no randomness.
Uses existing profitability results for NET break-even — never re-implements fees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from bot.core.enums import EntryQualityRecommendation, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class EntryQualityConfig:
    """Resolved thresholds for entry quality (from Settings or overrides)."""

    enabled: bool = True
    headroom_min_pct: Decimal = Decimal("0.0025")
    extension_moderate_pct: Decimal = Decimal("0.012")
    extension_max_pct: Decimal = Decimal("0.025")
    extension_extreme_pct: Decimal = Decimal("0.045")
    quality_min_score: Decimal = Decimal("60")
    reduced_size_score: Decimal = Decimal("70")
    normal_size_score: Decimal = Decimal("80")
    reduced_size_multiplier: Decimal = Decimal("0.75")
    small_size_multiplier: Decimal = Decimal("0.50")
    min_continuity_score: Decimal = Decimal("0.35")
    target_harvest_pct: Decimal = Decimal("0.012")
    range_lookback: int = 30
    extension_samples_5m: int = 5
    extension_samples_30m: int = 30
    extension_samples_2h: int = 120
    continuity_min_marks: int = 5
    headroom_unknown_score: Decimal = Decimal("0.45")
    weight_momentum: Decimal = Decimal("0.20")
    weight_continuity: Decimal = Decimal("0.15")
    weight_headroom: Decimal = Decimal("0.30")
    weight_extension: Decimal = Decimal("0.15")
    weight_net_edge: Decimal = Decimal("0.15")
    weight_liquidity: Decimal = Decimal("0.05")
    momentum_min_return: Decimal = Decimal("0.0015")
    momentum_short_min_return: Decimal = Decimal("0.001")
    momentum_short_samples: int = 6


@dataclass(frozen=True, slots=True)
class EntryQualityAssessment:
    """Outcome of entry quality evaluation."""

    score: Decimal
    momentum_score: Decimal
    trend_continuity: Decimal | None
    extension_pct: Decimal | None
    extension_score: Decimal
    local_range_position: Decimal | None
    headroom_pct: Decimal | None
    headroom_score: Decimal
    net_break_even_pct: Decimal
    required_move_pct: Decimal
    target_harvest_pct: Decimal
    expected_net_profit_eur: Decimal | None
    recommended_size_multiplier: Decimal
    recommendation: EntryQualityRecommendation
    reject_reason: str = ""
    extension_5m: Decimal | None = None
    extension_30m: Decimal | None = None
    extension_2h: Decimal | None = None
    range_low: Decimal | None = None
    range_high: Decimal | None = None
    liquidity_score: Decimal = _ZERO
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryQualityDiagnostics:
    """Session counters for entry quality (no persistence required)."""

    candidates: int = 0
    normal: int = 0
    reduced: int = 0
    rejected: int = 0
    headroom_reject: int = 0
    extension_reject: int = 0
    continuity_reject: int = 0
    headroom_unknown: int = 0
    _sum_headroom: Decimal = _ZERO
    _sum_extension: Decimal = _ZERO
    _sum_quality: Decimal = _ZERO
    _sum_continuity: Decimal = _ZERO
    _sum_required_move: Decimal = _ZERO
    _sum_multiplier: Decimal = _ZERO
    _count_scored: int = 0

    def record(self, assessment: EntryQualityAssessment) -> None:
        self.candidates += 1
        if assessment.recommendation == EntryQualityRecommendation.NORMAL_SIZE:
            self.normal += 1
        elif assessment.recommendation == EntryQualityRecommendation.REDUCED_SIZE:
            self.reduced += 1
        else:
            self.rejected += 1
        reason = assessment.reject_reason
        if "headroom" in reason:
            self.headroom_reject += 1
        if "extension" in reason:
            self.extension_reject += 1
        if "continuity" in reason:
            self.continuity_reject += 1
        if assessment.headroom_pct is None:
            self.headroom_unknown += 1
        if assessment.score > 0:
            self._count_scored += 1
            self._sum_quality += assessment.score
            self._sum_multiplier += assessment.recommended_size_multiplier
            self._sum_required_move += assessment.required_move_pct
            if assessment.headroom_pct is not None:
                self._sum_headroom += assessment.headroom_pct
            if assessment.extension_pct is not None:
                self._sum_extension += assessment.extension_pct
            if assessment.trend_continuity is not None:
                self._sum_continuity += assessment.trend_continuity

    def snapshot(self) -> dict[str, Any]:
        n = self._count_scored or 1
        return {
            "entry_quality_candidates": self.candidates,
            "entry_quality_normal": self.normal,
            "entry_quality_reduced": self.reduced,
            "entry_quality_rejected": self.rejected,
            "headroom_reject": self.headroom_reject,
            "extension_reject": self.extension_reject,
            "continuity_reject": self.continuity_reject,
            "headroom_unknown": self.headroom_unknown,
            "average_headroom_pct": str((self._sum_headroom / n).quantize(Decimal("0.0001")))
            if self._count_scored
            else None,
            "average_extension_pct": str((self._sum_extension / n).quantize(Decimal("0.0001")))
            if self._count_scored
            else None,
            "average_entry_quality": str((self._sum_quality / n).quantize(Decimal("0.1")))
            if self._count_scored
            else None,
            "average_continuity": str((self._sum_continuity / n).quantize(Decimal("0.0001")))
            if self._count_scored and self._sum_continuity > 0
            else None,
            "average_required_move_pct": str(
                (self._sum_required_move / n).quantize(Decimal("0.0001"))
            )
            if self._count_scored
            else None,
            "average_recommended_size_multiplier": str(
                (self._sum_multiplier / n).quantize(Decimal("0.01"))
            )
            if self._count_scored
            else None,
        }


def config_from_settings(settings: Any) -> EntryQualityConfig:
    """Build config from pydantic Settings."""
    return EntryQualityConfig(
        enabled=bool(getattr(settings, "live_micro_entry_headroom_enabled", True)),
        headroom_min_pct=Decimal(
            str(getattr(settings, "live_micro_entry_headroom_min_pct", 0.0025))
        ),
        extension_moderate_pct=Decimal(
            str(getattr(settings, "live_micro_entry_extension_moderate_pct", 0.012))
        ),
        extension_max_pct=Decimal(
            str(getattr(settings, "live_micro_entry_extension_max_pct", 0.025))
        ),
        extension_extreme_pct=Decimal(
            str(getattr(settings, "live_micro_entry_extension_extreme_pct", 0.045))
        ),
        quality_min_score=Decimal(
            str(getattr(settings, "live_micro_entry_quality_min_score", 60))
        ),
        reduced_size_score=Decimal(
            str(getattr(settings, "live_micro_entry_reduced_size_score", 70))
        ),
        normal_size_score=Decimal(
            str(getattr(settings, "live_micro_entry_normal_size_score", 80))
        ),
        reduced_size_multiplier=Decimal(
            str(getattr(settings, "live_micro_entry_reduced_size_multiplier", 0.75))
        ),
        small_size_multiplier=Decimal(
            str(getattr(settings, "live_micro_entry_small_size_multiplier", 0.50))
        ),
        min_continuity_score=Decimal(
            str(getattr(settings, "live_micro_entry_min_continuity_score", 0.35))
        ),
        target_harvest_pct=Decimal(
            str(getattr(settings, "live_micro_entry_target_harvest_pct", 0.012))
        ),
        range_lookback=int(getattr(settings, "live_micro_entry_range_lookback", 30)),
        extension_samples_5m=int(
            getattr(settings, "live_micro_entry_extension_samples_5m", 5)
        ),
        extension_samples_30m=int(
            getattr(settings, "live_micro_entry_extension_samples_30m", 30)
        ),
        extension_samples_2h=int(
            getattr(settings, "live_micro_entry_extension_samples_2h", 120)
        ),
        continuity_min_marks=int(
            getattr(settings, "live_micro_entry_continuity_min_marks", 5)
        ),
        headroom_unknown_score=Decimal(
            str(getattr(settings, "live_micro_entry_headroom_unknown_score", 0.45))
        ),
        weight_momentum=Decimal(
            str(getattr(settings, "live_micro_entry_weight_momentum", 0.20))
        ),
        weight_continuity=Decimal(
            str(getattr(settings, "live_micro_entry_weight_continuity", 0.15))
        ),
        weight_headroom=Decimal(
            str(getattr(settings, "live_micro_entry_weight_headroom", 0.30))
        ),
        weight_extension=Decimal(
            str(getattr(settings, "live_micro_entry_weight_extension", 0.15))
        ),
        weight_net_edge=Decimal(
            str(getattr(settings, "live_micro_entry_weight_net_edge", 0.15))
        ),
        weight_liquidity=Decimal(
            str(getattr(settings, "live_micro_entry_weight_liquidity", 0.05))
        ),
        momentum_min_return=Decimal(
            str(getattr(settings, "paper_buy_momentum_min_return", 0.0015))
        ),
        momentum_short_min_return=Decimal(
            str(getattr(settings, "live_micro_entry_short_momentum_min_return", 0.001))
        ),
        momentum_short_samples=int(
            getattr(settings, "live_micro_entry_short_momentum_samples", 6)
        ),
    )


def _step_returns(marks: Sequence[Decimal]) -> list[Decimal]:
    out: list[Decimal] = []
    if len(marks) < 2:
        return out
    prev = marks[0]
    for cur in marks[1:]:
        if prev > 0:
            out.append((cur - prev) / prev)
        prev = cur
    return out


def compute_trend_continuity(
    marks: Sequence[Decimal],
    *,
    min_marks: int = 5,
) -> Decimal | None:
    """0 = choppy/spike, 1 = smooth rising trend. None if insufficient history."""
    if len(marks) < min_marks:
        return None
    steps = _step_returns(marks)
    if len(steps) < min_marks - 1:
        return None
    ups = sum(1 for s in steps if s > 0)
    up_ratio = Decimal(ups) / Decimal(len(steps))
    abs_steps = [abs(s) for s in steps if s != 0]
    if not abs_steps:
        return Decimal("0.5")
    mean_abs = sum(abs_steps, _ZERO) / Decimal(len(abs_steps))
    max_abs = max(abs_steps)
    if mean_abs <= 0:
        spike_ratio = _ONE
    else:
        spike_ratio = min(_ONE, max_abs / (mean_abs * Decimal("3")))
    smoothness = _ONE - spike_ratio * Decimal("0.6")
    negative_penalty = sum(
        (abs(s) for s in steps if s < Decimal("-0.001")),
        _ZERO,
    )
    neg_factor = max(_ZERO, _ONE - negative_penalty * Decimal("8"))
    score = up_ratio * smoothness * neg_factor
    return max(_ZERO, min(_ONE, score))


def compute_extension_over_window(
    marks: Sequence[Decimal],
    window: int,
) -> Decimal | None:
    """Extension = (last - min_in_window) / min_in_window."""
    if len(marks) < 2 or window < 2:
        return None
    w = min(window, len(marks))
    segment = list(marks[-w:])
    low = min(segment)
    last = segment[-1]
    if low <= 0:
        return None
    return (last - low) / low


def compute_local_range(
    marks: Sequence[Decimal],
    *,
    lookback: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Return (range_low, range_high, position_in_range 0..1)."""
    if len(marks) < 2:
        return None, None, None
    w = min(lookback, len(marks))
    segment = list(marks[-w:])
    low = min(segment)
    high = max(segment)
    last = segment[-1]
    if high <= low:
        return low, high, Decimal("0.5")
    pos = (last - low) / (high - low)
    return low, high, max(_ZERO, min(_ONE, pos))


def compute_headroom_pct(
    marks: Sequence[Decimal],
    *,
    lookback: int,
    current_price: Decimal,
) -> Decimal | None:
    """Room to recent range high from current price (fraction)."""
    if current_price <= 0 or len(marks) < 2:
        return None
    w = min(lookback, len(marks))
    segment = list(marks[-w:])
    high = max(segment)
    if high <= current_price:
        return _ZERO
    return (high - current_price) / current_price


def net_break_even_pct(
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
) -> Decimal:
    """Round-trip cost fraction from existing profitability (no fee recompute)."""
    notional = opportunity.quantity * opportunity.entry_price
    if notional <= 0:
        return _ZERO
    est = profitability.estimate
    if est is not None:
        costs = est.buy_fee + est.sell_fee + est.slippage + est.execution_buffer
        return costs / notional
    costs = (
        profitability.buy_fee_usd
        + profitability.sell_fee_usd
        + profitability.slippage_usd
        + profitability.execution_buffer_usd
    )
    return costs / notional


def _momentum_score_from_marks(
    marks: Sequence[Decimal],
    *,
    min_return: Decimal,
    short_samples: int,
    short_min: Decimal,
) -> Decimal:
    if len(marks) < 2:
        return _ZERO
    first = marks[0]
    last = marks[-1]
    if first <= 0:
        return _ZERO
    full_ret = (last - first) / first
    if full_ret < min_return:
        base = max(_ZERO, full_ret / min_return) * Decimal("0.5")
    else:
        base = min(_ONE, full_ret / (min_return * Decimal("3")))
    short_ret = None
    if len(marks) >= short_samples:
        sw = list(marks[-short_samples:])
        if sw[0] > 0:
            short_ret = (sw[-1] - sw[0]) / sw[0]
    if short_ret is not None and short_ret < short_min:
        base *= Decimal("0.6")
    elif short_ret is not None and short_ret >= short_min:
        base = min(_ONE, base + Decimal("0.15"))
    return max(_ZERO, min(_ONE, base))


def _extension_score(
    extension_pct: Decimal | None,
    *,
    moderate: Decimal,
    max_pct: Decimal,
    extreme: Decimal,
) -> Decimal:
    if extension_pct is None:
        return Decimal("0.7")
    ext = extension_pct
    if ext >= extreme:
        return _ZERO
    if ext >= max_pct:
        return Decimal("0.25")
    if ext >= moderate:
        return Decimal("0.55")
    return _ONE


def _headroom_score(
    headroom_pct: Decimal | None,
    required_move_pct: Decimal,
    *,
    unknown_score: Decimal,
) -> Decimal:
    if headroom_pct is None:
        return unknown_score
    if required_move_pct <= 0:
        return _ONE if headroom_pct > 0 else unknown_score
    ratio = headroom_pct / required_move_pct
    if ratio >= Decimal("2.5"):
        return _ONE
    if ratio >= Decimal("1.5"):
        return Decimal("0.85")
    if ratio >= _ONE:
        return Decimal("0.65")
    if ratio >= Decimal("0.75"):
        return Decimal("0.40")
    return max(_ZERO, ratio * Decimal("0.5"))


def _net_edge_score(profitability: ProfitabilityResult) -> Decimal:
    ret = profitability.net_return
    if ret <= 0:
        meta_ret = profitability.assumptions.get("net_return")
        if meta_ret is not None:
            try:
                ret = Decimal(str(meta_ret))
            except Exception:  # noqa: BLE001
                ret = _ZERO
    if ret <= 0:
        return Decimal("0.3")
    return min(_ONE, ret / Decimal("0.004"))


def _liquidity_score(opportunity: TradeOpportunity) -> Decimal:
    meta = opportunity.metadata or {}
    book = opportunity.market.order_book if opportunity.market else None
    if book is not None:
        try:
            bid_depth = sum(
                (Decimal(str(l.quantity)) for l in book.bids[:5]),
                _ZERO,
            )
            if bid_depth > 0:
                return min(_ONE, bid_depth / Decimal("10"))
        except Exception:  # noqa: BLE001
            pass
    liq = meta.get("liquidity_base") or meta.get("liquidity")
    if liq is not None:
        try:
            return min(_ONE, Decimal(str(liq)) / Decimal("5"))
        except Exception:  # noqa: BLE001
            pass
    return Decimal("0.5")


def _weighted_score(
    *,
    momentum: Decimal,
    continuity: Decimal | None,
    headroom: Decimal,
    extension: Decimal,
    net_edge: Decimal,
    liquidity: Decimal,
    cfg: EntryQualityConfig,
) -> Decimal:
    cont = continuity if continuity is not None else Decimal("0.55")
    total_w = (
        cfg.weight_momentum
        + cfg.weight_continuity
        + cfg.weight_headroom
        + cfg.weight_extension
        + cfg.weight_net_edge
        + cfg.weight_liquidity
    )
    if total_w <= 0:
        total_w = _ONE
    raw = (
        momentum * cfg.weight_momentum
        + cont * cfg.weight_continuity
        + headroom * cfg.weight_headroom
        + extension * cfg.weight_extension
        + net_edge * cfg.weight_net_edge
        + liquidity * cfg.weight_liquidity
    ) / total_w
    return (raw * _HUNDRED).quantize(Decimal("0.1"))


def evaluate_entry_quality(
    *,
    opportunity: TradeOpportunity,
    profitability: ProfitabilityResult,
    marks: Sequence[Decimal] | None = None,
    config: EntryQualityConfig | None = None,
) -> EntryQualityAssessment:
    """Deterministic entry quality assessment (no I/O, no clock)."""
    cfg = config or EntryQualityConfig()
    side = opportunity.side
    if hasattr(side, "value"):
        side_val = str(side.value).lower()
    else:
        side_val = str(side).lower()
    is_buy = side_val in {"buy", "long"}

    be_pct = net_break_even_pct(opportunity, profitability)
    required = be_pct + cfg.target_harvest_pct
    target_harvest = cfg.target_harvest_pct
    expected_net = profitability.net_profit_usd

    if not is_buy or not cfg.enabled:
        return EntryQualityAssessment(
            score=_HUNDRED,
            momentum_score=_ONE,
            trend_continuity=None,
            extension_pct=None,
            extension_score=_ONE,
            local_range_position=None,
            headroom_pct=None,
            headroom_score=_ONE,
            net_break_even_pct=be_pct,
            required_move_pct=required,
            target_harvest_pct=target_harvest,
            expected_net_profit_eur=expected_net,
            recommended_size_multiplier=_ONE,
            recommendation=EntryQualityRecommendation.NORMAL_SIZE,
        )

    mark_list = [Decimal(str(m)) for m in (marks or []) if m and Decimal(str(m)) > 0]
    price = opportunity.entry_price
    if price <= 0 and mark_list:
        price = mark_list[-1]

    momentum_score = _momentum_score_from_marks(
        mark_list,
        min_return=cfg.momentum_min_return,
        short_samples=cfg.momentum_short_samples,
        short_min=cfg.momentum_short_min_return,
    )
    continuity = compute_trend_continuity(
        mark_list, min_marks=cfg.continuity_min_marks
    )
    ext_5m = compute_extension_over_window(mark_list, cfg.extension_samples_5m)
    ext_30m = compute_extension_over_window(mark_list, cfg.extension_samples_30m)
    ext_2h = compute_extension_over_window(mark_list, cfg.extension_samples_2h)
    extensions = [e for e in (ext_5m, ext_30m, ext_2h) if e is not None]
    extension_pct = max(extensions) if extensions else None

    range_low, range_high, range_pos = compute_local_range(
        mark_list, lookback=cfg.range_lookback
    )
    headroom = compute_headroom_pct(
        mark_list, lookback=cfg.range_lookback, current_price=price
    )

    ext_score = _extension_score(
        extension_pct,
        moderate=cfg.extension_moderate_pct,
        max_pct=cfg.extension_max_pct,
        extreme=cfg.extension_extreme_pct,
    )
    hr_score = _headroom_score(
        headroom, required, unknown_score=cfg.headroom_unknown_score
    )
    net_score = _net_edge_score(profitability)
    liq_score = _liquidity_score(opportunity)

    score = _weighted_score(
        momentum=momentum_score,
        continuity=continuity,
        headroom=hr_score,
        extension=ext_score,
        net_edge=net_score,
        liquidity=liq_score,
        cfg=cfg,
    )

    reject_reason = ""
    recommendation = EntryQualityRecommendation.NORMAL_SIZE
    multiplier = _ONE

    # Hard gates (conservative)
    if extension_pct is not None and extension_pct >= cfg.extension_extreme_pct:
        reject_reason = "extension_extreme"
        recommendation = EntryQualityRecommendation.REJECT
        multiplier = _ZERO
    elif (
        continuity is not None
        and continuity < cfg.min_continuity_score
        and extension_pct is not None
        and extension_pct >= cfg.extension_max_pct
    ):
        reject_reason = "continuity_spike"
        recommendation = EntryQualityRecommendation.REJECT
        multiplier = _ZERO
    elif headroom is not None and headroom < required * Decimal("0.85"):
        reject_reason = "insufficient_headroom"
        recommendation = EntryQualityRecommendation.REJECT
        multiplier = _ZERO
    elif headroom is not None and headroom < cfg.headroom_min_pct:
        reject_reason = "headroom_below_min"
        recommendation = EntryQualityRecommendation.REJECT
        multiplier = _ZERO
    elif score < cfg.quality_min_score:
        reject_reason = "quality_below_min"
        recommendation = EntryQualityRecommendation.REJECT
        multiplier = _ZERO
    elif score >= cfg.normal_size_score:
        recommendation = EntryQualityRecommendation.NORMAL_SIZE
        multiplier = _ONE
    elif score >= cfg.reduced_size_score:
        recommendation = EntryQualityRecommendation.REDUCED_SIZE
        multiplier = cfg.reduced_size_multiplier
    else:
        recommendation = EntryQualityRecommendation.REDUCED_SIZE
        multiplier = cfg.small_size_multiplier

    # Headroom sizing overlay (downward only)
    if recommendation != EntryQualityRecommendation.REJECT and headroom is not None:
        if headroom < required:
            reject_reason = "insufficient_headroom"
            recommendation = EntryQualityRecommendation.REJECT
            multiplier = _ZERO
        elif headroom < required * Decimal("1.25"):
            multiplier = min(multiplier, cfg.small_size_multiplier)
        elif headroom < required * Decimal("1.75"):
            multiplier = min(multiplier, cfg.reduced_size_multiplier)

    # Extension penalty on size (not auto-reject unless extreme)
    if (
        recommendation != EntryQualityRecommendation.REJECT
        and extension_pct is not None
    ):
        if extension_pct >= cfg.extension_max_pct:
            multiplier = min(multiplier, cfg.small_size_multiplier)
        elif extension_pct >= cfg.extension_moderate_pct:
            multiplier = min(multiplier, cfg.reduced_size_multiplier)

    multiplier = min(_ONE, max(_ZERO, multiplier))

    if recommendation == EntryQualityRecommendation.REJECT:
        multiplier = _ZERO

    return EntryQualityAssessment(
        score=score,
        momentum_score=momentum_score,
        trend_continuity=continuity,
        extension_pct=extension_pct,
        extension_score=ext_score,
        local_range_position=range_pos,
        headroom_pct=headroom,
        headroom_score=hr_score,
        net_break_even_pct=be_pct,
        required_move_pct=required,
        target_harvest_pct=target_harvest,
        expected_net_profit_eur=expected_net,
        recommended_size_multiplier=multiplier,
        recommendation=recommendation,
        reject_reason=reject_reason,
        extension_5m=ext_5m,
        extension_30m=ext_30m,
        extension_2h=ext_2h,
        range_low=range_low,
        range_high=range_high,
        liquidity_score=liq_score,
        details={
            "net_edge_score": str(net_score),
            "symbol": opportunity.symbol,
        },
    )


def apply_size_multiplier(
    quantity: Decimal,
    multiplier: Decimal,
    *,
    min_qty: Decimal = Decimal("0.00000001"),
) -> Decimal:
    """Apply entry quality multiplier (never above 1.0)."""
    mult = min(_ONE, max(_ZERO, multiplier))
    if mult <= 0:
        return _ZERO
    out = (quantity * mult).quantize(Decimal("0.00000001"))
    if out < min_qty:
        return _ZERO
    return out
