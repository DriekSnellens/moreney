"""Per-exchange cash and crypto for realistic paper arbitrage.

Coins cannot teleport. A buy on Binance does not create BTC on Kraken.
Live cross-exchange arb only works with pre-funded inventory on both venues.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

_ZERO = Decimal("0")
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "EUR", "GBP")


def infer_quote_asset(symbol: str, default: str = "EUR") -> str:
    text = symbol.upper().replace("/", "").replace("-", "")
    for suffix in _QUOTE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return suffix
    return default.upper()


def infer_base_asset(symbol: str, quote: str = "EUR") -> str:
    text = symbol.upper().replace("/", "").replace("-", "")
    q = infer_quote_asset(text, quote)
    if text.endswith(q) and len(text) > len(q):
        return text[: -len(q)]
    return text


class VenueLedger:
    """Balances keyed by exchange then asset."""

    def __init__(self, venues: list[str], *, quote: str, starting_quote: Decimal) -> None:
        self.quote = quote.upper()
        self.venues = [v.strip().lower() for v in venues if v.strip()]
        self._balances: dict[str, dict[str, Decimal]] = {v: {} for v in self.venues}
        self.seeded_assets: set[str] = set()
        self.start_quote_each = _ZERO
        if self.venues and starting_quote > 0:
            each = starting_quote / Decimal(len(self.venues))
            self.start_quote_each = each
            for venue in self.venues:
                self._balances[venue][self.quote] = each

    def available(self, venue: str, asset: str) -> Decimal:
        row = self._balances.get(str(venue).strip().lower())
        if not row:
            return _ZERO
        return row.get(asset.upper(), _ZERO)

    def can_buy(self, venue: str, quote_needed: Decimal) -> bool:
        return self.available(venue, self.quote) >= quote_needed and quote_needed > 0

    def can_sell(self, venue: str, base: str, quantity: Decimal) -> bool:
        return self.available(venue, base) >= quantity and quantity > 0

    def apply_buy(self, venue: str, *, base: str, quantity: Decimal, quote_spent: Decimal) -> None:
        self._add(venue, self.quote, -quote_spent)
        self._add(venue, base, quantity)

    def apply_sell(self, venue: str, *, base: str, quantity: Decimal, quote_received: Decimal) -> None:
        self._add(venue, base, -quantity)
        self._add(venue, self.quote, quote_received)

    def lock(self, venue: str, asset: str, amount: Decimal) -> bool:
        """Deduct ``amount`` so a resting order cannot spend the same balance twice."""
        if amount <= 0:
            return True
        if self.available(venue, asset) < amount:
            return False
        self._add(venue, asset, -amount)
        return True

    def unlock(self, venue: str, asset: str, amount: Decimal) -> None:
        if amount <= 0:
            return
        self._add(venue, asset, amount)

    def credit(self, venue: str, asset: str, amount: Decimal) -> None:
        if amount <= 0:
            return
        self._add(venue, asset, amount)

    def seed_asset(
        self,
        asset: str,
        *,
        price: Decimal,
        quote_budget: Decimal | None = None,
        pct: Decimal | None = None,
    ) -> list[tuple[str, Decimal, Decimal]]:
        """Convert quote into ``asset`` on each venue.

        Prefer ``quote_budget`` (absolute EUR per venue). ``pct`` is legacy and means
        percent of *remaining* quote — avoid with many symbols (compounds to dust).
        """
        asset = asset.upper()
        if asset in self.seeded_assets or price <= 0:
            return []
        moved: list[tuple[str, Decimal, Decimal]] = []
        for venue in self.venues:
            if quote_budget is not None:
                quote_amt = min(quote_budget, self.available(venue, self.quote))
            elif pct is not None and pct > 0:
                quote_amt = self.available(venue, self.quote) * (pct / Decimal("100"))
            else:
                continue
            if quote_amt <= 0:
                continue
            qty = quote_amt / price
            self._add(venue, self.quote, -quote_amt)
            self._add(venue, asset, qty)
            moved.append((venue, qty, quote_amt))
        if moved:
            self.seeded_assets.add(asset)
        return moved

    def export(self) -> dict[str, Any]:
        return {
            "quote": self.quote,
            "venues": list(self.venues),
            "seeded_assets": sorted(self.seeded_assets),
            "start_quote_each": str(self.start_quote_each),
            "balances": {
                venue: {asset: str(amount) for asset, amount in assets.items()}
                for venue, assets in self._balances.items()
            },
        }

    @classmethod
    def from_export(cls, data: dict[str, Any], *, fallback_quote: str, fallback_start: Decimal) -> VenueLedger:
        venues = list(data.get("venues") or [])
        quote = str(data.get("quote") or fallback_quote)
        ledger = cls(venues, quote=quote, starting_quote=_ZERO)
        if not ledger.venues:
            ledger = cls(
                [str(v) for v in (data.get("balances") or {})],
                quote=quote,
                starting_quote=_ZERO,
            )
        raw = data.get("balances") or {}
        for venue, assets in raw.items():
            key = str(venue).strip().lower()
            ledger._balances.setdefault(key, {})
            if key not in ledger.venues:
                ledger.venues.append(key)
            for asset, amount in (assets or {}).items():
                ledger._balances[key][str(asset).upper()] = Decimal(str(amount))
        ledger.seeded_assets = {str(a).upper() for a in (data.get("seeded_assets") or [])}
        raw_start = data.get("start_quote_each")
        if raw_start is not None:
            ledger.start_quote_each = Decimal(str(raw_start))
        elif ledger.venues and fallback_start > 0:
            ledger.start_quote_each = fallback_start / Decimal(len(ledger.venues))
        if not ledger.venues and fallback_start > 0:
            return cls([], quote=fallback_quote, starting_quote=fallback_start)
        return ledger

    def _add(self, venue: str, asset: str, delta: Decimal) -> None:
        key = str(venue).strip().lower()
        if key not in self._balances:
            self._balances[key] = {}
            if key not in self.venues:
                self.venues.append(key)
        row = self._balances[key]
        asset_key = asset.upper()
        row[asset_key] = row.get(asset_key, _ZERO) + delta
        if row[asset_key] < 0:
            row[asset_key] = _ZERO
