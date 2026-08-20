"""Phase 1 — live observe: read-only balances + shadow comparison (no orders)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from bot.core.config import Settings
from bot.funding.multi_venue import fetch_live_venue_balances, parse_venue_list
from bot.funding.models import VenueBalanceSnapshot


class LiveObserveService:
    """Fetch live venue balances for observation. Never enables trading."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def observe_venues(self) -> list[str]:
        raw = getattr(self._settings, "live_observe_venues", None) or getattr(
            self._settings, "funding_venues", "bitvavo,kraken,binance,okx"
        )
        return parse_venue_list(str(raw))

    async def snapshot(self) -> dict[str, Any]:
        venues = self.observe_venues()
        enabled = bool(getattr(self._settings, "live_observe_enabled", True))
        if not enabled:
            return {
                "enabled": False,
                "places_orders": False,
                "venues": [],
                "as_of": datetime.now(timezone.utc).isoformat(),
                "note": "LIVE_OBSERVE_ENABLED=false",
            }

        snaps: list[VenueBalanceSnapshot] = await fetch_live_venue_balances(
            self._settings, venues
        )
        online = sum(1 for s in snaps if s.online)
        total_eur = sum((s.total_value_eur for s in snaps), Decimal("0"))
        return {
            "enabled": True,
            "places_orders": False,
            "mode": "observe_only",
            "venues_requested": venues,
            "venues_online": online,
            "venues_total": len(snaps),
            "total_value_eur": str(total_eur),
            "balances": [s.model_dump(mode="json") for s in snaps],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "note": (
                "Read-only live balances. No orders placed. "
                "Configure per-venue API keys (withdraw permission must stay off)."
            ),
        }

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
