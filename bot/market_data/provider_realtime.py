"""Realtime market-data provider wrapping ``MarketDataService``.

Exposes normalized ``MarketSnapshot`` objects only — never raw exchange messages.
"""

from __future__ import annotations

from collections.abc import Sequence

from bot.core.exceptions import ExchangeError
from bot.core.exchange_types import OrderBook
from bot.core.models import MarketSnapshot
from bot.market_data.service import MarketDataService


class RealtimeMarketDataProvider:
    """``MarketDataProvider`` backed by synchronized public market-data feeds."""

    def __init__(self, service: MarketDataService) -> None:
        self._service = service

    @property
    def service(self) -> MarketDataService:
        return self._service

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        snapshots = self._service.snapshots_for_arbitrage(symbol)
        if not snapshots:
            raise ExchangeError(
                f"No synchronized fresh market snapshot available for {symbol.upper()}"
            )
        return snapshots[0]

    async def get_snapshots(self, symbols: Sequence[str]) -> list[MarketSnapshot]:
        out: list[MarketSnapshot] = []
        for symbol in symbols:
            out.append(await self.get_snapshot(symbol))
        return out

    async def get_venue_snapshots(self, symbol: str) -> list[MarketSnapshot]:
        """All valid venue books for a symbol (multi-exchange strategies)."""
        return self._service.snapshots_for_arbitrage(symbol)

    def get_order_book(self, exchange: str, symbol: str) -> OrderBook | None:
        return self._service.get_valid_order_book(exchange, symbol)
