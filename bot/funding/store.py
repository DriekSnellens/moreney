"""JSON-backed funding event store (no automatic withdrawals)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from bot.funding.models import (
    FundingEvent,
    FundingEventStatus,
    FundingEventType,
)

logger = logging.getLogger(__name__)


class FundingEventStore:
    """Persist funding events to a JSON file (same pattern as paper state)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._events: list[FundingEvent] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._events = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            rows = raw.get("events") if isinstance(raw, dict) else raw
            self._events = [FundingEvent.from_store(r) for r in (rows or [])]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Funding store load failed: %s", type(exc).__name__)
            self._events = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": [e.to_store() for e in self._events]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)

    def list_events(
        self,
        *,
        event_type: FundingEventType | str | None = None,
        venue: str | None = None,
        limit: int = 200,
    ) -> list[FundingEvent]:
        with self._lock:
            rows = list(self._events)
        if event_type is not None:
            key = (
                event_type
                if isinstance(event_type, FundingEventType)
                else FundingEventType(str(event_type))
            )
            rows = [e for e in rows if e.type == key]
        if venue:
            v = venue.strip().lower()
            rows = [e for e in rows if e.venue.lower() == v]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[: max(1, limit)]

    def add(self, event: FundingEvent) -> FundingEvent:
        with self._lock:
            self._events.append(event)
            self._save()
        return event

    def record_deposit(
        self,
        *,
        venue: str,
        amount: Any,
        asset: str = "EUR",
        currency: str = "EUR",
        external_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: FundingEventStatus = FundingEventStatus.COMPLETED,
    ) -> FundingEvent:
        from decimal import Decimal
        from datetime import datetime, timezone

        completed = datetime.now(timezone.utc) if status == FundingEventStatus.COMPLETED else None
        event = FundingEvent(
            type=FundingEventType.DEPOSIT,
            venue=str(venue).strip().lower(),
            asset=str(asset).upper(),
            amount=Decimal(str(amount)),
            currency=str(currency).upper(),
            status=status,
            external_reference=external_reference,
            completed_at=completed,
            metadata=dict(metadata or {}),
        )
        return self.add(event)

    def record_withdrawal_tracking(
        self,
        *,
        venue: str,
        amount: Any,
        asset: str = "EUR",
        currency: str = "EUR",
        external_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: FundingEventStatus = FundingEventStatus.COMPLETED,
    ) -> FundingEvent:
        """Record that the user withdrew via the exchange UI — does not execute."""
        from decimal import Decimal
        from datetime import datetime, timezone

        completed = datetime.now(timezone.utc) if status == FundingEventStatus.COMPLETED else None
        meta = dict(metadata or {})
        meta["executed_by"] = "exchange_ui"
        meta["bot_executed"] = False
        event = FundingEvent(
            type=FundingEventType.WITHDRAWAL,
            venue=str(venue).strip().lower(),
            asset=str(asset).upper(),
            amount=Decimal(str(amount)),
            currency=str(currency).upper(),
            status=status,
            external_reference=external_reference,
            completed_at=completed,
            metadata=meta,
        )
        return self.add(event)

    def totals(self, *, currency: str = "EUR") -> dict[str, Any]:
        from decimal import Decimal

        cur = currency.upper()
        deposited = Decimal("0")
        withdrawn = Decimal("0")
        pending = 0
        with self._lock:
            for e in self._events:
                if e.currency.upper() != cur and e.asset.upper() != cur:
                    continue
                if e.status == FundingEventStatus.PENDING:
                    pending += 1
                if e.status not in {FundingEventStatus.COMPLETED, FundingEventStatus.PENDING}:
                    continue
                if e.type == FundingEventType.DEPOSIT:
                    deposited += e.amount
                elif e.type == FundingEventType.WITHDRAWAL:
                    withdrawn += e.amount
        return {
            "total_deposited": deposited,
            "total_withdrawn": withdrawn,
            "pending_count": pending,
        }
