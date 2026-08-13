"""Profitability engine: expected NET profit after all costs and thresholds."""

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, TradingFee
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.profitability.net_profit import NetProfitCalculator


class DefaultProfitabilityEngine:
    """Evaluates every opportunity via ``NetProfitCalculator``.

    ``is_profitable`` / ``trade_allowed`` reflect NET profit after fees (buy/sell,
    maker/taker), slippage (incl. depth & impact), funding, execution buffer, and
    minimum absolute / percentage thresholds — never gross spread alone.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        trading_fee: TradingFee | None = None,
        calculator: NetProfitCalculator | None = None,
    ) -> None:
        self._calculator = calculator or NetProfitCalculator(
            settings,
            trading_fee=trading_fee,
        )

    async def evaluate(
        self,
        opportunity: TradeOpportunity,
        *,
        order_book: OrderBook | None = None,
        buy_fee_rate: Decimal | None = None,
        sell_fee_rate: Decimal | None = None,
    ) -> ProfitabilityResult:
        estimate = self._calculator.estimate(
            opportunity,
            order_book=order_book,
            buy_fee_rate=buy_fee_rate,
            sell_fee_rate=sell_fee_rate,
        )
        return ProfitabilityResult(
            opportunity_id=opportunity.id,
            gross_profit_usd=estimate.gross_profit,
            buy_fee_usd=estimate.buy_fee,
            sell_fee_usd=estimate.sell_fee,
            fees_usd=estimate.total_fees,
            slippage_usd=estimate.slippage,
            funding_usd=estimate.funding_cost,
            execution_buffer_usd=estimate.execution_buffer,
            net_profit_usd=estimate.net_profit,
            net_return=estimate.net_return,
            is_profitable=estimate.trade_allowed,
            trade_allowed=estimate.trade_allowed,
            estimate=estimate,
            assumptions=estimate.assumptions,
        )
