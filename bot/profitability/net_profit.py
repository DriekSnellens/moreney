"""NET profit estimation: fees, slippage, funding, buffer, and trade gates."""

from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import OpportunitySide
from bot.core.exchange_types import OrderBook, TradingFee
from bot.core.models import ProfitEstimate, TradeOpportunity
from bot.profitability.fee_calculator import FeeCalculator
from bot.profitability.slippage import SlippageModel

_BPS = Decimal("10000")
_ZERO = Decimal("0")


class NetProfitCalculator:
    """Builds a ``ProfitEstimate`` for every opportunity.

    ``trade_allowed`` is based solely on NET profit after all costs and on
    configured minimum absolute / percentage thresholds. Gross spread alone
    never marks a trade as allowed.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        trading_fee: TradingFee | None = None,
        fee_calculator: FeeCalculator | None = None,
        slippage_model: SlippageModel | None = None,
    ) -> None:
        self._settings = settings
        self._fees = fee_calculator or FeeCalculator(settings, trading_fee=trading_fee)
        self._slippage = slippage_model or SlippageModel(settings)
        self._funding_rate_default = Decimal(str(settings.profitability_funding_rate))
        self._apply_funding = settings.profitability_apply_funding
        self._buffer_bps = Decimal(str(settings.profitability_execution_buffer_bps))
        self._min_net_profit = Decimal(str(settings.profitability_min_net_profit_usd))
        self._min_net_return = Decimal(str(settings.profitability_min_net_return))

    def estimate(
        self,
        opportunity: TradeOpportunity,
        *,
        order_book: OrderBook | None = None,
        buy_fee_rate: Decimal | None = None,
        sell_fee_rate: Decimal | None = None,
    ) -> ProfitEstimate:
        exit_price = self._resolve_exit_price(opportunity)
        entry_notional = opportunity.quantity * opportunity.entry_price

        gross = self._gross_profit(opportunity, exit_price)
        fees = self._fees.calculate(
            opportunity,
            exit_price=exit_price,
            buy_fee_rate=buy_fee_rate,
            sell_fee_rate=sell_fee_rate,
        )
        slip = self._slippage.estimate(
            opportunity,
            exit_price=exit_price,
            order_book=order_book,
        )
        funding = self._funding_cost(opportunity, entry_notional)
        buffer = entry_notional * (self._buffer_bps / _BPS)

        net = (
            gross
            - fees.buy_fee
            - fees.sell_fee
            - slip.total_slippage
            - funding
            - buffer
        )
        net_return = net / entry_notional if entry_notional > 0 else _ZERO

        disallow_reasons = self._disallow_reasons(net=net, net_return=net_return, gross=gross)
        trade_allowed = len(disallow_reasons) == 0

        return ProfitEstimate(
            gross_profit=gross,
            buy_fee=fees.buy_fee,
            sell_fee=fees.sell_fee,
            slippage=slip.total_slippage,
            funding_cost=funding,
            execution_buffer=buffer,
            net_profit=net,
            net_return=net_return,
            trade_allowed=trade_allowed,
            disallow_reasons=disallow_reasons,
            assumptions={
                "entry_price": str(opportunity.entry_price),
                "exit_price": str(exit_price),
                "entry_notional": str(entry_notional),
                "side": opportunity.side.value,
                "entry_fee_role": opportunity.entry_fee_role.value,
                "exit_fee_role": opportunity.exit_fee_role.value,
                "buy_fee_rate": str(fees.buy_fee_rate),
                "sell_fee_rate": str(fees.sell_fee_rate),
                "maker_fee_rate": str(self._fees.maker_rate),
                "taker_fee_rate": str(self._fees.taker_rate),
                "base_slippage": str(slip.base_slippage),
                "market_impact": str(slip.market_impact),
                "thin_book_penalty": str(slip.thin_book_penalty),
                "depth_available": str(slip.depth_available),
                "depth_consumed_ratio": str(slip.depth_consumed_ratio),
                "vwap": str(slip.vwap) if slip.vwap is not None else None,
                "funding_periods": str(opportunity.funding_periods),
                "execution_buffer_bps": str(self._buffer_bps),
                "min_net_profit_usd": str(self._min_net_profit),
                "min_net_return": str(self._min_net_return),
                "gross_alone_never_allows_trade": True,
            },
        )

    def _disallow_reasons(
        self,
        *,
        net: Decimal,
        net_return: Decimal,
        gross: Decimal,
    ) -> list[str]:
        reasons: list[str] = []
        # Explicitly reject gross-only "profitability".
        if gross > 0 and net <= 0:
            reasons.append("Gross profit is positive but NET profit is not after costs")
        if net <= 0:
            reasons.append(f"NET profit {net} is not strictly positive")
        if net < self._min_net_profit:
            reasons.append(
                f"NET profit {net} below minimum absolute {self._min_net_profit}"
            )
        if net_return < self._min_net_return:
            reasons.append(
                f"NET return {net_return} below minimum {self._min_net_return}"
            )
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for reason in reasons:
            if reason not in seen:
                seen.add(reason)
                unique.append(reason)
        return unique

    @staticmethod
    def _resolve_exit_price(opportunity: TradeOpportunity) -> Decimal:
        if opportunity.expected_exit_price is not None:
            return opportunity.expected_exit_price
        # Conservative: unknown exit ⇒ flat (no gross edge).
        return opportunity.entry_price

    @staticmethod
    def _gross_profit(opportunity: TradeOpportunity, exit_price: Decimal) -> Decimal:
        delta = exit_price - opportunity.entry_price
        if opportunity.side in {OpportunitySide.SELL, OpportunitySide.SHORT}:
            delta = opportunity.entry_price - exit_price
        return opportunity.quantity * delta

    def _funding_cost(self, opportunity: TradeOpportunity, notional: Decimal) -> Decimal:
        meta = opportunity.metadata or {}
        apply_funding = self._apply_funding
        if "profitability_apply_funding" in meta:
            apply_funding = bool(meta["profitability_apply_funding"])
        if not apply_funding or opportunity.funding_periods <= 0:
            return _ZERO

        rate = self._funding_rate_default
        if opportunity.market is not None and opportunity.market.funding_rate is not None:
            rate = opportunity.market.funding_rate

        # Signed funding: longs pay positive funding; shorts receive it (negative cost).
        raw = notional * rate * opportunity.funding_periods
        if opportunity.side in {OpportunitySide.SELL, OpportunitySide.SHORT}:
            return -raw
        return raw
