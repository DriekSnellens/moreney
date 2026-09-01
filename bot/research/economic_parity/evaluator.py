"""Evaluate frozen research vs live NetProfitCalculator economics."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.core.models import ProfitEstimate, ProfitabilityResult, TradeOpportunity
from bot.core.venue_fees import venue_taker_fee
from bot.profitability.net_profit import NetProfitCalculator
from bot.research.economic_parity.formulas import EconomicsBreakdown, breakeven_dislocation_bps
from bot.research.shadow_validation.economics import expected_from_dislocation
from bot.research.shadow_validation.protocol import (
    NOTIONAL_EUR,
    VENUE_A,
    VENUE_B,
    strategy_fingerprint,
)

_ZERO = Decimal("0")


def _decision_snapshot(meta: dict[str, Any]) -> dict[str, Any]:
    """Immutable decision-time economics inputs stored on the candidate."""
    return dict(meta.get("decision_economics_snapshot") or meta)


def _dislocation_fraction(meta: dict[str, Any]) -> float:
    snap = _decision_snapshot(meta)
    if "dislocation_fraction" in snap:
        return abs(float(snap["dislocation_fraction"]))
    bps = snap.get("dislocation_bps", meta.get("dislocation_bps", 0))
    return abs(float(bps)) / 10000.0


def evaluate_frozen_research_economics(
    opportunity: TradeOpportunity,
) -> EconomicsBreakdown:
    """WORLD A: frozen decision-time economics (shadow / research parity)."""
    meta = dict(opportunity.metadata or {})
    snap = _decision_snapshot(meta)
    dis_frac = _dislocation_fraction(meta)
    notional = float(snap.get("notional_eur", meta.get("notional_eur", NOTIONAL_EUR)))
    expected = expected_from_dislocation(dis_frac, notional=notional)
    leader_fee = notional * float(venue_taker_fee(VENUE_A))
    follower_fee = notional * float(venue_taker_fee(VENUE_B))
    be = breakeven_dislocation_bps(notional=notional)
    dis_bps = dis_frac * 10000.0
    profitable = expected.expected_net > 0
    reason = "expected_net_positive" if profitable else "expected_net_not_positive"
    route = str(snap.get("route", f"{VENUE_A}|{VENUE_B}"))
    return EconomicsBreakdown(
        world="FROZEN_RESEARCH",
        symbol=opportunity.symbol,
        route=route,
        dislocation_bps=dis_bps,
        notional_eur=notional,
        leader_bid=_f(snap.get("leader_bid")),
        leader_ask=_f(snap.get("leader_ask")),
        follower_bid=_f(snap.get("follower_bid")),
        follower_ask=_f(snap.get("follower_ask")),
        gross_eur=expected.expected_gross,
        leader_fee_eur=leader_fee,
        follower_fee_eur=follower_fee,
        fees_eur=expected.expected_fees,
        slippage_eur=expected.expected_slippage,
        adverse_eur=expected.expected_adverse,
        inventory_eur=0.0,
        execution_buffer_eur=0.0,
        latency_eur=expected.expected_latency,
        expected_net_eur=expected.expected_net,
        profitable=profitable,
        rejection_reason=reason,
        breakeven_dislocation_bps=be,
        strategy_fingerprint=strategy_fingerprint(),
        metadata={"expected": expected.as_dict()},
    )


def evaluate_live_profitability_economics(
    opportunity: TradeOpportunity,
    *,
    settings: Settings,
) -> EconomicsBreakdown:
    """WORLD B: current live NetProfitCalculator path (diagnostic only)."""
    meta = dict(opportunity.metadata or {})
    snap = _decision_snapshot(meta)
    dis_frac = _dislocation_fraction(meta)
    notional = float(snap.get("notional_eur", meta.get("notional_eur", NOTIONAL_EUR)))
    calc = NetProfitCalculator(settings)
    # Do not pass order_book — profitability must use decision-time prices only.
    estimate = calc.estimate(
        opportunity,
        order_book=None,
        buy_fee_rate=Decimal(str(meta.get("buy_taker_fee_rate", "0"))),
        sell_fee_rate=Decimal(str(meta.get("sell_taker_fee_rate", "0"))),
    )
    buy_rate = float(meta.get("buy_taker_fee_rate") or 0)
    sell_rate = float(meta.get("sell_taker_fee_rate") or 0)
    leader_fee = notional * buy_rate if buy_rate else notional * float(venue_taker_fee(VENUE_A))
    follower_fee = notional * sell_rate if sell_rate else notional * float(venue_taker_fee(VENUE_B))
    be = breakeven_dislocation_bps(notional=notional)
    net = float(estimate.net_profit)
    profitable = bool(estimate.trade_allowed)
    reason = "; ".join(estimate.disallow_reasons) if estimate.disallow_reasons else "trade_allowed"
    return EconomicsBreakdown(
        world="CURRENT_LIVE",
        symbol=opportunity.symbol,
        route=str(snap.get("route", f"{VENUE_A}|{VENUE_B}")),
        dislocation_bps=dis_frac * 10000.0,
        notional_eur=notional,
        leader_bid=_f(snap.get("leader_bid")),
        leader_ask=_f(snap.get("leader_ask")),
        follower_bid=_f(snap.get("follower_bid")),
        follower_ask=_f(snap.get("follower_ask")),
        gross_eur=float(estimate.gross_profit),
        leader_fee_eur=leader_fee,
        follower_fee_eur=follower_fee,
        fees_eur=float(estimate.buy_fee + estimate.sell_fee),
        slippage_eur=float(estimate.slippage),
        adverse_eur=0.0,
        inventory_eur=0.0,
        execution_buffer_eur=float(estimate.execution_buffer),
        latency_eur=0.0,
        expected_net_eur=net,
        profitable=profitable,
        rejection_reason=reason,
        breakeven_dislocation_bps=be,
        strategy_fingerprint=strategy_fingerprint(),
        metadata={"estimate_assumptions": dict(estimate.assumptions)},
    )


def frozen_to_profitability_result(
    opportunity: TradeOpportunity,
    breakdown: EconomicsBreakdown,
) -> ProfitabilityResult:
    """Pipeline ProfitabilityResult using frozen research economics."""
    net = Decimal(str(breakdown.expected_net_eur))
    gross = Decimal(str(breakdown.gross_eur))
    fees = Decimal(str(breakdown.fees_eur))
    half = fees / Decimal("2")
    estimate = ProfitEstimate(
        gross_profit=gross,
        buy_fee=half,
        sell_fee=fees - half,
        slippage=Decimal(str(breakdown.slippage_eur)),
        funding_cost=_ZERO,
        execution_buffer=_ZERO,
        net_profit=net,
        net_return=net / Decimal(str(breakdown.notional_eur)) if breakdown.notional_eur else _ZERO,
        trade_allowed=breakdown.profitable,
        disallow_reasons=[] if breakdown.profitable else [breakdown.rejection_reason],
        assumptions={
            "economic_world": "FROZEN_RESEARCH",
            "frozen_cvd": True,
            "decision_snapshot_only": True,
        },
    )
    return ProfitabilityResult(
        opportunity_id=opportunity.id,
        gross_profit_usd=gross,
        buy_fee_usd=half,
        sell_fee_usd=fees - half,
        fees_usd=fees,
        slippage_usd=Decimal(str(breakdown.slippage_eur)),
        funding_usd=_ZERO,
        execution_buffer_usd=_ZERO,
        net_profit_usd=net,
        net_return=estimate.net_return,
        is_profitable=breakdown.profitable,
        trade_allowed=breakdown.profitable,
        estimate=estimate,
        assumptions=estimate.assumptions,
    )


def evaluate_frozen_cvd_immutable(
    opportunity: TradeOpportunity,
    *,
    settings: Settings,
) -> tuple[EconomicsBreakdown, EconomicsBreakdown]:
    """Evaluate frozen economics twice; second call after mutating live prices on a copy."""
    frozen_a = evaluate_frozen_research_economics(opportunity)
    # Mutate prices on a shallow copy — frozen path must ignore this.
    mutated = opportunity.model_copy(
        update={
            "entry_price": opportunity.entry_price * Decimal("1.5"),
            "expected_exit_price": (opportunity.expected_exit_price or opportunity.entry_price)
            * Decimal("0.5"),
        }
    )
    frozen_b = evaluate_frozen_research_economics(mutated)
    # Live path would change — returned for tests only.
    _ = settings
    return frozen_a, frozen_b


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
