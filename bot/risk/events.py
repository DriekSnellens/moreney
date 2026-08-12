"""Risk event persistence sinks (in-memory + SQLAlchemy)."""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.risk.models import RiskEvent
from database.models import RiskEventRecord

logger = logging.getLogger(__name__)


class RiskEventStore(Protocol):
    async def record(self, event: RiskEvent) -> None: ...


class InMemoryRiskEventStore:
    """Test / scaffolding store — no database required."""

    def __init__(self) -> None:
        self.events: list[RiskEvent] = []

    async def record(self, event: RiskEvent) -> None:
        self.events.append(event)
        logger.info(
            "RISK_EVENT type=%s state=%s reason=%s",
            event.event_type,
            event.kill_switch_state.value,
            event.reason,
        )


class DatabaseRiskEventStore:
    """Persists RiskEvent rows via async SQLAlchemy sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: RiskEvent) -> None:
        async with self._session_factory() as session:
            row = RiskEventRecord(
                id=event.id,
                event_type=event.event_type,
                kill_switch_state=event.kill_switch_state.value,
                reason=event.reason,
                reason_code=(
                    event.reason_code.value
                    if hasattr(event.reason_code, "value")
                    else (str(event.reason_code) if event.reason_code else None)
                ),
                symbol=event.symbol,
                extra=event.details,
            )
            session.add(row)
            await session.commit()
            logger.info(
                "RISK_EVENT_PERSISTED type=%s state=%s reason=%s id=%s",
                event.event_type,
                event.kill_switch_state.value,
                event.reason,
                event.id,
            )
