"""In-memory synchronized order book per (exchange, symbol).

Never exposes a book to strategies when synchronization is invalid.

Performance notes:
* Depth is capped (default 50 levels/side) — strategies only need near-top liquidity.
* Materialized ``OrderBook`` views are cached until the next mutation.
* Top-of-book helpers avoid sorting the full side on every WebSocket tick.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.market_data.models import OrderBookUpdate

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_DEFAULT_MAX_DEPTH = 50


class LocalOrderBook:
    """Maintains bids/asks with snapshot + incremental updates and sequence checks."""

    def __init__(
        self,
        exchange: str,
        symbol: str,
        *,
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ) -> None:
        self.exchange = exchange.lower()
        self.symbol = symbol.upper().replace("-", "").replace("_", "").replace("/", "")
        self._max_depth = max(1, int(max_depth))
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._sequence: int | None = None
        self._synchronized = False
        self._needs_snapshot = True
        self._timestamp = datetime.now(UTC)
        self._received_at = datetime.now(UTC)
        self._exchange_ts_available = False
        self._timestamp_quality = "UNSUPPORTED"
        self.sequence_gap_count = 0
        self._cached_order_book: OrderBook | None = None
        self._cache_valid = False

    def _ingest_clock_meta(self, update: OrderBookUpdate) -> None:
        meta = update.metadata or {}
        if "exchange_ts_available" in meta:
            self._exchange_ts_available = bool(meta.get("exchange_ts_available"))
        if meta.get("timestamp_quality"):
            self._timestamp_quality = str(meta.get("timestamp_quality"))
        self._timestamp = update.timestamp
        self._received_at = update.received_at

    @property
    def synchronized(self) -> bool:
        return self._synchronized and not self._needs_snapshot

    @property
    def needs_snapshot(self) -> bool:
        return self._needs_snapshot

    @property
    def sequence(self) -> int | None:
        return self._sequence

    @property
    def age_ms(self) -> float:
        ts = self._timestamp if self._timestamp.tzinfo else self._timestamp.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - ts).total_seconds() * 1000.0)

    def mark_desynchronized(self, reason: str) -> None:
        self._synchronized = False
        self._needs_snapshot = True
        self._invalidate_cache()
        logger.info(
            "ORDER_BOOK_DESYNC exchange=%s symbol=%s reason=%s",
            self.exchange,
            self.symbol,
            reason,
        )

    def apply_snapshot(self, update: OrderBookUpdate) -> None:
        if update.exchange != self.exchange or update.symbol != self.symbol:
            raise ValueError("Snapshot exchange/symbol mismatch")
        self._bids = {level.price: level.amount for level in update.bids if level.amount > 0}
        self._asks = {level.price: level.amount for level in update.asks if level.amount > 0}
        self._trim_depth()
        self._sequence = update.sequence
        self._ingest_clock_meta(update)
        self._synchronized = True
        self._needs_snapshot = False
        self._invalidate_cache()
        logger.debug(
            "ORDER_BOOK_SYNCHRONIZED exchange=%s symbol=%s sequence=%s bids=%s asks=%s",
            self.exchange,
            self.symbol,
            self._sequence,
            len(self._bids),
            len(self._asks),
        )

    def apply_update(self, update: OrderBookUpdate) -> bool:
        """Apply incremental update. Returns False if a sequence gap forces rebuild."""
        if update.is_snapshot:
            self.apply_snapshot(update)
            return True

        if self._needs_snapshot or not self._synchronized:
            self.mark_desynchronized("update_before_snapshot")
            return False

        if update.sequence is not None and self._sequence is not None:
            expected = self._sequence + 1
            # Some venues use prev_sequence; prefer explicit gap check.
            if update.prev_sequence is not None and update.prev_sequence != self._sequence:
                self.sequence_gap_count += 1
                logger.info(
                    "SEQUENCE_GAP exchange=%s symbol=%s local=%s prev=%s next=%s",
                    self.exchange,
                    self.symbol,
                    self._sequence,
                    update.prev_sequence,
                    update.sequence,
                )
                self.mark_desynchronized("sequence_gap_prev")
                return False
            if update.prev_sequence is None and update.sequence > expected:
                self.sequence_gap_count += 1
                logger.info(
                    "SEQUENCE_GAP exchange=%s symbol=%s local=%s next=%s",
                    self.exchange,
                    self.symbol,
                    self._sequence,
                    update.sequence,
                )
                self.mark_desynchronized("sequence_gap")
                return False
            # Ignore duplicates / already-applied sequences
            if update.sequence <= self._sequence:
                logger.debug(
                    "DUPLICATE_MESSAGE exchange=%s symbol=%s sequence=%s",
                    self.exchange,
                    self.symbol,
                    update.sequence,
                )
                return True

        self._apply_levels(self._bids, update.bids)
        self._apply_levels(self._asks, update.asks)
        self._trim_depth()
        if update.sequence is not None:
            self._sequence = update.sequence
        self._ingest_clock_meta(update)
        self._invalidate_cache()
        return True

    @staticmethod
    def _apply_levels(
        book: dict[Decimal, Decimal], levels: list[OrderBookLevel]
    ) -> None:
        for level in levels:
            if level.amount <= 0:
                book.pop(level.price, None)
            else:
                book[level.price] = level.amount

    def _trim_depth(self) -> None:
        """Keep only the nearest ``max_depth`` levels on each side."""
        max_depth = self._max_depth
        if len(self._bids) > max_depth:
            keep = sorted(self._bids.keys(), reverse=True)[:max_depth]
            self._bids = {price: self._bids[price] for price in keep}
        if len(self._asks) > max_depth:
            keep = sorted(self._asks.keys())[:max_depth]
            self._asks = {price: self._asks[price] for price in keep}

    def _invalidate_cache(self) -> None:
        self._cache_valid = False
        self._cached_order_book = None

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self._bids:
            return None
        price = max(self._bids)
        return price, self._bids[price]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self._asks:
            return None
        price = min(self._asks)
        return price, self._asks[price]

    def top_liquidity(self, *, levels: int | None = None) -> Decimal:
        """Sum ask-side size for risk/liquidity checks without full materialization."""
        if not self._asks:
            return _ZERO
        limit = levels if levels is not None else self._max_depth
        if limit >= len(self._asks):
            return sum(self._asks.values(), _ZERO)
        prices = sorted(self._asks.keys())[:limit]
        return sum((self._asks[p] for p in prices), _ZERO)

    def to_order_book(self, *, max_levels: int | None = None) -> OrderBook | None:
        """Return normalized book only when synchronized.

        Result is cached between mutations when ``max_levels`` is omitted / full depth.
        """
        if not self.synchronized:
            return None
        limit = self._max_depth if max_levels is None else max(1, int(max_levels))
        use_cache = max_levels is None or limit >= self._max_depth
        if use_cache and self._cache_valid and self._cached_order_book is not None:
            return self._cached_order_book

        bid_items = sorted(self._bids.items(), key=lambda x: x[0], reverse=True)
        ask_items = sorted(self._asks.items(), key=lambda x: x[0])
        if limit < len(bid_items):
            bid_items = bid_items[:limit]
        if limit < len(ask_items):
            ask_items = ask_items[:limit]
        bids = [OrderBookLevel(price=p, amount=a) for p, a in bid_items]
        asks = [OrderBookLevel(price=p, amount=a) for p, a in ask_items]
        if not bids or not asks:
            return None
        book = OrderBook(
            symbol=self.symbol,
            bids=bids,
            asks=asks,
            timestamp=self._timestamp,
            nonce=self._sequence,
            metadata={
                "exchange": self.exchange,
                "received_at": self._received_at.isoformat(),
                "synchronized": True,
                "exchange_ts": self._timestamp.isoformat() if self._exchange_ts_available else None,
                "exchange_ts_available": self._exchange_ts_available,
                "timestamp_quality": self._timestamp_quality,
            },
        )
        if use_cache:
            self._cached_order_book = book
            self._cache_valid = True
        return book
