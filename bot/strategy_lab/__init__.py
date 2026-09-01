"""Strategy Research Lab — compare strategies on identical market data.

Research / shadow only by default. Never places live exchange orders.
Does not alter production paper PnL unless explicitly wired (defaults off).
"""

from __future__ import annotations

__all__ = [
    "STRATEGY_IDS",
    "VERDICTS",
]

STRATEGY_IDS = (
    "maker_inventory",
    "executable_cross_venue_arb",
    "lead_lag",
    "order_book_imbalance",
    "funding_basis",
    "control_no_trade",
)

VERDICTS = (
    "INSUFFICIENT_DATA",
    "NO_EDGE",
    "EDGE_NEGATIVE_AFTER_COSTS",
    "IN_SAMPLE_ONLY",
    "OOS_UNSTABLE",
    "OOS_PROMISING",
    "OOS_ROBUST",
    "RESEARCH",
    "FAILED",
)
