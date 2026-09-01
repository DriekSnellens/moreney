"""Funding portfolio service — aggregates balances, funding, rebalance advice."""

from __future__ import annotations

import logging
from decimal import Decimal
from functools import lru_cache
from typing import Any

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode
from bot.funding.models import (
    FundingEvent,
    PortfolioSummary,
    RebalanceRecommendation,
    VenueBalanceSnapshot,
)
from bot.funding.multi_venue import (
    fetch_live_venue_balances,
    is_paper_mode,
    ledger_to_venue_snapshots,
    parse_target_weights,
    parse_venue_list,
)
from bot.funding.rebalance import recommend_quote_rebalance
from bot.funding.store import FundingEventStore

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


class FundingPortfolioService:
    """Central funding view over paper inventory or live exchange balances."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: FundingEventStore | None = None,
        paper_runner_getter: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        path = getattr(self._settings, "funding_persist_path", "./data/funding_events.json")
        self._store = store or FundingEventStore(path)
        self._paper_runner_getter = paper_runner_getter

    @property
    def store(self) -> FundingEventStore:
        return self._store

    @property
    def withdrawals_supported(self) -> bool:
        return False

    @property
    def automatic_withdrawals_enabled(self) -> bool:
        return bool(getattr(self._settings, "automatic_withdrawals_enabled", False))

    def main_funding_venue(self) -> str:
        return str(getattr(self._settings, "funding_main_venue", "bitvavo") or "bitvavo").lower()

    def configured_venues(self) -> list[str]:
        raw = getattr(self._settings, "funding_venues", None) or getattr(
            self._settings, "market_data_exchanges", "bitvavo,kraken,binance,okx"
        )
        venues = parse_venue_list(str(raw))
        # Prefer paper maker venues when in paper for consistency with inventory.
        if is_paper_mode(self._settings):
            maker = parse_venue_list(getattr(self._settings, "paper_maker_venues", "") or "")
            if maker:
                # Keep funding order but ensure maker venues present.
                for v in maker:
                    if v not in venues:
                        venues.append(v)
        return venues

    def _paper_ledger_export(self) -> dict[str, Any] | None:
        getter = self._paper_runner_getter
        if getter is None:
            try:
                from bot.main import get_paper_runner

                getter = get_paper_runner
            except Exception:  # noqa: BLE001
                return None
        try:
            runner = getter()
            ledger = getattr(getattr(runner, "portfolio", None), "venue_ledger", None)
            if ledger is None:
                return None
            return ledger.export()
        except Exception:  # noqa: BLE001
            logger.warning("Paper ledger unavailable for funding portfolio")
            return None

    def _paper_equity_and_pnl(self) -> tuple[Decimal, Decimal, Decimal]:
        """Return (current_equity, realized_pnl, unrealized_approx)."""
        getter = self._paper_runner_getter
        if getter is None:
            try:
                from bot.main import get_paper_runner

                getter = get_paper_runner
            except Exception:  # noqa: BLE001
                return _ZERO, _ZERO, _ZERO
        try:
            runner = getter()
            snap = runner.tracker.snapshot()
            current = Decimal(str(snap.current_equity))
            starting = Decimal(str(snap.starting_equity))
            realized = Decimal(str(getattr(snap, "realized_pnl", None) or snap.net_pnl))
            unrealized = current - starting - realized
            # If accounting does not split, treat net as realized for display.
            if abs(unrealized) < Decimal("0.0001"):
                unrealized = _ZERO
                realized = Decimal(str(snap.net_pnl))
            return current, realized, unrealized
        except Exception:  # noqa: BLE001
            return _ZERO, _ZERO, _ZERO

    async def get_venue_balances(self) -> list[VenueBalanceSnapshot]:
        venues = self.configured_venues()
        if is_paper_mode(self._settings):
            export = self._paper_ledger_export()
            if export:
                snaps = ledger_to_venue_snapshots(export, source="paper")
                # Ensure configured venues appear even if empty.
                have = {s.venue for s in snaps}
                for v in venues:
                    if v not in have:
                        snaps.append(
                            VenueBalanceSnapshot(
                                venue=v,
                                balances=[],
                                total_value_eur=_ZERO,
                                online=True,
                                error=None,
                                source="paper",
                            )
                        )
                return snaps
            return [
                VenueBalanceSnapshot(
                    venue=v,
                    balances=[],
                    total_value_eur=_ZERO,
                    online=False,
                    error="paper_ledger_unavailable",
                    source="paper",
                )
                for v in venues
            ]

        # Live: read-only balance fetch; never enable trading.
        return await fetch_live_venue_balances(self._settings, venues)

    async def get_balances_for_venue(self, venue: str) -> VenueBalanceSnapshot | None:
        key = venue.strip().lower()
        for snap in await self.get_venue_balances():
            if snap.venue == key:
                return snap
        return None

    def funding_events(
        self,
        *,
        event_type: FundingEventType | str | None = None,
        venue: str | None = None,
        limit: int = 200,
    ) -> list[FundingEvent]:
        return self._store.list_events(event_type=event_type, venue=venue, limit=limit)

    def record_deposit(self, **kwargs: Any) -> FundingEvent:
        return self._store.record_deposit(**kwargs)

    def record_withdrawal_tracking(self, **kwargs: Any) -> FundingEvent:
        if self.automatic_withdrawals_enabled:
            # Still do not execute — flag only enables recording helpers.
            pass
        return self._store.record_withdrawal_tracking(**kwargs)

    def rebalance_recommendations(self) -> list[RebalanceRecommendation]:
        export = self._paper_ledger_export()
        if not export and is_paper_mode(self._settings):
            return []
        quote = str(
            (export or {}).get("quote")
            or getattr(self._settings, "paper_quote_asset", "EUR")
            or "EUR"
        ).upper()
        balances: dict[str, Decimal] = {}
        if export:
            for venue, assets in (export.get("balances") or {}).items():
                balances[str(venue).lower()] = Decimal(str((assets or {}).get(quote, 0)))
        weights = parse_target_weights(
            str(getattr(self._settings, "funding_target_weights", "") or "")
        )
        fee_bps = Decimal(
            str(
                getattr(self._settings, "global_transfer_fee_bps", None)
                or getattr(self._settings, "paper_rebalance_fee_bps", 10)
                or 10
            )
        )
        return recommend_quote_rebalance(
            balances,
            asset=quote,
            target_weights=weights or None,
            fee_bps=fee_bps,
        )

    async def portfolio_summary(self) -> PortfolioSummary:
        venues = await self.get_venue_balances()
        quote = (self._settings.paper_quote_asset or "EUR").upper()
        totals = self._store.totals(currency=quote)
        deposited = Decimal(str(totals["total_deposited"]))
        withdrawn = Decimal(str(totals["total_withdrawn"]))

        current = sum((v.total_value_eur for v in venues), _ZERO)
        available = _ZERO
        reserved = _ZERO
        for v in venues:
            for b in v.balances:
                if b.asset == quote:
                    available += b.available
                    reserved += b.reserved

        realized = _ZERO
        unrealized = _ZERO
        if is_paper_mode(self._settings):
            equity, realized, unrealized = self._paper_equity_and_pnl()
            if equity > 0:
                current = equity
            # Seed deposited from paper starting capital when no funding events yet.
            if deposited <= 0:
                deposited = Decimal(str(self._settings.paper_starting_eur))

        pnl = current - (deposited - withdrawn)
        if is_paper_mode(self._settings) and realized == _ZERO and unrealized == _ZERO:
            realized = pnl

        mode = (
            ExecutionMode.PAPER.value
            if is_paper_mode(self._settings)
            else ExecutionMode.LIVE.value
        )
        pending = int(totals.get("pending_count") or 0)

        return PortfolioSummary(
            mode=mode,
            main_funding_venue=self.main_funding_venue(),
            quote_asset=quote,
            total_deposited=deposited,
            total_withdrawn=withdrawn,
            current_portfolio=current,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            pnl=pnl,
            available_capital=available,
            reserved_capital=reserved,
            pending_transfers=pending,
            venues=venues,
            withdrawals_supported=False,
            automatic_withdrawals_enabled=self.automatic_withdrawals_enabled,
        )

    def public_status_flags(self) -> dict[str, Any]:
        """Safe flags for /status — never includes secrets."""
        return {
            "withdrawals_supported": False,
            "automatic_withdrawals_enabled": False,
            "funding_main_venue": self.main_funding_venue(),
            "funding_venues": self.configured_venues(),
        }


@lru_cache
def get_funding_service() -> FundingPortfolioService:
    return FundingPortfolioService()


def reset_funding_service() -> None:
    get_funding_service.cache_clear()
