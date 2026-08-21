"""Tests for full-bot micro session bridge (no real exchange calls)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode, OpportunitySide, OrderStatus
from bot.core.models import OrderRequest
from bot.live.micro import MicroLivePolicy
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.live.micro_session import _non_btc_symbols, attach_micro_bridge, _session_settings
from bot.portfolio.portfolio import PaperPortfolio


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_micro_engine()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_micro_engine()


def _unlocked(**kwargs: object) -> Settings:
    base = dict(
        execution_mode=ExecutionMode.PAPER,
        paper_starting_eur=25.0,
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo",
        live_micro_symbols="*",
        live_micro_max_notional_eur=25,
        automatic_withdrawals_enabled=False,
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_policy_star_allows_any_symbol() -> None:
    pol = MicroLivePolicy(_unlocked())
    ok, _ = pol.validate_order(
        venue="bitvavo",
        symbol="SOLEUR",
        notional_eur=Decimal("10"),
    )
    assert ok is True


def test_non_btc_symbols_filters() -> None:
    s = Settings(
        market_data_symbols="BTCEUR,ETHEUR,SOLEUR,BTCUSDT,ADAEUR",
    )
    assert _non_btc_symbols(s) == ["ETHEUR", "SOLEUR", "ADAEUR"]


def test_session_settings_cap_capital(tmp_path: Path) -> None:
    cfg = _session_settings(
        Settings(),
        budget_eur=Decimal("5000"),
        symbols=["ETHEUR", "SOLEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.paper_starting_eur == 5000.0
    assert cfg.live_micro_symbols == "*"
    assert cfg.risk_max_position_usd == 5000.0
    assert cfg.live_micro_max_daily_loss_eur == 500.0
    assert cfg.paper_maker_enabled is True
    assert cfg.paper_seed_usdt_pct == 0.0
    assert "BTCEUR" not in cfg.market_data_symbols


@pytest.mark.asyncio
async def test_bridge_excludes_btc_and_tracks_skip() -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
        exclude_bases={"BTC"},
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.001"),
        limit_price=Decimal("60000"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("excluded_base", 0) >= 1


@pytest.mark.asyncio
async def test_bridge_skips_non_bitvavo_venue() -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ETHEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        limit_price=Decimal("2000"),
        metadata={"venue": "binance"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("venue_not_live", 0) >= 1


@pytest.mark.asyncio
async def test_bridge_mirrors_live_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()

    async def fake_submit(payload: dict, *, confirm: bool = False) -> dict:
        assert confirm is True
        return {
            "submitted": True,
            "executed": True,
            "order": {
                "filled_quantity": "0.01",
                "average_price": "2000",
                "exchange_order_id": "test-1",
                "symbol": "ETHEUR",
                "side": "buy",
            },
        }

    monkeypatch.setattr(engine, "submit", fake_submit)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
    )
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="ETHEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.01"),
        limit_price=Decimal("2000"),
        metadata={"venue": "bitvavo"},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == Decimal("0.01")
    assert (result.metadata or {}).get("real_exchange_order") is True
    # Pocket recycles: ~€5 free after €20 buy (+ tiny fee).
    assert bridge.budget_remaining < Decimal("5.1")
    assert bridge.budget_remaining > Decimal("4.5")
    assert bridge.snapshot_bridge()["capital_model"] == "pocket"
    assert len(bridge.live_trades) == 1


def test_attach_micro_bridge_does_not_pollute_paper_runner_source() -> None:
    import inspect

    from bot.paper.runner import PaperRunner

    src = inspect.getsource(PaperRunner)
    assert "LiveMicroEngine" not in src
    assert "MicroBudgetLiveExecutor" not in src
    assert callable(attach_micro_bridge)
