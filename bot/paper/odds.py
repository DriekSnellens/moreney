"""Highest-odds 24h path on €10k–€25k: liquid alt beta + maker overlay.

2–5% in 24 hours is a coin-move, not a bid/ask harvest. With no leverage the
highest probability of landing in that band is to hold a large book of liquid
EUR alts (ADA/ATOM/NEAR/DOT/XRP) and market-make around it.

A typical up-day of ~3.5% on those names × ~75% inventory ≈ 2.6% on equity —
inside the 2–5% window. Down days and flat BTC days miss. Maker fills add
bps; they are not the 2%.
"""

from __future__ import annotations

from decimal import Decimal

TARGET_LOW = Decimal("0.02")
TARGET_HIGH = Decimal("0.05")
# Liquid EUR-alt up-day, order of magnitude (not a forecast of every session).
TYPICAL_ALT_UP_DAY = Decimal("0.035")
DEFAULT_INVENTORY_FRAC = Decimal("0.75")


def equity_move(inventory_frac: Decimal, alt_move: Decimal) -> Decimal:
    """Mark-to-market equity move from inventory × underlying move."""
    if inventory_frac <= 0:
        return Decimal("0")
    return inventory_frac * alt_move


def hits_24h_band(move: Decimal, *, low: Decimal = TARGET_LOW, high: Decimal = TARGET_HIGH) -> bool:
    return low <= move <= high


def snapshot(
    capital_eur: Decimal,
    *,
    inventory_pct: Decimal = DEFAULT_INVENTORY_FRAC * Decimal("100"),
    alt_up_day: Decimal = TYPICAL_ALT_UP_DAY,
) -> dict[str, object]:
    frac = Decimal(str(inventory_pct)) / Decimal("100")
    move = equity_move(frac, alt_up_day)
    euro = Decimal(str(capital_eur or 0)) * move
    in_band = hits_24h_band(move)
    note = (
        f"Hoogste kans op +{TARGET_LOW * 100:.0f}–{TARGET_HIGH * 100:.0f}% in 24u: "
        f"{frac * 100:.0f}% inventory in liquide EUR-alts. "
        f"Typische up-day {alt_up_day * 100:.1f}% × inventory ≈ {move * 100:.1f}% "
        f"(€{euro:.0f} op €{capital_eur:,.0f}). "
        "Geen garantie; down-days en vlakke sessies missen de band. "
        "Maker-spread is extra, niet de 2%."
    )
    return {
        "inventory_frac": format(frac, "f"),
        "typical_alt_up_day": format(alt_up_day, "f"),
        "equity_move": format(move, "f"),
        "equity_move_pct": format(move * Decimal("100"), "f"),
        "euro_on_capital": format(euro, "f"),
        "target_low_pct": format(TARGET_LOW * Decimal("100"), "f"),
        "target_high_pct": format(TARGET_HIGH * Decimal("100"), "f"),
        "in_band": in_band,
        "note": note,
    }
