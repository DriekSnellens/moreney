"""Adverse selection engine — deterministic fill-toxicity signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence

from bot.core.enums import OpportunitySide
from bot.core.models import MarketSnapshot

_ZERO = Decimal("0")
_ONE = Decimal("1")


class FillQuality(str, Enum):
    FILL = "FILL"
    GOOD_FILL = "GOOD_FILL"
    TOXIC_FILL = "TOXIC_FILL"


@dataclass(frozen=True, slots=True)
class AdverseSelectionConfig:
    enabled: bool = True
    high_score_threshold: Decimal = Decimal("0.65")
    toxic_adverse_pct: Decimal = Decimal("0.003")
    moderate_adverse_pct: Decimal = Decimal("0.001")
    microprice_weight: Decimal = Decimal("0.25")
    imbalance_weight: Decimal = Decimal("0.20")
    momentum_weight: Decimal = Decimal("0.20")
    spread_weight: Decimal = Decimal("0.10")
    acceleration_weight: Decimal = Decimal("0.15")
    depth_weight: Decimal = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class AdverseSelectionAssessment:
    adverse_selection_score: Decimal
    microprice: Decimal | None
    midprice: Decimal | None
    microprice_vs_mid: Decimal | None
    orderbook_imbalance: Decimal | None
    short_term_return: Decimal | None
    reasons: tuple[str, ...]


def _clamp01(v: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, v))


def compute_microprice(snapshot: MarketSnapshot | None) -> Decimal | None:
    """Depth-weighted fair value from top-of-book."""
    if snapshot is None or snapshot.order_book is None:
        return None
    book = snapshot.order_book
    if not book.bids or not book.asks:
        return None
    bid = book.bids[0]
    ask = book.asks[0]
    bid_depth = sum((lvl.amount for lvl in book.bids[:5]), _ZERO)
    ask_depth = sum((lvl.amount for lvl in book.asks[:5]), _ZERO)
    total = bid_depth + ask_depth
    if total <= 0 or bid.price <= 0 or ask.price <= 0:
        return None
    # Standard microprice: weight ask side by bid depth (more bid depth → price closer to ask)
    w_bid = bid_depth / total
    return bid.price * w_bid + ask.price * (_ONE - w_bid)


def _orderbook_imbalance(snapshot: MarketSnapshot | None) -> Decimal | None:
    if snapshot is None or snapshot.order_book is None:
        return None
    book = snapshot.order_book
    bid_depth = sum((lvl.amount for lvl in book.bids[:5]), _ZERO)
    ask_depth = sum((lvl.amount for lvl in book.asks[:5]), _ZERO)
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return (bid_depth - ask_depth) / total


def _short_return(marks: Sequence[Decimal]) -> Decimal | None:
    if len(marks) < 2:
        return None
    prev, cur = marks[-2], marks[-1]
    if prev <= 0:
        return None
    return (cur - prev) / prev


def _acceleration(marks: Sequence[Decimal]) -> Decimal | None:
    if len(marks) < 3:
        return None
    r1 = _short_return(marks[-2:])
    r2 = None
    if marks[-3] > 0:
        r2 = (marks[-2] - marks[-3]) / marks[-3]
    if r1 is None or r2 is None:
        return None
    return r1 - r2


def assess_adverse_selection(
    *,
    snapshot: MarketSnapshot | None = None,
    marks: Sequence[Decimal] | None = None,
    side: OpportunitySide | str = OpportunitySide.BUY,
    order_price: Decimal | None = None,
    config: AdverseSelectionConfig | None = None,
) -> AdverseSelectionAssessment:
    """Score 0..1 — higher means more adverse-selection risk for a resting fill."""
    cfg = config or AdverseSelectionConfig()
    mark_series = list(marks or [])
    side_l = str(side.value if hasattr(side, "value") else side).lower()
    is_buy = side_l.startswith("b")
    reasons: list[str] = []
    score_parts: list[tuple[Decimal, Decimal]] = []

    micro = compute_microprice(snapshot)
    mid = snapshot.mid if snapshot is not None else None
    micro_vs_mid: Decimal | None = None
    if micro is not None and mid is not None and mid > 0:
        micro_vs_mid = (micro - mid) / mid

    imb = _orderbook_imbalance(snapshot)
    if imb is not None:
        if is_buy and imb < Decimal("-0.15"):
            score_parts.append((_clamp01(-imb), cfg.imbalance_weight))
            reasons.append("negative_order_flow_for_buy")
        elif not is_buy and imb > Decimal("0.15"):
            score_parts.append((_clamp01(imb), cfg.imbalance_weight))
            reasons.append("positive_order_flow_for_sell")

    if micro_vs_mid is not None:
        if is_buy and micro_vs_mid > Decimal("0.0003"):
            score_parts.append(
                (_clamp01(micro_vs_mid / Decimal("0.002")), cfg.microprice_weight)
            )
            reasons.append("microprice_above_mid")
        elif not is_buy and micro_vs_mid < Decimal("-0.0003"):
            score_parts.append(
                (_clamp01(-micro_vs_mid / Decimal("0.002")), cfg.microprice_weight)
            )
            reasons.append("microprice_below_mid")

    st_ret = _short_return(mark_series)
    if st_ret is not None:
        if is_buy and st_ret > Decimal("0.001"):
            score_parts.append((_clamp01(st_ret / Decimal("0.005")), cfg.momentum_weight))
            reasons.append("accelerating_upside_against_buy")
        elif not is_buy and st_ret < Decimal("-0.001"):
            score_parts.append(
                (_clamp01(-st_ret / Decimal("0.005")), cfg.momentum_weight)
            )
            reasons.append("accelerating_downside_against_sell")

    accel = _acceleration(mark_series)
    if accel is not None:
        if is_buy and accel > Decimal("0.0005"):
            score_parts.append((_clamp01(accel / Decimal("0.003")), cfg.acceleration_weight))
            reasons.append("price_acceleration")
        elif not is_buy and accel < Decimal("-0.0005"):
            score_parts.append(
                (_clamp01(-accel / Decimal("0.003")), cfg.acceleration_weight)
            )
            reasons.append("price_acceleration")

    spread_pct = None
    if snapshot is not None and snapshot.mid > 0:
        spread_pct = snapshot.spread / snapshot.mid
        if spread_pct < Decimal("0.0002"):
            score_parts.append((Decimal("0.6"), cfg.spread_weight))
            reasons.append("spread_collapsed")

    if snapshot is not None and snapshot.order_book is not None:
        book = snapshot.order_book
        near_depth = _ZERO
        if is_buy and book.asks:
            near_depth = sum((lvl.amount for lvl in book.asks[:3]), _ZERO)
        elif not is_buy and book.bids:
            near_depth = sum((lvl.amount for lvl in book.bids[:3]), _ZERO)
        if near_depth > 0 and near_depth < Decimal("0.5"):
            score_parts.append((Decimal("0.55"), cfg.depth_weight))
            reasons.append("thin_near_depth")

    if order_price is not None and micro is not None and micro > 0:
        dist = (order_price - micro) / micro
        if is_buy and dist > Decimal("0.001"):
            score_parts.append((_clamp01(dist / Decimal("0.005")), Decimal("0.10")))
            reasons.append("order_above_microprice")
        elif not is_buy and dist < Decimal("-0.001"):
            score_parts.append((_clamp01(-dist / Decimal("0.005")), Decimal("0.10")))
            reasons.append("order_below_microprice")

    total_w = sum(w for _, w in score_parts) or _ONE
    adverse = sum(v * w for v, w in score_parts) / total_w if score_parts else Decimal("0.35")
    adverse = _clamp01(adverse)

    if not cfg.enabled:
        adverse = Decimal("0.35")

    return AdverseSelectionAssessment(
        adverse_selection_score=adverse,
        microprice=micro,
        midprice=mid,
        microprice_vs_mid=micro_vs_mid,
        orderbook_imbalance=imb,
        short_term_return=st_ret,
        reasons=tuple(dict.fromkeys(reasons)) or ("neutral",),
    )


def post_fill_adverse_pct(
    *,
    side: OpportunitySide | str,
    entry_price: Decimal,
    future_price: Decimal,
) -> Decimal:
    """Direction-dependent adverse move after fill (positive = adverse)."""
    if entry_price <= 0:
        return _ZERO
    raw = (future_price - entry_price) / entry_price
    side_l = str(side.value if hasattr(side, "value") else side).lower()
    if side_l.startswith("b"):
        return max(_ZERO, -raw)
    return max(_ZERO, raw)


def classify_fill_quality(
    *,
    adverse_pct: Decimal,
    config: AdverseSelectionConfig | None = None,
) -> FillQuality:
    cfg = config or AdverseSelectionConfig()
    if adverse_pct >= cfg.toxic_adverse_pct:
        return FillQuality.TOXIC_FILL
    if adverse_pct <= cfg.moderate_adverse_pct:
        return FillQuality.GOOD_FILL
    return FillQuality.FILL


def config_from_settings(settings: Any) -> AdverseSelectionConfig:
    return AdverseSelectionConfig(
        enabled=bool(getattr(settings, "live_micro_adverse_selection_enabled", True)),
        high_score_threshold=Decimal(
            str(getattr(settings, "live_micro_adverse_selection_high_threshold", 0.65))
        ),
        toxic_adverse_pct=Decimal(
            str(getattr(settings, "live_micro_toxic_adverse_pct", 0.003))
        ),
        moderate_adverse_pct=Decimal(
            str(getattr(settings, "live_micro_moderate_adverse_pct", 0.001))
        ),
    )
