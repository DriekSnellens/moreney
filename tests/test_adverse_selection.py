"""Tests for Adverse Selection Engine."""

from __future__ import annotations

from decimal import Decimal

from bot.core.enums import OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot
from bot.intelligence.adverse_selection import (
    FillQuality,
    assess_adverse_selection,
    classify_fill_quality,
    compute_microprice,
    post_fill_adverse_pct,
)


def _book(bid_amt: str = "10", ask_amt: str = "2") -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTCEUR",
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        last=Decimal("100.05"),
        order_book=OrderBook(
            symbol="BTCEUR",
            bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal(bid_amt))],
            asks=[OrderBookLevel(price=Decimal("100.1"), amount=Decimal(ask_amt))],
        ),
    )


class TestMicroprice:
    def test_microprice_weighted_by_depth(self) -> None:
        snap = _book(bid_amt="20", ask_amt="2")
        micro = compute_microprice(snap)
        assert micro is not None
        assert snap.bid <= micro <= snap.ask

    def test_thin_ask_raises_adverse_for_buy(self) -> None:
        marks = [Decimal("100"), Decimal("100.05"), Decimal("100.15")]
        out = assess_adverse_selection(
            snapshot=_book(bid_amt="5", ask_amt="0.1"),
            marks=marks,
            side=OpportunitySide.BUY,
            order_price=Decimal("100"),
        )
        assert out.adverse_selection_score >= Decimal("0.4")


class TestFillToxicity:
    def test_buy_adverse_when_price_falls(self) -> None:
        adv = post_fill_adverse_pct(
            side=OpportunitySide.BUY,
            entry_price=Decimal("100"),
            future_price=Decimal("99"),
        )
        assert adv == Decimal("0.01")

    def test_toxic_classification(self) -> None:
        assert classify_fill_quality(adverse_pct=Decimal("0.005")).value == "TOXIC_FILL"
        assert classify_fill_quality(adverse_pct=Decimal("0.0001")).value == "GOOD_FILL"
