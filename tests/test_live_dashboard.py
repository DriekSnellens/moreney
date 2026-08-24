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
                "session_live_transaction_count": 7,
                "bridge": {
                    "free_quote_eur": "1623.39",
                    "portfolio_value_eur": "2100.50",
                    "netto_winst_eur": "100.50",
                    "session_live_transaction_count": 7,
                    "backfill_mirrored_count": 42,
                    "unrealized_mtm_eur": "-25.50",
                    "blocked_sells_session": "21",
                    "locked_notional_eur": "450.00",
                    "micro_locked_notional_eur": "120.00",
                    "long_hold_notional_eur": "330.00",
                    "skips": {},
                    "last_sync_by_venue": {
                        "bitvavo": {"ledger": {"EUR": "900.00"}, "venue_budget_remaining": "900.00"},
                        "okx": {"ledger": {"EUR": "700.00"}, "venue_budget_remaining": "700.00"},
                    },
                    "diagnostics": {
                        "why_idle": [
                            "HOLDING_BELOW_COST bitvavo:ETH:-2.10%",
                            "SELLS_BLOCKED_NEVER_LOSS sell_be=12 time_stop_be=9",
                            "VENUE_CASH bitvavo=€900 okx=€700",
                        ],
                        "skip_leaders": [
                            ["sell_below_break_even", 12],
                            ["time_stop_below_be", 9],
                        ],
                    },
                    "trail_take_profit": {
                        "states": {
                            "bitvavo:ETH": {
                                "venue": "bitvavo",
                                "base": "ETH",
                                "role": "long_hold",
                                "cost": "2000",
                                "mark": "1950",
                                "notional_eur": "330.00",
                                "unrealized_eur": "-8.25",
                                "gain_pct": "-2.50",
                                "pct_to_arm": "3.40",
                                "soft_armed": False,
                                "hard_armed": False,
                                "soft_arm_pct": "0.90",
                                "hard_arm_pct": "1.80",
                                "session_qty": "0.1",
                                "age_sec": 120,
                            }
                        }
                    },
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
                            {"asset": "EUR", "available": "900.00", "total": "900.00"},
                        ],
                    },
                    {
                        "venue": "okx",
                        "balances": [
                            {"asset": "EUR", "available": "700.00", "total": "700.00"},
                        ],
                    },
                ],
            },
            "readiness": {"active_phase": "phase1", "can_place_live_orders": False},
            "alerts": {"alerts": []},
        }
    ).body.decode()
    assert "Moreney" in html
    assert "Portfolio" in html
    assert "Vrij te besteden" in html
    assert "Gerealiseerd (live)" in html
    assert "Ongerealiseerd MTM" in html
    assert "Sessie-transacties" in html
    assert "Sessie PnL" in html
    assert "7" in html
    assert "42" in html
    assert "long-hold" in html
    assert "Start" in html
    assert "Emergency stop" not in html
    assert "Waarom nu stil" in html
    assert "Bags onder kostprijs" in html
    assert "bitvavo vrij EUR" in html
    assert "okx vrij EUR" in html
    assert "chart-portfolio" in html
    assert "manifest.webmanifest" in html
    assert "long-hold — buiten micro-recycle" in html
    assert "sell onder break-even" in html


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
        assert "Sessie-transacties" in live.text
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.get("/dashboard", follow_redirects=False).status_code == 200
        assert client.get("/live/manifest.webmanifest").status_code == 200
        assert client.get("/live/dashboard/metrics").status_code == 200
        for path in ("/paper/dashboard", "/paper/dashboard-lite", "/fleet", "/strategy-lab"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 303, path
            assert resp.headers.get("location") == "/live/dashboard"
        api = client.get("/api").json()
        assert api["live_dashboard"] == "/live/dashboard"
        assert "paper_dashboard" not in api
