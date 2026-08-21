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

    def status(self) -> dict[str, Any]:
        # Prefer in-memory; fall back to last status file after process restart.
        if self._status.get("updated_at"):
            out = dict(self._status)
            out["task_running"] = bool(self._task and not self._task.done())
            return out
        if _STATUS_PATH.exists():
            try:
                data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["task_running"] = bool(self._task and not self._task.done())
                    return data
            except Exception:  # noqa: BLE001
                logger.exception("failed reading micro session status file")
        return dict(self._status)

    def _publish(self, patch: dict[str, Any]) -> None:
        self._status.update(patch)
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
        budget_eur: float = 5000.0,
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
                    "paper": None,
                    "live_trades_executed": 0,
                    "live_trades_attempted": 0,
                    "elapsed_seconds": 0,
                    "remaining_seconds": None if continuous else float(minutes) * 60.0,
                }
            )

            async def _runner() -> None:
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
                                if k not in {"trades", "paper_status_end"}
                            },
                            "bridge": report.get("bridge"),
                            "live_trades_executed": report.get("live_trades_executed"),
                            "live_trades_attempted": report.get("live_trades_attempted"),
                            "pnl_paper_pocket_eur": report.get("pnl_paper_pocket_eur"),
                            "paper_cycles": report.get("paper_cycles"),
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
