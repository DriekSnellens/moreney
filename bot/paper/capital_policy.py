"""Capital-velocity policy: inventory skew, dust floors, dump guard, holding time.

Focus is NET euro per fill and turning alt inventory back into quote cash.
Alt mark-to-market is rest-beta, not trading alpha.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.portfolio.models import PortfolioState

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_BPS = Decimal("10000")
_QUOTE_LIKE = frozenset({"EUR", "USDT", "USDC", "USD", "GBP"})


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    """Portfolio split between quote cash and alt inventory."""

    equity: Decimal
    quote_cash_eur: Decimal
    alt_value_eur: Decimal
    alt_fraction: Decimal
    quote_fraction: Decimal


@dataclass(frozen=True, slots=True)
class QuoteSkew:
    """Avellaneda-Stoikov-style reservation shift in basis points.

    Positive ``ask_improve_bps`` tightens the sell (lower ask).
    Positive ``buy_extra_edge_bps`` demands a deeper dip before buying.
    ``allow_buy`` is False when alt inventory is already above the hard cap.
    ``sell_only`` mirrors that for execution (no new buy legs).
    """

    allow_buy: bool
    sell_only: bool
    ask_improve_bps: Decimal
    buy_extra_edge_bps: Decimal
    alt_fraction: Decimal
    mode: str


class InventorySkewPolicy:
    """Dynamic inventory skew from quote-cash vs alt share of equity."""

    def __init__(
        self,
        *,
        max_alt_pct: Decimal = Decimal("30"),
        min_alt_pct: Decimal = Decimal("10"),
        overweight_ask_improve_bps: Decimal = Decimal("4"),
        underweight_buy_extra_bps: Decimal = Decimal("8"),
    ) -> None:
        self._max_alt = max(Decimal("0"), min(max_alt_pct, _HUNDRED)) / _HUNDRED
        self._min_alt = max(Decimal("0"), min(min_alt_pct, _HUNDRED)) / _HUNDRED
        self._ask_improve = max(_ZERO, overweight_ask_improve_bps)
        self._buy_extra = max(_ZERO, underweight_buy_extra_bps)

    def snapshot(self, state: PortfolioState) -> InventorySnapshot:
        equity = state.total_equity
        quote = (state.quote_asset or "EUR").upper()
        quote_cash = _ZERO
        for balance in state.balances.values():
            asset = balance.asset.upper()
            if balance.total == 0:
                continue
            if asset == quote or asset in _QUOTE_LIKE:
                converted = state.cash_to_quote(asset, balance.total)
                if converted is not None:
                    quote_cash += converted
        if equity <= 0:
            return InventorySnapshot(
                equity=_ZERO,
                quote_cash_eur=quote_cash,
                alt_value_eur=_ZERO,
                alt_fraction=_ZERO,
                quote_fraction=_ZERO,
            )
        # Prefer balance-implied quote cash; clamp so alt share stays in [0, 1].
        quote_cash = min(max(quote_cash, _ZERO), equity)
        alt_value = equity - quote_cash
        alt_frac = alt_value / equity if equity > 0 else _ZERO
        return InventorySnapshot(
            equity=equity,
            quote_cash_eur=quote_cash,
            alt_value_eur=alt_value,
            alt_fraction=alt_frac,
            quote_fraction=_ONE_MINUS(alt_frac),
        )

    def skew(self, state: PortfolioState) -> QuoteSkew:
        snap = self.snapshot(state)
        alt = snap.alt_fraction
        if alt > self._max_alt:
            return QuoteSkew(
                allow_buy=False,
                sell_only=True,
                ask_improve_bps=self._ask_improve,
                buy_extra_edge_bps=_ZERO,
                alt_fraction=alt,
                mode="overweight_sell_only",
            )
        if alt < self._min_alt:
            return QuoteSkew(
                allow_buy=True,
                sell_only=False,
                ask_improve_bps=_ZERO,
                buy_extra_edge_bps=self._buy_extra,
                alt_fraction=alt,
                mode="underweight_selective_buy",
            )
        return QuoteSkew(
            allow_buy=True,
            sell_only=False,
            ask_improve_bps=_ZERO,
            buy_extra_edge_bps=_ZERO,
            alt_fraction=alt,
            mode="balanced",
        )

    def apply_prices(
        self,
        *,
        buy_price: Decimal,
        sell_price: Decimal,
        skew: QuoteSkew,
        best_bid: Decimal | None = None,
        best_ask: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Shift quote prices; never cross the book (post-only safe).

        Overweight: tighten the ask (free capital faster, slightly lower edge).
        Underweight: pull the bid deeper so we only lift inventory on a real dip.
        """
        buy = buy_price
        sell = sell_price
        if skew.ask_improve_bps > 0 and sell > 0:
            improved = sell * (Decimal("1") - skew.ask_improve_bps / _BPS)
            floor = best_bid if best_bid is not None and best_bid > 0 else buy
            # Stay strictly above bid so the sell remains post-only.
            sell = max(improved, floor * Decimal("1.00005"))
            if sell >= sell_price:
                sell = sell_price
        if skew.buy_extra_edge_bps > 0 and buy > 0:
            deeper = buy * (Decimal("1") - skew.buy_extra_edge_bps / _BPS)
            # Stay strictly below ask so the buy remains post-only.
            ceiling = best_ask if best_ask is not None and best_ask > 0 else sell
            buy = min(deeper, ceiling * Decimal("0.99995"))
            if buy <= 0:
                buy = buy_price
        return buy, sell

    def max_buy_vs_fair(
        self, fair_value: Decimal, skew: QuoteSkew
    ) -> Decimal | None:
        """Underweight gate: buy only this far below USDT→EUR fair value."""
        if fair_value <= 0 or skew.buy_extra_edge_bps <= 0:
            return None
        return fair_value * (Decimal("1") - skew.buy_extra_edge_bps / _BPS)


def _ONE_MINUS(value: Decimal) -> Decimal:
    return max(_ZERO, Decimal("1") - value)


class NetProfitDustFilter:
    """Hard floors on expected NET euro and notional size (no stofjes)."""

    def __init__(
        self,
        *,
        min_net_profit_eur: Decimal = Decimal("0.15"),
        min_net_return: Decimal = Decimal("0.0025"),
        min_notional_eur: Decimal = Decimal("10"),
    ) -> None:
        self.min_net_profit_eur = max(_ZERO, min_net_profit_eur)
        self.min_net_return = max(_ZERO, min_net_return)
        self.min_notional_eur = max(_ZERO, min_notional_eur)

    def reject_reason(
        self,
        *,
        quantity: Decimal,
        buy_price: Decimal,
        net_profit_eur: Decimal,
        net_return: Decimal,
    ) -> str | None:
        notional = quantity * buy_price
        if self.min_notional_eur > 0 and notional < self.min_notional_eur:
            return (
                f"notional {notional} EUR below dust floor {self.min_notional_eur} EUR"
            )
        if self.min_net_profit_eur > 0 and net_profit_eur < self.min_net_profit_eur:
            return (
                f"NET {net_profit_eur} EUR below floor {self.min_net_profit_eur} EUR"
            )
        if self.min_net_return > 0 and net_return < self.min_net_return:
            return (
                f"NET return {net_return} below floor {self.min_net_return}"
            )
        return None


class VolatilityDumpGuard:
    """Cancel buys / sell-only when mid dumps faster than X% in Y seconds."""

    def __init__(
        self,
        *,
        move_pct: Decimal = Decimal("1.5"),
        window_sec: float = 300.0,
        cool_down_sec: float = 120.0,
    ) -> None:
        self._move = max(_ZERO, move_pct) / _HUNDRED
        self._window = max(1.0, float(window_sec))
        self._cool_down = max(0.0, float(cool_down_sec))
        self._mids: dict[str, deque[tuple[float, Decimal]]] = {}
        self._dump_until: dict[str, float] = {}

    def observe(self, symbol: str, mid: Decimal, *, now: float | None = None) -> None:
        if mid <= 0:
            return
        key = symbol.upper()
        ts = time.monotonic() if now is None else now
        buf = self._mids.setdefault(key, deque())
        buf.append((ts, mid))
        cutoff = ts - self._window
        while buf and buf[0][0] < cutoff:
            buf.popleft()
        if len(buf) < 2 or self._move <= 0:
            return
        oldest = buf[0][1]
        if oldest <= 0:
            return
        # Dump = mid fell by more than move_pct inside the window.
        drop = (oldest - mid) / oldest
        if drop >= self._move:
            self._dump_until[key] = ts + self._cool_down

    def is_dump(self, symbol: str, *, now: float | None = None) -> bool:
        key = symbol.upper()
        until = self._dump_until.get(key)
        if until is None:
            return False
        ts = time.monotonic() if now is None else now
        if ts >= until:
            self._dump_until.pop(key, None)
            return False
        return True

    def active_symbols(self, *, now: float | None = None) -> list[str]:
        ts = time.monotonic() if now is None else now
        return [sym for sym in list(self._dump_until) if self.is_dump(sym, now=ts)]


class HoldingTimeController:
    """Force gradual ALT→EUR recycle after max holding time."""

    def __init__(self, *, max_holding_sec: float = 7200.0) -> None:
        self._max_holding = max(0.0, float(max_holding_sec))
        self._opened_at: dict[str, float] = {}
        self._seed_qty: dict[str, Decimal] = {}

    def note_balances(
        self,
        balances: dict[str, Decimal],
        *,
        now: float | None = None,
    ) -> None:
        """Track when each base first held a positive balance (post-seed)."""
        ts = time.monotonic() if now is None else now
        for asset, qty in balances.items():
            key = asset.upper()
            if key in _QUOTE_LIKE:
                continue
            if qty > 0:
                if key not in self._opened_at:
                    self._opened_at[key] = ts
                    self._seed_qty.setdefault(key, qty)
            else:
                self._opened_at.pop(key, None)
                self._seed_qty.pop(key, None)

    def overdue(
        self,
        balances: dict[str, Decimal],
        *,
        mark_prices: dict[str, Decimal],
        entry_prices: dict[str, Decimal],
        quote: str = "EUR",
        now: float | None = None,
    ) -> list[tuple[str, Decimal]]:
        """Return (base, qty) that exceeded holding time and are flat/losing."""
        if self._max_holding <= 0:
            return []
        ts = time.monotonic() if now is None else now
        out: list[tuple[str, Decimal]] = []
        q = quote.upper()
        for asset, qty in balances.items():
            key = asset.upper()
            if key in _QUOTE_LIKE or qty <= 0:
                continue
            opened = self._opened_at.get(key)
            if opened is None:
                continue
            if ts - opened < self._max_holding:
                continue
            mark = mark_prices.get(f"{key}{q}") or mark_prices.get(key)
            entry = entry_prices.get(f"{key}{q}") or entry_prices.get(key)
            # Recycle when flat or underwater — do not dump winners early.
            if mark is not None and entry is not None and mark > entry:
                continue
            out.append((key, qty))
        return out


def portfolio_base_balances(state: PortfolioState) -> dict[str, Decimal]:
    return {
        bal.asset.upper(): bal.total
        for bal in state.balances.values()
        if bal.total > 0
    }


def portfolio_entry_prices(state: PortfolioState) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for symbol, pos in state.positions.items():
        if pos.quantity > 0 and pos.average_entry_price > 0:
            out[symbol.upper()] = pos.average_entry_price
    return out


def mid_from_book(book: Any) -> Decimal | None:
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    return (bids[0].price + asks[0].price) / Decimal("2")
