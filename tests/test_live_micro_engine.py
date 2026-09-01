"""Tests for LiveMicroEngine wiring (fail-closed + mocked submit)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode, OpportunitySide, OrderStatus
from bot.core.models import ExecutionResult, MarketSnapshot, OrderRequest
from bot.execution.factory import create_executor
from bot.live.executor import MultiVenueLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.live.service import reset_live_service
from bot.main import app, reset_risk_singletons
from bot.paper.runner import PaperRunner


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_risk_singletons()
    reset_live_service()
    reset_micro_engine()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("LIVE_MICRO_ENABLED", "false")
    monkeypatch.setenv("LIVE_ORDERS_UNLOCKED", "false")
    monkeypatch.setenv("LIVE_ALLOW_WITHOUT_RESEARCH_UNLOCK", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_micro_engine()
    reset_live_service()
    reset_risk_singletons()


def _unlocked_settings(**kwargs: object) -> Settings:
    base = dict(
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo",
        live_micro_symbols="BTCEUR,ETHEUR",
        live_micro_max_notional_eur=50,
        automatic_withdrawals_enabled=False,
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_paper_runner_source_not_coupled_to_micro_engine() -> None:
    import inspect

    src = inspect.getsource(PaperRunner)
    assert "LiveMicroEngine" not in src
    assert "MultiVenueLiveExecutor" not in src


def test_create_executor_paper_unchanged() -> None:
    ex = create_executor(Settings(execution_mode=ExecutionMode.PAPER))
    assert ex.name in {"paper", "execution_service"} or hasattr(ex, "execute")


def test_engine_blocked_until_arm_and_unlocks() -> None:
    engine = LiveMicroEngine(Settings())
    st = engine.status()
    assert st["can_place_orders"] is False
    assert st["paper_runner_coupled"] is False
    arm = engine.arm()
    assert arm["armed"] is False


@pytest.mark.asyncio
async def test_submit_requires_confirm() -> None:
    engine = LiveMicroEngine(_unlocked_settings())
    engine.arm()
    out = await engine.submit(
        {"venue": "bitvavo", "symbol": "BTCEUR", "notional_eur": 20},
        confirm=False,
    )
    assert out["submitted"] is False
    assert out["reason"] == "confirmation_required"


@pytest.mark.asyncio
async def test_submit_real_path_with_mock_executor() -> None:
    settings = _unlocked_settings()

    class FakeExec(MultiVenueLiveExecutor):
        async def execute(self, order: OrderRequest) -> ExecutionResult:
            return ExecutionResult(
                order_id=order.id,
                opportunity_id=order.opportunity_id,
                status=OrderStatus.FILLED,
                filled_quantity=order.quantity,
                average_price=Decimal("50000"),
                message="mock fill",
                metadata={"exchange": "bitvavo", "dry_run": False, "exchange_order_id": "x1"},
            )

    class FakeRegistry:
        def get_client(self, venue: str, *, enable_trading: bool = False):  # noqa: ANN001
            class C:
                async def fetch_ticker(self, symbol: str) -> MarketSnapshot:
                    return MarketSnapshot(
                        symbol=symbol,
                        bid=Decimal("49900"),
                        ask=Decimal("50100"),
                        last=Decimal("50000"),
                    )

            return C()

    engine = LiveMicroEngine(
        settings,
        registry=FakeRegistry(),  # type: ignore[arg-type]
        executor=FakeExec(settings, force_enabled=True),
    )
    assert engine.arm()["armed"] is True
    out = await engine.submit(
        {
            "venue": "bitvavo",
            "symbol": "BTCEUR",
            "side": "buy",
            "notional_eur": 25,
            "confirm": True,
        },
        confirm=True,
    )
    assert out["submitted"] is True
    assert out["executed"] is True
    assert out["order"]["venue"] == "bitvavo"
    assert Decimal(out["order"]["quantity"]) > 0


def test_api_micro_engine_endpoints() -> None:
    client = TestClient(app)
    assert client.get("/live/micro/engine").status_code == 200
    body = client.get("/live/micro/engine").json()
    assert body["paper_runner_coupled"] is False
    assert body["can_place_orders"] is False

    arm = client.post("/live/micro/arm").json()
    assert arm["armed"] is False

    order = client.post(
        "/live/micro/orders",
        json={"venue": "bitvavo", "symbol": "BTCEUR", "notional_eur": 10, "confirm": True},
    ).json()
    assert order["submitted"] is False

    disarm = client.post("/live/micro/disarm").json()
    assert disarm["armed"] is False


@pytest.mark.asyncio
async def test_submit_exchange_error_audits_context(tmp_path: Path) -> None:
    from bot.core.exceptions import ExchangeError

    settings = _unlocked_settings()
    audit_path = tmp_path / "audit.jsonl"

    class FailExec(MultiVenueLiveExecutor):
        async def execute(self, order: OrderRequest) -> ExecutionResult:  # noqa: ARG002
            raise ExchangeError('okx {"sCode":"51000","sMsg":"Parameter clOrdId error"}')

    engine = LiveMicroEngine(
        settings,
        executor=FailExec(settings, force_enabled=True),
    )
    engine._audit._path = audit_path  # noqa: SLF001
    assert engine.arm()["armed"] is True
    out = await engine.submit(
        {
            "venue": "bitvavo",
            "symbol": "BTCEUR",
            "side": "buy",
            "notional_eur": 25,
            "confirm": True,
        },
        confirm=True,
    )
    assert out["submitted"] is False
    assert out["reason"] == "exchange_error"
    rows = audit_path.read_text(encoding="utf-8").strip().splitlines()
    payload = __import__("json").loads(rows[-1])["payload"]
    assert payload["venue"] == "bitvavo"
    assert payload["symbol"] == "BTCEUR"
    assert payload["side"] == "buy"
    assert "clOrdId" in payload["message"]
