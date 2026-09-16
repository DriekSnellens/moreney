"""Resting Order Intelligence — cancel/reprice decisions with hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from bot.intelligence.adverse_selection import AdverseSelectionAssessment, assess_adverse_selection
from bot.intelligence.market_regime_engine import MarketRegime, MarketRegimeAssessment

_ZERO = Decimal("0")
_ONE = Decimal("1")


class RestingOrderAction(str, Enum):
    HOLD = "HOLD"
    CANCEL = "CANCEL"
    REPLACE = "REPLACE"
    EXPIRE = "EXPIRE"


@dataclass(frozen=True, slots=True)
class RestingOrderConfig:
    enabled: bool = True
    min_reprice_interval_sec: float = 5.0
    max_resting_order_age_sec: float = 30.0
    adverse_cancel_threshold: Decimal = Decimal("0.70")
    adverse_cancel_hysteresis: Decimal = Decimal("0.55")
    microprice_move_threshold: Decimal = Decimal("0.002")
    spread_collapse_threshold: Decimal = Decimal("0.00015")
    min_price_improvement: Decimal = Decimal("0.0005")
    min_score_improvement: Decimal = Decimal("5")
    churn_penalty_per_replace: Decimal = Decimal("0.02")


@dataclass(frozen=True, slots=True)
class RestingOrderAssessment:
    action: RestingOrderAction
    adverse_selection_score: Decimal
    expected_value_of_waiting: Decimal
    estimated_fill_probability: Decimal
    distance_to_mid: Decimal | None
    distance_to_microprice: Decimal | None
    current_spread_pct: Decimal | None
    reasons: tuple[str, ...]
    observation_only: bool = False


def expected_value_of_waiting(
    *,
    fill_probability: Decimal,
    expected_net_if_filled: Decimal,
    opportunity_cost_per_min: Decimal,
    wait_minutes: Decimal,
    adverse_cost: Decimal,
) -> Decimal:
    """EV = P(fill) × NET - opportunity_cost - adverse_cost."""
    return fill_probability * expected_net_if_filled - opportunity_cost_per_min * wait_minutes - adverse_cost


def assess_resting_order(
    *,
    side: str,
    order_price: Decimal,
    age_sec: float,
    snapshot: Any = None,
    marks: list[Decimal] | None = None,
    adverse: AdverseSelectionAssessment | None = None,
    regime: MarketRegimeAssessment | None = None,
    expected_net_eur: Decimal = Decimal("0.50"),
    opportunity_score: Decimal = Decimal("70"),
    previous_adverse_score: Decimal | None = None,
    last_reprice_mono: float | None = None,
    now_mono: float | None = None,
    replace_count: int = 0,
    config: RestingOrderConfig | None = None,
    observation_mode: bool = False,
) -> RestingOrderAssessment:
    """Decide whether to hold, cancel, or replace a resting order."""
    cfg = config or RestingOrderConfig()
    side_l = side.lower()
    is_buy = side_l.startswith("b")

    if adverse is None and snapshot is not None:
        from bot.core.enums import OpportunitySide

        adverse = assess_adverse_selection(
            snapshot=snapshot,
            marks=marks or [],
            side=OpportunitySide.BUY if is_buy else OpportunitySide.SELL,
            order_price=order_price,
        )

    adv_score = adverse.adverse_selection_score if adverse else Decimal("0.35")
    mid = snapshot.mid if snapshot is not None else None
    micro = adverse.microprice if adverse else None

    dist_mid: Decimal | None = None
    dist_micro: Decimal | None = None
    spread_pct: Decimal | None = None
    if mid is not None and mid > 0:
        dist_mid = (order_price - mid) / mid
        if snapshot is not None:
            spread_pct = snapshot.spread / mid
    if micro is not None and micro > 0:
        dist_micro = (order_price - micro) / micro

    fill_p = Decimal("0.35")
    if spread_pct is not None:
        if spread_pct < Decimal("0.001"):
            fill_p = Decimal("0.55")
        elif spread_pct > Decimal("0.005"):
            fill_p = Decimal("0.20")

    wait_min = Decimal(str(max(0.0, (cfg.max_resting_order_age_sec - age_sec) / 60.0)))
    adv_cost = adv_score * expected_net_eur * Decimal("0.3")
    ev_wait = expected_value_of_waiting(
        fill_probability=fill_p,
        expected_net_if_filled=expected_net_eur,
        opportunity_cost_per_min=Decimal("0.01"),
        wait_minutes=wait_min,
        adverse_cost=adv_cost,
    )

    reasons: list[str] = []
    action = RestingOrderAction.HOLD

    # Age expiry
    if age_sec >= cfg.max_resting_order_age_sec:
        action = RestingOrderAction.EXPIRE
        reasons.append("max_resting_age")

    # Adverse selection with hysteresis
    cancel_threshold = cfg.adverse_cancel_threshold
    if previous_adverse_score is not None and previous_adverse_score < cancel_threshold:
        cancel_threshold = cfg.adverse_cancel_hysteresis

    if adv_score >= cancel_threshold:
        action = RestingOrderAction.CANCEL
        reasons.append("adverse_selection_high")

    # Regime change
    if regime is not None and regime.regime in {
        MarketRegime.CHAOTIC,
        MarketRegime.BREAKOUT,
        MarketRegime.DEAD_MARKET,
    }:
        if regime.regime == MarketRegime.DEAD_MARKET:
            action = RestingOrderAction.CANCEL
            reasons.append("dead_market")
        elif adv_score >= Decimal("0.5"):
            action = RestingOrderAction.CANCEL
            reasons.append(f"regime_{regime.regime.value.lower()}")

    # Microprice moved away
    if dist_micro is not None:
        if is_buy and dist_micro > cfg.microprice_move_threshold:
            if action == RestingOrderAction.HOLD:
                action = RestingOrderAction.CANCEL
            reasons.append("microprice_moved_away")
        elif not is_buy and dist_micro < -cfg.microprice_move_threshold:
            if action == RestingOrderAction.HOLD:
                action = RestingOrderAction.CANCEL
            reasons.append("microprice_moved_away")

    # Spread collapsed
    if spread_pct is not None and spread_pct <= cfg.spread_collapse_threshold:
        action = RestingOrderAction.CANCEL
        reasons.append("spread_collapsed")

    # Negative economics
    if ev_wait < _ZERO:
        action = RestingOrderAction.CANCEL
        reasons.append("negative_wait_ev")

    # Low opportunity score
    if opportunity_score < Decimal("50"):
        action = RestingOrderAction.CANCEL
        reasons.append("opportunity_score_dropped")

    # Repricing hysteresis — only replace if enough time passed
    if action == RestingOrderAction.CANCEL and ev_wait > _ZERO and opportunity_score >= Decimal("60"):
        if last_reprice_mono is not None and now_mono is not None:
            elapsed = now_mono - last_reprice_mono
            if elapsed >= cfg.min_reprice_interval_sec and replace_count < 3:
                action = RestingOrderAction.REPLACE
                reasons.append("reprice_candidate")

    if observation_mode and action in {RestingOrderAction.CANCEL, RestingOrderAction.REPLACE, RestingOrderAction.EXPIRE}:
        reasons.append("observation_mode")

    return RestingOrderAssessment(
        action=action if not observation_mode else RestingOrderAction.HOLD,
        adverse_selection_score=adv_score,
        expected_value_of_waiting=ev_wait,
        estimated_fill_probability=fill_p,
        distance_to_mid=dist_mid,
        distance_to_microprice=dist_micro,
        current_spread_pct=spread_pct,
        reasons=tuple(dict.fromkeys(reasons)),
        observation_only=observation_mode and action != RestingOrderAction.HOLD,
    )


def config_from_settings(settings: Any) -> RestingOrderConfig:
    return RestingOrderConfig(
        enabled=bool(getattr(settings, "live_micro_resting_order_intelligence_enabled", True)),
        min_reprice_interval_sec=float(
            getattr(settings, "live_micro_min_reprice_interval_sec", 5.0)
        ),
        max_resting_order_age_sec=float(
            getattr(settings, "live_micro_max_resting_order_age_sec", 30.0)
        ),
    )
