"""Frozen research vs live profitability formulas — documented for parity audit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.core.venue_fees import venue_taker_fee
from bot.research.shadow_validation.protocol import (
    ADVERSE_BPS,
    DISLOCATION_BPS,
    FEE_RATE_ROUNDTRIP,
    LATENCY_BPS,
    NOTIONAL_EUR,
    SLIPPAGE_BPS,
    VENUE_A,
    VENUE_B,
)

_BPS = 10000.0


def breakeven_dislocation_bps(*, notional: float = NOTIONAL_EUR) -> float:
    """Minimum |dislocation| bps for expected_net = 0 under frozen decision-time costs."""
    if notional <= 0:
        return 0.0
    cost_eur = notional * (
        FEE_RATE_ROUNDTRIP
        + SLIPPAGE_BPS / _BPS
        + ADVERSE_BPS / _BPS
        + LATENCY_BPS / _BPS
    )
    return (cost_eur / notional) * _BPS


_BREAKEVEN_BPS = breakeven_dislocation_bps()

RESEARCH_PROFITABILITY_FORMULA = {
    "label": "FROZEN_RESEARCH_DECISION_TIME",
    "source": "bot.research.shadow_validation.economics.expected_from_dislocation",
    "route": f"{VENUE_A}|{VENUE_B}",
    "dislocation_threshold_bps": DISLOCATION_BPS,
    "notional_eur": NOTIONAL_EUR,
    "gross_eur": "NOTIONAL_EUR × |dislocation_fraction|",
    "fees_eur": f"NOTIONAL_EUR × FEE_RATE_ROUNDTRIP ({FEE_RATE_ROUNDTRIP})",
    "leader_fee_eur": f"NOTIONAL_EUR × venue_taker_fee({VENUE_A})",
    "follower_fee_eur": f"NOTIONAL_EUR × venue_taker_fee({VENUE_B})",
    "slippage_eur": f"NOTIONAL_EUR × ({SLIPPAGE_BPS} / 10000)",
    "adverse_eur": f"NOTIONAL_EUR × ({ADVERSE_BPS} / 10000)",
    "latency_eur": f"NOTIONAL_EUR × ({LATENCY_BPS} / 10000)",
    "inventory_eur": "0 (frozen protocol)",
    "execution_buffer_eur": "0 (adverse+latency explicit; not maker gate buffer)",
    "expected_net_eur": "gross - fees - slippage - adverse - latency",
    "profitable_when": "expected_net_eur > 0",
    "breakeven_dislocation_bps": (
        f"FEE_RATE_ROUNDTRIP×10000 + SLIPPAGE + ADVERSE + LATENCY = {_BREAKEVEN_BPS:.4f} bps"
    ),
    "pricing_basis": "mid dislocation at decision time (|mid_okx-mid_bitvavo|/mid_okx)",
    "outcome_horizon_ms": 5000,
    "note": (
        "Canonical replay uses forward_i at T+5s for validation totals; "
        "decision-time gate uses |dislocation| per shadow protocol."
    ),
}

LIVE_PROFITABILITY_FORMULA = {
    "label": "CURRENT_LIVE_NET_PROFIT_CALCULATOR",
    "source": "bot.profitability.net_profit.NetProfitCalculator.estimate",
    "gate_settings": "bot.paper.runner.PaperRunner._gate_settings (maker paper)",
    "gross_eur": "quantity × (exit_price - entry_price) for BUY side",
    "fees_eur": "buy_fee + sell_fee from metadata taker rates",
    "slippage_eur": "SlippageModel (0 bps when maker gate slippage=0)",
    "adverse_eur": "folded into profitability_execution_buffer_bps (maker gate)",
    "inventory_eur": "0",
    "execution_buffer_eur": "entry_notional × execution_buffer_bps / 10000",
    "expected_net_eur": "gross - fees - slippage - funding - buffer - extra",
    "profitable_when": "net > 0 AND net >= min_net_profit AND net_return >= min_net_return",
    "pricing_basis": "top-of-book entry/exit from decision-time candidate",
    "parity_note": (
        "Must NOT be used for frozen CVD gate; BUY-side gross on cross-venue "
        "prices diverges from frozen |dislocation| economics."
    ),
}


@dataclass(frozen=True, slots=True)
class EconomicsBreakdown:
    """Single-world economics for one decision-time candidate."""

    world: str
    symbol: str
    route: str
    dislocation_bps: float
    notional_eur: float
    leader_bid: float | None
    leader_ask: float | None
    follower_bid: float | None
    follower_ask: float | None
    gross_eur: float
    leader_fee_eur: float
    follower_fee_eur: float
    fees_eur: float
    slippage_eur: float
    adverse_eur: float
    inventory_eur: float
    execution_buffer_eur: float
    latency_eur: float
    expected_net_eur: float
    profitable: bool
    rejection_reason: str
    breakeven_dislocation_bps: float
    strategy_fingerprint: str
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "world": self.world,
            "symbol": self.symbol,
            "route": self.route,
            "dislocation_bps": self.dislocation_bps,
            "notional_eur": self.notional_eur,
            "leader_bid": self.leader_bid,
            "leader_ask": self.leader_ask,
            "follower_bid": self.follower_bid,
            "follower_ask": self.follower_ask,
            "gross_eur": self.gross_eur,
            "leader_fee_eur": self.leader_fee_eur,
            "follower_fee_eur": self.follower_fee_eur,
            "fees_eur": self.fees_eur,
            "slippage_eur": self.slippage_eur,
            "adverse_eur": self.adverse_eur,
            "inventory_eur": self.inventory_eur,
            "execution_buffer_eur": self.execution_buffer_eur,
            "latency_eur": self.latency_eur,
            "expected_net_eur": self.expected_net_eur,
            "profitable": self.profitable,
            "rejection_reason": self.rejection_reason,
            "breakeven_dislocation_bps": self.breakeven_dislocation_bps,
            "strategy_fingerprint": self.strategy_fingerprint,
        }


def synthetic_economics_table(
    dislocation_bps_list: tuple[float, ...] = (40.0, 50.0, 100.0, 200.0),
) -> list[dict[str, Any]]:
    """Deterministic economics table at fixed dislocations (mid-based research)."""
    rows: list[dict[str, Any]] = []
    n = float(NOTIONAL_EUR)
    be = breakeven_dislocation_bps()
    fees = n * FEE_RATE_ROUNDTRIP
    slip = n * (SLIPPAGE_BPS / _BPS)
    adverse = n * (ADVERSE_BPS / _BPS)
    latency = n * (LATENCY_BPS / _BPS)
    for bps in dislocation_bps_list:
        gross = n * (bps / _BPS)
        net = gross - fees - slip - adverse - latency
        rows.append(
            {
                "dislocation_bps": bps,
                "gross_eur": gross,
                "fees_eur": fees,
                "slippage_eur": slip,
                "adverse_eur": adverse,
                "latency_eur": latency,
                "expected_net_eur": net,
                "profitable_research": net > 0,
                "breakeven_dislocation_bps": be,
            }
        )
    return rows
