"""Deterministic €200 paper-trading scenario (ZERO real exchange orders)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderType
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import OrderRequest
from bot.execution.executor import ExecutionService


async def run_scenario() -> dict:
    settings = Settings(
        execution_mode="paper",
        paper_starting_eur=200.0,
        paper_quote_asset="EUR",
        paper_fee_rate=0.001,
        paper_slippage_mode="order_book",
        paper_simulated_latency_ms=0.0,
        paper_partial_fills_on_thin_book=True,
    )
    execution = ExecutionService(settings)
    portfolio = execution.portfolio

    starting = {
        "EUR": str(portfolio.available("EUR")),
        "BTC": str(portfolio.available("BTC")),
        "equity": str(portfolio.state.total_equity),
    }

    buy_book = OrderBook(
        symbol="BTCEUR",
        asks=[
            OrderBookLevel(price=Decimal("100"), amount=Decimal("0.5")),
            OrderBookLevel(price=Decimal("101"), amount=Decimal("0.5")),
        ],
        bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("1"))],
    )
    buy = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("105"),
    )
    buy_result = await execution.execute(
        buy, order_book=buy_book, strategy="paper_scenario", order_type=OrderType.LIMIT
    )

    sell_book = OrderBook(
        symbol="BTCEUR",
        asks=[OrderBookLevel(price=Decimal("112"), amount=Decimal("1"))],
        bids=[
            OrderBookLevel(price=Decimal("110"), amount=Decimal("0.5")),
            OrderBookLevel(price=Decimal("109"), amount=Decimal("0.5")),
        ],
    )
    sell = OrderRequest(
        opportunity_id=uuid4(),
        symbol="BTCEUR",
        side=OpportunitySide.SELL,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
    )
    sell_result = await execution.execute(
        sell, order_book=sell_book, strategy="paper_scenario", order_type=OrderType.LIMIT
    )

    orders = [
        {
            "id": str(o.id),
            "side": o.side.value,
            "status": o.status.value,
            "requested_qty": str(o.requested_quantity),
            "filled_qty": str(o.filled_quantity),
            "avg_price": str(o.average_fill_price),
            "fee": str(o.fee),
            "slippage": str(o.slippage),
            "rejection_reason": o.rejection_reason,
        }
        for o in execution.order_manager.list_orders()
    ]
    fills = [
        {
            "id": str(f.id),
            "side": f.side.value,
            "qty": str(f.quantity),
            "price": str(f.price),
            "fee": str(f.fee),
            "slippage": str(f.slippage),
        }
        for f in execution.fill_tracker.fills
    ]

    ending = {
        "EUR": str(portfolio.available("EUR")),
        "BTC": str(portfolio.available("BTC")),
        "equity": str(portfolio.state.total_equity),
        "realized_pnl": str(portfolio.state.stats.realized_pnl),
        "fees_paid": str(portfolio.state.stats.fees_paid),
        "trades": portfolio.state.stats.number_of_trades,
        "win_rate": str(portfolio.state.stats.win_rate),
    }

    assert buy_result.metadata.get("real_exchange_order") is False
    assert sell_result.metadata.get("real_exchange_order") is False
    assert all(o["status"] != "live" for o in orders)

    return {
        "starting": starting,
        "orders": orders,
        "fills": fills,
        "buy_execution": {
            "status": buy_result.status.value,
            "filled": str(buy_result.filled_quantity),
            "avg_price": str(buy_result.average_price),
            "fee": str(buy_result.fees_usd),
            "slippage": buy_result.metadata.get("slippage"),
            "real_exchange_order": buy_result.metadata.get("real_exchange_order"),
        },
        "sell_execution": {
            "status": sell_result.status.value,
            "filled": str(sell_result.filled_quantity),
            "avg_price": str(sell_result.average_price),
            "fee": str(sell_result.fees_usd),
            "slippage": sell_result.metadata.get("slippage"),
            "real_exchange_order": sell_result.metadata.get("real_exchange_order"),
        },
        "ending": ending,
        "real_exchange_orders_placed": 0,
    }


def main() -> None:
    result = asyncio.run(run_scenario())
    print("=== PAPER TRADING SCENARIO (€200) ===")
    print(f"Starting balance: {result['starting']}")
    print(f"Buy:  {result['buy_execution']}")
    print(f"Sell: {result['sell_execution']}")
    print(f"Orders ({len(result['orders'])}):")
    for order in result["orders"]:
        print(f"  - {order}")
    print(f"Fills ({len(result['fills'])}):")
    for fill in result["fills"]:
        print(f"  - {fill}")
    print(f"Ending: {result['ending']}")
    print(f"Real exchange orders placed: {result['real_exchange_orders_placed']}")
    print("Confirmed: ZERO real exchange orders were placed.")


if __name__ == "__main__":
    main()
