"""Stale status after process restart must not look like a live session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.live import micro_session_manager as msm


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    msm.reset_micro_session_manager()
    monkeypatch.setattr(msm, "_STATUS_PATH", tmp_path / "live_micro_session_status.json")
    yield
    msm.reset_micro_session_manager()


def test_status_marks_stale_when_task_dead(tmp_path: Path) -> None:
    path = tmp_path / "live_micro_session_status.json"
    path.write_text(
        json.dumps(
            {
                "running": True,
                "continuous": True,
                "updated_at": "2026-08-24T21:08:08+00:00",
                "message": "running",
                "budget_eur": "2000.0",
                "exclude_btc": True,
                "portfolio_value_eur": "4179.06",
            }
        ),
        encoding="utf-8",
    )
    mgr = msm.get_micro_session_manager()
    st = mgr.status()
    assert st["task_running"] is False
    assert st["running"] is False
    assert st["stale"] is True
    assert st["stale_reason"] == "session_task_not_running"
    assert st["portfolio_value_eur"] == "4179.06"


def test_interrupted_continuous_resume_kwargs(tmp_path: Path) -> None:
    path = tmp_path / "live_micro_session_status.json"
    path.write_text(
        json.dumps(
            {
                "running": True,
                "continuous": True,
                "updated_at": "2026-08-24T21:08:08+00:00",
                "budget_eur": "2000.0",
                "exclude_btc": True,
            }
        ),
        encoding="utf-8",
    )
    mgr = msm.get_micro_session_manager()
    kwargs = mgr.interrupted_continuous_resume()
    assert kwargs == {
        "minutes": None,
        "budget_eur": 2000.0,
        "exclude_btc": True,
    }
