"""Base exchange client. Credentials from Settings only; no withdrawals."""

from __future__ import annotations

import logging
from typing import Any

from bot.core.config import Settings
from bot.core.interfaces import ExchangeClient
from bot.core.models import ExecutionResult, MarketSnapshot, OrderRequest, PortfolioSnapshot
from bot.exchanges.models import ExchangeOrder, HealthCheckResult, OrderBook, TradingFee
from bot.exchanges.retry import DEFAULT_RETRY_POLICY, RetryPolicy

logger = logging.getLogger(__name__)


class BaseExchangeClient(ExchangeClient):
    """Shared scaffolding for exchange adapters.

    Subclasses implement market data and trading reads/writes.
    There is intentionally no withdraw / transfer method.
    """

    name: str = "base"

    def __init__(
        self,
        settings: Settings,
        *,
        retry_policy: RetryPolicy | None = None,
        enable_trading: bool = False,
    ) -> None:
        self._settings = settings
        self._retry_policy = retry_policy or DEFAULT_RETRY_POLICY
        # Fail closed: real order placement requires an explicit opt-in.
        self._enable_trading = enable_trading
        self._api_key = (
            settings.exchange_api_key.get_secret_value() if settings.exchange_api_key else None
        )
        self._api_secret = (
            settings.exchange_api_secret.get_secret_value()
            if settings.exchange_api_secret
            else None
        )
        self._passphrase = (
            settings.exchange_passphrase.get_secret_value()
            if settings.exchange_passphrase
            else None
        )
        self._base_url = settings.exchange_base_url

    @property
    def enable_trading(self) -> bool:
        return self._enable_trading

    def credential_fingerprint(self) -> dict[str, Any]:
        """Safe credential presence flags for diagnostics (never raw secrets)."""
        return {
            "api_key_present": bool(self._api_key),
            "api_secret_present": bool(self._api_secret),
            "passphrase_present": bool(self._passphrase),
            "base_url_set": bool(self._base_url),
            "enable_trading": self._enable_trading,
        }

    async def fetch_ticker(self, symbol: str) -> MarketSnapshot:
        raise NotImplementedError(f"{self.name} fetch_ticker is not implemented yet")

    async def fetch_order_book(self, symbol: str, *, limit: int | None = None) -> OrderBook:
        raise NotImplementedError(f"{self.name} fetch_order_book is not implemented yet")

    async def fetch_trading_fees(self, symbol: str) -> TradingFee:
        raise NotImplementedError(f"{self.name} fetch_trading_fees is not implemented yet")

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        raise NotImplementedError(f"{self.name} fetch_open_orders is not implemented yet")

    async def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        raise NotImplementedError(f"{self.name} fetch_order is not implemented yet")

    async def place_order(self, order: OrderRequest) -> ExecutionResult:
        raise NotImplementedError(f"{self.name} place_order is not implemented yet")

    async def cancel_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        raise NotImplementedError(f"{self.name} cancel_order is not implemented yet")

    async def get_balances(self) -> PortfolioSnapshot:
        raise NotImplementedError(f"{self.name} get_balances is not implemented yet")

    async def health_check(self) -> HealthCheckResult:
        raise NotImplementedError(f"{self.name} health_check is not implemented yet")
