"""Maker emit preference: unheld bases rank above held bags."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from bot.core.enums import OpportunitySide
from bot.core.models import TradeOpportunity
from bot.strategies.maker_inventory import MakerInventoryStrategy
from tests.test_maker_inventory import _maker_settings


def test_rank_deprioritizes_held_base() -> None:
    strat = MakerInventoryStrategy(_maker_settings())
    strat._venue_held_bases = {"bitvavo": {"SOL"}}  # noqa: SLF001

    def _opp(symbol: str, net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol=symbol,
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("90"),
            expected_exit_price=Decimal("91"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": "bitvavo",
                "sell_exchange": "bitvavo",
                "net_profit_eur": net,
            },
        )

    sol = _opp("SOLEUR", "0.20")
    ada = _opp("ADAEUR", "0.08")
    assert strat._rank_opportunity(sol) < strat._rank_opportunity(ada)  # noqa: SLF001
