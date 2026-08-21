"""Paper portfolio: balances, positions, equity, drawdown — fill-driven only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide
from bot.core.models import Balance, PortfolioSnapshot, Position
from bot.portfolio.accounting import AccountingEngine
from bot.portfolio.models import (
    AccountingResult,
    AssetBalance,
    Fill,
    Order,
    PortfolioState,
    PortfolioStats,
    PositionState,
)
from bot.portfolio.venue_ledger import VenueLedger, infer_base_asset

_ZERO = Decimal("0")


class PaperPortfolio:
    """In-memory paper portfolio with optional persistence hooks.

    Implements ``PortfolioService.get_snapshot`` for the risk / trading engine.
    Starting capital defaults to ``PAPER_STARTING_EUR`` (EUR only, no crypto).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        accounting: AccountingEngine | None = None,
        starting_eur: Decimal | None = None,
    ) -> None:
        self._settings = settings
        self._quote = settings.paper_quote_asset.upper()
        self._accounting = accounting or AccountingEngine()
        start = (
            starting_eur
            if starting_eur is not None
            else Decimal(str(settings.paper_starting_eur))
        )
        self._state = PortfolioState(
            quote_asset=self._quote,
            balances={
                self._quote: AssetBalance(
                    asset=self._quote, available=start, reserved=_ZERO
                )
            },
            positions={},
            stats=PortfolioStats(peak_equity=start),
            mark_prices={},
        )
        self._update_drawdown()
        self.venue_ledger: VenueLedger | None = None

    @property
    def accounting(self) -> AccountingEngine:
        return self._accounting

    @property
    def state(self) -> PortfolioState:
        return self._state

    def snapshot_state(self) -> PortfolioState:
        return self._state.model_copy(deep=True)

    def load_state(self, state: PortfolioState, *, processed_fill_ids: set[str] | None = None) -> None:
        self._state = state.model_copy(deep=True)
        if processed_fill_ids:
            self._accounting.load_processed_ids(processed_fill_ids)
        self._update_unrealized()
        self._update_drawdown()

    async def get_snapshot(self) -> PortfolioSnapshot:
        """Risk-engine compatible snapshot (quote treated as equity currency)."""
        self._update_unrealized()
        self._update_drawdown()
        balances = [
            Balance(asset=b.asset, free=b.available, locked=b.reserved)
            for b in self._state.balances.values()
        ]
        positions: list[Position] = []
        for pos in self._state.positions.values():
            if pos.quantity == 0:
                continue
            mark = self._state.mark_prices.get(pos.symbol, pos.average_entry_price)
            unrealized = (mark - pos.average_entry_price) * pos.quantity
            positions.append(
                Position(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    average_entry_price=pos.average_entry_price,
                    unrealized_pnl_usd=unrealized,
                    side=OpportunitySide.BUY,
                )
            )
        equity = self._state.total_equity
        return PortfolioSnapshot(
            balances=balances,
            positions=positions,
            equity_usd=equity,
            peak_equity_usd=self._state.stats.peak_equity,
            daily_realized_pnl_usd=self._state.stats.realized_pnl,
            open_position_count=len(positions),
            as_of=datetime.now(UTC),
        )

    def init_venue_ledger(self, venues: list[str], *, starting_quote: Decimal | None = None) -> None:
        start = starting_quote if starting_quote is not None else Decimal(str(self._settings.paper_starting_eur))
        self.venue_ledger = VenueLedger(venues, quote=self._quote, starting_quote=start)

    def load_venue_ledger(self, data: dict | None) -> None:
        if not data:
            self.venue_ledger = None
            return
        self.venue_ledger = VenueLedger.from_export(
            data,
            fallback_quote=self._quote,
            fallback_start=Decimal(str(self._settings.paper_starting_eur)),
        )

    def maybe_seed_inventory(self, symbol: str, price: Decimal) -> None:
        """Pre-fund base asset on maker venues from a shared inventory budget.

        ``paper_seed_inventory_pct`` is the *total* share of starting capital used for
        all seeded coins combined (not per-coin). Only ``paper_seed_max_assets`` coins
        are funded so sizes stay tradeable; other symbols are still scanned.
        """
        ledger = self.venue_ledger
        if ledger is None or price <= 0:
            return
        pct = Decimal(str(getattr(self._settings, "paper_seed_inventory_pct", 0) or 0))
        if pct <= 0:
            return
        base = infer_base_asset(symbol, self._quote)
        quote = symbol.upper()
        if not quote.endswith(self._quote):
            return
        allow = {
            part.strip().upper().replace("-", "").replace("/", "")
            for part in str(getattr(self._settings, "paper_seed_symbols", "") or "").split(",")
            if part.strip()
        }
        if allow and quote not in allow:
            return
        max_assets = int(getattr(self._settings, "paper_seed_max_assets", 0) or 0)
        if max_assets > 0 and base not in ledger.seeded_assets:
            if len(ledger.seeded_assets) >= max_assets:
                return
        asset_slots = max_assets if max_assets > 0 else self._seed_asset_slot_count()
        asset_slots = max(1, asset_slots)
        start_each = ledger.start_quote_each
        if start_each <= 0:
            start_each = Decimal(str(self._settings.paper_starting_eur)) / Decimal(
                max(1, len(ledger.venues))
            )
        quote_budget = start_each * (pct / Decimal("100")) / Decimal(asset_slots)
        moved = ledger.seed_asset(base, price=price, quote_budget=quote_budget)
        if not moved:
            return
        total_qty = sum((qty for _, qty, _ in moved), _ZERO)
        total_cost = sum((cost for _, _, cost in moved), _ZERO)
        eur = self._state.balances.setdefault(
            self._quote, AssetBalance(asset=self._quote, available=_ZERO, reserved=_ZERO)
        )
        take = min(total_cost, eur.available)
        eur.available -= take
        crypto = self._state.balances.setdefault(
            base, AssetBalance(asset=base, available=_ZERO, reserved=_ZERO)
        )
        crypto.available += total_qty
        pos = self._state.positions.setdefault(
            symbol.upper(),
            PositionState(symbol=symbol.upper(), quantity=_ZERO, average_entry_price=price),
        )
        pos.quantity += total_qty
        pos.average_entry_price = price
        self._state.mark_prices[symbol.upper()] = price
        self._update_unrealized()
        self._update_drawdown()

    def _seed_asset_slot_count(self) -> int:
        allow = [
            part.strip()
            for part in str(getattr(self._settings, "paper_seed_symbols", "") or "").split(",")
            if part.strip()
        ]
        if allow:
            return len(allow)
        symbols = [
            part.strip()
            for part in str(getattr(self._settings, "market_data_symbols", "") or "").split(",")
            if part.strip() and part.strip().upper().endswith(self._quote)
        ]
        return max(1, len(symbols))

    def set_mark_price(self, symbol: str, price: Decimal) -> None:
        self._state.mark_prices[symbol.upper()] = price
        self._update_unrealized()
        self._update_drawdown()

    def available(self, asset: str) -> Decimal:
        bal = self._state.balances.get(asset.upper())
        return bal.available if bal else _ZERO

    def reserved(self, asset: str) -> Decimal:
        bal = self._state.balances.get(asset.upper())
        return bal.reserved if bal else _ZERO

    def sync_live_balances(
        self,
        balances: list[Balance],
        *,
        quote_available_cap: Decimal | None = None,
    ) -> dict[str, str]:
        """Replace paper cash/inventory with live venue balances (EUR pocket capped).

        ``quote_available_cap`` limits free quote used as the trading pocket so a
        larger exchange balance cannot inflate paper risk beyond the session budget.
        Locked quote stays reserved so open orders are visible to the pocket.
        """
        quote = self._quote
        next_balances: dict[str, AssetBalance] = {}
        for bal in balances:
            asset = str(bal.asset or "").upper()
            if not asset:
                continue
            free = Decimal(str(bal.free or 0))
            locked = Decimal(str(bal.locked or 0))
            if free < 0:
                free = _ZERO
            if locked < 0:
                locked = _ZERO
            if asset == quote and quote_available_cap is not None:
                cap = Decimal(str(quote_available_cap))
                if cap < 0:
                    cap = _ZERO
                free = min(free, cap)
            if free == 0 and locked == 0:
                continue
            next_balances[asset] = AssetBalance(
                asset=asset, available=free, reserved=locked
            )
        if quote not in next_balances:
            next_balances[quote] = AssetBalance(
                asset=quote, available=_ZERO, reserved=_ZERO
            )
        self._state.balances = next_balances

        # Rebuild EUR-quoted positions from non-quote balances so sells see inventory.
        keep_symbols: set[str] = set()
        for asset, bal in next_balances.items():
            if asset == quote:
                continue
            symbol = f"{asset}{quote}"
            qty = bal.total
            if qty <= 0:
                continue
            keep_symbols.add(symbol)
            mark = self._state.mark_prices.get(symbol) or _ZERO
            prev = self._state.positions.get(symbol)
            entry = (
                prev.average_entry_price
                if prev is not None and prev.average_entry_price > 0
                else mark
            )
            if entry <= 0:
                entry = Decimal("1")
            self._state.positions[symbol] = PositionState(
                symbol=symbol,
                quantity=qty,
                average_entry_price=entry,
                realized_pnl=prev.realized_pnl if prev is not None else _ZERO,
                fees_paid=prev.fees_paid if prev is not None else _ZERO,
            )
            if mark > 0:
                self._state.mark_prices[symbol] = mark
        for symbol in list(self._state.positions.keys()):
            if symbol not in keep_symbols:
                del self._state.positions[symbol]

        self._update_unrealized()
        self._update_drawdown()
        self._state.as_of = datetime.now(UTC)
        return {
            asset: f"{bal.available}/{bal.reserved}"
            for asset, bal in sorted(next_balances.items())
        }

    def reserve(self, asset: str, amount: Decimal) -> bool:
        """Move available → reserved for a pending order. Returns False if short."""
        if amount <= 0:
            return True
        key = asset.upper()
        bal = self._state.balances.setdefault(
            key, AssetBalance(asset=key, available=_ZERO, reserved=_ZERO)
        )
        if bal.available < amount:
            return False
        bal.available -= amount
        bal.reserved += amount
        return True

    def release_reservation(self, asset: str, amount: Decimal) -> None:
        if amount <= 0:
            return
        key = asset.upper()
        bal = self._state.balances.setdefault(
            key, AssetBalance(asset=key, available=_ZERO, reserved=_ZERO)
        )
        release = min(amount, bal.reserved)
        bal.reserved -= release
        bal.available += release

    def apply_fill(self, order: Order, fill: Fill) -> AccountingResult:
        """Update portfolio from a fill only (idempotent)."""
        result = self._accounting.apply_fill(self._state, order, fill)
        if result.applied:
            self._update_unrealized()
            self._update_drawdown()
            self._state.as_of = datetime.now(UTC)
        return result

    def base_asset_for(self, symbol: str) -> str:
        from bot.portfolio.venue_ledger import infer_base_asset, infer_quote_asset

        quote = infer_quote_asset(symbol, self._quote)
        return infer_base_asset(symbol, quote)

    def _update_unrealized(self) -> None:
        self._state.cap_positions_to_balances()
        unrealized = _ZERO
        for symbol, pos in self._state.positions.items():
            if pos.quantity == 0:
                continue
            mark = self._state.mark_prices.get(symbol, pos.average_entry_price)
            unrealized += (mark - pos.average_entry_price) * pos.quantity
        self._state.stats.unrealized_pnl = unrealized

    def _update_drawdown(self) -> None:
        equity = self._state.total_equity
        stats = self._state.stats
        if equity > stats.peak_equity:
            stats.peak_equity = equity
        peak = stats.peak_equity
        if peak > 0:
            dd = (peak - equity) / peak
            stats.current_drawdown = max(dd, _ZERO)
            if stats.current_drawdown > stats.maximum_drawdown:
                stats.maximum_drawdown = stats.current_drawdown
        else:
            stats.current_drawdown = _ZERO
