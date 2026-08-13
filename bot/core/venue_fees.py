"""Retail taker fees used for both gating and paper fills.

Paper execution must charge the same venue rates the strategy already uses
to decide whether a route is NET-profitable. Otherwise the dashboard shows
cheap paper wins that would not exist with real money.
"""

from __future__ import annotations

from decimal import Decimal

# Typical retail taker fee rates (conservative, no VIP discount).
VENUE_TAKER_FEE: dict[str, Decimal] = {
    "binance": Decimal("0.001"),
    "kraken": Decimal("0.0026"),
    "coinbase": Decimal("0.006"),
    "bitvavo": Decimal("0.0025"),
    "okx": Decimal("0.001"),
    "bybit": Decimal("0.001"),
}

_DEFAULT = Decimal("0.001")


def venue_taker_fee(exchange: str | None, *, fallback: Decimal | None = None) -> Decimal:
    """Return the retail taker rate for ``exchange``, or ``fallback`` / 0.1%."""
    key = str(exchange or "").strip().lower()
    if key in VENUE_TAKER_FEE:
        return VENUE_TAKER_FEE[key]
    return fallback if fallback is not None else _DEFAULT
