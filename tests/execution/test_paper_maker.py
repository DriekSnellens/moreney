"""Post-only maker quoting in the paper executor."""

from decimal import Decimal
from uuid import uuid4

import pytest

from bot.core.enums import OpportunitySide, OrderStatus
from bot.core.models import OrderRequest
from bot.execution.paper_executor import PaperExecutor
from bot.portfolio.models import AssetBalance
from bot.portfolio.portfolio import PaperPortfolio
from tests.execution.conftest import make_book


def _maker_buy(
    qty: str = "0.1",
    px: str = "99",
    *,
    venue: str = "binance",
) -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
        metadata={"venue": venue, "post_only": True, "fee_role": "maker"},
    )


def _maker_sell(
    qty: str = "0.1",
    px: str = "101",
    *,
    venue: str = "kraken",
) -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
        metadata={"venue": venue, "post_only": True, "fee_role": "maker"},
    )


@pytest.mark.asyncio
async def test_post_only_rests_when_not_marketable(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(update={"paper_maker_rest_ms": 0})
    executor = PaperExecutor(settings, portfolio=portfolio)
    book = make_book(ask_price="100", bid_price="99")
    result = await executor.execute(_maker_buy("0.1", "99"), order_book=book)
    assert result.status == OrderStatus.OPEN
    assert result.filled_quantity == Decimal("0")
    assert portfolio.available("EUR") < Decimal("200")  # reserved


@pytest.mark.asyncio
async def test_post_only_rejects_if_it_would_take(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    executor = PaperExecutor(exec_settings, portfolio=portfolio)
    book = make_book(ask_price="100", bid_price="99")
    result = await executor.execute(_maker_buy("0.1", "100"), order_book=book)
    assert result.status == OrderStatus.REJECTED
    assert result.metadata["rejection_reason"] == "WOULD_TAKE"
    assert portfolio.available("EUR") == Decimal("200")


@pytest.mark.asyncio
async def test_match_resting_fills_traded_through(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(
        update={"paper_maker_rest_ms": 10_000, "paper_maker_trade_through_fill_pct": 1.0}
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    rest_book = make_book(ask_price="100", bid_price="99")
    placed = await executor.execute(_maker_buy("0.1", "99"), order_book=rest_book)
    assert placed.status == OrderStatus.OPEN
    through = make_book(ask_price="99.5", bid_price="98.5")
    fills = executor.match_resting({"binance": {"BTCEUR": through}})
    assert len(fills) == 1
    assert fills[0].status == OrderStatus.FILLED
    assert fills[0].filled_quantity == Decimal("0.1")
    assert fills[0].average_price == Decimal("99")
    assert fills[0].fees_usd == Decimal("0.1") * Decimal("99") * Decimal("0.001")
    assert portfolio.available("BTC") == Decimal("0.1")


@pytest.mark.asyncio
async def test_match_resting_fills_at_touch_after_rest(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(
        update={"paper_maker_rest_ms": 0, "paper_maker_queue_fill_pct": 1.0}
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    book = make_book(ask_price="100", bid_price="99", bid_qty="5")
    placed = await executor.execute(_maker_buy("0.1", "99"), order_book=book)
    assert placed.status == OrderStatus.OPEN
    fills = executor.match_resting({"binance": {"BTCEUR": book}})
    assert len(fills) == 1
    assert fills[0].status == OrderStatus.FILLED
    assert fills[0].average_price == Decimal("99")


@pytest.mark.asyncio
async def test_at_touch_does_not_fill_when_queue_pct_zero(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(
        update={
            "paper_maker_rest_ms": 0,
            "paper_maker_queue_fill_pct": 0.0,
            "paper_maker_trade_through_fill_pct": 0.2,
        }
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    book = make_book(ask_price="100", bid_price="99", bid_qty="5")
    placed = await executor.execute(_maker_buy("0.1", "99"), order_book=book)
    assert placed.status == OrderStatus.OPEN
    fills = executor.match_resting({"binance": {"BTCEUR": book}})
    assert fills == []


@pytest.mark.asyncio
async def test_cancel_post_only_releases_reservation(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    executor = PaperExecutor(exec_settings, portfolio=portfolio)
    book = make_book(ask_price="100", bid_price="99")
    placed = await executor.execute(_maker_buy("0.1", "99"), order_book=book)
    assert placed.status == OrderStatus.OPEN
    cancelled = await executor.cancel(placed.order_id, reason="expired")
    assert cancelled.status == OrderStatus.CANCELLED
    assert portfolio.available("EUR") == Decimal("200")


@pytest.mark.asyncio
async def test_maker_sell_uses_prefunded_inventory(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(
        update={"paper_maker_rest_ms": 0, "paper_maker_queue_fill_pct": 1.0}
    )
    portfolio.init_venue_ledger(["binance", "kraken"], starting_quote=Decimal("200"))
    assert portfolio.venue_ledger is not None
    portfolio.venue_ledger.credit("kraken", "BTC", Decimal("0.1"))
    portfolio.state.balances["BTC"] = AssetBalance(
        asset="BTC", available=Decimal("0.1"), reserved=Decimal("0")
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    book = make_book(ask_price="101", bid_price="99")
    placed = await executor.execute(_maker_sell("0.1", "101"), order_book=book)
    assert placed.status == OrderStatus.OPEN
    fills = executor.match_resting({"kraken": {"BTCEUR": book}})
    assert len(fills) == 1
    assert fills[0].status == OrderStatus.FILLED
    assert fills[0].fees_usd == Decimal("0.1") * Decimal("101") * Decimal("0.0016")
