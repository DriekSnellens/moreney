"""In-process manager for full-bot micro sessions (dashboard + API)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.live.micro_session import run_session

logger = logging.getLogger(__name__)

_STATUS_PATH = Path("./data/live_micro_session_status.json")
_REPORT_PATH = Path("./data/live_micro_session_report.json")

# Legacy paper-pocket fields that caused false +PnL / false kill signals.
_LEGACY_PAPER_STATUS_KEYS = (
    "pnl_paper_pocket_eur",
    "paper_cycles",
    "starting_equity_eur",
    "current_equity_eur",
    "ending_equity_eur",
    "paper_status_start",
    "paper_status_end",
    "paper",
)


class MicroSessionManager:
    """Single background full-bot micro session with live status for the dashboard."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._status: dict[str, Any] = {
            "running": False,
            "ok": None,
            "mode": "full_bot_micro",
            "updated_at": None,
            "message": "idle",
        }

    def _task_alive(self) -> bool:
        return bool(self._task and not self._task.done())

    @staticmethod
    def _strip_legacy_paper(data: dict[str, Any]) -> dict[str, Any]:
        out = dict(data)
        for key in _LEGACY_PAPER_STATUS_KEYS:
            out.pop(key, None)
        report = out.get("report")
        if isinstance(report, dict):
            cleaned = dict(report)
            for key in _LEGACY_PAPER_STATUS_KEYS:
                cleaned.pop(key, None)
            out["report"] = cleaned
        return out

    @staticmethod
    def _with_liveness(data: dict[str, Any], *, task_alive: bool) -> dict[str, Any]:
        """Annotate status so a dead task cannot look like a live session.

        After uvicorn restarts the status file may still say ``running: true``
        while no asyncio task is ticking — portfolio/PnL then freeze and the
        dashboard shows stale numbers. Surface that clearly.
        """
        out = MicroSessionManager._strip_legacy_paper(data)
        out["task_running"] = task_alive
        claimed_running = bool(out.get("running") or out.get("task_running"))
        if claimed_running and not task_alive:
            out["running"] = False
            out["stale"] = True
            out["stale_reason"] = "session_task_not_running"
            if not out.get("message") or out.get("message") == "running":
                out["message"] = "stale_status_task_not_running"
        else:
            out["stale"] = False
            out.pop("stale_reason", None)
        return out

    def status(self) -> dict[str, Any]:
        # Prefer in-memory; fall back to last status file after process restart.
        task_alive = self._task_alive()
        if self._status.get("updated_at"):
            return self._with_liveness(self._status, task_alive=task_alive)
        if _STATUS_PATH.exists():
            try:
                data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return self._with_liveness(data, task_alive=task_alive)
            except Exception:  # noqa: BLE001
                logger.exception("failed reading micro session status file")
        return self._with_liveness(self._status, task_alive=task_alive)

    def interrupted_continuous_resume(self) -> dict[str, Any] | None:
        """Return start kwargs if a continuous session was killed mid-run.

        Used on process boot so a uvicorn restart does not leave the dashboard
        on frozen portfolio figures overnight.
        """
        if self._task_alive():
            return None
        raw: dict[str, Any] | None = None
        if _STATUS_PATH.exists():
            try:
                data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    raw = data
            except Exception:  # noqa: BLE001
                logger.exception("failed reading micro session status for resume")
        if raw is None and self._status.get("updated_at"):
            raw = dict(self._status)
        if not raw:
            return None
        was_running = bool(raw.get("running"))
        continuous = bool(raw.get("continuous")) or raw.get("minutes") in (None, "", 0, 0.0)
        if not (was_running and continuous):
            return None
        budget = raw.get("budget_eur")
        try:
            budget_f = float(budget) if budget is not None else 2000.0
        except (TypeError, ValueError):
            budget_f = 2000.0
        return {
            "minutes": None,
            "budget_eur": budget_f,
            "exclude_btc": bool(raw.get("exclude_btc", True)),
        }

    async def resume_if_interrupted(self) -> dict[str, Any] | None:
        """Restart a continuous session that died with the process."""
        kwargs = self.interrupted_continuous_resume()
        if kwargs is None:
            return None
        # Ensure env-backed flags (LIVE_TRADING_ENABLED etc.) are re-read after boot.
        get_settings.cache_clear()
        logger.warning(
            "resuming interrupted continuous micro session budget_eur=%s",
            kwargs.get("budget_eur"),
        )
        return await self.start(**kwargs)

    def _publish(self, patch: dict[str, Any]) -> None:
        self._status.update(patch)
        self._status = self._strip_legacy_paper(self._status)
        self._status["updated_at"] = datetime.now(UTC).isoformat()
        try:
            _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATUS_PATH.write_text(
                json.dumps(self._status, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed writing micro session status")

    async def start(
        self,
        *,
        minutes: float | None = None,
        budget_eur: float = 2000.0,
        exclude_btc: bool = True,
        symbols: list[str] | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            if self._task and not self._task.done():
                return {
                    "started": False,
                    "reason": "already_running",
                    "status": self.status(),
                }
            self._stop_requested = False
            continuous = minutes is None or float(minutes) <= 0
            self._publish(
                {
                    "running": True,
                    "ok": None,
                    "message": "starting",
                    "continuous": continuous,
                    "minutes": None if continuous else minutes,
                    "budget_eur": str(budget_eur),
                    "exclude_btc": exclude_btc,
                    "started_at": datetime.now(UTC).isoformat(),
                    "finished_at": None,
                    "report": None,
                    "bridge": None,
                    "live_trades_executed": 0,
                    "live_trades_attempted": 0,
                    "elapsed_seconds": 0,
                    "remaining_seconds": None if continuous else float(minutes) * 60.0,
                }
            )

            async def _runner() -> None:
                # Share process kill-switch with API /risk/kill-switch/* so
                # emergency-stop actually blocks micro-session new orders.
                shared_ks = None
                try:
                    from bot.main import get_kill_switch

                    shared_ks = get_kill_switch()
                except Exception:  # noqa: BLE001
                    logger.warning("micro session could not bind shared kill switch")
                try:
                    report = await run_session(
                        minutes=None if continuous else minutes,
                        budget_eur=Decimal(str(budget_eur)),
                        symbols=symbols,
                        settings=settings or get_settings(),
                        exclude_btc=exclude_btc,
                        report_path=_REPORT_PATH,
                        status_callback=self._on_session_tick,
                        should_stop=lambda: self._stop_requested,
                        kill_switch=shared_ks,
                    )
                    self._publish(
                        {
                            "running": False,
                            "ok": bool(report.get("ok")),
                            "message": "finished" if report.get("ok") else "failed",
                            "finished_at": datetime.now(UTC).isoformat(),
                            "report": {
                                k: report.get(k)
                                for k in report
                                if k not in {"trades", "runner_status"}
                            },
                            "bridge": report.get("bridge"),
                            "live_trades_executed": report.get("live_trades_executed"),
                            "live_trades_attempted": report.get("live_trades_attempted"),
                            "realized_trade_pnl_eur": report.get(
                                "realized_trade_pnl_eur"
                            ),
                            "strategy_cycles": report.get("strategy_cycles"),
                            "reason": report.get("reason"),
                            "detail": report.get("detail"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("micro session crashed")
                    self._publish(
                        {
                            "running": False,
                            "ok": False,
                            "message": f"error: {type(exc).__name__}: {exc}",
                            "finished_at": datetime.now(UTC).isoformat(),
                        }
                    )

            self._task = asyncio.create_task(_runner(), name="full-bot-micro-session")
            return {"started": True, "status": self.status()}

    def _on_session_tick(self, snapshot: dict[str, Any]) -> None:
        self._publish(
            {
                "running": True,
                "message": "running",
                **snapshot,
            }
        )
        try:
            from bot.live.dashboard_history import seed_from_session_status

            seed_from_session_status(self._status)
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            self._stop_requested = True
            self._publish({"message": "stop_requested"})
            task = self._task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=30.0)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                self._publish(
                    {
                        "running": False,
                        "ok": False,
                        "message": "force_stopped",
                        "finished_at": datetime.now(UTC).isoformat(),
                    }
                )
        return {"stopped": True, "status": self.status()}


_manager: MicroSessionManager | None = None


def get_micro_session_manager() -> MicroSessionManager:
    global _manager
    if _manager is None:
        _manager = MicroSessionManager()
    return _manager


def reset_micro_session_manager() -> None:
    global _manager
    _manager = None
