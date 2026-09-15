"""Maker/taker fee calculation for buy and sell legs."""

from dataclasses import dataclass
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.exchange_types import TradingFee
from bot.core.models import TradeOpportunity


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """Fee costs split by buy/sell legs."""

    buy_fee: Decimal
    sell_fee: Decimal
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    buy_notional: Decimal
    sell_notional: Decimal

    @property
    def total_fees(self) -> Decimal:
        return self.buy_fee + self.sell_fee


class FeeCalculator:
    """Computes buy and sell fees using maker/taker rates.

    Entry and exit liquidity roles come from the opportunity (default: taker/taker).
    Optional ``TradingFee`` overrides settings for a specific market.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        trading_fee: TradingFee | None = None,
    ) -> None:
        fallback = Decimal(str(settings.profitability_fee_rate))
        maker = (
            Decimal(str(settings.profitability_maker_fee_rate))
            if settings.profitability_maker_fee_rate is not None
            else fallback
        )
        taker = (
            Decimal(str(settings.profitability_taker_fee_rate))
            if settings.profitability_taker_fee_rate is not None
            else fallback
        )
        if trading_fee is not None:
            maker = trading_fee.maker
            taker = trading_fee.taker
        self._maker_rate = maker
        self._taker_rate = taker

    @property
    def maker_rate(self) -> Decimal:
        return self._maker_rate

    @property
    def taker_rate(self) -> Decimal:
        return self._taker_rate

    def rate_for(self, role: FeeRole) -> Decimal:
        return self._maker_rate if role == FeeRole.MAKER else self._taker_rate

    def calculate(
        self,
        opportunity: TradeOpportunity,
        *,
        exit_price: Decimal,
        buy_fee_rate: Decimal | None = None,
        sell_fee_rate: Decimal | None = None,
    ) -> FeeBreakdown:
        """Calculate buy/sell fees for the round-trip implied by the opportunity."""
        entry_notional = opportunity.quantity * opportunity.entry_price
        exit_notional = opportunity.quantity * exit_price

        buy_notional, sell_notional, buy_role, sell_role = self._leg_notionals(
            opportunity.side,
            entry_notional=entry_notional,
            exit_notional=exit_notional,
            entry_role=opportunity.entry_fee_role,
            exit_role=opportunity.exit_fee_role,
        )

        buy_rate = buy_fee_rate if buy_fee_rate is not None else self.rate_for(buy_role)
        sell_rate = sell_fee_rate if sell_fee_rate is not None else self.rate_for(sell_role)
        return FeeBreakdown(
            buy_fee=buy_notional * buy_rate,
            sell_fee=sell_notional * sell_rate,
            buy_fee_rate=buy_rate,
            sell_fee_rate=sell_rate,
            buy_notional=buy_notional,
            sell_notional=sell_notional,
        )

    @staticmethod
    def _leg_notionals(
        side: OpportunitySide,
        *,
        entry_notional: Decimal,
        exit_notional: Decimal,
        entry_role: FeeRole,
        exit_role: FeeRole,
    ) -> tuple[Decimal, Decimal, FeeRole, FeeRole]:
        """Map entry/exit to buy/sell notionals and fee roles."""
        if side in {OpportunitySide.BUY, OpportunitySide.LONG}:
            # Enter buying, exit selling.
            return entry_notional, exit_notional, entry_role, exit_role
        # Enter selling / shorting, exit buying to cover.
        return exit_notional, entry_notional, exit_role, entry_role
