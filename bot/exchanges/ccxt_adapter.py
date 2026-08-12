"""CCXT async adapter implementing the exchange abstraction.

Normalizes CCXT payloads into internal models. Never logs API secrets.
Live ``create_order`` calls require ``enable_trading=True``; otherwise
``place_order`` returns a dry-run result without contacting the venue.
Withdrawals are intentionally unsupported.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import OpportunitySide, OrderStatus
from bot.core.exceptions import (
    ExchangeAuthError,
    ExchangeError,
    ExchangeRateLimitError,
    ExchangeTradingDisabledError,
    ExchangeTransientError,
)
from bot.core.models import (
    Balance,
    ExecutionResult,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
)
from bot.exchanges.base import BaseExchangeClient
from bot.exchanges.models import (
    ExchangeOrder,
    HealthCheckResult,
    OrderBook,
    OrderBookLevel,
    TradingFee,
)
from bot.exchanges.retry import RetryPolicy, with_retries
from bot.exchanges.sanitize import redact_mapping, safe_exc_message
from bot.exchanges.symbols import to_ccxt_symbol, to_internal_symbol

logger = logging.getLogger(__name__)

try:
    import ccxt.async_support as ccxt
    from ccxt.base.errors import (
        AuthenticationError as CcxtAuthenticationError,
    )
    from ccxt.base.errors import (
        DDoSProtection,
        ExchangeNotAvailable,
        NetworkError,
        RequestTimeout,
    )
    from ccxt.base.errors import (
        ExchangeError as CcxtExchangeError,
    )
    from ccxt.base.errors import (
        RateLimitExceeded as CcxtRateLimitExceeded,
    )
except ImportError:  # pragma: no cover - exercised when ccxt missing in env
    ccxt = None  # type: ignore[assignment]
    CcxtAuthenticationError = type("CcxtAuthenticationError", (Exception,), {})
    CcxtExchangeError = type("CcxtExchangeError", (Exception,), {})
    CcxtRateLimitExceeded = type("CcxtRateLimitExceeded", (Exception,), {})
    DDoSProtection = type("DDoSProtection", (Exception,), {})
    ExchangeNotAvailable = type("ExchangeNotAvailable", (Exception,), {})
    NetworkError = type("NetworkError", (Exception,), {})
    RequestTimeout = type("RequestTimeout", (Exception,), {})


_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.SUBMITTED,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "pending": OrderStatus.PENDING,
}


class CcxtExchangeAdapter(BaseExchangeClient):
    """Generic async CCXT-backed exchange client."""

    name = "ccxt"
    ccxt_id: str = "binance"

    def __init__(
        self,
        settings: Settings,
        *,
        ccxt_id: str | None = None,
        exchange: Any | None = None,
        retry_policy: RetryPolicy | None = None,
        enable_trading: bool = False,
        default_health_symbol: str = "BTC/USDT",
    ) -> None:
        super().__init__(settings, retry_policy=retry_policy, enable_trading=enable_trading)
        if ccxt_id:
            self.ccxt_id = ccxt_id
            self.name = ccxt_id
        self._default_health_symbol = default_health_symbol
        self._exchange = exchange
        self._owns_exchange = exchange is None

    async def _get_exchange(self) -> Any:
        if self._exchange is not None:
            return self._exchange
        if ccxt is None:
            raise ExchangeError(
                "ccxt is not installed. Add the 'ccxt' dependency to use live adapters."
            )
        exchange_cls = getattr(ccxt, self.ccxt_id, None)
        if exchange_cls is None:
            raise ExchangeError(f"Unsupported CCXT exchange id: {self.ccxt_id}")

        config: dict[str, Any] = {
            "apiKey": self._api_key or "",
            "secret": self._api_secret or "",
            "enableRateLimit": True,
            "timeout": 30_000,
        }
        if self._passphrase:
            config["password"] = self._passphrase
        if self._base_url:
            config["urls"] = {"api": self._base_url}

        logger.info(
            "Initializing CCXT exchange %s with %s",
            self.ccxt_id,
            redact_mapping(
                {
                    "apiKey": bool(self._api_key),
                    "secret": bool(self._api_secret),
                    "password": bool(self._passphrase),
                    "enableRateLimit": True,
                }
            ),
        )
        self._exchange = exchange_cls(config)
        return self._exchange

    async def close(self) -> None:
        if self._exchange is not None and self._owns_exchange:
            close = getattr(self._exchange, "close", None)
            if close is not None:
                await close()
        self._exchange = None

    async def fetch_ticker(self, symbol: str) -> MarketSnapshot:
        ccxt_symbol = to_ccxt_symbol(symbol)

        async def _op() -> Any:
            exchange = await self._get_exchange()
            return await self._call(exchange.fetch_ticker, ccxt_symbol)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.fetch_ticker",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_ticker(raw, symbol)

    async def fetch_order_book(self, symbol: str, *, limit: int | None = None) -> OrderBook:
        ccxt_symbol = to_ccxt_symbol(symbol)

        async def _op() -> Any:
            exchange = await self._get_exchange()
            if limit is None:
                return await self._call(exchange.fetch_order_book, ccxt_symbol)
            return await self._call(exchange.fetch_order_book, ccxt_symbol, limit)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.fetch_order_book",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_order_book(raw, symbol)

    async def fetch_trading_fees(self, symbol: str) -> TradingFee:
        ccxt_symbol = to_ccxt_symbol(symbol)

        async def _op() -> Any:
            exchange = await self._get_exchange()
            if hasattr(exchange, "fetch_trading_fee"):
                return await self._call(exchange.fetch_trading_fee, ccxt_symbol)
            fees = await self._call(exchange.fetch_trading_fees)
            if isinstance(fees, dict) and "trading" in fees:
                return fees["trading"].get(ccxt_symbol) or fees.get(ccxt_symbol) or fees
            if isinstance(fees, dict):
                return fees.get(ccxt_symbol) or fees
            return fees

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.fetch_trading_fees",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_trading_fee(raw, symbol)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        ccxt_symbol = to_ccxt_symbol(symbol) if symbol else None

        async def _op() -> Any:
            exchange = await self._get_exchange()
            if ccxt_symbol:
                return await self._call(exchange.fetch_open_orders, ccxt_symbol)
            return await self._call(exchange.fetch_open_orders)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.fetch_open_orders",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return [self._normalize_order(item) for item in (raw or [])]

    async def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        ccxt_symbol = to_ccxt_symbol(symbol)

        async def _op() -> Any:
            exchange = await self._get_exchange()
            return await self._call(exchange.fetch_order, order_id, ccxt_symbol)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.fetch_order",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_order(raw, fallback_symbol=symbol)

    async def place_order(self, order: OrderRequest) -> ExecutionResult:
        """Place an order, or return a dry-run result when trading is disabled."""
        if not self._enable_trading:
            logger.info(
                "Dry-run place_order on %s for %s (live trading disabled)",
                self.name,
                to_internal_symbol(order.symbol),
            )
            return ExecutionResult(
                order_id=order.id,
                opportunity_id=order.opportunity_id,
                status=OrderStatus.SUBMITTED,
                filled_quantity=Decimal("0"),
                average_price=order.limit_price,
                message="Dry-run only: live trading disabled on exchange adapter",
                metadata={
                    "exchange": self.name,
                    "dry_run": True,
                    "symbol": to_internal_symbol(order.symbol),
                    "side": order.side.value,
                },
            )

        ccxt_symbol = to_ccxt_symbol(order.symbol)
        side = _to_ccxt_side(order.side)
        order_type = "limit" if order.limit_price is not None else "market"
        amount = float(order.quantity)
        price = float(order.limit_price) if order.limit_price is not None else None
        params: dict[str, Any] = {}
        if order.client_order_id:
            params["clientOrderId"] = order.client_order_id

        async def _op() -> Any:
            exchange = await self._get_exchange()
            if order_type == "limit":
                return await self._call(
                    exchange.create_order, ccxt_symbol, order_type, side, amount, price, params
                )
            return await self._call(
                exchange.create_order, ccxt_symbol, order_type, side, amount, None, params
            )

        try:
            raw = await with_retries(
                _op,
                policy=self._retry_policy,
                operation_name=f"{self.name}.place_order",
                is_retryable=_is_retryable,
                get_retry_after=_retry_after,
            )
        except ExchangeTradingDisabledError:
            raise
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(safe_exc_message(exc)) from exc

        normalized = self._normalize_order(raw, fallback_symbol=order.symbol)
        return ExecutionResult(
            order_id=order.id,
            opportunity_id=order.opportunity_id,
            status=normalized.status,
            filled_quantity=normalized.filled_quantity,
            average_price=normalized.average_price,
            fees_usd=normalized.fee_cost or Decimal("0"),
            message="Order submitted to exchange",
            metadata={
                "exchange": self.name,
                "dry_run": False,
                "exchange_order_id": normalized.id,
                "symbol": normalized.symbol,
            },
        )

    async def cancel_order(self, order_id: str, symbol: str) -> ExchangeOrder:
        if not self._enable_trading:
            raise ExchangeTradingDisabledError(
                f"{self.name} cancel_order blocked: enable_trading is False"
            )
        ccxt_symbol = to_ccxt_symbol(symbol)

        async def _op() -> Any:
            exchange = await self._get_exchange()
            return await self._call(exchange.cancel_order, order_id, ccxt_symbol)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.cancel_order",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_order(raw, fallback_symbol=symbol)

    async def get_balances(self) -> PortfolioSnapshot:
        async def _op() -> Any:
            exchange = await self._get_exchange()
            return await self._call(exchange.fetch_balance)

        raw = await with_retries(
            _op,
            policy=self._retry_policy,
            operation_name=f"{self.name}.get_balances",
            is_retryable=_is_retryable,
            get_retry_after=_retry_after,
        )
        return self._normalize_balances(raw)

    async def health_check(self) -> HealthCheckResult:
        started = time.perf_counter()
        authenticated = False
        details: dict[str, Any] = {"credentials": self.credential_fingerprint()}
        try:
            exchange = await self._get_exchange()
            await self._call(exchange.load_markets)
            await self._call(exchange.fetch_ticker, self._default_health_symbol)
            if self._api_key and self._api_secret:
                await self._call(exchange.fetch_balance)
                authenticated = True
            latency_ms = (time.perf_counter() - started) * 1000.0
            return HealthCheckResult(
                exchange=self.name,
                healthy=True,
                authenticated=authenticated,
                latency_ms=latency_ms,
                message="ok",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000.0
            logger.warning(
                "Health check failed for %s: %s", self.name, type(exc).__name__
            )
            return HealthCheckResult(
                exchange=self.name,
                healthy=False,
                authenticated=authenticated,
                latency_ms=latency_ms,
                message=safe_exc_message(exc),
                details=details,
            )

    async def _call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except CcxtAuthenticationError as exc:
            raise ExchangeAuthError(safe_exc_message(exc)) from exc
        except (CcxtRateLimitExceeded, DDoSProtection) as exc:
            raise ExchangeRateLimitError(
                safe_exc_message(exc),
                retry_after=_extract_retry_after(exc),
            ) from exc
        except (RequestTimeout, NetworkError, ExchangeNotAvailable) as exc:
            raise ExchangeTransientError(safe_exc_message(exc)) from exc
        except CcxtExchangeError as exc:
            raise ExchangeError(safe_exc_message(exc)) from exc

    def _normalize_ticker(self, raw: dict[str, Any], symbol: str) -> MarketSnapshot:
        bid = _dec(raw.get("bid"))
        ask = _dec(raw.get("ask"))
        last = _dec(raw.get("last") or raw.get("close") or bid or ask)
        if bid is None and last is not None:
            bid = last
        if ask is None and last is not None:
            ask = last
        if bid is None or ask is None or last is None:
            raise ExchangeError(f"Incomplete ticker payload for {symbol}")
        return MarketSnapshot(
            symbol=to_internal_symbol(str(raw.get("symbol") or symbol)),
            bid=bid,
            ask=ask,
            last=last,
            volume_24h=_dec(raw.get("baseVolume") or raw.get("quoteVolume")),
            timestamp=_ts(raw.get("timestamp")),
            metadata={"exchange": self.name, "raw_symbol": raw.get("symbol")},
        )

    def _normalize_order_book(self, raw: dict[str, Any], symbol: str) -> OrderBook:
        bids = [
            OrderBookLevel(price=_dec(level[0]) or Decimal("0"), amount=_dec(level[1]) or Decimal("0"))
            for level in (raw.get("bids") or [])
            if level
        ]
        asks = [
            OrderBookLevel(price=_dec(level[0]) or Decimal("0"), amount=_dec(level[1]) or Decimal("0"))
            for level in (raw.get("asks") or [])
            if level
        ]
        return OrderBook(
            symbol=to_internal_symbol(symbol),
            bids=bids,
            asks=asks,
            timestamp=_ts(raw.get("timestamp")),
            nonce=raw.get("nonce"),
            metadata={"exchange": self.name},
        )

    def _normalize_trading_fee(self, raw: Any, symbol: str) -> TradingFee:
        if not isinstance(raw, dict):
            raise ExchangeError(f"Unexpected trading fee payload for {symbol}")
        maker = _dec(raw.get("maker"))
        taker = _dec(raw.get("taker"))
        if maker is None or taker is None:
            # Some venues nest under info / percentage fields only.
            maker = maker if maker is not None else Decimal("0")
            taker = taker if taker is not None else Decimal("0")
        return TradingFee(
            symbol=to_internal_symbol(symbol),
            maker=maker,
            taker=taker,
            percentage=bool(raw.get("percentage", True)),
            metadata={"exchange": self.name},
        )

    def _normalize_order(
        self, raw: dict[str, Any], *, fallback_symbol: str | None = None
    ) -> ExchangeOrder:
        status_raw = str(raw.get("status") or "pending").lower()
        filled = _dec(raw.get("filled")) or Decimal("0")
        amount = _dec(raw.get("amount")) or filled
        side_raw = str(raw.get("side") or "buy").lower()
        side = OpportunitySide.BUY if side_raw.startswith("b") else OpportunitySide.SELL
        if status_raw in {"closed", "filled"} and filled < amount:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = _STATUS_MAP.get(status_raw, OrderStatus.PENDING)
            if status == OrderStatus.FILLED and filled > 0 and filled < amount:
                status = OrderStatus.PARTIALLY_FILLED

        fee = raw.get("fee") or {}
        fee_cost = _dec(fee.get("cost")) if isinstance(fee, dict) else None
        fee_currency = fee.get("currency") if isinstance(fee, dict) else None

        return ExchangeOrder(
            id=str(raw.get("id") or uuid4()),
            symbol=to_internal_symbol(str(raw.get("symbol") or fallback_symbol or "")),
            side=side,
            status=status,
            quantity=amount,
            filled_quantity=filled,
            price=_dec(raw.get("price")),
            average_price=_dec(raw.get("average")),
            client_order_id=(
                str(raw.get("clientOrderId")) if raw.get("clientOrderId") is not None else None
            ),
            fee_cost=fee_cost,
            fee_currency=str(fee_currency) if fee_currency else None,
            created_at=_ts_optional(raw.get("timestamp")),
            updated_at=_ts_optional(raw.get("lastTradeTimestamp") or raw.get("timestamp")),
            metadata={"exchange": self.name},
        )

    def _normalize_balances(self, raw: dict[str, Any]) -> PortfolioSnapshot:
        balances: list[Balance] = []
        free_map = raw.get("free") if isinstance(raw.get("free"), dict) else {}
        used_map = raw.get("used") if isinstance(raw.get("used"), dict) else {}
        total_map = raw.get("total") if isinstance(raw.get("total"), dict) else {}

        assets = set(free_map) | set(used_map) | set(total_map)
        if not assets:
            # Fall back to per-currency dict entries used by some CCXT versions.
            for key, value in raw.items():
                if key in {"info", "free", "used", "total", "datetime", "timestamp"}:
                    continue
                if isinstance(value, dict) and ("free" in value or "total" in value):
                    assets.add(key)

        for asset in sorted(assets):
            entry = raw.get(asset) if isinstance(raw.get(asset), dict) else {}
            free = _dec(free_map.get(asset) if free_map else entry.get("free")) or Decimal("0")
            locked = _dec(used_map.get(asset) if used_map else entry.get("used")) or Decimal("0")
            total = _dec(total_map.get(asset) if total_map else entry.get("total"))
            if total is not None and free == 0 and locked == 0:
                free = total
            if free == 0 and locked == 0:
                continue
            balances.append(Balance(asset=str(asset).upper(), free=free, locked=locked))

        equity = sum((b.total for b in balances), Decimal("0"))
        return PortfolioSnapshot(
            balances=balances,
            positions=[],
            equity_usd=equity,
            daily_realized_pnl_usd=Decimal("0"),
            open_position_count=0,
        )


def _to_ccxt_side(side: OpportunitySide) -> str:
    if side in {OpportunitySide.BUY, OpportunitySide.LONG}:
        return "buy"
    return "sell"


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _ts(value: Any) -> datetime:
    optional = _ts_optional(value)
    return optional or datetime.now(UTC)


def _ts_optional(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        ms = int(value)
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except Exception:  # noqa: BLE001
        return None


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(
        exc,
        ExchangeTransientError | ExchangeRateLimitError | TimeoutError | OSError,
    )


def _retry_after(exc: BaseException) -> float | None:
    if isinstance(exc, ExchangeRateLimitError):
        return exc.retry_after
    return _extract_retry_after(exc)


def _extract_retry_after(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None)
    if isinstance(headers, dict):
        for key in ("Retry-After", "retry-after", "X-RateLimit-Reset"):
            if key in headers:
                try:
                    return float(headers[key])
                except (TypeError, ValueError):
                    continue
    return None
