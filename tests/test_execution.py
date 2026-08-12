"""Tests for execution layer (paper only)."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from bot.core.config import Settings
from bot.core.enums import ExecutionMode, OpportunitySide, OrderStatus, OrderType
from bot.core.exceptions import ConfigurationError, ExecutionError
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import OrderRequest
from bot.execution.executor import ExecutionService
from bot.execution.factory import create_executor
from bot.execution.live import LiveExecutor
from bot.execution.paper_executor import PaperExecutor
from bot.exchanges.stub import StubExchangeClient
from bot.portfolio.portfolio import PaperPortfolio


def _order(**kwargs) -> OrderRequest:
    base = dict(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.1"),
        limit_price=Decimal("100"),
    )
    base.update(kwargs)
    return OrderRequest(**base)  # type: ignore[arg-type]


def _book(ask_px: str = "100", ask_qty: str = "1", bid_px: str = "99", bid_qty: str = "1") -> OrderBook:
    return OrderBook(
        symbol="BTCEUR",
        asks=[OrderBookLevel(price=Decimal(ask_px), amount=Decimal(ask_qty))],
        bids=[OrderBookLevel(price=Decimal(bid_px), amount=Decimal(bid_qty))],
    )


@pytest.fixture
def paper_settings() -> Settings:
    return Settings(
        execution_mode="paper",
        paper_starting_eur=200.0,
        paper_fee_rate=0.001,
        paper_slippage_mode="order_book",
        paper_fixed_slippage_pct=0.05,
        paper_partial_fills_on_thin_book=True,
        paper_reject_on_insufficient_liquidity=False,
        paper_simulated_latency_ms=0.0,
    )


@pytest.fixture
def paper_stack(paper_settings: Settings) -> ExecutionService:
    return ExecutionService(paper_settings)


@pytest.mark.asyncio
async def test_paper_executor_simulates_fill(paper_stack: ExecutionService) -> None:
    result = await paper_stack.execute(_order(), order_book=_book())
    assert result.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}
    assert result.filled_quantity == Decimal("0.1")
    assert result.metadata.get("real_exchange_order") is False


@pytest.mark.asyncio
async def test_live_executor_disabled_by_default(settings) -> None:
    client = StubExchangeClient(settings)
    executor = LiveExecutor(client, enabled=False)
    with pytest.raises(ExecutionError, match="disabled"):
        await executor.execute(_order())


@pytest.mark.asyncio
async def test_live_executor_when_enabled(settings) -> None:
    client = StubExchangeClient(settings)
    executor = LiveExecutor(client, enabled=True)
    result = await executor.execute(_order())
    assert result.status == OrderStatus.SUBMITTED


def test_create_executor_paper(paper_settings: Settings) -> None:
    executor = create_executor(paper_settings)
    assert isinstance(executor, ExecutionService)


def test_create_executor_live_requires_credentials() -> None:
    settings = Settings(execution_mode=ExecutionMode.LIVE)
    with pytest.raises(ConfigurationError):
        create_executor(settings, exchange_client=StubExchangeClient(settings), live_enabled=True)


def test_create_executor_live_with_client() -> None:
    settings = Settings(
        execution_mode=ExecutionMode.LIVE,
        exchange_api_key=SecretStr("k"),
        exchange_api_secret=SecretStr("s"),
    )
    client = StubExchangeClient(settings)
    executor = create_executor(settings, exchange_client=client, live_enabled=True)
    assert isinstance(executor, LiveExecutor)


def test_executors_have_no_withdraw() -> None:
    assert not hasattr(PaperExecutor, "withdraw")
    assert not hasattr(LiveExecutor, "withdraw")
    assert not hasattr(ExecutionService, "withdraw")


@pytest.mark.asyncio
async def test_paper_never_uses_credentials(paper_settings: Settings) -> None:
    settings = paper_settings.model_copy(
        update={
            "exchange_api_key": SecretStr("should-not-be-used"),
            "exchange_api_secret": SecretStr("should-not-be-used"),
        }
    )
    portfolio = PaperPortfolio(settings)
    executor = PaperExecutor(settings, portfolio=portfolio)
    # Touching secrets must not be required for paper fills.
    result = await executor.execute(_order(), order_book=_book())
    assert result.metadata["real_exchange_order"] is False
    assert result.status == OrderStatus.FILLED
