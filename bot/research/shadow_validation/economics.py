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
    return float(shadow_execution_net_eur) - float(expected_net)


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
