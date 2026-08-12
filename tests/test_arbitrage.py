"""Unit tests for cross-exchange arbitrage with synthetic order books."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot
from bot.strategies.arbitrage import (
    CrossExchangeArbitrageStrategy,
    top_of_book_snapshot,
    walk_book,
)


def _arb_settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        app_env="development",
        execution_mode="paper",
        profitability_fee_rate=0.001,
        profitability_maker_fee_rate=0.0008,
        profitability_taker_fee_rate=0.001,
        profitability_slippage_bps=5.0,
        profitability_market_impact_factor=1.0,
        profitability_thin_book_penalty_bps=25.0,
        profitability_funding_rate=0.0,
        profitability_apply_funding=False,
        profitability_execution_buffer_bps=10.0,
        profitability_min_net_profit_usd=1.0,
        profitability_min_net_return=0.001,
        arbitrage_min_profit_eur=1.0,
        arbitrage_min_profit_pct=0.001,
        arbitrage_min_liquidity_base=0.01,
        arbitrage_max_quantity=1.0,
        arbitrage_max_latency_ms=500.0,
        arbitrage_max_book_age_ms=1000.0,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _book(
    *,
    symbol: str = "BTCEUR",
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    age_ms: float = 0.0,
) -> OrderBook:
    ts = datetime.now(UTC) - timedelta(milliseconds=age_ms)
    return OrderBook(
        symbol=symbol,
        bids=[OrderBookLevel(price=Decimal(p), amount=Decimal(a)) for p, a in bids],
        asks=[OrderBookLevel(price=Decimal(p), amount=Decimal(a)) for p, a in asks],
        timestamp=ts,
    )


def _cheap_asks() -> OrderBook:
    """Venue where we can buy cheaply."""
    return _book(
        bids=[("99", "2")],
        asks=[("100", "0.5"), ("100.5", "0.5"), ("101", "2")],
    )


def _rich_bids() -> OrderBook:
    """Venue where we can sell expensively (clear arb vs cheap asks)."""
    return _book(
        bids=[("120", "0.5"), ("119", "0.5"), ("118", "2")],
        asks=[("121", "2")],
    )


def _flat_books() -> tuple[OrderBook, OrderBook]:
    a = _book(bids=[("100", "1")], asks=[("100.1", "1")])
    b = _book(bids=[("100.05", "1")], asks=[("100.15", "1")])
    return a, b


# ---------------------------------------------------------------------------
# Depth helper
# ---------------------------------------------------------------------------


def test_walk_book_computes_vwap_across_levels() -> None:
    book = _cheap_asks()
    fill = walk_book(book.asks, Decimal("1"))
    # 0.5@100 + 0.5@100.5 = 100.25 VWAP
    assert fill.sufficient is True
    assert fill.filled_quantity == Decimal("1")
    assert fill.vwap == Decimal("100.25")
    assert fill.levels_consumed == 2


def test_walk_book_insufficient_depth() -> None:
    book = _book(bids=[("100", "0.2")], asks=[("101", "0.2")])
    fill = walk_book(book.asks, Decimal("1"))
    assert fill.sufficient is False
    assert fill.filled_quantity == Decimal("0.2")


# ---------------------------------------------------------------------------
# Strategy behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emits_opportunity_when_net_profit_clears_thresholds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _arb_settings(
        arbitrage_min_profit_eur=1.0,
        arbitrage_min_profit_pct=0.001,
        # Keep costs low so large depth edge clears NET gates.
        profitability_fee_rate=0.0001,
        profitability_maker_fee_rate=0.0001,
        profitability_taker_fee_rate=0.0001,
        profitability_slippage_bps=1.0,
        profitability_execution_buffer_bps=1.0,
    )
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=_cheap_asks()),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert len(opps) >= 1
    opp = opps[0]
    assert opp.strategy_name == "cross_exchange_arbitrage"
    assert opp.side == OpportunitySide.BUY
    assert opp.entry_price == Decimal("100.25")  # depth VWAP, not top ask 100
    assert opp.expected_exit_price == Decimal("119.5")  # 0.5@120 + 0.5@119
    assert opp.metadata["buy_exchange"] == "binance"
    assert opp.metadata["sell_exchange"] == "kraken"
    assert opp.metadata["pricing"] == "order_book_depth_vwap"
    assert "arbitrage opportunity accepted" in caplog.text


@pytest.mark.asyncio
async def test_rejects_when_below_min_profit_eur(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings(
        arbitrage_min_profit_eur=10_000.0,  # impossible for qty=1
        arbitrage_min_profit_pct=0.0,
        profitability_fee_rate=0.0,
        profitability_maker_fee_rate=0.0,
        profitability_taker_fee_rate=0.0,
        profitability_slippage_bps=0.0,
        profitability_execution_buffer_bps=0.0,
    )
    # Engine mins also mapped from EUR setting in strategy constructor.
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=_cheap_asks()),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "rejected" in caplog.text
    assert "profitability" in caplog.text or "min_profit_eur" in caplog.text


@pytest.mark.asyncio
async def test_rejects_when_below_min_profit_pct(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings(
        arbitrage_min_profit_eur=0.0,
        arbitrage_min_profit_pct=0.90,  # 90% return required
        profitability_fee_rate=0.0,
        profitability_maker_fee_rate=0.0,
        profitability_taker_fee_rate=0.0,
        profitability_slippage_bps=0.0,
        profitability_execution_buffer_bps=0.0,
    )
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=_cheap_asks()),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "rejected" in caplog.text


@pytest.mark.asyncio
async def test_rejects_insufficient_liquidity(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings(arbitrage_min_liquidity_base=5.0)
    thin = _book(bids=[("120", "0.1")], asks=[("100", "0.1")])
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=thin),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=_rich_bids()),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "insufficient_liquidity" in caplog.text


@pytest.mark.asyncio
async def test_rejects_high_latency(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings(arbitrage_max_latency_ms=50.0)
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(
            exchange="binance",
            symbol="BTCEUR",
            order_book=_cheap_asks(),
            latency_ms=10.0,
        ),
        top_of_book_snapshot(
            exchange="kraken",
            symbol="BTCEUR",
            order_book=_rich_bids(),
            latency_ms=250.0,
        ),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "latency" in caplog.text


@pytest.mark.asyncio
async def test_rejects_stale_order_book(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings(arbitrage_max_book_age_ms=100.0)
    strategy = CrossExchangeArbitrageStrategy(settings)
    stale = _book(
        bids=[("120", "1")],
        asks=[("100", "1")],
        age_ms=5_000.0,
    )
    fresh = _rich_bids()
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=stale),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=fresh),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "stale_book" in caplog.text


@pytest.mark.asyncio
async def test_rejects_when_no_depth_edge(caplog: pytest.LogCaptureFixture) -> None:
    settings = _arb_settings()
    strategy = CrossExchangeArbitrageStrategy(settings)
    a, b = _flat_books()
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=a),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=b),
    ]

    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)

    assert opps == []
    assert "no_depth_edge" in caplog.text or "insufficient_venues" not in caplog.text


@pytest.mark.asyncio
async def test_single_snapshot_evaluate_emits_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    strategy = CrossExchangeArbitrageStrategy(_arb_settings())
    snap = top_of_book_snapshot(
        exchange="binance",
        symbol="BTCEUR",
        order_book=_cheap_asks(),
    )
    with caplog.at_level(logging.INFO):
        assert await strategy.evaluate(snap) == []
    assert "single_snapshot" in caplog.text


@pytest.mark.asyncio
async def test_uses_depth_not_ticker_top_of_book() -> None:
    """Top ask is 100, but filling 1.0 requires deeper levels → VWAP 100.25."""
    settings = _arb_settings(
        arbitrage_min_profit_eur=0.5,
        arbitrage_min_profit_pct=0.001,
        profitability_fee_rate=0.0001,
        profitability_maker_fee_rate=0.0001,
        profitability_taker_fee_rate=0.0001,
        profitability_slippage_bps=1.0,
        profitability_execution_buffer_bps=1.0,
    )
    strategy = CrossExchangeArbitrageStrategy(settings)
    buy_book = _cheap_asks()
    sell_book = _rich_bids()
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=buy_book),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=sell_book),
    ]
    opps = await strategy.evaluate_markets(snapshots)
    assert opps
    assert snapshots[0].ask == Decimal("100")  # ticker top
    assert opps[0].entry_price > snapshots[0].ask  # depth VWAP worse than top


@pytest.mark.asyncio
async def test_strategy_does_not_execute_or_import_exchanges() -> None:
    import inspect

    import bot.strategies.arbitrage as arb_mod

    source = inspect.getsource(arb_mod)
    assert "bot.exchanges" not in source
    assert "Executor" not in source
    assert "place_order" not in source
    assert "LiveExecutor" not in source


@pytest.mark.asyncio
async def test_positive_gross_spread_alone_not_enough(caplog: pytest.LogCaptureFixture) -> None:
    """Tiny depth edge that is positive gross but fails NET costs/thresholds."""
    settings = _arb_settings(
        arbitrage_min_profit_eur=0.0,
        arbitrage_min_profit_pct=0.0,
        profitability_fee_rate=0.02,
        profitability_maker_fee_rate=0.02,
        profitability_taker_fee_rate=0.02,
        profitability_slippage_bps=100.0,
        profitability_execution_buffer_bps=100.0,
    )
    # Small edge: buy ~100, sell ~100.5
    buy = _book(bids=[("99", "1")], asks=[("100", "1")])
    sell = _book(bids=[("100.5", "1")], asks=[("101", "1")])
    strategy = CrossExchangeArbitrageStrategy(settings)
    snapshots = [
        top_of_book_snapshot(exchange="binance", symbol="BTCEUR", order_book=buy),
        top_of_book_snapshot(exchange="kraken", symbol="BTCEUR", order_book=sell),
    ]
    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets(snapshots)
    assert opps == []
    assert "profitability" in caplog.text


@pytest.mark.asyncio
async def test_missing_order_book_is_rejected(caplog: pytest.LogCaptureFixture) -> None:
    strategy = CrossExchangeArbitrageStrategy(_arb_settings())
    snap = MarketSnapshot(
        symbol="BTCEUR",
        bid=Decimal("100"),
        ask=Decimal("101"),
        last=Decimal("100.5"),
        exchange="binance",
        order_book=None,
        latency_ms=10.0,
    )
    with caplog.at_level(logging.INFO):
        opps = await strategy.evaluate_markets([snap, snap.model_copy(update={"exchange": "kraken"})])
    assert opps == []
    assert "missing_order_book" in caplog.text
