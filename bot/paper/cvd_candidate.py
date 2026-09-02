"""Create frozen cross-venue dislocation candidates at decision time.

Mirrors the exact semantics of the frozen research:
- Signal at T when |mid_okx - mid_bitvavo| / mid_okx >= 40 bps
- Candidate created IMMEDIATELY (no 5s wait)
- Uses frozen notional, frozen fee model
- 5s horizon is OUTCOME MEASUREMENT only (handled by shadow observer)

Does NOT change:
- 40 bps threshold
- Fee model
- Slippage assumptions
- Adverse assumptions
- Route (okx → bitvavo)
- Strategy fingerprint
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.core.venue_fees import venue_taker_fee
from bot.research.shadow_validation.protocol import (
    DISLOCATION_BPS,
    NOTIONAL_EUR,
    VENUE_A,
    VENUE_B,
)

logger = logging.getLogger(__name__)

_THRESHOLD = Decimal(str(DISLOCATION_BPS)) / Decimal("10000")
_NOTIONAL = Decimal(str(NOTIONAL_EUR))
_VENUE_A = VENUE_A  # okx
_VENUE_B = VENUE_B  # bitvavo


def create_cvd_candidates(
    snapshots: list[MarketSnapshot],
) -> list[TradeOpportunity]:
    """Detect frozen CVD signals and emit TradeOpportunity immediately.

    This implements the exact research semantics:
    - Mid-price dislocation check (not depth-VWAP)
    - Immediate candidate creation at decision time
    - Frozen notional sizing
    - No 5s entry gate
    """
    by_symbol: dict[str, dict[str, MarketSnapshot]] = {}
    for snap in snapshots:
        if snap.exchange in (_VENUE_A, _VENUE_B) and snap.order_book is not None:
            book = snap.order_book
            if book.bids and book.asks:
                by_symbol.setdefault(snap.symbol, {})[snap.exchange] = snap

    opportunities: list[TradeOpportunity] = []
    for symbol, venues in by_symbol.items():
        a_snap = venues.get(_VENUE_A)
        b_snap = venues.get(_VENUE_B)
        if a_snap is None or b_snap is None:
            continue
        opp = _check_dislocation(symbol, a_snap, b_snap)
        if opp is not None:
            opportunities.append(opp)
    return opportunities


def _check_dislocation(
    symbol: str,
    a_snap: MarketSnapshot,
    b_snap: MarketSnapshot,
) -> TradeOpportunity | None:
    """Check mid-price dislocation and create candidate at decision time."""
    a_book = a_snap.order_book
    b_book = b_snap.order_book
    if not a_book or not b_book:
        return None
    if not a_book.bids or not a_book.asks or not b_book.bids or not b_book.asks:
        return None

    mid_a = (a_book.bids[0].price + a_book.asks[0].price) / 2
    mid_b = (b_book.bids[0].price + b_book.asks[0].price) / 2
    if mid_a <= 0 or mid_b <= 0:
        return None

    dis = (mid_a - mid_b) / mid_a
    if abs(dis) < _THRESHOLD:
        return None

    a_rich = dis > 0
    if a_rich:
        # A is expensive: sell on A (hit bid), buy on B (lift ask)
        entry_price = a_book.bids[0].price
        exit_price = b_book.asks[0].price
        buy_exchange = _VENUE_B
        sell_exchange = _VENUE_A
        buy_snap = b_snap
    else:
        # B is expensive: buy on A (lift ask), sell on B (hit bid)
        entry_price = a_book.asks[0].price
        exit_price = b_book.bids[0].price
        buy_exchange = _VENUE_A
        sell_exchange = _VENUE_B
        buy_snap = a_snap

    if entry_price <= 0:
        return None

    quantity = _NOTIONAL / entry_price
    dis_bps = float(abs(dis) * 10000)
    leader_bid = float(a_book.bids[0].price)
    leader_ask = float(a_book.asks[0].price)
    follower_bid = float(b_book.bids[0].price)
    follower_ask = float(b_book.asks[0].price)

    decision_snapshot = {
        "route": f"{_VENUE_A}|{_VENUE_B}",
        "dislocation_bps": dis_bps,
        "dislocation_fraction": float(abs(dis)),
        "notional_eur": float(_NOTIONAL),
        "leader_bid": leader_bid,
        "leader_ask": leader_ask,
        "follower_bid": follower_bid,
        "follower_ask": follower_ask,
        "mid_a": float(mid_a),
        "mid_b": float(mid_b),
        "a_rich": a_rich,
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "buy_exchange": buy_exchange,
        "sell_exchange": sell_exchange,
    }

    return TradeOpportunity(
        strategy_name="cross_venue_dislocation",
        symbol=symbol,
        side=OpportunitySide.BUY,
        quantity=quantity,
        entry_price=entry_price,
        expected_exit_price=exit_price,
        confidence=min(0.9, 0.5 + float(abs(dis)) * 10.0),
        rationale=(
            f"CVD frozen signal: |dis|={abs(dis)*10000:.1f} bps >= {DISLOCATION_BPS} bps. "
            f"Buy {buy_exchange} sell {sell_exchange}. Decision-time candidate."
        ),
        market=buy_snap.model_copy(update={"order_book": None}),
        entry_fee_role=FeeRole.TAKER,
        exit_fee_role=FeeRole.TAKER,
        funding_periods=Decimal("0"),
        metadata={
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "dislocation_bps": dis_bps,
            "mid_a": float(mid_a),
            "mid_b": float(mid_b),
            "a_rich": a_rich,
            "frozen_cvd": True,
            "decision_time_candidate": True,
            "sleeve": "S2",
            "profit_sleeve": "S2",
            "decision_economics_snapshot": decision_snapshot,
            "outcome_horizon_ms": 5000,
            "entry_semantics": "immediate_at_signal_time",
            "buy_taker_fee_rate": str(venue_taker_fee(buy_exchange)),
            "sell_taker_fee_rate": str(venue_taker_fee(sell_exchange)),
            "pricing": "top_of_book_mid_dislocation",
            "notional_eur": float(_NOTIONAL),
            "round_trip": True,
        },
    )
