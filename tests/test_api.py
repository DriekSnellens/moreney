"""Tests for FastAPI app surface (no withdrawal routes)."""

from fastapi.testclient import TestClient

from bot.main import app, reset_risk_singletons


def setup_function() -> None:
    reset_risk_singletons()


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_reports_no_withdrawals() -> None:
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["withdrawals_supported"] is False
    assert body["leverage_supported"] is False
    assert body["execution_mode"] == "paper"
    assert "kill_switch" in body


def test_kill_switch_endpoints() -> None:
    client = TestClient(app)
    status = client.get("/risk/kill-switch")
    assert status.status_code == 200
    assert status.json()["state"] == "running"
    assert status.json()["allows_new_orders"] is True

    stop = client.post("/risk/kill-switch/emergency-stop", json={"reason": "test stop"})
    assert stop.status_code == 200
    assert stop.json()["status"]["state"] == "emergency_stop"

    denied = client.post("/risk/kill-switch/recover")
    assert denied.status_code == 409


def test_no_withdrawal_routes() -> None:
    paths = {route.path.lower() for route in app.routes if hasattr(route, "path")}
    # No routes that execute withdrawals; tracking endpoints may mention exits.
    assert not any(p.rstrip("/").endswith("withdraw") for p in paths)
    assert not any("/withdraw/" in p for p in paths)


def test_market_data_status_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/market-data/status")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    # Default configured exchanges appear even before live sockets.
    for exchange in ("binance", "kraken", "coinbase", "bitvavo"):
        assert exchange in body
        assert "connected" in body[exchange]
        assert "stale" in body[exchange]
        assert "last_message_age_ms" in body[exchange]


def test_status_safety_flags() -> None:
    client = TestClient(app)
    body = client.get("/status").json()
    assert body["paper_mode"] is True
    assert body["live_trading_enabled"] is False
    assert body["withdrawals_supported"] is False
    assert body["leverage_supported"] is False
    assert body["execution_mode"] == "paper"


def test_paper_overview_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/paper/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_mode"] == "paper"
    assert "updated_at" in body
    assert "status" in body
    assert "market_data" in body
    assert "portfolio" in body
    assert "performance" in body
