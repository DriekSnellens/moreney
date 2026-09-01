"""Tests for micro bridge operator improvements (MTM, persist, counters)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bot.core.config import Settings, get_settings
from bot.core.enums import ExecutionMode, OpportunitySide, OrderStatus
from bot.core.models import Balance, OrderRequest
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine, reset_micro_engine
from bot.portfolio.portfolio import PaperPortfolio
from uuid import uuid4


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_micro_engine()
    monkeypatch.setenv("LIVE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_micro_engine()


def _settings(**kwargs: object) -> Settings:
    base = dict(
        execution_mode=ExecutionMode.PAPER,
        paper_starting_eur=100.0,
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo",
        live_micro_execute_venues="bitvavo",
        live_micro_bridge_persist_path=str(kwargs.pop("persist_path", "./data/test_bridge.json")),
        live_micro_long_hold_bases="ETH",
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_long_hold_excluded_from_held_alt_bases() -> None:
    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    portfolio.sync_live_balances(
        [
            Balance(asset="EUR", free=Decimal("50"), locked=Decimal("0")),
            Balance(asset="ETH", free=Decimal("0.1"), locked=Decimal("0")),
            Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
        ],
        quote_available_cap=Decimal("50"),
    )
    portfolio.set_mark_price("ETHEUR", Decimal("2000"))
    portfolio.set_mark_price("SOLEUR", Decimal("80"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="ETH", free=Decimal("0.1"), locked=Decimal("0")),
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    held = bridge._held_alt_bases()  # noqa: SLF001
    assert "ETH" not in held
    assert "SOL" in held


def test_trail_states_include_notional_and_long_hold_role() -> None:
    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="ETH", free=Decimal("0.1"), locked=Decimal("0")),
    ]
    portfolio.set_mark_price("ETHEUR", Decimal("2000"))
    bridge._cost_lots[bridge._lots_key("bitvavo", "ETH")] = [[Decimal("0.1"), Decimal("2100")]]  # noqa: SLF001
    bridge._trail_update_state("bitvavo", "ETH", cost=Decimal("2100"), mark=Decimal("2000"))  # noqa: SLF001
    states = bridge._trail_states_public()  # noqa: SLF001
    eth = states["bitvavo:ETH"]
    assert eth["role"] == "long_hold"
    assert Decimal(str(eth["notional_eur"])) == Decimal("200.00")
    assert Decimal(str(eth["unrealized_eur"])) == Decimal("-10.00")


def test_mtm_summary_splits_micro_and_long_hold() -> None:
    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    bridge._venue_raw_balances["bitvavo"] = [  # noqa: SLF001
        Balance(asset="ETH", free=Decimal("0.1"), locked=Decimal("0")),
        Balance(asset="SOL", free=Decimal("1"), locked=Decimal("0")),
    ]
    portfolio.set_mark_price("ETHEUR", Decimal("2000"))
    portfolio.set_mark_price("SOLEUR", Decimal("80"))
    bridge._trail_update_state("bitvavo", "ETH", cost=Decimal("2100"), mark=Decimal("2000"))  # noqa: SLF001
    bridge._trail_update_state("bitvavo", "SOL", cost=Decimal("81"), mark=Decimal("80"))  # noqa: SLF001
    bridge.skips["sell_below_break_even"] = 3
    bridge.skips["time_stop_below_be"] = 2
    summary = bridge._mtm_summary()  # noqa: SLF001
    assert summary["blocked_sells_session"] == "5"
    assert Decimal(summary["long_hold_notional_eur"]) == Decimal("200.00")
    assert Decimal(summary["micro_locked_notional_eur"]) == Decimal("80.00")


def test_persist_roundtrip_trail_and_resting(tmp_path: Path) -> None:
    path = tmp_path / "bridge.json"
    settings = _settings(persist_path=str(path))
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    bridge._trail[bridge._lots_key("bitvavo", "SOL")] = {  # noqa: SLF001
        "venue": "bitvavo",
        "base": "SOL",
        "cost": "80",
        "last_mark": "81",
        "soft_armed": False,
    }
    bridge._resting.append(  # noqa: SLF001
        {
            "venue": "bitvavo",
            "symbol": "SOLEUR",
            "side": "buy",
            "exchange_order_id": "oid-1",
            "quantity": Decimal("1"),
            "price": Decimal("80"),
            "strategy": "maker_inventory",
            "opportunity_id": None,
            "placed_mono": 1.0,
            "placed_at": 1_700_000_000.0,
        }
    )
    bridge.session_live_transaction_count = 4
    bridge.skips["sell_below_break_even"] = 9
    bridge.persist_runtime_state()

    bridge2 = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("100")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    assert bridge2.load_persisted_state(path) is True
    assert bridge2._trail  # noqa: SLF001
    assert len(bridge2._resting) == 1  # noqa: SLF001
    assert bridge2.session_live_transaction_count == 4
    assert bridge2.skips.get("sell_below_break_even") == 9


@pytest.mark.asyncio
async def test_mirror_live_fill_increments_session_counter_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25"))
    engine = LiveMicroEngine(settings)
    engine.arm()

    async def fake_submit(payload: dict, *, confirm: bool = False) -> dict:
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
    monkeypatch.setattr(bridge, "_venue_budget_remaining", lambda _v: Decimal("25"))
    bridge._momentum_enabled = False  # noqa: SLF001
    bridge.backfill_mirrored_count = 99
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
    assert bridge.session_live_transaction_count == 1
    assert bridge.backfill_mirrored_count == 99
    assert len(bridge.recent_live_fills) == 1
    assert bridge.recent_live_fills[0]["symbol"] == "ETHEUR"


def test_mirror_exchange_trade_increments_fill_feed() -> None:
    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("100"))
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=portfolio,
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("100"),
    )
    bridge._session_started_ms = 0
    trade = {
        "id": "t-42",
        "timestamp": 1_700_000_000_000,
        "side": "sell",
        "amount": "0.5",
        "price": "100",
        "fee": {"cost": "0.05", "currency": "EUR"},
        "order": "ord-1",
    }
    assert bridge._mirror_exchange_trade(
        venue="bitvavo",
        base="SOL",
        trade=trade,
        source="backfill",
    )
    assert bridge.session_live_fill_count == 1
    assert bridge.recent_live_fills[-1]["source"] == "backfill"
    assert bridge._mirror_exchange_trade(
        venue="bitvavo",
        base="SOL",
        trade=trade,
        source="backfill",
    ) is False
    assert bridge.session_live_fill_count == 1


def test_recent_fills_for_display_prefers_session_feed() -> None:
    from bot.live.dashboard_history import recent_fills_for_display

    diag = {
        "recent_live_fills": [
            {
                "ts": "2026-08-31T10:52:57+00:00",
                "venue": "bitvavo",
                "symbol": "SOLEUR",
                "side": "sell",
                "qty": "0.31",
                "price": "89.42",
                "notional_eur": "27.71",
                "source": "backfill",
            }
        ]
    }
    fills = recent_fills_for_display(diag)
    assert len(fills) == 1
    assert fills[0]["symbol"] == "SOLEUR"
