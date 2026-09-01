"""Market data adapters for non-crypto asset classes (paper/stub when live feed absent)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from bot.core.models import MarketSnapshot


class MarketAdapter(ABC):
    """Normalized quote source for a single asset class."""

    @abstractmethod
    async def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        ...

    @abstractmethod
    def is_available(self, symbol: str) -> bool:
        ...


class CryptoSpotAdapter(MarketAdapter):
    """Wraps existing realtime provider — no duplicate WS."""

    def __init__(self, provider: object) -> None:
        self._provider = provider

    async def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        getter = getattr(self._provider, "get_snapshot", None)
        if not callable(getter):
            return None
        return await getter(symbol)

    def is_available(self, symbol: str) -> bool:
        return bool(symbol)


class FxStubAdapter(MarketAdapter):
    """Derives major FX from crypto EURUSDT mid when dedicated feed unavailable."""

    def __init__(self, crypto_provider: object, *, enabled: bool = False) -> None:
        self._crypto = crypto_provider
        self._enabled = enabled
        self._pairs = {
            "EURUSD": ("EURUSDT", Decimal("1")),
            "GBPUSD": ("EURUSDT", Decimal("0.85")),
            "USDJPY": ("EURUSDT", Decimal("130")),
        }

    async def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        if not self._enabled:
            return None
        sym = symbol.upper().replace("/", "")
        ref = self._pairs.get(sym)
        if ref is None:
            return None
        fx_sym, scale = ref
        getter = getattr(self._crypto, "get_snapshot", None)
        if not callable(getter):
            return None
        base = await getter(fx_sym)
        if base is None:
            return None
        mid = base.mid * scale if sym != "EURUSD" else Decimal("1") / base.mid
        spread = mid * Decimal("0.0001")
        return MarketSnapshot(
            symbol=sym,
            bid=mid - spread / 2,
            ask=mid + spread / 2,
            last=mid,
            exchange="fx_stub",
            metadata={"derived_from": fx_sym, "asset_class": "fx"},
        )

    def is_available(self, symbol: str) -> bool:
        return self._enabled and symbol.upper().replace("/", "") in self._pairs


class EquityStubAdapter(MarketAdapter):
    """Offline fallback quotes. Live paper uses Nasdaq/Yahoo via EquityQuoteService."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled
        self._prices = {
            "SPY.US": Decimal("450"),
            "AAPL.US": Decimal("180"),
            "SAP.DE": Decimal("160"),
        }

    async def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        if not self._enabled:
            return None
        sym = symbol.upper()
        px = self._prices.get(sym)
        if px is None:
            return None
        spread = px * Decimal("0.0002")
        return MarketSnapshot(
            symbol=sym,
            bid=px - spread / 2,
            ask=px + spread / 2,
            last=px,
            exchange="equity_stub",
            metadata={"asset_class": "equity", "stub": "true"},
        )

    def is_available(self, symbol: str) -> bool:
        return self._enabled and symbol.upper() in self._prices
