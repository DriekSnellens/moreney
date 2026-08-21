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
        budget_eur=Decimal("2024"),
        symbols=["ETHEUR", "SOLEUR"],
        persist_path=tmp_path / "state.json",
    )
    assert cfg.paper_starting_eur == 2024.0
    assert cfg.live_micro_symbols == "*"
    assert cfg.risk_max_position_usd == 2024.0
    assert cfg.live_micro_max_daily_loss_eur == 202.4
    assert cfg.global_max_venue_exposure_pct == 100.0
    assert cfg.paper_maker_enabled is True
    assert cfg.paper_venue_inventory is True
    assert cfg.paper_seed_usdt_pct == 0.0
    assert "BTCEUR" not in cfg.market_data_symbols


def test_portfolio_sync_live_balances_caps_quote() -> None:
    from bot.core.models import Balance

    settings = _unlocked(paper_starting_eur=2024.0)
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("2024"))
    mapped = portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("1623.39"), locked=Decimal("100")),
            Balance(asset="NEAR", free=Decimal("121.9"), locked=Decimal("0")),
            Balance(asset="ATOM", free=Decimal("147.39"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("2024"),
    )
    assert portfolio.available("EUR") == Decimal("1623.39")
    assert portfolio.reserved("EUR") == Decimal("100")
    assert portfolio.available("NEAR") == Decimal("121.9")
    assert "NEAREUR" in portfolio.state.positions
    assert portfolio.state.positions["NEAREUR"].quantity == Decimal("121.9")
    assert "EUR" in mapped


@pytest.mark.asyncio
async def test_bridge_skips_sell_without_live_base(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.core.models import Balance

    settings = _unlocked()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    # Paper thinks it holds NEAR, but live free is 0.
    portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("25"), locked=Decimal("0")),
            Balance(asset="NEAR", free=Decimal("10"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("25"),
    )
    engine = LiveMicroEngine(settings)
    engine.arm()
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
        live_maker=True,
    )

    async def no_near(_venue: str, asset: str) -> Decimal:
        return Decimal("0") if asset == "NEAR" else Decimal("25")

    monkeypatch.setattr(bridge, "_live_free", no_near)
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="NEAREUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("10"),
        limit_price=Decimal("1.65"),
        metadata={"venue": "bitvavo", "post_only": True},
    )
    result = await bridge.execute(req)
    assert result.status == OrderStatus.REJECTED
    assert bridge.skips.get("insufficient_live_base", 0) >= 1


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

    async def fake_live_free(_venue: str, asset: str) -> Decimal:
        return Decimal("25") if asset.upper() == "EUR" else Decimal("0")

    monkeypatch.setattr(engine, "submit", fake_submit)
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=engine,
        budget_eur=Decimal("25"),
    )
    monkeypatch.setattr(bridge, "_live_free", fake_live_free)
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
