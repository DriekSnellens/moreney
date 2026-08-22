"""Live dashboard is the primary HTML UI; paper dashboards redirect."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.core.config import get_settings
from bot.live.dashboard import render_live_dashboard
from bot.live.micro_engine import reset_micro_engine
from bot.live.micro_session_manager import reset_micro_session_manager
from bot.live.service import reset_live_service
from bot.main import app, reset_risk_singletons


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_PERSIST_PATH", str(tmp_path / "live-dash.json"))
    monkeypatch.setenv("DASHBOARD_BASIC_AUTH_ENABLED", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    get_settings.cache_clear()
    reset_risk_singletons()
    reset_live_service()
    reset_micro_engine()
    reset_micro_session_manager()
    yield
    get_settings.cache_clear()
    reset_risk_singletons()


def test_render_live_dashboard_contains_controls() -> None:
    html = render_live_dashboard(
        {
            "session": {
                "running": True,
                "continuous": True,
                "budget_eur": "2024",
                "portfolio_value_eur": "2100.50",
                "starting_portfolio_eur": "2000.00",
                "netto_winst_eur": "100.50",
                "live_transaction_count": 7,
                "bridge": {
                    "free_quote_eur": "1623.39",
                    "portfolio_value_eur": "2100.50",
                    "netto_winst_eur": "100.50",
                    "live_transaction_count": 7,
                    "skips": {},
                },
            },
            "engine": {"armed": False, "can_place_orders": False, "block_reason": "locked"},
            "unlock": {"can_place_orders": False, "flags": []},
            "observe": {
                "venues_online": 1,
                "venues_total": 1,
                "balances": [
                    {
                        "venue": "bitvavo",
                        "balances": [
                            {"asset": "EUR", "available": "1623.39", "total": "1623.39"},
                        ],
                    }
                ],
            },
            "readiness": {"active_phase": "phase1", "can_place_live_orders": False},
            "alerts": {"alerts": []},
        }
    ).body.decode()
    assert "Moreney" in html
    assert "Portfolio" in html
    assert "Vrij te besteden" in html
    assert "Netto winst" in html
    assert "Transacties" in html
    assert "7" in html
    assert "Start" in html
    assert "Emergency stop" not in html


def test_render_live_dashboard_cross_venue_panel() -> None:
    html = render_live_dashboard(
        {
            "session": {
                "running": True,
                "pipeline_funnel": {
                    "cross_venue": {
                        "pairs_evaluated": 1200,
                        "edges_found": 45,
                        "opportunities_emitted": 3,
                        "profitability_passed": 1,
                        "profitability_rejected": 2,
                        "risk_passed": 1,
                        "live_orders": 0,
                        "live_fills": 0,
                        "top_rejection_reasons": [
                            {"reason": "fees_eat_edge", "count": 30}
                        ],
                    }
                },
                "bridge": {},
            },
            "observe": {},
            "engine": {},
            "unlock": {},
            "readiness": {},
            "alerts": {},
        }
    ).body.decode()
    assert "Cross-venue OKX ↔ Bitvavo" in html
    assert "1200" in html
    assert "fees_eat_edge" in html


def test_live_dashboard_routes_and_paper_redirects() -> None:
    with TestClient(app) as client:
        live = client.get("/live/dashboard")
        assert live.status_code == 200
        assert "Vrij te besteden" in live.text
        assert "Portfolio" in live.text
        assert "Transacties" in live.text
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.get("/dashboard", follow_redirects=False).status_code == 200
        for path in ("/paper/dashboard", "/paper/dashboard-lite", "/fleet", "/strategy-lab"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 303, path
            assert resp.headers.get("location") == "/live/dashboard"
        api = client.get("/api").json()
        assert api["live_dashboard"] == "/live/dashboard"
        assert "paper_dashboard" not in api
