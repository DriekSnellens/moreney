"""Orchestrates live readiness phases 0–5 (fail-closed)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode
from bot.funding.service import FundingPortfolioService, get_funding_service
from bot.live.alerts import LiveAlertService
from bot.live.audit import LiveAuditLog
from bot.live.executor import MultiVenueLiveExecutor
from bot.live.gates import evaluate_go_no_go
from bot.live.micro import MicroLivePolicy
from bot.live.observe import LiveObserveService
from bot.live.phases import PHASE_ORDER, LivePhase, phase_public
from bot.live.registry import MultiVenueRegistry
from bot.live.production_flags import PRODUCTION_EXECUTION_ENABLED


class LiveReadinessService:
    """Single entrypoint for live readiness status across all phases."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        funding: FundingPortfolioService | None = None,
        paper_runner_getter: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._funding = funding
        self._paper_runner_getter = paper_runner_getter
        self._observe = LiveObserveService(self._settings)
        self._registry = MultiVenueRegistry(self._settings)
        self._policy = MicroLivePolicy(self._settings)
        self._audit = LiveAuditLog(
            getattr(self._settings, "live_audit_path", "./data/live_audit.jsonl")
        )
        self._alerts = LiveAlertService(self._settings, funding=funding)
        self._executor = MultiVenueLiveExecutor(
            self._settings,
            registry=self._registry,
            policy=self._policy,
            audit=self._audit,
            force_enabled=False,
        )

    def _paper_status(self) -> dict[str, Any]:
        getter = self._paper_runner_getter
        if getter is None:
            try:
                from bot.main import get_paper_runner

                getter = get_paper_runner
            except Exception:  # noqa: BLE001
                return {}
        try:
            return getter().status()
        except Exception:  # noqa: BLE001
            return {}

    def _get_funding(self) -> FundingPortfolioService:
        if self._funding is not None:
            return self._funding
        return get_funding_service()

    def active_phase(self) -> LivePhase:
        """Highest phase whose *scaffolding* is considered active (not order-placing)."""
        s = self._settings
        if bool(getattr(s, "live_hardening_enabled", True)):
            # Hardening tooling always present; does not imply live trading.
            pass
        if bool(getattr(s, "live_trading_enabled", False)) and bool(
            getattr(s, "live_micro_enabled", False)
        ):
            return LivePhase.MICRO_LIVE
        if bool(getattr(s, "live_scaffolding_ready", True)):
            # Registry/executor scaffolding is always available in this codebase.
            if bool(getattr(s, "live_observe_enabled", True)):
                return LivePhase.OBSERVE
            return LivePhase.SCAFFOLDING
        return LivePhase.GO_NO_GO

    def phase0(self) -> dict[str, Any]:
        status = self._paper_status()
        # Enrich with credential readiness for optional Phase 0 item (no secrets).
        try:
            status = {
                **status,
                "live_readiness": {
                    **(status.get("live_readiness") or {}),
                    "credentials": self._observe.credentials(),
                },
            }
        except Exception:  # noqa: BLE001
            pass
        ks = None
        if isinstance(status.get("kill_switch"), dict):
            ks = status["kill_switch"].get("state")
        result = evaluate_go_no_go(
            self._settings, paper_status=status, kill_switch_state=ks
        )
        return result.to_dict()

    async def phase1_observe(self, *, probe: bool = False) -> dict[str, Any]:
        snap = await self._observe.snapshot(probe=probe)
        paper = self._paper_status()
        inv = (paper.get("inventory") or {}).get("venues") or {}
        snap["paper_compare"] = self._observe.compare_to_paper(
            live_snaps=snap.get("balances") or [],
            paper_venues=inv,
        )
        self._audit.record("observe_snapshot", {
            "venues_online": snap.get("venues_online"),
            "venues_total": snap.get("venues_total"),
            "configured_credentials": (snap.get("credentials") or {}).get(
                "configured_count"
            ),
        })
        return snap

    def micro_unlock_checklist(self) -> dict[str, Any]:
        from bot.live.micro_unlock import unlock_checklist

        return unlock_checklist(self._settings)

    async def credentials(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            return await self._observe.probe_credentials()
        return self._observe.credentials()

    def micro_dry_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from bot.live.micro_unlock import dry_run_order

        result = dry_run_order(
            self._settings,
            venue=str(payload.get("venue") or "bitvavo"),
            symbol=str(payload.get("symbol") or "BTCEUR"),
            side=str(payload.get("side") or "buy"),
            quantity=payload.get("quantity") or "0.001",
            limit_price=payload.get("limit_price"),
            notional_eur=payload.get("notional_eur"),
        )
        self._audit.record("micro_dry_run", {
            "venue": result.get("venue"),
            "symbol": result.get("symbol"),
            "policy_allows": result.get("policy_allows"),
            "detail": result.get("detail"),
        })
        return result

    def compact_status(self) -> dict[str, Any]:
        """Lightweight summary for paper/fleet status (sync, no live HTTP)."""
        p0 = self.phase0()
        creds = self._observe.credentials()
        micro = self._policy.status()
        return {
            "active_phase": self.active_phase().name.lower(),
            "go_no_go_ready": bool(p0.get("ready")),
            "go_no_go_blocking": list(p0.get("blocking") or []),
            "observe_enabled": bool(getattr(self._settings, "live_observe_enabled", True)),
            "credentials_configured": int(creds.get("configured_count") or 0),
            "credentials_missing": list(creds.get("missing_venues") or []),
            "live_trading_enabled": bool(
                getattr(self._settings, "live_trading_enabled", False)
            ),
            "can_place_live_orders": bool(micro.get("can_place_orders")),
            "block_reason": micro.get("block_reason"),
            "withdrawals_supported": False,
        }

    def phase2_scaffolding(self) -> dict[str, Any]:
        from bot.live.micro_engine import get_micro_engine

        return {
            "registry": self._registry.status(),
            "executor": self._executor.status(),
            "micro_engine": get_micro_engine().status(),
            "places_orders": False,
            "note": (
                "MultiVenueLiveExecutor + LiveMicroEngine wired. "
                "Orders still blocked until env unlocks + arm + confirm."
            ),
        }

    def phase3_micro(self) -> dict[str, Any]:
        return self._policy.status()

    def phase4_alerts(self, observe: dict[str, Any] | None = None) -> dict[str, Any]:
        recs = self._get_funding().rebalance_recommendations()
        obs = observe or {"enabled": False, "balances": [], "venues_online": 0, "venues_total": 0}
        alerts = self._alerts.from_observe(obs, recommendations=recs)
        return {
            "alerts": alerts,
            "auto_rebalance": False,
            "auto_withdraw": False,
            "recommendation_count": len(recs),
        }

    def phase5_hardening(self) -> dict[str, Any]:
        return {
            "audit_path": str(getattr(self._settings, "live_audit_path", "./data/live_audit.jsonl")),
            "recent_audit": self._audit.recent(limit=20),
            "withdrawals_supported": False,
            "automatic_withdrawals_enabled": False,
            "secrets_in_api": False,
            "paper_live_separated": True,
            "production_execution_enabled": bool(PRODUCTION_EXECUTION_ENABLED),
            "runbook": {
                "kill_switch": "POST /risk/kill-switch/emergency-stop",
                "withdraw": "Use exchange UI only",
                "rebalance": "Follow /rebalancing/recommendations manually",
                "enable_micro_live": (
                    "Set LIVE_TRADING_ENABLED=true, LIVE_MICRO_ENABLED=true, "
                    "LIVE_ORDERS_UNLOCKED=true — only after Phase 0+1 pass"
                ),
            },
        }

    async def full_status(self) -> dict[str, Any]:
        p0 = self.phase0()
        observe = await self.phase1_observe()
        p2 = self.phase2_scaffolding()
        p3 = self.phase3_micro()
        p4 = self.phase4_alerts(observe)
        p5 = self.phase5_hardening()
        allowed, reason = self._policy.can_place_orders()
        return {
            "active_phase": phase_public(self.active_phase()),
            "phases": [phase_public(p) for p in PHASE_ORDER],
            "execution_mode": self._settings.execution_mode.value,
            "paper_mode": self._settings.execution_mode == ExecutionMode.PAPER,
            "live_trading_enabled": bool(
                getattr(self._settings, "live_trading_enabled", False)
            ),
            "can_place_live_orders": allowed,
            "block_reason": None if allowed else reason,
            "withdrawals_supported": False,
            "production_execution_enabled": bool(PRODUCTION_EXECUTION_ENABLED),
            "phase0_go_no_go": p0,
            "phase1_observe": observe,
            "phase2_scaffolding": p2,
            "phase3_micro": p3,
            "phase4_alerts": p4,
            "phase5_hardening": p5,
        }


@lru_cache
def get_live_service() -> LiveReadinessService:
    return LiveReadinessService()


def reset_live_service() -> None:
    get_live_service.cache_clear()
