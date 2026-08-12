"""Market data providers that expose normalized MarketSnapshot objects."""

from collections.abc import Sequence

from bot.core.exceptions import ExchangeError
from bot.core.interfaces import ExchangeClient
from bot.core.models import MarketSnapshot

# Re-export realtime provider for a single import surface.
from bot.market_data.provider_realtime import RealtimeMarketDataProvider  # noqa: E402

__all__ = [
    "ExchangeMarketDataProvider",
    "RealtimeMarketDataProvider",
    "StaticMarketDataProvider",
]


class StaticMarketDataProvider:
    """In-memory provider for tests and scaffolding (no network I/O)."""

    def __init__(self, snapshots: dict[str, MarketSnapshot] | None = None) -> None:
        self._snapshots: dict[str, MarketSnapshot] = {
            key.upper(): value for key, value in (snapshots or {}).items()
        }

    def set_snapshot(self, snapshot: MarketSnapshot) -> None:
        self._snapshots[snapshot.symbol.upper()] = snapshot

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        key = symbol.upper()
        if key not in self._snapshots:
            raise ExchangeError(f"No market snapshot available for {key}")
        return self._snapshots[key]

    async def get_snapshots(self, symbols: Sequence[str]) -> list[MarketSnapshot]:
        return [await self.get_snapshot(symbol) for symbol in symbols]


class ExchangeMarketDataProvider:
    """Market data provider backed by an ExchangeClient adapter."""

    def __init__(self, client: ExchangeClient) -> None:
        self._client = client

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        return await self._client.fetch_ticker(symbol)

    async def get_snapshots(self, symbols: Sequence[str]) -> list[MarketSnapshot]:
        return [await self.get_snapshot(symbol) for symbol in symbols]
