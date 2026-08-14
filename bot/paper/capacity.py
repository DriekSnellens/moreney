"""Paper-only fill-rate math. Not a high-certainty live income model.

See ``bot.paper.certainty`` for what €10k–€25k can actually promise per day.
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
