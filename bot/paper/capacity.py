"""Retail maker capacity: map observed fills onto a daily PnL path.

€300/day on €25k is 1.2% / day. That is a turnover problem, not a fee-tier
problem: two live Realistic fills were already NET-positive after retail
maker fees. Price-priority trade-through (fill when the book prints through
a resting quote) scales that observed window onto a daily target.
"""

from __future__ import annotations

from decimal import Decimal

TARGET_DAILY_EUR = Decimal("300")
SECONDS_PER_DAY = Decimal("86400")


def project_daily_pnl(net_pnl: Decimal, window_seconds: float) -> Decimal:
    """Linear projection of a realized window onto 24h."""
    if window_seconds <= 0:
        return Decimal("0")
    return net_pnl * (SECONDS_PER_DAY / Decimal(str(window_seconds)))


def scale_through_fill(projected: Decimal, *, from_pct: Decimal, to_pct: Decimal) -> Decimal:
    """Resize a projection when trade-through fill fraction changes.

    At-touch queue uncertainty stays a separate knob. This only scales the
    *through* event (best bid/ask crossed the resting limit).
    """
    if from_pct <= 0:
        return projected
    return projected * (to_pct / from_pct)


def hits_daily_target(
    projected: Decimal, *, target: Decimal = TARGET_DAILY_EUR
) -> bool:
    return projected >= target
