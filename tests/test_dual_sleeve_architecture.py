"""Tests for dual-sleeve profit architecture (S1 unlock + S2 CVD limited)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import Balance, MarketSnapshot, OrderRequest
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
from bot.live.micro_engine import LiveMicroEngine
from bot.paper.cvd_candidate import create_cvd_candidates
from bot.portfolio.portfolio import PaperPortfolio


def _unlocked(**kwargs: object) -> Settings:
    base = dict(
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_disable_research_hooks=True,
    )
    base.update(kwargs)
    return Settings(**base)


def test_cvd_candidate_tags_s2_sleeve() -> None:
    book_a = OrderBook(
        symbol="SOLEUR",
        bids=[OrderBookLevel(price=Decimal("101"), amount=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("101.2"), amount=Decimal("10"))],
    )
    book_b = OrderBook(
        symbol="SOLEUR",
        bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("100.2"), amount=Decimal("10"))],
    )
    snaps = [
        MarketSnapshot(
            symbol="SOLEUR",
            exchange="okx",
            bid=Decimal("101"),
            ask=Decimal("101.2"),
            last=Decimal("101.1"),
            order_book=book_a,
        ),
        MarketSnapshot(
            symbol="SOLEUR",
            exchange="bitvavo",
            bid=Decimal("100"),
            ask=Decimal("100.2"),
            last=Decimal("100.1"),
            order_book=book_b,
        ),
    ]
    opps = create_cvd_candidates(snaps)
    assert opps
    assert opps[0].metadata.get("sleeve") == "S2"
    assert opps[0].metadata.get("profit_sleeve") == "S2"
    assert opps[0].metadata.get("frozen_cvd") is True


def test_cvd_buy_rejected_when_limited_hard_off(tmp_path: Path) -> None:
    settings = _unlocked(
        live_cvd_limited_enabled=False,
        live_micro_bridge_persist_path=str(tmp_path / "cvd_gate.json"),
        live_micro_execute_venues="bitvavo",
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    bridge._bal_cache["bitvavo"] = [  # noqa: SLF001
        Balance(asset="EUR", free=Decimal("1500"), locked=Decimal("0")),
    ]
    bridge._venue_raw_balances["bitvavo"] = bridge._bal_cache["bitvavo"]  # noqa: SLF001
    req = OrderRequest(
        opportunity_id=uuid4(),
        symbol="SOLEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        metadata={
            "venue": "bitvavo",
            "frozen_cvd": True,
            "sleeve": "S2",
            "notional_eur": 100,
        },
    )

    asyncio.run(bridge.execute(req, strategy="cross_venue_dislocation"))
    assert bridge.skips.get("cvd_limited_disabled", 0) >= 1


def test_snapshot_exposes_dual_sleeve_fields(tmp_path: Path) -> None:
    settings = _unlocked(
        live_cvd_limited_enabled=False,
        live_cvd_risk_sleeve_eur=500.0,
        live_desk_daily_loss_cap_eur=75.0,
        live_micro_ring_util_b_ignore_underwater=True,
        live_micro_bridge_persist_path=str(tmp_path / "snap.json"),
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    snap = bridge.snapshot_bridge()
    assert snap["cvd_limited_enabled"] is False
    assert Decimal(str(snap["cvd_risk_sleeve_eur"])) == Decimal("500")
    assert Decimal(str(snap["desk_daily_loss_cap_eur"])) == Decimal("75")
    assert snap["s1_target_low_eur"] == "20"
    assert snap["desk_target_high_eur"] == "100"
    assert snap["trail_take_profit"]["ring_util_b_ignore_underwater"] is True
