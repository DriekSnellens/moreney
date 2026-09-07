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

# Live-observed schedule that differs from the public USDT-pair table. OKX EUR
# spot pairs (regular tier) bill 0.20% maker / 0.35% taker (300+ fills, p10=p90).
# Applied process-wide by the live runner via set_venue_fee_overrides(); research
# fixtures keep the static table above.
LIVE_VENUE_FEE_OVERRIDES = "okx:0.0020:0.0035"

# venue -> (maker, taker); None keeps the table value.
_OVERRIDES: dict[str, tuple[Decimal | None, Decimal | None]] = {}


def set_venue_fee_overrides(spec: str | None) -> dict[str, tuple[Decimal | None, Decimal | None]]:
    """Parse ``"venue:maker:taker,venue2:maker:taker"`` into process-wide overrides.

    Empty / None clears all overrides. Empty fields keep the table value
    (``"okx::0.0035"`` overrides only taker).
    """
    global _OVERRIDES
    parsed: dict[str, tuple[Decimal | None, Decimal | None]] = {}
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        venue = parts[0].strip().lower()
        if not venue:
            continue

        def _dec(raw: str) -> Decimal | None:
            raw = raw.strip()
            if not raw:
                return None
            try:
                val = Decimal(raw)
            except Exception:  # noqa: BLE001
                return None
            return val if 0 <= val <= Decimal("0.02") else None

        maker = _dec(parts[1]) if len(parts) > 1 else None
        taker = _dec(parts[2]) if len(parts) > 2 else None
        parsed[venue] = (maker, taker)
    _OVERRIDES = parsed
    return dict(parsed)


def get_venue_fee_overrides() -> dict[str, tuple[Decimal | None, Decimal | None]]:
    return dict(_OVERRIDES)

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
    override = _OVERRIDES.get(key)
    if override is not None and override[1] is not None:
        return override[1] * _tier_mult(tier)
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
    override = _OVERRIDES.get(key)
    if override is not None and override[0] is not None:
        return override[0] * _tier_mult(tier)
    base = VENUE_MAKER_FEE.get(key, fallback if fallback is not None else _DEFAULT)
    return base * _tier_mult(tier)
