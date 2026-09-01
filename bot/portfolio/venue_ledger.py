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

    def ensure_venues(self, venues: list[str], *, fee_bps: Decimal = Decimal("5")) -> list[str]:
        """Add missing venues and fund them from existing balances (paper transfer)."""
        wanted = [v.strip().lower() for v in venues if v.strip()]
        added: list[str] = []
        for venue in wanted:
            if venue in self.venues:
                continue
            self.venues.append(venue)
            self._balances[venue] = {}
            added.append(venue)
        if not added:
            return []
        if self.venues:
            total_quote = sum((self.available(v, self.quote) for v in self.venues), _ZERO)
            if total_quote > 0:
                self.start_quote_each = total_quote / Decimal(len(self.venues))
        self.rebalance_quote(fee_bps=fee_bps)
        assets = sorted(self.seeded_assets)
        for asset in assets:
            if asset == self.quote:
                continue
            donors = sorted(
                ((v, self.available(v, asset)) for v in self.venues if v not in added),
                key=lambda x: x[1],
                reverse=True,
            )
            if not donors or donors[0][1] <= 0:
                continue
            src, have = donors[0]
            slice_amt = have / Decimal(len(self.venues))
            for dst in added:
                if slice_amt <= 0:
                    break
                self.transfer(
                    from_venue=src,
                    to_venue=dst,
                    asset=asset,
                    amount=slice_amt,
                    fee_bps=fee_bps,
                )
        return added

    def available(self, venue: str, asset: str) -> Decimal:
        row = self._balances.get(str(venue).strip().lower())
        if not row:
            return _ZERO
        return row.get(asset.upper(), _ZERO)

    def equity_breakdown(
        self,
        venue: str,
        mark_prices: dict[str, Decimal] | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return (quote_cash, alt_market_value, total_equity) for one venue."""
        v = str(venue).strip().lower()
        row = self._balances.get(v) or {}
        quote_cash = Decimal(str(row.get(self.quote, 0) or 0))
        alt_value = _ZERO
        marks = mark_prices or {}
        for asset, qty_raw in row.items():
            asset_u = str(asset or "").upper()
            if not asset_u or asset_u == self.quote:
                continue
            qty = Decimal(str(qty_raw or 0))
            if qty <= 0:
                continue
            symbol = f"{asset_u}{self.quote}"
            mark = marks.get(symbol) or marks.get(asset_u)
            if mark is not None and Decimal(str(mark)) > 0:
                alt_value += qty * Decimal(str(mark))
            else:
                alt_value += qty
        total = quote_cash + alt_value
        return quote_cash, alt_value, total

    def replace_balances(self, venue: str, balances: dict[str, Decimal]) -> None:
        """Overwrite one venue's balances (used to mirror live exchange inventory)."""
        v = str(venue).strip().lower()
        if not v:
            return
        if v not in self._balances:
            self.venues.append(v)
            self._balances[v] = {}
        cleaned: dict[str, Decimal] = {}
        for asset, qty in balances.items():
            key = str(asset or "").upper()
            if not key:
                continue
            amount = Decimal(str(qty))
            if amount > 0:
                cleaned[key] = amount
        self._balances[v] = cleaned

    def can_buy(self, venue: str, quote_needed: Decimal) -> bool:
        return self.available(venue, self.quote) >= quote_needed and quote_needed > 0

    def can_sell(self, venue: str, base: str, quantity: Decimal) -> bool:
        return self.available(venue, base) >= quantity and quantity > 0

    def apply_buy(
        self,
        venue: str,
        *,
        base: str,
        quantity: Decimal,
        quote_spent: Decimal,
        quote_asset: str | None = None,
    ) -> None:
        q = (quote_asset or self.quote).upper()
        self._add(venue, q, -quote_spent)
        self._add(venue, base, quantity)

    def apply_sell(
        self,
        venue: str,
        *,
        base: str,
        quantity: Decimal,
        quote_received: Decimal,
        quote_asset: str | None = None,
    ) -> None:
        q = (quote_asset or self.quote).upper()
        self._add(venue, base, -quantity)
        self._add(venue, q, quote_received)

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

    def transfer(
        self,
        *,
        from_venue: str,
        to_venue: str,
        asset: str,
        amount: Decimal,
        fee_bps: Decimal = Decimal("0"),
    ) -> tuple[Decimal, Decimal] | None:
        """Move ``amount`` of ``asset`` between venues; fee taken from amount.

        Returns ``(received, fee)`` or ``None`` if insufficient balance.
        Paper model of withdraw+deposit (no teleport without cost/time elsewhere).
        """
        if amount <= 0:
            return None
        src = str(from_venue).strip().lower()
        dst = str(to_venue).strip().lower()
        asset_key = asset.upper()
        if src == dst:
            return None
        if self.available(src, asset_key) < amount:
            return None
        fee = amount * (fee_bps / Decimal("10000"))
        received = amount - fee
        if received <= 0:
            return None
        self._add(src, asset_key, -amount)
        self._add(dst, asset_key, received)
        return received, fee

    def rebalance_quote(
        self,
        *,
        target_each: Decimal | None = None,
        fee_bps: Decimal = Decimal("5"),
    ) -> list[dict[str, str]]:
        """Push quote cash toward equal (or ``target_each``) across venues."""
        if not self.venues:
            return []
        bals = {v: self.available(v, self.quote) for v in self.venues}
        total = sum(bals.values(), _ZERO)
        if total <= 0:
            return []
        target = target_each if target_each is not None else total / Decimal(len(self.venues))
        rich = sorted(
            ((v, bal - target) for v, bal in bals.items() if bal > target),
            key=lambda x: x[1],
            reverse=True,
        )
        poor = sorted(
            ((v, target - bal) for v, bal in bals.items() if bal < target),
            key=lambda x: x[1],
            reverse=True,
        )
        moves: list[dict[str, str]] = []
        i = j = 0
        while i < len(rich) and j < len(poor):
            src, surplus = rich[i]
            dst, need = poor[j]
            qty = min(surplus, need)
            if qty <= 0:
                break
            result = self.transfer(
                from_venue=src, to_venue=dst, asset=self.quote, amount=qty, fee_bps=fee_bps
            )
            if result is None:
                break
            received, fee = result
            moves.append(
                {
                    "from": src,
                    "to": dst,
                    "asset": self.quote,
                    "sent": str(qty),
                    "received": str(received),
                    "fee": str(fee),
                }
            )
            surplus -= qty
            need -= qty
            rich[i] = (src, surplus)
            poor[j] = (dst, need)
            if surplus <= 0:
                i += 1
            if need <= 0:
                j += 1
        return moves

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
