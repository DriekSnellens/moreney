"""Portfolio accounting and PnL tests."""

from decimal import Decimal
from uuid import uuid4

import pytest

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderSide, OrderType
from bot.core.models import OrderRequest
from bot.execution.executor import ExecutionService
from bot.portfolio.models import Fill, Order
from bot.portfolio.persistence import InMemoryPaperStore
from bot.portfolio.portfolio import PaperPortfolio
from tests.execution.conftest import make_book


@pytest.fixture
def settings() -> Settings:
    return Settings(
        execution_mode="paper",
        paper_starting_eur=200.0,
        paper_fee_rate=0.001,
        paper_slippage_mode="order_book",
        paper_simulated_latency_ms=0.0,
        paper_quote_asset="EUR",
    )


@pytest.fixture
def portfolio(settings: Settings) -> PaperPortfolio:
    return PaperPortfolio(settings, starting_eur=Decimal("200"))


@pytest.fixture
def execution(settings: Settings, portfolio: PaperPortfolio) -> ExecutionService:
    return ExecutionService(settings, portfolio=portfolio)


def _buy_req(qty: str, px: str) -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
    )


def _sell_req(qty: str, px: str) -> OrderRequest:
    return OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal(qty),
        limit_price=Decimal(px),
    )


@pytest.mark.asyncio
async def test_initial_eur_200(portfolio: PaperPortfolio) -> None:
    assert portfolio.available("EUR") == Decimal("200")
    assert portfolio.state.total_equity == Decimal("200")


@pytest.mark.asyncio
async def test_trading_fees_reduce_cash(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(_buy_req("1", "100"), order_book=make_book(ask_qty="2"))
    assert portfolio.available("EUR") == Decimal("200") - Decimal("100.1")
    assert portfolio.state.stats.fees_paid == Decimal("0.1")


@pytest.mark.asyncio
async def test_profitable_trade_realized_pnl(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(
        _buy_req("1", "100"), order_book=make_book(ask_price="100", ask_qty="2")
    )
    await execution.execute(
        _sell_req("1", "110"),
        order_book=make_book(bid_price="110", bid_qty="2", ask_price="111", ask_qty="2"),
    )
    assert portfolio.state.stats.realized_pnl == Decimal("9.79")
    assert portfolio.state.stats.winning_trades == 1
    assert portfolio.available("BTC") == Decimal("0")


@pytest.mark.asyncio
async def test_losing_trade_realized_pnl(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(
        _buy_req("1", "100"), order_book=make_book(ask_price="100", ask_qty="2")
    )
    await execution.execute(
        _sell_req("1", "90"),
        order_book=make_book(bid_price="90", bid_qty="2", ask_price="91", ask_qty="2"),
    )
    # proceeds 90 - 0.09 = 89.91; cost 100.1; pnl = -10.19
    assert portfolio.state.stats.realized_pnl == Decimal("-10.19")
    assert portfolio.state.stats.losing_trades == 1


@pytest.mark.asyncio
async def test_unrealized_pnl(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(
        _buy_req("1", "100"), order_book=make_book(ask_price="100", ask_qty="2")
    )
    portfolio.set_mark_price("BTCEUR", Decimal("105"))
    # cost basis avg includes fee: 100.1; unrealized = (105-100.1)*1 = 4.9
    assert portfolio.state.stats.unrealized_pnl == Decimal("4.9")


def test_usdt_cash_counts_in_eur_equity(portfolio: PaperPortfolio) -> None:
    from bot.portfolio.models import AssetBalance

    state = portfolio.state
    eur = state.balances["EUR"]
    # Move 40 EUR into USDT at EURUSDT=1.16 without creating a position.
    eur.available -= Decimal("40")
    state.balances["USDT"] = AssetBalance(
        asset="USDT", available=Decimal("46.4"), reserved=Decimal("0")
    )
    state.mark_prices["EURUSDT"] = Decimal("1.16")
    assert state.total_equity == Decimal("200")


def test_usdt_cash_does_not_double_count_eur_positions(portfolio: PaperPortfolio) -> None:
    from bot.portfolio.models import AssetBalance, PositionState

    state = portfolio.state
    state.balances["EUR"].available = Decimal("100")
    state.balances["XRP"] = AssetBalance(
        asset="XRP", available=Decimal("10"), reserved=Decimal("0")
    )
    state.positions["XRPEUR"] = PositionState(
        symbol="XRPEUR",
        quantity=Decimal("10"),
        average_entry_price=Decimal("10"),
    )
    state.mark_prices["XRPEUR"] = Decimal("10")
    # 100 EUR cash + 10 XRP * 10 (balances are marked; positions are not added again).
    assert state.total_equity == Decimal("200")


def test_stale_eur_lot_after_usdt_sell_does_not_inflate_equity(
    portfolio: PaperPortfolio,
) -> None:
    from bot.portfolio.models import AssetBalance, PositionState

    state = portfolio.state
    state.balances["EUR"].available = Decimal("100")
    state.balances["XRP"] = AssetBalance(
        asset="XRP", available=Decimal("40"), reserved=Decimal("0")
    )
    state.balances["USDT"] = AssetBalance(
        asset="USDT", available=Decimal("69.6"), reserved=Decimal("0")
    )
    # Sold 60 XRP on USDT; the EUR lot was never reduced (live 25k bug).
    state.positions["XRPEUR"] = PositionState(
        symbol="XRPEUR",
        quantity=Decimal("100"),
        average_entry_price=Decimal("1"),
    )
    state.positions["XRPUSDT"] = PositionState(
        symbol="XRPUSDT",
        quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    state.mark_prices["XRPEUR"] = Decimal("1")
    state.mark_prices["EURUSDT"] = Decimal("1.16")
    # Cash 100 + 40 XRP + 69.6 USDT / 1.16 = 200. Must not count the leftover 60 XRP lot.
    assert state.total_equity == Decimal("200")
    state.cap_positions_to_balances()
    assert state.positions["XRPEUR"].quantity == Decimal("40")


def test_usdt_sell_closes_seeded_eur_position(portfolio: PaperPortfolio) -> None:
    from bot.core.enums import OrderSide, OrderType
    from bot.portfolio.models import AssetBalance, Fill, Order, PositionState

    state = portfolio.state
    state.balances["EUR"].available = Decimal("100")
    state.balances["ATOM"] = AssetBalance(
        asset="ATOM", available=Decimal("100"), reserved=Decimal("0")
    )
    state.positions["ATOMEUR"] = PositionState(
        symbol="ATOMEUR",
        quantity=Decimal("100"),
        average_entry_price=Decimal("1"),
    )
    state.mark_prices["ATOMEUR"] = Decimal("1")
    state.mark_prices["EURUSDT"] = Decimal("1.16")
    order = Order(
        strategy="triangle",
        symbol="ATOMUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        requested_quantity=Decimal("40"),
        requested_price=Decimal("1.16"),
    )
    fill = Fill(
        order_id=order.id,
        symbol="ATOMUSDT",
        side=OrderSide.SELL,
        quantity=Decimal("40"),
        price=Decimal("1.16"),
        fee=Decimal("0"),
    )
    result = portfolio.apply_fill(order, fill)
    assert result.applied is True
    assert state.positions["ATOMEUR"].quantity == Decimal("60")
    assert state.balances["ATOM"].total == Decimal("60")


@pytest.mark.asyncio
async def test_multiple_sequential_trades(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(
        _buy_req("0.5", "100"), order_book=make_book(ask_price="100", ask_qty="5")
    )
    await execution.execute(
        _buy_req("0.5", "100"), order_book=make_book(ask_price="100", ask_qty="5")
    )
    assert portfolio.available("BTC") == Decimal("1")
    await execution.execute(
        _sell_req("1", "100"),
        order_book=make_book(bid_price="100", bid_qty="5", ask_price="101", ask_qty="5"),
    )
    assert portfolio.state.stats.number_of_trades == 3


@pytest.mark.asyncio
async def test_drawdown_and_max_drawdown(
    execution: ExecutionService, portfolio: PaperPortfolio
) -> None:
    await execution.execute(
        _buy_req("1", "100"), order_book=make_book(ask_price="100", ask_qty="2")
    )
    portfolio.set_mark_price("BTCEUR", Decimal("80"))
    assert portfolio.state.stats.current_drawdown > 0
    assert portfolio.state.stats.maximum_drawdown >= portfolio.state.stats.current_drawdown
    peak = portfolio.state.stats.peak_equity
    assert peak >= Decimal("200")


@pytest.mark.asyncio
async def test_duplicate_fill_protection(portfolio: PaperPortfolio) -> None:
    order = Order(
        strategy="t",
        symbol="BTCEUR",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        requested_quantity=Decimal("1"),
        requested_price=Decimal("100"),
    )
    fill = Fill(
        order_id=order.id,
        symbol="BTCEUR",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0.1"),
    )
    # Manually reserve for accounting
    assert portfolio.reserve("EUR", Decimal("100.1"))
    first = portfolio.apply_fill(order, fill)
    second = portfolio.apply_fill(order, fill)
    assert first.applied is True
    assert second.duplicate is True
    assert second.applied is False
    assert portfolio.available("BTC") == Decimal("1")


@pytest.mark.asyncio
async def test_persistence_reload(settings: Settings, portfolio: PaperPortfolio) -> None:
    execution = ExecutionService(settings, portfolio=portfolio)
    await execution.execute(
        _buy_req("0.5", "100"), order_book=make_book(ask_price="100", ask_qty="2")
    )
    store = InMemoryPaperStore()
    store.save_portfolio(portfolio)
    for order in execution.order_manager.list_orders():
        store.save_order(order)
    for fill in execution.fill_tracker.fills:
        store.save_fill(fill)

    restored = store.reload_portfolio(settings)
    assert restored.available("BTC") == Decimal("0.5")
    assert restored.available("EUR") == portfolio.available("EUR")
    # Duplicate fill after reload must not double-apply
    fill = execution.fill_tracker.fills[0]
    order = execution.order_manager.get(fill.order_id)
    assert order is not None
    result = restored.apply_fill(order, fill)
    assert result.duplicate is True
