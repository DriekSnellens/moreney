"""Kill switch: blocks NEW orders under PAUSED / EMERGENCY_STOP.

Does not automatically resume. Recovery requires configured conditions to be
satisfied and an explicit ``recover()`` call when manual recovery is required.
Never places orders and never hides losses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from bot.core.config import Settings
from bot.core.enums import KillSwitchState, RiskRejectReason
from bot.risk.models import KillSwitchStatus, RiskEvent

logger = logging.getLogger(__name__)

RiskEventHandler = Callable[[RiskEvent], Awaitable[None]]


class KillSwitch:
    """Process-local kill switch with RUNNING / WARNING / PAUSED / EMERGENCY_STOP."""

    def __init__(
        self,
        settings: Settings,
        *,
        on_event: RiskEventHandler | None = None,
    ) -> None:
        self._settings = settings
        self._on_event = on_event
        self._state = KillSwitchState.RUNNING
        self._reason: str | None = None
        self._reason_code: RiskRejectReason | str | None = None
        self._activated_at: datetime | None = None
        self._consecutive_failures = 0
        self._last_conditions: dict[str, Any] = {}

    @property
    def state(self) -> KillSwitchState:
        return self._state

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def allows_new_orders(self) -> bool:
        return self._state in {KillSwitchState.RUNNING, KillSwitchState.WARNING}

    def status(self) -> KillSwitchStatus:
        return KillSwitchStatus(
            state=self._state,
            reason=self._reason,
            activated_at=self._activated_at,
            consecutive_failures=self._consecutive_failures,
            allows_new_orders=self.allows_new_orders,
            recovery_conditions_met=self.recovery_conditions_met(self._last_conditions),
        )

    async def warn(self, reason: str, *, code: RiskRejectReason | str | None = None) -> None:
        if self._state in {KillSwitchState.PAUSED, KillSwitchState.EMERGENCY_STOP}:
            return
        await self._transition(KillSwitchState.WARNING, reason, code=code)

    async def pause(self, reason: str, *, code: RiskRejectReason | str | None = None) -> None:
        if self._state == KillSwitchState.EMERGENCY_STOP:
            return
        await self._transition(KillSwitchState.PAUSED, reason, code=code)

    async def emergency_stop(
        self,
        reason: str,
        *,
        code: RiskRejectReason | str | None = None,
    ) -> None:
        await self._transition(KillSwitchState.EMERGENCY_STOP, reason, code=code)

    def record_execution_success(self) -> None:
        self._consecutive_failures = 0

    async def record_execution_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        limit = self._settings.risk_consecutive_failure_limit
        if self._consecutive_failures >= limit:
            await self.emergency_stop(
                (
                    f"Consecutive execution failures {self._consecutive_failures} "
                    f">= limit {limit}: {reason}"
                ),
                code=RiskRejectReason.KILL_SWITCH,
            )

    def update_conditions(self, conditions: dict[str, Any]) -> None:
        """Refresh latest recovery-condition snapshot (does not auto-resume)."""
        self._last_conditions = dict(conditions)

    def recovery_conditions_met(self, conditions: dict[str, Any] | None = None) -> bool:
        data = conditions if conditions is not None else self._last_conditions
        if not data:
            return False
        required = (
            "daily_loss_ok",
            "drawdown_ok",
            "market_data_fresh",
            "exchange_healthy",
            "execution_stable",
        )
        return all(bool(data.get(key)) for key in required)

    async def recover(self, *, force: bool = False) -> bool:
        """Attempt resume to RUNNING. Never silent — requires conditions (unless force)."""
        if self._state == KillSwitchState.RUNNING:
            return True
        if self._state == KillSwitchState.WARNING:
            await self._transition(KillSwitchState.RUNNING, "warning cleared", code=None)
            return True

        if not force and self._settings.risk_require_manual_recovery:
            if not self.recovery_conditions_met():
                logger.info(
                    "KILL_SWITCH_RECOVER_DENIED state=%s reason=recovery_conditions_not_met "
                    "conditions=%s",
                    self._state.value,
                    self._last_conditions,
                )
                return False

        if not force and not self.recovery_conditions_met():
            logger.info(
                "KILL_SWITCH_RECOVER_DENIED state=%s reason=recovery_conditions_not_met",
                self._state.value,
            )
            return False

        await self._transition(
            KillSwitchState.RUNNING,
            "manual recovery accepted",
            code=None,
        )
        self._consecutive_failures = 0
        return True

    async def _transition(
        self,
        new_state: KillSwitchState,
        reason: str,
        *,
        code: RiskRejectReason | str | None,
    ) -> None:
        previous = self._state
        if previous == new_state and self._reason == reason:
            return

        self._state = new_state
        self._reason = reason
        self._reason_code = code
        if new_state in {KillSwitchState.PAUSED, KillSwitchState.EMERGENCY_STOP}:
            self._activated_at = datetime.now(UTC)
        if new_state == KillSwitchState.RUNNING:
            self._activated_at = None
            self._reason = None
            self._reason_code = None

        logger.info(
            "KILL_SWITCH_STATE previous=%s state=%s reason=%s code=%s",
            previous.value,
            new_state.value,
            reason,
            code,
        )

        if new_state in {KillSwitchState.PAUSED, KillSwitchState.EMERGENCY_STOP}:
            event = RiskEvent(
                event_type="kill_switch_activated",
                kill_switch_state=new_state,
                reason=reason,
                reason_code=code,
                details={"previous_state": previous.value},
            )
            if self._on_event is not None:
                await self._on_event(event)
        elif previous in {KillSwitchState.PAUSED, KillSwitchState.EMERGENCY_STOP}:
            event = RiskEvent(
                event_type="kill_switch_recovered",
                kill_switch_state=new_state,
                reason=reason,
                reason_code=code,
                details={"previous_state": previous.value},
            )
            if self._on_event is not None:
                await self._on_event(event)
