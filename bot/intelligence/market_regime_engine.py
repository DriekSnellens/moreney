"""Market Regime Engine — deterministic classification from snapshot/history."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence

from bot.core.models import MarketSnapshot

_ZERO = Decimal("0")
_ONE = Decimal("1")


class MarketRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    POST_BREAKOUT = "POST_BREAKOUT"
    CHAOTIC = "CHAOTIC"
    DEAD_MARKET = "DEAD_MARKET"
    OPPORTUNITY_BURST = "OPPORTUNITY_BURST"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MarketRegimeConfig:
    enabled: bool = True
    min_confidence: Decimal = Decimal("0.45")
    stale_market_data_sec: float = 5.0
    breakout_return_threshold: Decimal = Decimal("0.015")
    post_breakout_extension: Decimal = Decimal("0.025")
    chaotic_vol_threshold: Decimal = Decimal("0.008")
    dead_spread_max: Decimal = Decimal("0.0003")
    dead_vol_max: Decimal = Decimal("0.0004")
    burst_candidate_min: int = 8
    maker_inventory_range_fit: Decimal = Decimal("0.90")
    maker_inventory_trend_fit: Decimal = Decimal("0.65")
    maker_inventory_breakout_fit: Decimal = Decimal("0.35")
    maker_inventory_chaotic_fit: Decimal = Decimal("0.15")


@dataclass(frozen=True, slots=True)
class MarketRegimeAssessment:
    regime: MarketRegime
    confidence: Decimal
    reasons: tuple[str, ...]
    return_1m: Decimal | None = None
    return_5m: Decimal | None = None
    return_15m: Decimal | None = None
    realized_volatility: Decimal | None = None
    range_width: Decimal | None = None
    trend_strength: Decimal | None = None
    momentum_consistency: Decimal | None = None
    spread_pct: Decimal | None = None
    orderbook_imbalance: Decimal | None = None
    data_freshness_score: Decimal = _ONE
    regime_fit: Decimal = _ONE


def _clamp01(v: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, v))


def _return_over(marks: Sequence[Decimal], n: int) -> Decimal | None:
    if len(marks) < n + 1:
        return None
    start = marks[-(n + 1)]
    end = marks[-1]
    if start <= 0:
        return None
    return (end - start) / start


def _realized_vol(marks: Sequence[Decimal]) -> Decimal | None:
    if len(marks) < 3:
        return None
    steps: list[Decimal] = []
    prev = marks[0]
    for cur in marks[1:]:
        if prev > 0:
            steps.append(abs((cur - prev) / prev))
        prev = cur
    if not steps:
        return None
    return sum(steps, _ZERO) / Decimal(len(steps))


def _range_width(marks: Sequence[Decimal]) -> Decimal | None:
    if len(marks) < 2:
        return None
    lo, hi = min(marks), max(marks)
    mid = (lo + hi) / 2 if (lo + hi) > 0 else _ONE
    if mid <= 0:
        return None
    return (hi - lo) / mid


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


def _spread_pct(snapshot: MarketSnapshot | None) -> Decimal | None:
    if snapshot is None:
        return None
    mid = snapshot.mid
    if mid <= 0:
        return None
    return snapshot.spread / mid


def data_freshness_score(*, latency_ms: float | None, stale_sec: float) -> Decimal:
    if latency_ms is None:
        return Decimal("0.7")
    if latency_ms <= stale_sec * 1000:
        return _ONE
    if latency_ms <= stale_sec * 3000:
        return Decimal("0.5")
    return Decimal("0.1")


def classify_market_regime(
    *,
    marks: Sequence[Decimal] | None = None,
    snapshot: MarketSnapshot | None = None,
    config: MarketRegimeConfig | None = None,
    candidate_count: int = 0,
    avg_opportunity_score: Decimal | None = None,
) -> MarketRegimeAssessment:
    """Classify regime using only data available at evaluation time."""
    cfg = config or MarketRegimeConfig()
    mark_series = list(marks or [])
    reasons: list[str] = []
    r1 = _return_over(mark_series, 1) if mark_series else None
    r5 = _return_over(mark_series, 5) if mark_series else None
    r15 = _return_over(mark_series, 15) if mark_series else None
    vol = _realized_vol(mark_series)
    rw = _range_width(mark_series)
    spread = _spread_pct(snapshot)
    imb = _orderbook_imbalance(snapshot)
    lat = getattr(snapshot, "latency_ms", None) if snapshot else None
    fresh = data_freshness_score(latency_ms=lat, stale_sec=cfg.stale_market_data_sec)

    trend_strength = _ZERO
    if r5 is not None:
        trend_strength = _clamp01(abs(r5) / Decimal("0.01"))
    cont = _ZERO
    if r1 is not None and r5 is not None and r5 != 0:
        same_sign = (r1 > 0 and r5 > 0) or (r1 < 0 and r5 < 0)
        cont = _ONE if same_sign else Decimal("0.3")

    regime = MarketRegime.UNKNOWN
    confidence = Decimal("0.4")

    if fresh < Decimal("0.2"):
        regime = MarketRegime.UNKNOWN
        reasons.append("stale_market_data")
        confidence = Decimal("0.2")
    elif (
        spread is not None
        and vol is not None
        and spread <= cfg.dead_spread_max
        and vol <= cfg.dead_vol_max
        and candidate_count <= 1
    ):
        regime = MarketRegime.DEAD_MARKET
        reasons.extend(["low_spread", "low_volatility", "few_candidates"])
        confidence = Decimal("0.75")
    elif candidate_count >= cfg.burst_candidate_min and (
        avg_opportunity_score is None or avg_opportunity_score >= Decimal("65")
    ):
        regime = MarketRegime.OPPORTUNITY_BURST
        reasons.append("high_candidate_count")
        confidence = Decimal("0.7")
    elif vol is not None and vol >= cfg.chaotic_vol_threshold:
        regime = MarketRegime.CHAOTIC
        reasons.append("high_realized_volatility")
        confidence = _clamp01(vol / cfg.chaotic_vol_threshold)
    elif r5 is not None and abs(r5) >= cfg.breakout_return_threshold:
        if r15 is not None and abs(r15) >= cfg.post_breakout_extension:
            regime = MarketRegime.POST_BREAKOUT
            reasons.append("extended_move")
        else:
            regime = MarketRegime.BREAKOUT
            reasons.append("strong_short_return")
        confidence = _clamp01(abs(r5) / cfg.breakout_return_threshold * Decimal("0.6"))
    elif vol is not None and vol <= cfg.dead_vol_max * Decimal("2"):
        regime = MarketRegime.LOW_VOLATILITY
        reasons.append("low_volatility")
        confidence = Decimal("0.65")
    elif vol is not None and vol >= cfg.chaotic_vol_threshold * Decimal("0.6"):
        regime = MarketRegime.HIGH_VOLATILITY
        reasons.append("elevated_volatility")
        confidence = Decimal("0.6")
    elif r5 is not None and r5 > Decimal("0.002"):
        regime = MarketRegime.TREND_UP
        reasons.extend(["positive_5m_return", "rising_tape"])
        confidence = _clamp01(trend_strength + cont * Decimal("0.3"))
    elif r5 is not None and r5 < Decimal("-0.002"):
        regime = MarketRegime.TREND_DOWN
        reasons.append("negative_5m_return")
        confidence = _clamp01(trend_strength)
    elif rw is not None and rw < Decimal("0.008"):
        regime = MarketRegime.RANGE
        reasons.append("narrow_range")
        confidence = Decimal("0.7")
    else:
        regime = MarketRegime.UNKNOWN
        reasons.append("insufficient_signal")

    if confidence < cfg.min_confidence and regime not in {
        MarketRegime.DEAD_MARKET,
        MarketRegime.OPPORTUNITY_BURST,
    }:
        regime = MarketRegime.UNKNOWN
        reasons.append("low_confidence")

    return MarketRegimeAssessment(
        regime=regime,
        confidence=confidence,
        reasons=tuple(reasons),
        return_1m=r1,
        return_5m=r5,
        return_15m=r15,
        realized_volatility=vol,
        range_width=rw,
        trend_strength=trend_strength,
        momentum_consistency=cont,
        spread_pct=spread,
        orderbook_imbalance=imb,
        data_freshness_score=fresh,
    )


def regime_fit_for_strategy(
    *,
    strategy: str,
    regime: MarketRegime,
    config: MarketRegimeConfig | None = None,
) -> Decimal:
    cfg = config or MarketRegimeConfig()
    name = strategy.lower()
    if "maker" in name or "inventory" in name:
        mapping = {
            MarketRegime.RANGE: cfg.maker_inventory_range_fit,
            MarketRegime.LOW_VOLATILITY: cfg.maker_inventory_range_fit,
            MarketRegime.TREND_UP: cfg.maker_inventory_trend_fit,
            MarketRegime.TREND_DOWN: Decimal("0.40"),
            MarketRegime.BREAKOUT: cfg.maker_inventory_breakout_fit,
            MarketRegime.POST_BREAKOUT: Decimal("0.25"),
            MarketRegime.CHAOTIC: cfg.maker_inventory_chaotic_fit,
            MarketRegime.HIGH_VOLATILITY: Decimal("0.45"),
            MarketRegime.DEAD_MARKET: Decimal("0.55"),
            MarketRegime.OPPORTUNITY_BURST: Decimal("0.70"),
            MarketRegime.UNKNOWN: Decimal("0.50"),
        }
        return mapping.get(regime, Decimal("0.50"))
    return _ONE


def config_from_settings(settings: Any) -> MarketRegimeConfig:
    return MarketRegimeConfig(
        enabled=bool(getattr(settings, "live_micro_regime_engine_enabled", True)),
        min_confidence=Decimal(str(getattr(settings, "live_micro_regime_min_confidence", 0.45))),
        stale_market_data_sec=float(getattr(settings, "live_micro_stale_market_data_sec", 5.0)),
        burst_candidate_min=int(getattr(settings, "live_micro_burst_candidate_min", 8)),
    )
