"""Per-exchange inventory: paper arb cannot teleport coins."""

from decimal import Decimal
from uuid import uuid4

import pytest

from bot.core.enums import OpportunitySide, OrderStatus
from bot.core.models import OrderRequest
from bot.execution.paper_executor import PaperExecutor
from bot.portfolio.models import AssetBalance
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import VenueLedger, infer_base_asset
from tests.execution.conftest import make_book


def test_infer_base_and_quote() -> None:
    assert infer_base_asset("BTCEUR", "EUR") == "BTC"
    assert infer_base_asset("BTCUSDT", "EUR") == "BTC"


def test_ledger_buy_does_not_create_coins_on_other_venue() -> None:
    ledger = VenueLedger(["binance", "kraken"], quote="EUR", starting_quote=Decimal("200"))
    assert ledger.available("binance", "EUR") == Decimal("100")
    ledger.apply_buy("binance", base="BTC", quantity=Decimal("0.01"), quote_spent=Decimal("50"))
    assert ledger.available("binance", "BTC") == Decimal("0.01")
    assert ledger.available("kraken", "BTC") == Decimal("0")
    assert ledger.can_sell("kraken", "BTC", Decimal("0.01")) is False
    assert ledger.can_sell("binance", "BTC", Decimal("0.01")) is True


@pytest.mark.asyncio
async def test_executor_rejects_sell_on_venue_without_coins(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    portfolio.init_venue_ledger(["binance", "kraken"], starting_quote=Decimal("200"))
    executor = PaperExecutor(exec_settings, portfolio=portfolio)
    buy = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("0.5"),
        limit_price=Decimal("100"),
        metadata={"venue": "binance", "arb_leg": True},
    )
    bought = await executor.execute(buy, order_book=make_book())
    assert bought.status == OrderStatus.FILLED
    sell = OrderRequest(
        opportunity_id=buy.opportunity_id,
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("0.5"),
        limit_price=Decimal("99"),
        metadata={"venue": "kraken", "arb_leg": True},
    )
    sold = await executor.execute(sell, order_book=make_book(bid_price="99"))
    assert sold.status == OrderStatus.REJECTED
    assert sold.metadata["rejection_reason"] == "INSUFFICIENT_BALANCE"


@pytest.mark.asyncio
async def test_executor_sells_only_with_prefunded_inventory(
    exec_settings, portfolio: PaperPortfolio
) -> None:
    portfolio.init_venue_ledger(["binance", "kraken"], starting_quote=Decimal("200"))
    assert portfolio.venue_ledger is not None
    portfolio.venue_ledger.apply_buy(
        "kraken", base="BTC", quantity=Decimal("0.5"), quote_spent=Decimal("0")
    )
    portfolio.state.balances["BTC"] = AssetBalance(
        asset="BTC", available=Decimal("0.5"), reserved=Decimal("0")
    )
    executor = PaperExecutor(exec_settings, portfolio=portfolio)
    sell = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("0.5"),
        limit_price=Decimal("99"),
        metadata={"venue": "kraken", "arb_leg": True},
    )
    sold = await executor.execute(sell, order_book=make_book(bid_price="99"))
    assert sold.status == OrderStatus.FILLED
