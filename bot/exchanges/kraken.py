"""Kraken exchange adapter (CCXT async)."""

from __future__ import annotations

from typing import Any

from bot.core.config import Settings
from bot.exchanges.ccxt_adapter import CcxtExchangeAdapter
from bot.exchanges.retry import RetryPolicy


class KrakenExchange(CcxtExchangeAdapter):
    """Kraken spot adapter. Live orders require ``enable_trading=True``."""

    name = "kraken"
    ccxt_id = "kraken"

    def __init__(
        self,
        settings: Settings,
        *,
        exchange: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        enable_trading: bool = False,
    ) -> None:
        super().__init__(
            settings,
            ccxt_id="kraken",
            exchange=exchange,
            retry_policy=retry_policy,
            enable_trading=enable_trading,
            default_health_symbol="BTC/USD",
        )
