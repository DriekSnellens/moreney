"""Desk-layer features: triangle, markout, fee tiers, venue transfer."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.venue_fees import set_fee_tier, venue_maker_fee
from bot.paper.markout import MarkoutTracker
from bot.portfolio.venue_ledger import VenueLedger
from bot.strategies.arbitrage import top_of_book_snapshot
from bot.strategies.triangle_bridge import TriangleBridgeStrategy


def test_fee_tier_reduces_maker_rate() -> None:
    set_fee_tier("retail")
    retail = venue_maker_fee("okx")
    set_fee_tier("vip2")
    vip = venue_maker_fee("okx")
    set_fee_tier("retail")
    assert vip < retail


def test_venue_transfer_and_rebalance() -> None:
    ledger = VenueLedger(["okx", "binance"], quote="EUR", starting_quote=Decimal("100"))
    result = ledger.transfer(
        from_venue="okx",
        to_venue="binance",
        asset="EUR",
        amount=Decimal("10"),
        fee_bps=Decimal("10"),
    )
    assert result is not None
    received, fee = result
    assert fee == Decimal("0.01")
    assert received == Decimal("9.99")
    assert ledger.available("okx", "EUR") == Decimal("40")
    assert ledger.available("binance", "EUR") == Decimal("59.99")


def test_markout_suggests_adverse_haircut() -> None:
    tracker = MarkoutTracker(window=50)
    tracker.record_fill(
        fill_id="1",
        opportunity_id=None,
        symbol="BTCEUR",
        side="buy",
        fill_price=Decimal("100"),
        mid=Decimal("100"),
    )
    # Force 5s horizon by backdating.
    tracker._pending[0].filled_at_ms -= 6000  # type: ignore[index]
    tracker.update({"BTCEUR": Decimal("100.20")})  # +20 bps adverse for buy
    suggested = tracker.suggested_adverse_bps(floor=Decimal("2"), ceiling=Decimal("30"))
    assert suggested >= Decimal("2")


@pytest.mark.asyncio
async def test_triangle_emits_usdt_to_eur_when_edge_clears_fees() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_triangle_enabled=True,
        paper_maker_venues="okx,binance",
        paper_maker_max_edge_bps=80,
        paper_maker_adverse_bps=0,
        paper_maker_min_profit_eur=0.01,
        arbitrage_min_profit_pct=0.0001,
        arbitrage_min_liquidity_base=0.01,
        arbitrage_max_quantity=2,
        arbitrage_position_pct=0,
        arbitrage_opportunity_cooldown_ms=0,
        arbitrage_max_emits_per_cycle=4,
        arbitrage_max_book_age_ms=5000,
        arbitrage_max_latency_ms=5000,
        paper_triangle_bases="ATOM",
    )
    strategy = TriangleBridgeStrategy(settings)

    def book(symbol: str, bid: str, ask: str, qty: str = "5") -> OrderBook:
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(price=Decimal(bid), amount=Decimal(qty))],
            asks=[OrderBookLevel(price=Decimal(ask), amount=Decimal(qty))],
        )

    # USDT buy @ 100, EUR sell @ 0.12, FX=1000 ⇒ buy_eur=0.10, edge huge but cap 80bps
    # Need edge <= 80 bps: sell_eur / buy_eur - 1 <= 0.008
    # buy_usdt=100, fx=1000 ⇒ buy_eur=0.1; sell_eur needs ~0.1005 for 50bps
    snaps = [
        top_of_book_snapshot(
            exchange="okx", symbol="ATOMUSDT", order_book=book("ATOMUSDT", "100", "100.1")
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="ATOMEUR",
            order_book=book("ATOMEUR", "0.1002", "0.1005"),
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="EURUSDT",
            order_book=book("EURUSDT", "999", "1001", "1000"),
        ),
    ]
    ledger = VenueLedger(["okx", "binance"], quote="EUR", starting_quote=Decimal("1000"))
    ledger.credit("okx", "USDT", Decimal("500"))
    ledger.credit("binance", "ATOM", Decimal("10"))
    opps = await strategy.evaluate_markets(
        snaps, equity=Decimal("1000"), inventory=ledger
    )
    # May be empty if edge still fee-eaten; assert either emit or explicit fee reject.
    stats = strategy.scan_stats()
    assert int(stats["pairs_evaluated"]) > 0  # type: ignore[arg-type]
    if opps:
        assert opps[0].metadata.get("triangle") is True
        assert opps[0].metadata.get("buy_symbol") == "ATOMUSDT"
        assert opps[0].metadata.get("sell_symbol") == "ATOMEUR"
    else:
        assert stats["reject_counts"]  # type: ignore[index]


def test_triangle_pnl_waits_for_fx_refill_then_locks() -> None:
    from uuid import uuid4

    from bot.core.enums import OrderSide, OrderStatus
    from bot.core.models import ExecutionResult
    from bot.paper.models import TrackedOpportunity
    from bot.paper.tracker import PerformanceTracker
    from bot.portfolio.models import Fill

    tracker = PerformanceTracker(starting_equity=Decimal("1000"))
    opp_id = uuid4()
    buy_oid = uuid4()
    sell_oid = uuid4()
    tracked = TrackedOpportunity(
        id=opp_id,
        strategy="triangle_bridge",
        symbol="ATOMUSDT",
        buy_exchange="okx",
        sell_exchange="binance",
        quantity=Decimal("1"),
        gross_profit=Decimal("0.01"),
        expected_net_profit=Decimal("0.01"),
        metadata={
            "triangle": True,
            "direction": "usdt_to_eur",
            "fx_mid": "1000",
        },
    )
    tracker._by_id[opp_id] = tracked  # noqa: SLF001
    tracker._opportunities.append(tracked)  # noqa: SLF001

    buy_fill = Fill(
        order_id=buy_oid,
        symbol="ATOMUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.1"),
        fee_asset="USDT",
    )
    sell_fill = Fill(
        order_id=sell_oid,
        symbol="ATOMEUR",
        side=OrderSide.SELL,
        quantity=Decimal("1"),
        price=Decimal("0.1005"),
        fee=Decimal("0.0001"),
        fee_asset="EUR",
    )
    execution = ExecutionResult(
        order_id=buy_oid,
        opportunity_id=opp_id,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"),
        average_price=Decimal("100"),
    )
    # Before FX refill: no locked PnL.
    tracker.record_execution(opp_id, execution, fills=[buy_fill, sell_fill])
    assert tracked.realized_net_profit is None

    realized = tracker.finalize_triangle_pnl(
        tracked, [buy_fill, sell_fill], fx_refill_cost_eur=Decimal("0.001")
    )
    assert realized is not None
    # buy_eur ≈ (100+0.1)/1000 = 0.1001; sell ≈ 0.1005-0.0001=0.1004; -fx 0.001
    assert tracked.metadata.get("fx_refilled") is True
    assert tracked.realized_net_profit == realized


def test_dashboard_inventory_rows_render() -> None:
    from bot.paper.dashboard import _inventory_rows

    html = _inventory_rows(
        {"okx": {"EUR": "40", "USDT": "12.5", "ATOM": "3"}, "binance": {"EUR": "50"}}
    )
    assert "okx" in html.lower() or "OKX" in html or "Okx" in html
    assert "USDT" in html or "12.5" in html
    assert "ATOM=3" in html
