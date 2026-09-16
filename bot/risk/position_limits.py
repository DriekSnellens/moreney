"""Position and exposure limit calculations (Decimal-based, no leverage)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bot.core.config import Settings
from bot.core.models import PortfolioSnapshot, TradeOpportunity

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PositionLimitResult:
    """Outcome of applying position / exposure caps to a trade."""

    requested_notional: Decimal
    allowed_notional: Decimal
    allowed_quantity: Decimal
    max_by_absolute: Decimal
    max_by_percent: Decimal
    remaining_exposure_capacity: Decimal
    breached_codes: list[str]


class PositionLimitCalculator:
    """Computes size caps without leverage and without mutating portfolio PnL."""

    def __init__(self, settings: Settings) -> None:
        self._max_position_abs = Decimal(str(settings.risk_max_position_usd))
        self._max_position_pct = Decimal(str(settings.max_position_percent))
        self._max_exposure_pct = Decimal(str(settings.max_total_exposure_percent))
        self._max_open = (
            settings.max_simultaneous_positions
            if settings.max_simultaneous_positions is not None
            else settings.risk_max_open_positions
        )

    @property
    def max_simultaneous_positions(self) -> int:
        return self._max_open

    def trade_notional(self, opportunity: TradeOpportunity) -> Decimal:
        return opportunity.quantity * opportunity.entry_price

    def max_notional_by_percent(self, equity: Decimal) -> Decimal:
        if equity <= 0:
            return _ZERO
        return equity * (self._max_position_pct / _HUNDRED)

    def max_total_exposure(self, equity: Decimal) -> Decimal:
        if equity <= 0:
            return _ZERO
        return equity * (self._max_exposure_pct / _HUNDRED)

    def evaluate(
        self,
        opportunity: TradeOpportunity,
        portfolio: PortfolioSnapshot,
    ) -> PositionLimitResult:
        requested = self.trade_notional(opportunity)
        equity = portfolio.equity_usd
        max_by_pct = self.max_notional_by_percent(equity)
        max_by_abs = self._max_position_abs
        per_trade_cap = min(max_by_abs, max_by_pct) if max_by_pct > 0 else max_by_abs

        current_exposure = self._effective_exposure(opportunity, portfolio)
        max_exposure = self.max_total_exposure(equity)
        remaining = max(max_exposure - current_exposure, _ZERO)

        allowed_notional = min(per_trade_cap, remaining)
        breached: list[str] = []

        if requested > max_by_abs:
            breached.append("MAX_POSITION_SIZE")
        if max_by_pct > 0 and requested > max_by_pct:
            breached.append("MAX_POSITION_PERCENT")
        if requested > remaining:
            breached.append("MAX_TOTAL_EXPOSURE")

        if opportunity.entry_price > 0:
            allowed_qty = allowed_notional / opportunity.entry_price
        else:
            allowed_qty = _ZERO

        # Never increase size beyond the request.
        if allowed_qty > opportunity.quantity:
            allowed_qty = opportunity.quantity
            allowed_notional = requested

        return PositionLimitResult(
            requested_notional=requested,
            allowed_notional=allowed_notional,
            allowed_quantity=allowed_qty,
            max_by_absolute=max_by_abs,
            max_by_percent=max_by_pct,
            remaining_exposure_capacity=remaining,
            breached_codes=breached,
        )

    @staticmethod
    def _effective_exposure(
        opportunity: TradeOpportunity,
        portfolio: PortfolioSnapshot,
    ) -> Decimal:
        """Exposure for risk caps.

        Cross-exchange round-trip arb completes buy+sell in one cycle, so stale
        inventory must not permanently consume the exposure budget. Use the larger
        of correlated BTC notionals (net directional risk) instead of summing all
        open positions.
        """
        meta = opportunity.metadata or {}
        round_trip = opportunity.strategy_name in {
            "cross_exchange_arbitrage",
            "maker_inventory",
            "triangle_bridge",
        } or (meta.get("post_only") and meta.get("round_trip"))
        if round_trip and (meta.get("sell_exchange") or meta.get("post_only")):
            if not portfolio.positions:
                return _ZERO
            notionals = [
                abs(p.quantity * p.average_entry_price)
                for p in portfolio.positions
                if p.quantity > 0
            ]
            return max(notionals, default=_ZERO)
        return portfolio.gross_exposure_usd

    def daily_loss_limit(self, equity: Decimal, settings: Settings) -> Decimal:
        pct_limit = equity * (Decimal(str(settings.max_daily_loss_percent)) / _HUNDRED)
        abs_limit = Decimal(str(settings.risk_max_daily_loss_usd))
        return min(pct_limit, abs_limit) if equity > 0 else abs_limit

    def drawdown_fraction(self, portfolio: PortfolioSnapshot) -> Decimal:
        peak = portfolio.peak_equity
        if peak <= 0:
            return _ZERO
        drawdown = peak - portfolio.equity_usd
        if drawdown <= 0:
            return _ZERO
        return drawdown / peak
