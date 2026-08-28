"""Dashboard history + PWA routes."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.core.config import get_settings
from bot.live.dashboard import render_live_dashboard
from bot.live.dashboard_history import (
    chart_series_from_history,
    extract_metrics,
    load_history,
    record_snapshot,
    weekly_realized_delta,
)
from bot.live.micro_engine import reset_micro_engine
from bot.live.micro_session_manager import reset_micro_session_manager
from bot.live.service import reset_live_service
from bot.main import app, reset_risk_singletons


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "dash-hist.json"))
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    reset_risk_singletons()
    reset_live_service()
    reset_micro_engine()
    reset_micro_session_manager()
    yield
    get_settings.cache_clear()
    reset_risk_singletons()


def test_record_and_load_history(tmp_path: Path) -> None:
    hist = tmp_path / "hist.jsonl"
    payload = {
        "session": {
            "running": True,
            "portfolio_value_eur": "4100.50",
            "realized_trade_pnl_eur": "-5.25",
            "starting_portfolio_eur": "4180.00",
            "bridge": {
                "unrealized_mtm_eur": "-12.00",
                "winnable_mtm_eur": "0.00",
                "free_quote_eur": "1600.00",
            },
        }
    }
    assert record_snapshot(payload, path=hist, force=True) is True
    assert record_snapshot(payload, path=hist, force=False) is False
    rows = load_history(path=hist)
    assert len(rows) == 1
    assert rows[0]["portfolio_eur"] == "4100.50"
    assert rows[0]["realized_pnl_eur"] == "-5.25"
    assert rows[0]["winnable_eur"] == "0.00"
    assert Decimal(rows[0]["session_pnl_eur"]) == Decimal("-79.50")


def test_chart_series_from_history() -> None:
    series = chart_series_from_history(
        [
            {
                "t": "2026-08-24T17:00:00+00:00",
                "portfolio_eur": "4100",
                "realized_pnl_eur": "-5",
                "unrealized_eur": "-10",
                "session_pnl_eur": "-80",
            }
        ]
    )
    assert series["labels"] == ["17:00"]
    assert series["portfolio"] == [4100.0]


def test_weekly_realized_delta() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    history = [
        {
            "t": (now - timedelta(days=3)).isoformat(),
            "realized_pnl_eur": "-10",
        },
        {
            "t": (now - timedelta(days=1)).isoformat(),
            "realized_pnl_eur": "-5",
        },
    ]
    delta = weekly_realized_delta(history, current_realized=Decimal("7.50"))
    assert delta == Decimal("17.50")


def test_render_dashboard_includes_charts_and_pwa() -> None:
    html = render_live_dashboard(
        {
            "session": {"running": True, "bridge": {}},
            "observe": {},
            "history": [],
        }
    ).body.decode()
    assert "chart-portfolio" in html
    assert "chart-pnl" in html
    assert "manifest.webmanifest" in html
    assert "serviceWorker" in html
    assert "Sessie PnL" in html
    assert "Week gerealiseerd" in html
    assert "Sessie gerealiseerd" in html


def test_pwa_and_metrics_routes() -> None:
    with TestClient(app) as client:
        manifest = client.get("/live/manifest.webmanifest")
        assert manifest.status_code == 200
        assert "standalone" in manifest.text
        sw = client.get("/live/sw.js")
        assert sw.status_code == 200
        assert "moreney-dash" in sw.text
        icon = client.get("/live/icon.svg")
        assert icon.status_code == 200
        metrics = client.get("/live/dashboard/metrics")
        assert metrics.status_code == 200
        body = metrics.json()
        assert "metrics" in body
        assert "history" in body
