"""Paper executor behaviour tests."""

from decimal import Decimal
from uuid import uuid4

import pytest

from bot.core.enums import OpportunitySide, OrderStatus, OrderType
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import OrderRequest
from bot.execution.executor import ExecutionService
from bot.portfolio.portfolio import PaperPortfolio
from tests.execution.conftest import make_book


def _buy(qty: str = "0.5", px: str = "100") -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
    )


def _sell(qty: str = "0.5", px: str = "110") -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
    )


@pytest.mark.asyncio
async def test_starting_balance_200_eur(portfolio: PaperPortfolio) -> None:
    assert portfolio.available("EUR") == Decimal("200")
    assert portfolio.available("BTC") == Decimal("0")
    snap = await portfolio.get_snapshot()
    assert snap.equity_usd == Decimal("200")


@pytest.mark.asyncio
async def test_full_fill(execution: ExecutionService) -> None:
    result = await execution.execute(_buy("0.5", "100"), order_book=make_book())
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == Decimal("0.5")
    assert result.average_price == Decimal("100")
    assert result.fees_usd == Decimal("0.5") * Decimal("100") * Decimal("0.001")


@pytest.mark.asyncio
async def test_partial_fill_on_thin_book(execution: ExecutionService) -> None:
    book = make_book(ask_qty="0.2")
    result = await execution.execute(_buy("1.0", "100"), order_book=book)
    assert result.status == OrderStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.2")


@pytest.mark.asyncio
async def test_reject_insufficient_liquidity_when_configured(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    settings = exec_settings.model_copy(
        update={
            "paper_partial_fills_on_thin_book": False,
            "paper_reject_on_insufficient_liquidity": True,
        }
    )
    from bot.execution.paper_executor import PaperExecutor

    executor = PaperExecutor(settings, portfolio=portfolio)
    book = make_book(ask_qty="0.1")
    result = await executor.execute(_buy("1.0", "100"), order_book=book)
    assert result.status == OrderStatus.REJECTED
    assert result.metadata["rejection_reason"] == "INSUFFICIENT_LIQUIDITY"


@pytest.mark.asyncio
async def test_insufficient_balance(execution: ExecutionService) -> None:
    # 3 BTC * 100 EUR > 200 EUR starting capital
    result = await execution.execute(_buy("3", "100"), order_book=make_book(ask_qty="10"))
    assert result.status == OrderStatus.REJECTED
    assert result.metadata["rejection_reason"] == "INSUFFICIENT_BALANCE"


@pytest.mark.asyncio
async def test_fixed_slippage(exec_settings, portfolio: PaperPortfolio) -> None:
    settings = exec_settings.model_copy(
        update={"paper_slippage_mode": "fixed", "paper_fixed_slippage_pct": 1.0}
    )
    from bot.execution.paper_executor import PaperExecutor

    executor = PaperExecutor(settings, portfolio=portfolio)
    result = await executor.execute(_buy("0.1", "100"), order_book=None, order_type=OrderType.MARKET)
    assert result.status == OrderStatus.FILLED
    assert result.average_price == Decimal("101")  # +1%


@pytest.mark.asyncio
async def test_order_book_slippage_walk(execution: ExecutionService) -> None:
    book = OrderBook(
        symbol="BTCEUR",
        asks=[
            OrderBookLevel(price=Decimal("100"), amount=Decimal("0.25")),
            OrderBookLevel(price=Decimal("102"), amount=Decimal("0.25")),
        ],
        bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("1"))],
    )
    result = await execution.execute(_buy("0.5", "110"), order_book=book)
    assert result.filled_quantity == Decimal("0.5")
    assert result.average_price == Decimal("101")  # (100*0.25 + 102*0.25)/0.5


@pytest.mark.asyncio
async def test_cancellation(execution: ExecutionService) -> None:
    # Non-marketable limit rests as OPEN
    book = make_book(ask_price="105")
    result = await execution.execute(_buy("0.1", "100"), order_book=book)
    assert result.status == OrderStatus.OPEN
    cancelled = await execution.paper.cancel(result.order_id)
    assert cancelled.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_rejected_order_does_not_change_portfolio(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    before = portfolio.available("EUR")
    await execution.execute(_buy("5", "100"), order_book=make_book())
    assert portfolio.available("EUR") == before


@pytest.mark.asyncio
async def test_zero_real_exchange_orders_flag(execution: ExecutionService) -> None:
    result = await execution.execute(_buy("0.1", "100"), order_book=make_book())
    assert result.metadata["real_exchange_order"] is False
    assert execution.paper.name == "paper"
