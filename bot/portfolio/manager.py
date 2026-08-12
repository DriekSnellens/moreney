"""Portfolio service implementations."""

from decimal import Decimal

from bot.core.models import Balance, PortfolioSnapshot, Position


class InMemoryPortfolioService:
    """Simple in-memory portfolio for scaffolding and unit tests."""

    def __init__(
        self,
        *,
        equity_usd: Decimal = Decimal("10000"),
        daily_realized_pnl_usd: Decimal = Decimal("0"),
        balances: list[Balance] | None = None,
        positions: list[Position] | None = None,
    ) -> None:
        self._equity_usd = equity_usd
        self._daily_realized_pnl_usd = daily_realized_pnl_usd
        self._balances = balances or [Balance(asset="USD", free=equity_usd)]
        self._positions = positions or []

    async def get_snapshot(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            balances=list(self._balances),
            positions=list(self._positions),
            equity_usd=self._equity_usd,
            daily_realized_pnl_usd=self._daily_realized_pnl_usd,
            open_position_count=len(self._positions),
        )

    def set_daily_pnl(self, pnl: Decimal) -> None:
        self._daily_realized_pnl_usd = pnl

    def set_positions(self, positions: list[Position]) -> None:
        self._positions = list(positions)
