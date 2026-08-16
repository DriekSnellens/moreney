"""Realtime market-data service: adapters → local books → cache → strategies.

Does not place orders. Does not call private APIs. Does not modify RiskEngine /
ProfitabilityEngine / PaperExecutor — consumers build RiskContext from health.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from bot.core.config import Settings
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot
from bot.market_data.adapters.base import PublicMarketDataAdapter
from bot.market_data.adapters.binance import BinancePublicAdapter
from bot.market_data.adapters.bitvavo import BitvavoPublicAdapter
from bot.market_data.adapters.bybit import BybitPublicAdapter
from bot.market_data.adapters.coinbase import CoinbasePublicAdapter
from bot.market_data.adapters.kraken import KrakenPublicAdapter
from bot.market_data.adapters.okx import OkxPublicAdapter
from bot.market_data.cache import MarketDataCache
from bot.market_data.equity import EquityQuoteService
from bot.market_data.funding import FundingRateService
from bot.market_data.local_order_book import LocalOrderBook
from bot.market_data.models import (
    ConnectionState,
    ExchangeHealth,
    MarketDataEvent,
    MarketTick,
)
from bot.market_data.recorder import MarketDataRecorder
from bot.market_data.websocket_manager import WebSocketManager
from bot.risk.models import RiskContext

logger = logging.getLogger(__name__)


_ADAPTERS: dict[str, type[PublicMarketDataAdapter]] = {
    "binance": BinancePublicAdapter,
    "kraken": KrakenPublicAdapter,
    "coinbase": CoinbasePublicAdapter,
    "bitvavo": BitvavoPublicAdapter,
    "okx": OkxPublicAdapter,
    "bybit": BybitPublicAdapter,
}


class MarketDataService:
    """Orchestrates public feeds, local books, freshness, and strategy snapshots."""

    def __init__(
        self,
        settings: Settings,
        *,
        adapters: Sequence[PublicMarketDataAdapter] | None = None,
        cache: MarketDataCache | None = None,
        recorder: MarketDataRecorder | None = None,
        start_websockets: bool = False,
    ) -> None:
        self._settings = settings
        self._max_age_ms = settings.max_market_data_age_ms
        self._symbols = [
            s.strip().upper().replace("-", "").replace("/", "")
            for s in settings.market_data_symbols.split(",")
            if s.strip()
        ]
        self._exchanges = [
            e.strip().lower()
            for e in settings.market_data_exchanges.split(",")
            if e.strip()
        ]
        self._cache = cache or MarketDataCache(ttl_seconds=settings.market_data_redis_ttl_seconds)
        self._recorder = recorder or MarketDataRecorder(
            enabled=settings.market_data_recording_enabled,
            path=settings.market_data_recording_path,
        )
        from bot.market_data.research.recorder import ResearchMarketDataRecorder

        self._research_recorder = ResearchMarketDataRecorder(
            enabled=bool(
                getattr(settings, "research_marketdata_recording_enabled", True)
            ),
            path=str(
                getattr(
                    settings,
                    "research_marketdata_recording_path",
                    "./data/research_marketdata",
                )
            ),
            max_queue=int(getattr(settings, "research_marketdata_max_queue", 50_000) or 50_000),
            max_depth_levels=int(
                getattr(settings, "research_marketdata_depth_levels", 10) or 10
            ),
        )
        self._books: dict[tuple[str, str], LocalOrderBook] = {}
        self._ticks: dict[tuple[str, str], MarketTick] = {}
        self._managers: dict[str, WebSocketManager] = {}
        self._adapters: list[PublicMarketDataAdapter] = list(adapters or [])
        self._started = False
        self._book_depth = int(getattr(settings, "market_data_book_depth", 50))
        self._cache_interval_s = float(getattr(settings, "market_data_cache_interval_ms", 250.0)) / 1000.0
        self._last_cache_write: dict[str, float] = {}
        self._last_health: dict[str, ExchangeHealth] = {}
        self._last_stale_log: dict[str, float] = {}
        self._mode = str(getattr(settings, "market_data_mode", "local") or "local").lower()
        self._redis_poll_s = float(getattr(settings, "market_data_redis_poll_ms", 100.0)) / 1000.0
        self._remote_health: dict[str, ExchangeHealth] = {}
        self._consumer_task: asyncio.Task[None] | None = None
        self._funding = FundingRateService(settings)
        self._funding_publish_task: asyncio.Task[None] | None = None
        self._equity = EquityQuoteService(settings)
        self._equity_publish_task: asyncio.Task[None] | None = None
        from bot.perf.cycle_metrics import CycleLatencyTracker

        self._latency = CycleLatencyTracker(
            enabled=bool(getattr(settings, "perf_instrumentation_enabled", False)),
            window=int(getattr(settings, "perf_instrumentation_window", 512) or 512),
        )

        if not self._adapters and start_websockets:
            self._adapters = self._build_live_adapters()

        for exchange in self._exchanges:
            for symbol in self._symbols:
                self._books[(exchange, symbol)] = LocalOrderBook(
                    exchange, symbol, max_depth=self._book_depth
                )

    def _build_live_adapters(self) -> list[PublicMarketDataAdapter]:
        adapters: list[PublicMarketDataAdapter] = []
        for name in self._exchanges:
            cls = _ADAPTERS.get(name)
            if cls is None:
                continue
            manager = WebSocketManager(
                name=name,
                url=cls.ws_url,  # type: ignore[attr-defined]
                reconnect_base_ms=self._settings.market_data_ws_reconnect_base_ms,
                reconnect_max_ms=self._settings.market_data_ws_reconnect_max_ms,
                heartbeat_interval_ms=self._settings.market_data_heartbeat_interval_ms,
                connection_timeout_ms=self._settings.market_data_connection_timeout_ms,
            )
            self._managers[name] = manager
            adapters.append(cls(self._symbols, manager=manager))
        return adapters

    @property
    def exchanges(self) -> list[str]:
        return list(self._exchanges)

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def shared_mode(self) -> bool:
        return self._mode == "shared"

    @property
    def funding(self) -> FundingRateService:
        return self._funding

    @property
    def equity(self) -> EquityQuoteService:
        return self._equity

    async def start(self) -> None:
        if self._started:
            return
        if self.shared_mode:
            await self.start_shared_consumer()
            return
        for adapter in self._adapters:
            if adapter._manager is not None:  # noqa: SLF001 — intentional live start
                await adapter.start(self.handle_event)
        await self._start_funding(publish=self._mode == "publisher")
        await self._start_equity(publish=self._mode == "publisher")
        self._started = True

    async def start_shared_consumer(self) -> None:
        """Hydrate local books from Redis published by the shared market-data process."""
        if self._started:
            return
        if self._cache.redis_client is None:
            raise RuntimeError(
                "MARKET_DATA_MODE=shared requires a Redis-backed MarketDataCache"
            )
        self._started = True
        self._consumer_task = asyncio.create_task(
            self._consumer_loop(), name="market-data-redis-consumer"
        )
        await self._start_funding(publish=False)
        await self._start_equity(publish=False)
        # Prime once so paper start does not wait a full poll interval.
        await self.hydrate_from_redis()
        logger.info(
            "MARKET_DATA_SHARED_CONSUMER_STARTED poll_ms=%s exchanges=%s symbols=%s",
            self._redis_poll_s * 1000.0,
            self._exchanges,
            self._symbols,
        )

    async def stop(self) -> None:
        self._started = False
        await self._funding.stop()
        await self._equity.stop()
        if self._funding_publish_task is not None:
            self._funding_publish_task.cancel()
            try:
                await self._funding_publish_task
            except asyncio.CancelledError:
                pass
            self._funding_publish_task = None
        if self._equity_publish_task is not None:
            self._equity_publish_task.cancel()
            try:
                await self._equity_publish_task
            except asyncio.CancelledError:
                pass
            self._equity_publish_task = None
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        for adapter in self._adapters:
            await adapter.stop()

    async def _consumer_loop(self) -> None:
        while self._started:
            try:
                await self.hydrate_from_redis()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — keep consumer alive
                logger.info(
                    "MARKET_DATA_REDIS_HYDRATE_FAILED error=%s",
                    type(exc).__name__,
                )
            await asyncio.sleep(self._redis_poll_s)

    async def hydrate_from_redis(self) -> None:
        """Pull latest published books/ticks/health into local memory.

        Uses one Redis pipeline round-trip for the full exchange×symbol bundle,
        then skips JSON decode / book rebuild when payloads are byte-identical
        to the previous poll (publisher still holds the latest snapshot).
        """
        t0 = time.perf_counter()
        bundle = await self._cache.fetch_hydrate_raw(
            exchanges=self._exchanges,
            symbols=self._symbols,
        )
        self._cache.mark_hydrate()
        redis_ms = time.perf_counter() - t0
        self._latency.record("redis_read", redis_ms)

        t_parse = time.perf_counter()
        for exchange in self._exchanges:
            health_raw = self._cache.consume_changed_raw(
                self._cache.health_key(exchange),
                bundle.get(self._cache.health_key(exchange)),
            )
            if health_raw is not None:
                try:
                    self._remote_health[exchange] = ExchangeHealth.model_validate_json(
                        health_raw
                    )
                except Exception:  # noqa: BLE001 — keep hydrate alive
                    logger.debug("HYDRATE_HEALTH_PARSE_FAILED exchange=%s", exchange)
            for symbol in self._symbols:
                book_key = self._cache.book_key(exchange, symbol)
                book_raw = self._cache.consume_changed_raw(
                    book_key, bundle.get(book_key)
                )
                if book_raw is not None:
                    try:
                        data = json.loads(book_raw)
                        data.pop("exchange", None)
                        book = OrderBook.model_validate(data)
                        self.apply_remote_book(exchange, symbol, book)
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "HYDRATE_BOOK_PARSE_FAILED exchange=%s symbol=%s",
                            exchange,
                            symbol,
                        )
                tick_key = self._cache.tick_key(exchange, symbol)
                tick_raw = self._cache.consume_changed_raw(
                    tick_key, bundle.get(tick_key)
                )
                if tick_raw is not None:
                    try:
                        self._ticks[(exchange, symbol)] = MarketTick.model_validate_json(
                            tick_raw
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "HYDRATE_TICK_PARSE_FAILED exchange=%s symbol=%s",
                            exchange,
                            symbol,
                        )
        funding_raw = self._cache.consume_changed_raw(
            self._cache.funding_key(),
            bundle.get(self._cache.funding_key()),
        )
        if funding_raw is not None:
            try:
                data = json.loads(funding_raw)
                if isinstance(data, dict) and data:
                    self._funding.import_rates({str(k): str(v) for k, v in data.items()})
            except json.JSONDecodeError:
                pass
        equity_raw = self._cache.consume_changed_raw(
            self._cache.equity_key(),
            bundle.get(self._cache.equity_key()),
        )
        if equity_raw is not None:
            try:
                data = json.loads(equity_raw)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and data:
                quotes: dict[str, dict[str, str]] = {}
                for sym, payload in data.items():
                    if isinstance(payload, dict):
                        quotes[str(sym)] = {str(k): str(v) for k, v in payload.items()}
                if quotes:
                    self._equity.import_quotes(quotes)
        self._latency.record("hydrate_parse", time.perf_counter() - t_parse)
        self._latency.record("hydrate_total", time.perf_counter() - t0)

    async def _start_funding(self, *, publish: bool) -> None:
        if not self._funding.enabled:
            return
        if self._mode in {"local", "publisher"}:
            await self._funding.start()
        if publish:
            self._funding_publish_task = asyncio.create_task(
                self._funding_publish_loop(), name="funding-redis-publisher"
            )

    async def _start_equity(self, *, publish: bool) -> None:
        if not self._equity.enabled:
            return
        if self._mode in {"local", "publisher"}:
            await self._equity.start()
        if publish:
            self._equity_publish_task = asyncio.create_task(
                self._equity_publish_loop(), name="equity-redis-publisher"
            )

    async def _funding_publish_loop(self) -> None:
        while self._started:
            try:
                await self._cache.set_funding_rates(self._funding.export_rates())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("FUNDING_REDIS_PUBLISH_FAILED error=%s", exc)
            await asyncio.sleep(self._funding._poll_s)  # noqa: SLF001

    async def _equity_publish_loop(self) -> None:
        while self._started:
            try:
                await self._cache.set_equity_quotes(self._equity.export_quotes())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("EQUITY_REDIS_PUBLISH_FAILED error=%s", exc)
            await asyncio.sleep(self._equity._poll_s)  # noqa: SLF001

    def apply_remote_book(self, exchange: str, symbol: str, book: OrderBook) -> None:
        """Replace local synchronized book from a Redis-published snapshot.

        Preserves publisher ``received_at`` / exchange_ts flags from metadata.
        Never treats hydrate wall-clock as an exchange timestamp.
        """
        from bot.market_data.models import OrderBookUpdate

        exchange = exchange.lower()
        symbol = symbol.upper().replace("-", "").replace("/", "")
        local = self._books.get((exchange, symbol)) or LocalOrderBook(
            exchange, symbol, max_depth=self._book_depth
        )
        self._books[(exchange, symbol)] = local
        # Skip rebuild when publisher snapshot is unchanged.
        if (
            local.synchronized
            and local.sequence == book.nonce
            and local._timestamp == book.timestamp  # noqa: SLF001
            and len(local._bids) == len(book.bids)  # noqa: SLF001
            and len(local._asks) == len(book.asks)  # noqa: SLF001
        ):
            return
        meta = dict(book.metadata or {})
        received_at = datetime.now(UTC)
        raw_recv = meta.get("received_at")
        if raw_recv:
            try:
                text = str(raw_recv)
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                received_at = datetime.fromisoformat(text)
                if received_at.tzinfo is None:
                    received_at = received_at.replace(tzinfo=UTC)
            except Exception:
                received_at = datetime.now(UTC)
        exchange_ts_available = bool(meta.get("exchange_ts_available"))
        timestamp_quality = str(meta.get("timestamp_quality") or (
            "MEDIUM" if exchange_ts_available else "UNSUPPORTED"
        ))
        update = OrderBookUpdate(
            exchange=exchange,
            symbol=symbol,
            bids=list(book.bids),
            asks=list(book.asks),
            is_snapshot=True,
            sequence=book.nonce,
            timestamp=book.timestamp,
            received_at=received_at,
            metadata={
                "exchange_ts_available": exchange_ts_available,
                "timestamp_quality": timestamp_quality,
                "exchange_ts": meta.get("exchange_ts"),
                "received_at": received_at.isoformat(),
                "hydrated_from_redis": True,
                "hydrate_wall_clock": datetime.now(UTC).isoformat(),
            },
        )
        local.apply_snapshot(update)

    async def handle_event(self, event: MarketDataEvent) -> None:
        # Heartbeats / acks: cheap path — refresh connection age only.
        if event.event_type in {"heartbeat", "control"}:
            return

        await self._recorder.record(event)
        # Research tape: same source events as Redis publisher; non-blocking enqueue.
        self._research_recorder.enqueue_live(event)
        key = (event.exchange, event.symbol)
        cache_due = self._cache_due(event.exchange)

        if event.event_type in {"book_snapshot", "book_update"} and event.book_update:
            book = self._books.get(key) or LocalOrderBook(
                event.exchange, event.symbol, max_depth=self._book_depth
            )
            self._books[key] = book
            update = event.book_update
            # When desynced, a full-sided update rebuilds the local book (snapshot).
            promote_snapshot = update.is_snapshot or (
                book.needs_snapshot and bool(update.bids) and bool(update.asks)
            )
            if promote_snapshot:
                book.apply_snapshot(update)
                logger.debug(
                    "SNAPSHOT_RECEIVED exchange=%s symbol=%s sequence=%s",
                    event.exchange,
                    event.symbol,
                    update.sequence,
                )
            else:
                ok = book.apply_update(update)
                if not ok:
                    logger.info(
                        "EXCHANGE_UNHEALTHY exchange=%s symbol=%s reason=sequence_gap",
                        event.exchange,
                        event.symbol,
                    )
            # Hot path: derive tick from top-of-book without sorting entire depth.
            if book.synchronized:
                best_bid = book.best_bid()
                best_ask = book.best_ask()
                if best_bid is not None and best_ask is not None:
                    tick = MarketTick(
                        exchange=event.exchange,
                        symbol=event.symbol,
                        bid=best_bid[0],
                        ask=best_ask[0],
                        bid_size=best_bid[1],
                        ask_size=best_ask[1],
                        timestamp=book._timestamp,  # noqa: SLF001
                        sequence=book.sequence,
                    )
                    self._ticks[key] = tick
                    if cache_due:
                        pending_tick = tick
                        synced = book.to_order_book()
                        if synced is not None:
                            pending_book = synced
                        else:
                            pending_book = None
                    else:
                        pending_tick = None
                        pending_book = None
                else:
                    pending_tick = None
                    pending_book = None
            else:
                pending_tick = None
                pending_book = None
        else:
            pending_tick = None
            pending_book = None

        if event.tick is not None:
            self._ticks[key] = event.tick
            if cache_due:
                pending_tick = event.tick

        if cache_due:
            health = self.get_exchange_health(event.exchange)
            self._last_health[event.exchange] = health
            items: list[tuple[str, str]] = []
            if pending_tick is not None:
                items.append(
                    (
                        self._cache.tick_key(pending_tick.exchange, pending_tick.symbol),
                        pending_tick.model_dump_json(),
                    )
                )
            if pending_book is not None:
                payload = pending_book.model_dump(mode="json")
                payload["exchange"] = event.exchange.lower()
                items.append(
                    (
                        self._cache.book_key(event.exchange, pending_book.symbol),
                        json.dumps(payload),
                    )
                )
            items.append(
                (self._cache.health_key(health.exchange), health.model_dump_json())
            )
            await self._cache.pipeline_set(items)
            self._mark_cache_written(event.exchange)
            if health.stale:
                self._log_stale_throttled(event.exchange, health.last_message_age_ms)

    def _cache_due(self, exchange: str) -> bool:
        if self._cache_interval_s <= 0:
            return True
        last = self._last_cache_write.get(exchange)
        if last is None:
            return True
        return (time.monotonic() - last) >= self._cache_interval_s

    def _mark_cache_written(self, exchange: str) -> None:
        self._last_cache_write[exchange] = time.monotonic()

    def _log_stale_throttled(self, exchange: str, age_ms: float | None) -> None:
        now = time.monotonic()
        last = self._last_stale_log.get(exchange, 0.0)
        if now - last < 5.0:
            return
        self._last_stale_log[exchange] = now
        logger.info(
            "STALE_MARKET_DATA exchange=%s age_ms=%s",
            exchange,
            age_ms,
        )

    def inject_snapshot(
        self,
        exchange: str,
        symbol: str,
        *,
        bid: Decimal,
        ask: Decimal,
        bid_size: Decimal = Decimal("1"),
        ask_size: Decimal = Decimal("1"),
        sequence: int = 1,
    ) -> None:
        """Test helper: inject a synchronized top-of-book snapshot."""
        from bot.market_data.models import OrderBookUpdate

        exchange = exchange.lower()
        symbol = symbol.upper().replace("-", "").replace("/", "")
        book = self._books.get((exchange, symbol)) or LocalOrderBook(
            exchange, symbol, max_depth=self._book_depth
        )
        self._books[(exchange, symbol)] = book
        update = OrderBookUpdate(
            exchange=exchange,
            symbol=symbol,
            bids=[OrderBookLevel(price=bid, amount=bid_size)],
            asks=[OrderBookLevel(price=ask, amount=ask_size)],
            is_snapshot=True,
            sequence=sequence,
            timestamp=datetime.now(UTC),
        )
        book.apply_snapshot(update)
        tick = MarketTick(
            exchange=exchange,
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            last=(bid + ask) / Decimal("2"),
            sequence=sequence,
        )
        self._ticks[(exchange, symbol)] = tick

    def get_local_book(self, exchange: str, symbol: str) -> LocalOrderBook | None:
        return self._books.get((exchange.lower(), symbol.upper().replace("-", "").replace("/", "")))

    def get_valid_order_book(self, exchange: str, symbol: str) -> OrderBook | None:
        book = self.get_local_book(exchange, symbol)
        if book is None or not book.synchronized:
            return None
        if book.age_ms > self._max_age_ms:
            logger.info(
                "STALE_MARKET_DATA exchange=%s symbol=%s age_ms=%s limit=%s",
                exchange,
                symbol,
                book.age_ms,
                self._max_age_ms,
            )
            return None
        return book.to_order_book()

    def get_exchange_health(self, exchange: str) -> ExchangeHealth:
        exchange = exchange.lower()
        manager = self._managers.get(exchange)
        books = [b for (ex, _), b in self._books.items() if ex == exchange]
        ticks = [t for (ex, _), t in self._ticks.items() if ex == exchange]
        synced_books = [b for b in books if b.synchronized]
        last_at = None
        book_ages: list[float] = []
        for book in synced_books:
            book_ages.append(book.age_ms)
            last_at = book._received_at  # noqa: SLF001
        for tick in ticks:
            last_at = tick.received_at
        # Freshness is driven by synchronized books (strategies trade on books).
        # Use the freshest book so one quiet symbol does not poison the venue.
        age = (
            min(book_ages)
            if book_ages
            else (manager.last_message_age_ms if manager else None)
        )
        connected = manager.connected if manager else bool(synced_books)
        synchronized = bool(synced_books)
        stale = True
        if age is not None and synchronized:
            stale = age > self._max_age_ms
        elif manager is not None and not book_ages:
            stale = manager.is_stale(self._max_age_ms)
            connected = manager.connected
        remote = self._remote_health.get(exchange)
        if manager is None and remote is not None:
            # Shared consumers: use publisher health for connection/rate, local books for age.
            connected = remote.connected or bool(synced_books)
            if age is not None and synchronized:
                stale = age > self._max_age_ms
            else:
                stale = remote.stale if synchronized else True
            return ExchangeHealth(
                exchange=exchange,
                connected=connected,
                connection_state=(
                    remote.connection_state
                    if connected
                    else ConnectionState.DISCONNECTED
                ),
                stale=stale,
                synchronized=synchronized,
                last_message_age_ms=age if age is not None else remote.last_message_age_ms,
                last_message_at=last_at or remote.last_message_at,
                last_snapshot_at=last_at or remote.last_snapshot_at,
                last_sequence=(
                    synced_books[0].sequence if synced_books else remote.last_sequence
                ),
                message_rate_per_sec=remote.message_rate_per_sec,
                reconnect_count=remote.reconnect_count,
                sequence_gap_count=remote.sequence_gap_count,
                symbols=sorted({b.symbol for b in synced_books}) or list(remote.symbols),
                details={"source": "shared_redis", **dict(remote.details or {})},
            )
        return ExchangeHealth(
            exchange=exchange,
            connected=connected,
            connection_state=(
                manager.state if manager else (
                    ConnectionState.CONNECTED if synchronized and not stale
                    else ConnectionState.DISCONNECTED
                )
            ),
            stale=stale,
            synchronized=synchronized,
            last_message_age_ms=age,
            last_message_at=last_at or (manager.last_message_at if manager else None),
            last_snapshot_at=last_at,
            last_sequence=synced_books[0].sequence if synced_books else None,
            message_rate_per_sec=manager.message_rate_per_sec if manager else 0.0,
            reconnect_count=manager.reconnect_count if manager else 0,
            sequence_gap_count=sum(b.sequence_gap_count for b in books),
            symbols=sorted({b.symbol for b in synced_books}),
        )

    def status(self) -> dict[str, dict]:
        return {ex: self.get_exchange_health(ex).model_dump(mode="json") for ex in self._exchanges}

    def research_recorder_status(self) -> dict:
        """Research infrastructure metrics — does not affect trading."""
        rec = getattr(self, "_research_recorder", None)
        if rec is None:
            return {"enabled": False, "affects_trading": False}
        return rec.snapshot()

    def build_risk_context(self, exchange: str, symbol: str) -> RiskContext:
        """Build RiskContext for existing RiskEngine without modifying it."""
        health = self.get_exchange_health(exchange)
        book = self.get_local_book(exchange, symbol)
        tick = self._ticks.get((exchange.lower(), symbol.upper()))
        liquidity = None
        if book and book.synchronized:
            liquidity = book.top_liquidity()
        return RiskContext(
            exchange_healthy=health.connected and not health.stale and health.synchronized,
            market_data_age_ms=health.last_message_age_ms,
            estimated_slippage_pct=Decimal("0"),
            liquidity_base=liquidity,
            reference_price=tick.mid if tick else None,
            current_price=tick.last if tick and tick.last else (tick.mid if tick else None),
            metadata={"exchange": exchange, "stale": health.stale},
        )

    def snapshots_for_arbitrage(self, symbol: str) -> list[MarketSnapshot]:
        """Normalized multi-exchange snapshots for CrossExchangeArbitrageStrategy.

        Stale or unsynchronized books are omitted — strategies never see invalid data.
        """
        symbol = symbol.upper().replace("-", "").replace("/", "")
        if self._equity.is_equity_symbol(symbol):
            equity_snap = self._equity.snapshot_for(symbol)
            return [equity_snap] if equity_snap is not None else []
        snapshots: list[MarketSnapshot] = []
        for exchange in self._exchanges:
            book = self.get_valid_order_book(exchange, symbol)
            if book is None:
                health = self.get_exchange_health(exchange)
                logger.info(
                    "OPPORTUNITY_REJECTED reason=STALE_OR_INVALID_BOOK exchange=%s "
                    "symbol=%s connected=%s stale=%s synchronized=%s",
                    exchange,
                    symbol,
                    health.connected,
                    health.stale,
                    health.synchronized,
                )
                continue
            bid = book.bids[0]
            ask = book.asks[0]
            age = self.get_local_book(exchange, symbol)
            funding_rate = self._funding.rate_for_spot(exchange, symbol)
            meta: dict[str, str] = {"source": "realtime_market_data"}
            perp = None
            if funding_rate is not None:
                from bot.market_data.funding import perp_symbol_for

                perp = perp_symbol_for(symbol)
                meta["funding_source"] = "binance_perp"
                meta["perp_symbol"] = perp or ""
                meta["funding_rate"] = str(funding_rate)
            snapshots.append(
                MarketSnapshot(
                    symbol=symbol,
                    bid=bid.price,
                    ask=ask.price,
                    last=(bid.price + ask.price) / Decimal("2"),
                    order_book=book,
                    exchange=exchange,
                    latency_ms=age.age_ms if age else None,
                    timestamp=book.timestamp,
                    funding_rate=funding_rate,
                    metadata=meta,
                )
            )
        return snapshots
