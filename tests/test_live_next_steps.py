"""Tests for live observe credentials, Phase 0 embedding, micro dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings, get_settings
from bot.live.credentials import credential_report, resolve_credentials
from bot.live.micro_unlock import dry_run_order, unlock_checklist
from bot.live.service import reset_live_service
from bot.main import app, reset_risk_singletons
from bot.paper.fleet import collect_fleet_overview


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_risk_singletons()
    reset_live_service()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_MICRO_ENABLED", "false")
    monkeypatch.setenv("LIVE_ORDERS_UNLOCKED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_live_service()
    reset_risk_singletons()


def test_credential_report_missing_and_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BITVAVO_API_KEY", raising=False)
    monkeypatch.delenv("BITVAVO_API_SECRET", raising=False)
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    settings = Settings(funding_venues="bitvavo,kraken", exchange_name="stub")
    report = credential_report(settings, ["bitvavo", "kraken"])
    assert "bitvavo" in report["missing_venues"]
    monkeypatch.setenv("BITVAVO_API_KEY", "unique-key-value-zzz")
    monkeypatch.setenv("BITVAVO_API_SECRET", "unique-secret-value-zzz")
    report2 = credential_report(settings, ["bitvavo", "kraken"])
    assert report2["configured_count"] >= 1
    assert "bitvavo" not in report2["missing_venues"]
    row = resolve_credentials(settings, "bitvavo")
    assert row["configured"] is True
    assert "unique-key-value-zzz" not in str(row)
    assert "unique-secret-value-zzz" not in str(row)


def test_unlock_checklist_lists_missing_flags() -> None:
    checklist = unlock_checklist(Settings())
    assert checklist["can_place_orders"] is False
    assert "LIVE_TRADING_ENABLED" in checklist["missing"]
    assert checklist["places_orders_via_this_endpoint"] is False


def test_dry_run_never_submits() -> None:
    result = dry_run_order(
        Settings(),
        venue="bitvavo",
        symbol="BTCEUR",
        notional_eur="25",
    )
    assert result["would_submit"] is False
    assert result["policy_allows"] is False
    assert result["withdrawals_supported"] is False


def test_paper_status_includes_live_readiness() -> None:
    client = TestClient(app)
    body = client.get("/paper/status").json()
    assert "live_readiness" in body
    lr = body["live_readiness"]
    assert "go_no_go_ready" in lr
    assert lr["can_place_live_orders"] is False
    assert lr["withdrawals_supported"] is False


def test_live_credentials_and_dry_run_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITVAVO_API_KEY", "secret-key-abc")
    monkeypatch.setenv("BITVAVO_API_SECRET", "secret-secret-xyz")
    get_settings.cache_clear()
    reset_live_service()
    client = TestClient(app)

    creds = client.get("/live/credentials").json()
    assert creds["configured_count"] >= 1
    blob = str(creds)
    assert "secret-key-abc" not in blob
    assert "secret-secret-xyz" not in blob

    observe = client.get("/live/observe").json()
    assert "credentials" in observe
    assert observe["places_orders"] is False

    checklist = client.get("/live/micro/unlock-checklist").json()
    assert checklist["can_place_orders"] is False

    dry = client.post(
        "/live/micro/dry-run",
        json={"venue": "bitvavo", "symbol": "BTCEUR", "notional_eur": 25},
    ).json()
    assert dry["would_submit"] is False
    assert dry["policy_allows"] is False


@pytest.mark.asyncio
async def test_fleet_includes_live_readiness() -> None:
    settings = Settings(paper_fleet_urls="")
    overview = await collect_fleet_overview(settings)
    assert "live_readiness" in overview
    assert overview["live_readiness"].get("withdrawals_supported") is False
