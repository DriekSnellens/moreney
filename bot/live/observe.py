"""Phase 1 — live observe: read-only balances + shadow comparison (no orders)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.funding.multi_venue import (
    fetch_live_venue_balances,
    fetch_public_eur_prices,
    parse_venue_list,
    summarize_venue_snapshot,
)
from bot.funding.models import VenueBalanceSnapshot
from bot.live.credentials import credential_report, probe_all_venues


class LiveObserveService:
    """Fetch live venue balances for observation. Never enables trading."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def observe_venues(self) -> list[str]:
        raw = getattr(self._settings, "live_observe_venues", None) or getattr(
            self._settings, "funding_venues", "bitvavo,kraken,binance,okx"
        )
        return parse_venue_list(str(raw))

    def credentials(self) -> dict[str, Any]:
        return credential_report(self._settings, self.observe_venues())

    async def probe_credentials(self) -> dict[str, Any]:
        return await probe_all_venues(self._settings, self.observe_venues())

    async def snapshot(self, *, probe: bool = False) -> dict[str, Any]:
        venues = self.observe_venues()
        enabled = bool(getattr(self._settings, "live_observe_enabled", True))
        creds = self.credentials()
        if not enabled:
            return {
                "enabled": False,
                "places_orders": False,
                "venues": [],
                "credentials": creds,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "note": "LIVE_OBSERVE_ENABLED=false",
            }

        quote = (getattr(self._settings, "paper_quote_asset", None) or "EUR").upper()
        prices = await fetch_public_eur_prices()
        snaps: list[VenueBalanceSnapshot] = await fetch_live_venue_balances(
            self._settings, venues, prices_eur=prices or None
        )
        online = sum(1 for s in snaps if s.online)
        summaries = [
            summarize_venue_snapshot(s, quote=quote) for s in snaps if s.online
        ]
        total_eur = sum(
            (Decimal(str(s.get("total_value_eur") or 0)) for s in summaries),
            Decimal("0"),
        )
        free_quote = sum(
            (Decimal(str(s.get("free_quote_eur") or 0)) for s in summaries),
            Decimal("0"),
        )
        inventory = sum(
            (Decimal(str(s.get("inventory_mtm_eur") or 0)) for s in summaries),
            Decimal("0"),
        )
        payload: dict[str, Any] = {
            "enabled": True,
            "places_orders": False,
            "mode": "observe_only",
            "venues_requested": venues,
            "venues_online": online,
            "venues_total": len(snaps),
            "quote_asset": quote,
            "total_value_eur": str(total_eur),
            "free_quote_eur": str(free_quote),
            "inventory_mtm_eur": str(inventory),
            "prices_eur_count": len(prices),
            "venue_summaries": summaries,
            "balances": [s.model_dump(mode="json") for s in snaps],
            "credentials": creds,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "note": (
                "Read-only live balances with public EUR marks for non-quote inventory. "
                "No orders placed. free_quote_eur is deployable cash; "
                "inventory_mtm_eur is crypto marked to EUR."
            ),
            "next_step": (
                "Add missing venue keys from credentials.missing_venues, "
                "then GET /live/credentials?probe=true"
                if creds.get("missing_venues")
                else "Credentials present — verify /live/observe balances match exchange UI"
            ),
        }
        if probe:
            payload["credential_probes"] = await self.probe_credentials()
        return payload

    def compare_to_paper(
        self,
        *,
        live_snaps: list[dict[str, Any]],
        paper_venues: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        live_set = {str(s.get("venue") or "").lower() for s in live_snaps if s.get("online")}
        paper_set = {str(v).lower() for v in (paper_venues or {})}
        return {
            "paper_venues": sorted(paper_set),
            "live_online_venues": sorted(live_set),
            "overlap": sorted(paper_set & live_set),
            "paper_only": sorted(paper_set - live_set),
            "live_only": sorted(live_set - paper_set),
        }
