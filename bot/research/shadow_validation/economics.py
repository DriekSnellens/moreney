"""Four-world accounting. These quantities must never be displayed as one NET.

A. SIGNAL
B. EXPECTED ECONOMICS  — frozen strategy prediction at decision
C. SHADOW EXECUTION    — frozen execution model on observed live quotes
D. REALIZED MARKET     — what the market did after the decision
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.shadow_validation.protocol import (
    ACCOUNTING_TOLERANCE,
    ADVERSE_BPS,
    FEE_RATE_ROUNDTRIP,
    LATENCY_BPS,
    NOTIONAL_EUR,
    SLIPPAGE_BPS,
)

_BPS = 10000.0


@dataclass(slots=True)
class ExpectedEconomics:
    expected_gross: float
    expected_fees: float
    expected_slippage: float
    expected_adverse: float
    expected_latency: float
    expected_net: float
    notional_eur: float
    gross_edge_fraction: float

    def residual(self) -> float:
        recon = (
            self.expected_gross
            - self.expected_fees
            - self.expected_slippage
            - self.expected_adverse
            - self.expected_latency
        )
        return abs(recon - self.expected_net)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": "B_EXPECTED_ECONOMICS",
            "expected_gross": self.expected_gross,
            "expected_fees": self.expected_fees,
            "expected_slippage": self.expected_slippage,
            "expected_adverse": self.expected_adverse,
            "expected_latency": self.expected_latency,
            "expected_net": self.expected_net,
            "notional_eur": self.notional_eur,
            "gross_edge_fraction": self.gross_edge_fraction,
            "not_shadow_execution_net": True,
            "not_realized_market_outcome": True,
        }


def expected_from_dislocation(dislocation: float, *, notional: float = NOTIONAL_EUR) -> ExpectedEconomics:
    """Predict waterfall from |dislocation| as theoretical full-convergence edge."""
    edge = abs(float(dislocation))
    gross = notional * edge
    fees = notional * FEE_RATE_ROUNDTRIP
    slip = notional * (SLIPPAGE_BPS / _BPS)
    adverse = notional * (ADVERSE_BPS / _BPS)
    latency = notional * (LATENCY_BPS / _BPS)
    net = gross - fees - slip - adverse - latency
    return ExpectedEconomics(
        expected_gross=gross,
        expected_fees=fees,
        expected_slippage=slip,
        expected_adverse=adverse,
        expected_latency=latency,
        expected_net=net,
        notional_eur=notional,
        gross_edge_fraction=edge,
    )


def shadow_execution_net(
    *,
    fill_fraction: float,
    captured_edge_fraction: float,
    extra_adverse_bps: float = 0.0,
    notional: float = NOTIONAL_EUR,
) -> dict[str, float]:
    """Frozen cost model applied only when a fill is observed. No fabricated fills.

    fill_fraction == 0 → all zeros. Costs scale with filled notional.
    """
    frac = max(0.0, min(1.0, float(fill_fraction)))
    if frac <= 0.0:
        return {
            "shadow_gross": 0.0,
            "shadow_fees": 0.0,
            "shadow_slippage": 0.0,
            "shadow_adverse": 0.0,
            "shadow_latency": 0.0,
            "shadow_execution_net": 0.0,
            "fill_fraction": 0.0,
        }
    filled = notional * frac
    gross = filled * float(captured_edge_fraction)
    fees = filled * FEE_RATE_ROUNDTRIP
    slip = filled * (SLIPPAGE_BPS / _BPS)
    adverse = filled * ((ADVERSE_BPS + extra_adverse_bps) / _BPS)
    latency = filled * (LATENCY_BPS / _BPS)
    net = gross - fees - slip - adverse - latency
    return {
        "shadow_gross": gross,
        "shadow_fees": fees,
        "shadow_slippage": slip,
        "shadow_adverse": adverse,
        "shadow_latency": latency,
        "shadow_execution_net": net,
        "fill_fraction": frac,
    }


def execution_gap(shadow_execution_net_eur: float, expected_net: float) -> float:
    """prediction_gap = C − B. Alias kept for existing callers."""
    return prediction_gap(shadow_execution_net_eur, expected_net)


def prediction_gap(shadow_execution_net_eur: float, expected_net: float) -> float:
    return float(shadow_execution_net_eur) - float(expected_net)


def realized_market_net(
    *,
    signed_markout_fraction: float | None,
    notional: float = NOTIONAL_EUR,
) -> float | None:
    """EUR translation of the 5s signed mid markout.

    Not a fill. No fees. Not shadow execution. Missing markout → None.
    """
    if signed_markout_fraction is None:
        return None
    return float(notional) * float(signed_markout_fraction)


def market_gap(realized_market_net_eur: float | None, shadow_execution_net_eur: float) -> float | None:
    if realized_market_net_eur is None:
        return None
    return float(realized_market_net_eur) - float(shadow_execution_net_eur)


def total_gap(realized_market_net_eur: float | None, expected_net: float) -> float | None:
    if realized_market_net_eur is None:
        return None
    return float(realized_market_net_eur) - float(expected_net)


def identities_hold(
    *,
    expected_net: float,
    shadow_execution_net_eur: float,
    realized_market_net_eur: float | None,
    prediction_gap_eur: float,
    market_gap_eur: float | None,
    total_gap_eur: float | None,
    shadow_legs: dict[str, float] | None = None,
    expected: ExpectedEconomics | None = None,
) -> bool:
    """expected + prediction_gap = shadow; shadow + market_gap = realized."""
    if abs((expected_net + prediction_gap_eur) - shadow_execution_net_eur) > ACCOUNTING_TOLERANCE:
        return False
    if expected is not None and expected.residual() > ACCOUNTING_TOLERANCE:
        return False
    if shadow_legs is not None:
        recon = (
            shadow_legs["shadow_gross"]
            - shadow_legs["shadow_fees"]
            - shadow_legs["shadow_slippage"]
            - shadow_legs["shadow_adverse"]
            - shadow_legs["shadow_latency"]
        )
        if abs(recon - shadow_execution_net_eur) > ACCOUNTING_TOLERANCE:
            return False
    if realized_market_net_eur is None:
        return market_gap_eur is None and total_gap_eur is None
    if market_gap_eur is None or total_gap_eur is None:
        return False
    if abs((shadow_execution_net_eur + market_gap_eur) - realized_market_net_eur) > ACCOUNTING_TOLERANCE:
        return False
    if abs((expected_net + total_gap_eur) - realized_market_net_eur) > ACCOUNTING_TOLERANCE:
        return False
    if abs((prediction_gap_eur + market_gap_eur) - total_gap_eur) > ACCOUNTING_TOLERANCE:
        return False
    return True


def accounting_pass(expected: ExpectedEconomics, shadow: dict[str, float]) -> bool:
    if expected.residual() > ACCOUNTING_TOLERANCE:
        return False
    recon = (
        shadow["shadow_gross"]
        - shadow["shadow_fees"]
        - shadow["shadow_slippage"]
        - shadow["shadow_adverse"]
        - shadow["shadow_latency"]
    )
    return abs(recon - shadow["shadow_execution_net"]) <= ACCOUNTING_TOLERANCE
