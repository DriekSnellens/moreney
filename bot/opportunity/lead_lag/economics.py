"""Executable economics for lead-lag shadow opportunities.

Uses depth VWAP (never mid when depth exists). Cost decomposition aligns with
NetProfitCalculator structure: gross − fees − slippage − buffer/haircuts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from bot.core.exchange_types import OrderBookLevel
from bot.core.venue_fees import venue_taker_fee
from bot.opportunity.lead_lag.states import LeadLagState
from bot.opportunity.lead_lag.types import HedgeLeg, LeadLagOpportunity, LeadLagSignal
from bot.strategies.arbitrage import walk_book

_ZERO = Decimal("0")
_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class BookSide:
    levels: tuple[OrderBookLevel, ...]


def executable_vwap(
    side: str,
    *,
    bids: Sequence[OrderBookLevel],
    asks: Sequence[OrderBookLevel],
    quantity: Decimal,
) -> tuple[Decimal, Decimal, bool, Decimal]:
    """Return (vwap, filled_qty, sufficient, depth_available). Never uses mid."""
    if side == "buy":
        fill = walk_book(asks, quantity)
    else:
        fill = walk_book(bids, quantity)
    return fill.vwap, fill.filled_quantity, fill.sufficient, fill.depth_available


def build_shadow_opportunity(
    signal: LeadLagSignal,
    *,
    follower_bids: Sequence[OrderBookLevel],
    follower_asks: Sequence[OrderBookLevel],
    leader_bids: Sequence[OrderBookLevel],
    leader_asks: Sequence[OrderBookLevel],
    quantity: Decimal,
    latency_ms: float = 0.0,
    hedge_mode: str = "FULLY_HEDGED",
    uncertainty_weight: Decimal = Decimal("1"),
    latency_haircut_bps_per_100ms: Decimal = Decimal("2"),
) -> LeadLagOpportunity:
    """Build a fully costed shadow opportunity; reject if hedge missing when required."""
    pred = signal.predicted_follower_move_bps
    if pred == 0:
        return _reject(signal, LeadLagState.SIGNAL_REJECTED, "zero_predicted_move", latency_ms)

    # Trade follower in direction of predicted move (buy if up, sell if down).
    entry_side = "buy" if pred > 0 else "sell"
    entry_px, filled, ok, depth = executable_vwap(
        entry_side,
        bids=follower_bids,
        asks=follower_asks,
        quantity=quantity,
    )
    if not ok or filled <= 0 or entry_px <= 0:
        return _reject(signal, LeadLagState.NOT_EXECUTABLE, "insufficient_follower_depth", latency_ms)

    qty = filled
    notional = qty * entry_px
    fee_rate = venue_taker_fee(signal.follower_venue)
    entry_fee = notional * fee_rate

    hedge: HedgeLeg | None = None
    hedge_fee = _ZERO
    hedge_slip = _ZERO
    if hedge_mode == "FULLY_HEDGED":
        hedge_side = "sell" if entry_side == "buy" else "buy"
        h_px, h_filled, h_ok, h_depth = executable_vwap(
            hedge_side,
            bids=leader_bids,
            asks=leader_asks,
            quantity=qty,
        )
        if not h_ok or h_filled <= 0 or h_px <= 0:
            return _reject(signal, LeadLagState.HEDGE_UNAVAILABLE, "hedge_not_executable", latency_ms)
        h_notional = h_filled * h_px
        hedge_fee = h_notional * venue_taker_fee(signal.leader_venue)
        # Cross-venue residual as slippage proxy when prices differ
        hedge_slip = abs(h_notional - notional) * Decimal("0.0005")
        hedge = HedgeLeg(
            venue=signal.leader_venue,
            symbol=signal.symbol,
            side=hedge_side,
            executable_price=h_px,
            quantity=h_filled,
            depth_available=h_depth,
            fees_eur=hedge_fee,
            slippage_eur=hedge_slip,
            delay_ms=latency_ms,
            feasible=True,
        )

    # Predicted gross edge on follower notional
    gross = notional * (abs(pred) / _BPS)
    # Latency haircut: predeclared bps per 100ms of delay
    lat_bps = latency_haircut_bps_per_100ms * Decimal(str(latency_ms)) / Decimal("100")
    latency_haircut = notional * (lat_bps / _BPS)
    # Uncertainty allowance (predeclared): uncertainty_bps * weight
    unc = signal.uncertainty_bps * uncertainty_weight
    unc_cost = notional * (unc / _BPS)

    # Spread crossing already in VWAP vs mid; add small buffer like NetProfitCalculator
    buffer = notional * Decimal("0.0002")
    fees = entry_fee + hedge_fee
    slippage = hedge_slip + buffer
    other = _ZERO
    expected_net = gross - fees - slippage - latency_haircut - other
    conservative_net = expected_net - unc_cost

    if conservative_net <= 0:
        state = LeadLagState.NEGATIVE_CONSERVATIVE_NET
        gate = "negative_conservative_net"
    else:
        state = LeadLagState.SHADOW_ADMITTED
        gate = "shadow_conservative_net_accept"

    return LeadLagOpportunity(
        signal=signal,
        entry_side=entry_side,
        entry_venue=signal.follower_venue,
        executable_entry_price=entry_px,
        executable_quantity=qty,
        hedge=hedge,
        gross_predicted_edge_eur=gross,
        fees_eur=fees,
        slippage_eur=slippage,
        latency_haircut_eur=latency_haircut,
        hedge_haircut_eur=hedge_slip,
        other_costs_eur=other + unc_cost,
        expected_net_eur=expected_net,
        conservative_net_eur=conservative_net,
        capital_required_eur=notional,
        estimated_capital_lock_ms=float(signal.horizon_ms) + float(latency_ms),
        hedge_mode=hedge_mode,
        state=state.value,
        first_gate=gate,
        latency_scenario_ms=latency_ms,
        observational=True,
    )


def _reject(
    signal: LeadLagSignal,
    state: LeadLagState,
    gate: str,
    latency_ms: float,
) -> LeadLagOpportunity:
    return LeadLagOpportunity(
        signal=signal,
        entry_side="",
        entry_venue=signal.follower_venue,
        executable_entry_price=_ZERO,
        executable_quantity=_ZERO,
        hedge=None,
        gross_predicted_edge_eur=_ZERO,
        fees_eur=_ZERO,
        slippage_eur=_ZERO,
        latency_haircut_eur=_ZERO,
        hedge_haircut_eur=_ZERO,
        other_costs_eur=_ZERO,
        expected_net_eur=_ZERO,
        conservative_net_eur=_ZERO,
        capital_required_eur=_ZERO,
        estimated_capital_lock_ms=0.0,
        state=state.value,
        first_gate=gate,
        latency_scenario_ms=latency_ms,
        observational=True,
    )
