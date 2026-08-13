"""Venue fee tables with optional VIP / rebate tiers."""

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

# Typical retail maker fee rates (conservative, no VIP / BNB discount).
VENUE_MAKER_FEE: dict[str, Decimal] = {
    "binance": Decimal("0.001"),
    "kraken": Decimal("0.0016"),
    "coinbase": Decimal("0.004"),
    "bitvavo": Decimal("0.0015"),
    "okx": Decimal("0.0008"),
    "bybit": Decimal("0.001"),
}

# Multipliers vs retail. vip3 ≈ high-volume; rebate = negative maker (rare retail).
FEE_TIER_MULTIPLIER: dict[str, Decimal] = {
    "retail": Decimal("1.0"),
    "vip1": Decimal("0.85"),
    "vip2": Decimal("0.70"),
    "vip3": Decimal("0.55"),
    "rebate": Decimal("0.0"),  # zero maker (optimistic floor; never negative in paper)
}

_DEFAULT = Decimal("0.001")
_ACTIVE_TIER = "retail"


def set_fee_tier(tier: str | None) -> None:
    """Process-wide fee tier used by venue_*_fee helpers."""
    global _ACTIVE_TIER
    key = str(tier or "retail").strip().lower()
    _ACTIVE_TIER = key if key in FEE_TIER_MULTIPLIER else "retail"


def get_fee_tier() -> str:
    return _ACTIVE_TIER


def _tier_mult(tier: str | None = None) -> Decimal:
    key = str(tier or _ACTIVE_TIER).strip().lower()
    return FEE_TIER_MULTIPLIER.get(key, Decimal("1.0"))


def venue_taker_fee(
    exchange: str | None,
    *,
    fallback: Decimal | None = None,
    tier: str | None = None,
) -> Decimal:
    """Return the taker rate for ``exchange`` after fee-tier multiplier."""
    key = str(exchange or "").strip().lower()
    base = VENUE_TAKER_FEE.get(key, fallback if fallback is not None else _DEFAULT)
    return base * _tier_mult(tier)


def venue_maker_fee(
    exchange: str | None,
    *,
    fallback: Decimal | None = None,
    tier: str | None = None,
) -> Decimal:
    """Return the maker rate for ``exchange`` after fee-tier multiplier."""
    key = str(exchange or "").strip().lower()
    base = VENUE_MAKER_FEE.get(key, fallback if fallback is not None else _DEFAULT)
    return base * _tier_mult(tier)
