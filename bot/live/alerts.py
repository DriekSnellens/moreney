"""Phase 4 — venue health and rebalance alerts (non-executing)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bot.core.config import Settings
from bot.funding.models import RebalanceRecommendation
from bot.funding.service import FundingPortfolioService


class LiveAlertService:
    """Build operator alerts; never executes transfers or withdrawals."""

    def __init__(
        self,
        settings: Settings,
        *,
        funding: FundingPortfolioService | None = None,
    ) -> None:
        self._settings = settings
        self._funding = funding

    def from_observe(
        self,
        observe: dict[str, Any],
        *,
        recommendations: list[RebalanceRecommendation] | list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        for bal in observe.get("balances") or []:
            venue = bal.get("venue")
            if not bal.get("online"):
                alerts.append(
                    {
                        "severity": "warning",
                        "kind": "venue_offline",
                        "venue": venue,
                        "message": f"{venue} balance fetch offline: {bal.get('error')}",
                        "action": "Check API keys / exchange status. Portfolio continues.",
                        "ts": now,
                    }
                )

        online = int(observe.get("venues_online") or 0)
        total = int(observe.get("venues_total") or 0)
        if total > 0 and online == 0 and observe.get("enabled"):
            alerts.append(
                {
                    "severity": "critical",
                    "kind": "all_venues_offline",
                    "message": "No live venues returned balances",
                    "action": "Verify credentials and network before any live trading.",
                    "ts": now,
                }
            )

        for rec in recommendations or []:
            if hasattr(rec, "model_dump"):
                data = rec.model_dump(mode="json")
            else:
                data = dict(rec)
            alerts.append(
                {
                    "severity": "info",
                    "kind": "rebalance_recommended",
                    "venue": data.get("to_venue"),
                    "message": (
                        f"Rebalance {data.get('amount')} {data.get('asset')} "
                        f"{data.get('from_venue')} → {data.get('to_venue')}"
                    ),
                    "action": "Transfer manually via exchange UI (bot will not withdraw).",
                    "recommendation": data,
                    "ts": now,
                }
            )

        if bool(getattr(self._settings, "live_trading_enabled", False)) and not bool(
            getattr(self._settings, "live_micro_enabled", False)
        ):
            alerts.append(
                {
                    "severity": "warning",
                    "kind": "live_flag_without_micro",
                    "message": "LIVE_TRADING_ENABLED without LIVE_MICRO_ENABLED",
                    "action": "Disable live trading or enable micro gates.",
                    "ts": now,
                }
            )

        return alerts
