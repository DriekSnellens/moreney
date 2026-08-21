"""Phase 0 — go/no-go checklist evaluation (never places orders)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
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
    paper_status: dict[str, Any] | None = None,
    kill_switch_state: str | None = None,
) -> GoNoGoResult:
    """Evaluate whether paper is stable enough to consider live observe/micro."""
    status = paper_status or {}
    items: list[ChecklistItem] = []

    paper_mode = settings.execution_mode == ExecutionMode.PAPER
    items.append(
        ChecklistItem(
            id="paper_mode_default",
            label="Default execution mode is paper (safe baseline)",
            passed=paper_mode or not bool(getattr(settings, "live_trading_enabled", False)),
            detail=f"execution_mode={settings.execution_mode.value}",
        )
    )

    prod_flag = bool(PRODUCTION_EXECUTION_ENABLED)
    items.append(
        ChecklistItem(
            id="production_execution_off",
            label="PRODUCTION_EXECUTION_ENABLED is False",
            passed=not prod_flag,
            detail=f"PRODUCTION_EXECUTION_ENABLED={prod_flag}",
        )
    )

    live_flag = bool(getattr(settings, "live_trading_enabled", False))
    items.append(
        ChecklistItem(
            id="live_trading_flag_off_until_micro",
            label="LIVE_TRADING_ENABLED defaults off until micro-live gates pass",
            passed=not live_flag or bool(getattr(settings, "live_micro_enabled", False)),
            detail=f"live_trading_enabled={live_flag}",
            required=False,
        )
    )

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

    realism = bool(getattr(settings, "paper_use_realism_fills", False))
    items.append(
        ChecklistItem(
            id="realism_fills_preferred",
            label="Paper uses live-equivalent realism fills",
            passed=realism,
            detail=f"paper_use_realism_fills={realism}",
            required=False,
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

    # Live trading must stay off while paper validates (Phase 0).
    live_on = bool(getattr(settings, "live_trading_enabled", False))
    micro_on = bool(getattr(settings, "live_micro_enabled", False))
    items.append(
        ChecklistItem(
            id="live_orders_still_locked",
            label="Live order unlocks remain off during Phase 0",
            passed=not (live_on and micro_on and bool(getattr(settings, "live_orders_unlocked", False))),
            detail=(
                f"live_trading={live_on} micro={micro_on} "
                f"unlocked={bool(getattr(settings, 'live_orders_unlocked', False))}"
            ),
            required=False,
        )
    )

    observe_creds = None
    if isinstance(paper_status, dict):
        observe_creds = (paper_status.get("live_readiness") or {}).get("credentials")
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

    trade_count = int(status.get("trade_count") or 0)
    items.append(
        ChecklistItem(
            id="paper_has_trade_history",
            label="Paper has recorded trades (stability signal)",
            passed=trade_count > 0,
            detail=f"trade_count={trade_count}",
            required=False,
        )
    )

    blocking = [i.id for i in items if i.required and not i.passed]
    return GoNoGoResult(ready=len(blocking) == 0, items=items, blocking=blocking)
