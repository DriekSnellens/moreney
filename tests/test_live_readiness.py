"""Tests for live readiness phases 0–5 (fail-closed)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode
from bot.core.exceptions import ExecutionError
from bot.core.models import OrderRequest
from bot.core.enums import OpportunitySide
from bot.live.audit import LiveAuditLog
from bot.live.executor import MultiVenueLiveExecutor
from bot.live.gates import evaluate_go_no_go
from bot.live.micro import MicroLivePolicy
from bot.live.phases import LivePhase
from bot.live.production_flags import PRODUCTION_EXECUTION_ENABLED
from bot.live.registry import MultiVenueRegistry
from bot.live.service import LiveReadinessService, reset_live_service
from bot.main import app, reset_risk_singletons


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_risk_singletons()
    reset_live_service()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "live_audit.jsonl"))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_MICRO_ENABLED", "false")
    monkeypatch.setenv("LIVE_ORDERS_UNLOCKED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_live_service()
    reset_risk_singletons()


def test_production_execution_is_live_micro() -> None:
    assert PRODUCTION_EXECUTION_ENABLED is True


def test_phase0_go_no_go_passes_defaults() -> None:
    settings = Settings(funding_main_venue="bitvavo")
    result = evaluate_go_no_go(settings, kill_switch_state="running")
    assert result.ready is True
    assert not result.blocking


def test_phase0_blocks_auto_withdrawals() -> None:
    settings = Settings(automatic_withdrawals_enabled=True)
    result = evaluate_go_no_go(settings)
    assert result.ready is False
    assert "no_auto_withdrawals" in result.blocking


def test_micro_policy_fail_closed() -> None:
    policy = MicroLivePolicy(Settings())
    ok, reason = policy.can_place_orders()
    assert ok is False
    assert "LIVE_TRADING_ENABLED" in reason


def test_micro_policy_requires_all_gates() -> None:
    settings = Settings(
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo,kraken",
        live_micro_symbols="BTCEUR",
        live_micro_max_notional_eur=50,
    )
    policy = MicroLivePolicy(settings)
    assert policy.can_place_orders()[0] is True
    ok, _ = policy.validate_order(
        venue="bitvavo", symbol="BTCEUR", notional_eur=Decimal("40")
    )
    assert ok is True
    bad, reason = policy.validate_order(
        venue="okx", symbol="BTCEUR", notional_eur=Decimal("40")
    )
    assert bad is False
    assert "allowlist" in reason


def test_multi_venue_executor_scaffolding_blocks() -> None:
    from uuid import uuid4

    settings = Settings(
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
    )
    ex = MultiVenueLiveExecutor(settings, force_enabled=False)
    with pytest.raises(ExecutionError, match="blocked"):
        import asyncio

        asyncio.run(
            ex.execute(
                OrderRequest(
                    opportunity_id=uuid4(),
                    symbol="BTCEUR",
                    side=OpportunitySide.BUY,
                    quantity=Decimal("0.001"),
                    metadata={"venue": "bitvavo"},
                )
            )
        )


def test_audit_redacts_secrets(tmp_path: Path) -> None:
    log = LiveAuditLog(tmp_path / "a.jsonl")
    log.record("test", {"api_key": "SECRET", "venue": "bitvavo"})
    rows = log.recent(1)
    assert rows[0]["payload"]["api_key"] == "[redacted]"
    assert rows[0]["payload"]["venue"] == "bitvavo"


def test_registry_credential_status_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITVAVO_API_KEY", "abc")
    monkeypatch.setenv("BITVAVO_API_SECRET", "xyz")
    reg = MultiVenueRegistry(
        Settings(live_trading_venues="bitvavo,kraken", funding_venues="bitvavo,kraken")
    )
    status = reg.status()
    blob = str(status)
    assert "abc" not in blob
    assert "xyz" not in blob
    assert status["credentials"]["bitvavo"]["api_key_present"] is True
    assert status["credentials"]["kraken"]["api_key_present"] is False


@pytest.mark.asyncio
async def test_full_status_and_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    reset_live_service()
    svc = LiveReadinessService(Settings(live_observe_enabled=True))
    full = await svc.full_status()
    assert full["withdrawals_supported"] is False
    assert full["can_place_live_orders"] is False
    assert full["phase0_go_no_go"]["ready"] is True
    assert full["phase1_observe"]["places_orders"] is False
    assert full["phase2_scaffolding"]["places_orders"] is False
    assert "alerts" in full["phase4_alerts"]
    assert "runbook" in full["phase5_hardening"]
    assert full["active_phase"]["phase"] == int(LivePhase.OBSERVE)

    client = TestClient(app)
    r = client.get("/live/status")
    assert r.status_code == 200
    assert r.json()["can_place_live_orders"] is False
    assert r.json()["withdrawals_supported"] is False

    readiness = client.get("/live/readiness")
    assert readiness.status_code == 200
    body = readiness.json()
    assert "phase0_go_no_go" in body
    assert body["production_execution_enabled"] is True

    assert client.get("/live/observe").status_code == 200
    assert client.get("/live/alerts").status_code == 200
    assert client.get("/live/audit").status_code == 200

    status = client.get("/status").json()
    assert "live_readiness" in status
    assert status["live_trading_enabled"] is False


def test_status_never_leaks_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITVAVO_API_KEY", "super-secret-key-value")
    monkeypatch.setenv("BITVAVO_API_SECRET", "super-secret-secret-value")
    get_settings.cache_clear()
    reset_live_service()
    client = TestClient(app)
    blob = str(client.get("/live/readiness").json())
    assert "super-secret-key-value" not in blob
    assert "super-secret-secret-value" not in blob
