"""Phase 0 — go/no-go checklist evaluation (never places orders)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.core.config import Settings
from bot.live.production_flags import PRODUCTION_EXECUTION_ENABLED


@dataclass
class ChecklistItem:
    id: str
    label: str
    passed: bool
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass
class GoNoGoResult:
    ready: bool
    items: list[ChecklistItem] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "items": [i.to_dict() for i in self.items],
            "blocking": self.blocking,
        }


def evaluate_go_no_go(
    settings: Settings,
    *,
    runner_status: dict[str, Any] | None = None,
    paper_status: dict[str, Any] | None = None,
    kill_switch_state: str | None = None,
) -> GoNoGoResult:
    """Evaluate whether the process is safe for live observe/micro.

    ``paper_status`` is accepted as a deprecated alias of ``runner_status``.
    """
    status = runner_status if runner_status is not None else (paper_status or {})
    items: list[ChecklistItem] = []

    withdrawals = bool(getattr(settings, "automatic_withdrawals_enabled", False))
    items.append(
        ChecklistItem(
            id="no_auto_withdrawals",
            label="Automatic withdrawals disabled",
            passed=not withdrawals,
            detail=f"automatic_withdrawals_enabled={withdrawals}",
        )
    )

    ks = (kill_switch_state or "").lower()
    if not ks and isinstance(status.get("kill_switch"), dict):
        ks = str(status["kill_switch"].get("state") or "").lower()
    items.append(
        ChecklistItem(
            id="kill_switch_not_emergency",
            label="Kill switch is not in emergency stop",
            passed=ks not in {"emergency_stop", "emergency"},
            detail=f"kill_switch={ks or 'unknown'}",
        )
    )

    funding_main = str(getattr(settings, "funding_main_venue", "") or "")
    items.append(
        ChecklistItem(
            id="funding_main_venue_set",
            label="Main funding venue configured (SEPA on-ramp)",
            passed=bool(funding_main.strip()),
            detail=f"funding_main_venue={funding_main or 'unset'}",
        )
    )

    # Live micro is the production path; flag is informational only.
    items.append(
        ChecklistItem(
            id="live_micro_production_path",
            label="Live micro is the production execution path",
            passed=bool(PRODUCTION_EXECUTION_ENABLED),
            detail=f"PRODUCTION_EXECUTION_ENABLED={bool(PRODUCTION_EXECUTION_ENABLED)}",
            required=False,
        )
    )

    live_on = bool(getattr(settings, "live_trading_enabled", False))
    micro_on = bool(getattr(settings, "live_micro_enabled", False))
    items.append(
        ChecklistItem(
            id="live_orders_still_locked",
            label="Live order unlocks remain off during Phase 0",
            passed=not (
                live_on
                and micro_on
                and bool(getattr(settings, "live_orders_unlocked", False))
            ),
            detail=(
                f"live_trading={live_on} micro={micro_on} "
                f"unlocked={bool(getattr(settings, 'live_orders_unlocked', False))}"
            ),
            required=False,
        )
    )

    observe_creds = None
    if isinstance(status, dict):
        observe_creds = (status.get("live_readiness") or {}).get("credentials")
    if isinstance(observe_creds, dict):
        ready = bool(observe_creds.get("ready_for_observe"))
        items.append(
            ChecklistItem(
                id="observe_credentials_optional",
                label="At least one venue API key configured for live observe",
                passed=ready,
                detail=f"configured={observe_creds.get('configured_count', 0)}",
                required=False,
            )
        )

    blocking = [i.id for i in items if i.required and not i.passed]
    return GoNoGoResult(ready=len(blocking) == 0, items=items, blocking=blocking)
