"""Cross-exchange arbitrage strategy.

Receives normalized multi-exchange order books, derives executable VWAP prices
from depth (not tickers), gates on NET profit via the profitability engine, and
emits ``TradeOpportunity`` objects only. Never places trades.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.interfaces import ProfitabilityEngine
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.core.venue_fees import venue_taker_fee
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class DepthFill:
    """Result of walking an order-book side for a target quantity."""

    vwap: Decimal
    filled_quantity: Decimal
    depth_available: Decimal
    levels_consumed: int
    sufficient: bool


@dataclass(frozen=True, slots=True)
class ArbitrageCandidate:
    """Internal buy-low / sell-high candidate before profitability gating."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    quantity: Decimal
    buy_vwap: Decimal
    sell_vwap: Decimal
    buy_depth: Decimal
    sell_depth: Decimal
    buy_snapshot: MarketSnapshot
    sell_snapshot: MarketSnapshot


class CrossExchangeArbitrageStrategy(BaseStrategy):
    """Detect simultaneous cross-exchange buy/sell opportunities.

    Pipeline inside the strategy (still no execution):

    1. Liquidity + latency / freshness checks on venue books
    2. Depth-based VWAP executable prices
    3. Profitability engine NET evaluation (fees, slippage, buffer, thresholds)
    4. Emit ``TradeOpportunity`` only when NET clears configured EUR / % gates

    Rejection reasons are logged; nothing is sent to an executor from here.
    """

    name = "cross_exchange_arbitrage"

    def __init__(
        self,
        settings: Settings,
        *,
        profitability: ProfitabilityEngine | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._settings = settings
        self._min_profit_eur = Decimal(str(settings.arbitrage_min_profit_eur))
        self._min_profit_pct = Decimal(str(settings.arbitrage_min_profit_pct))
        self._min_liquidity = Decimal(str(settings.arbitrage_min_liquidity_base))
        self._max_quantity = Decimal(str(settings.arbitrage_max_quantity))
        self._position_pct = Decimal(str(settings.arbitrage_position_pct))
        self._cooldown_ms = float(settings.arbitrage_opportunity_cooldown_ms)
        self._max_emits = int(settings.arbitrage_max_emits_per_cycle)
        self._max_latency_ms = settings.arbitrage_max_latency_ms
        self._max_book_age_ms = settings.arbitrage_max_book_age_ms
        self._profitability = profitability or self._build_profitability_engine(settings)
        self._pairs_evaluated = 0
        self._depth_edges_found = 0
        self._scan_rejections = 0
        self._opportunities_emitted = 0
        self._reject_counts: dict[str, int] = {}
        self._last_emit_monotonic: dict[str, float] = {}

    @staticmethod
    def _build_profitability_engine(settings: Settings) -> DefaultProfitabilityEngine:
        """Wire NET gates to arbitrage EUR / percentage thresholds (spot: no funding)."""
        arb_settings = settings.model_copy(
            update={
                "profitability_min_net_profit_usd": settings.arbitrage_min_profit_eur,
                "profitability_min_net_return": settings.arbitrage_min_profit_pct,
                "profitability_apply_funding": False,
            }
        )
        return DefaultProfitabilityEngine(arb_settings)

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        self._reject(
            snapshot.symbol,
            "single_snapshot",
            "Cross-exchange arbitrage requires order books from multiple exchanges",
            exchange=snapshot.exchange,
        )
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
    ) -> list[TradeOpportunity]:
        by_symbol = self._group_valid_snapshots(snapshots)
        opportunities: list[TradeOpportunity] = []

        for symbol, venues in by_symbol.items():
            if len(venues) < 2:
                self._reject(
                    symbol,
                    "insufficient_venues",
                    f"Need at least 2 exchange books, got {len(venues)}",
                )
                continue
            opportunities.extend(await self._evaluate_symbol(symbol, venues, equity=equity))

        return opportunities

    async def _evaluate_symbol(
        self,
        symbol: str,
        venues: list[MarketSnapshot],
        *,
        equity: Decimal | None = None,
    ) -> list[TradeOpportunity]:
        ranked: list[TradeOpportunity] = []
        for buy_snap in venues:
            for sell_snap in venues:
                if buy_snap.exchange == sell_snap.exchange:
                    continue
                self._pairs_evaluated += 1
                candidate = self._build_candidate(buy_snap, sell_snap, equity=equity)
                if candidate is None:
                    continue
                self._depth_edges_found += 1
                opportunity = await self._gate_candidate(candidate)
                if opportunity is None:
                    continue
                if self._in_cooldown(opportunity):
                    self._reject(
                        symbol,
                        "cooldown",
                        "Pair recently traded; waiting for cooldown",
                        buy_exchange=candidate.buy_exchange,
                        sell_exchange=candidate.sell_exchange,
                    )
                    continue
                ranked.append(opportunity)

        ranked.sort(
            key=lambda o: Decimal(str(o.metadata.get("net_profit_eur", "0"))),
            reverse=True,
        )
        selected = ranked[: self._max_emits]
        for opp in selected:
            self._opportunities_emitted += 1
            self._mark_emitted(opp)
        return selected

    def _in_cooldown(self, opportunity: TradeOpportunity) -> bool:
        if self._cooldown_ms <= 0:
            return False
        key = self._pair_key(opportunity)
        last = self._last_emit_monotonic.get(key)
        if last is None:
            return False
        return (time.monotonic() - last) * 1000.0 < self._cooldown_ms

    def _mark_emitted(self, opportunity: TradeOpportunity) -> None:
        self._last_emit_monotonic[self._pair_key(opportunity)] = time.monotonic()

    @staticmethod
    def _pair_key(opportunity: TradeOpportunity) -> str:
        meta = opportunity.metadata or {}
        return (
            f"{opportunity.symbol.upper()}|{meta.get('buy_exchange')}|"
            f"{meta.get('sell_exchange')}"
        )

    def _group_valid_snapshots(
        self,
        snapshots: Sequence[MarketSnapshot],
    ) -> dict[str, list[MarketSnapshot]]:
        grouped: dict[str, list[MarketSnapshot]] = {}
        for snapshot in snapshots:
            reason = self._venue_rejection_reason(snapshot)
            if reason is not None:
                self._reject(
                    snapshot.symbol,
                    reason[0],
                    reason[1],
                    exchange=snapshot.exchange,
                )
                continue
            assert snapshot.exchange is not None
            grouped.setdefault(snapshot.symbol.upper(), []).append(snapshot)
        return grouped

    def _venue_rejection_reason(
        self,
        snapshot: MarketSnapshot,
    ) -> tuple[str, str] | None:
        if not snapshot.exchange:
            return ("missing_exchange", "Snapshot is missing exchange identifier")
        if snapshot.order_book is None:
            return ("missing_order_book", "Snapshot has no order book depth")
        book = snapshot.order_book
        if not book.asks or not book.bids:
            return ("empty_book", "Order book missing bids or asks")

        ask_depth = _side_depth(book.asks)
        bid_depth = _side_depth(book.bids)
        if ask_depth < self._min_liquidity or bid_depth < self._min_liquidity:
            return (
                "insufficient_liquidity",
                (
                    f"Book depth ask={ask_depth} bid={bid_depth} "
                    f"below min_liquidity={self._min_liquidity}"
                ),
            )

        if snapshot.latency_ms is not None and snapshot.latency_ms > self._max_latency_ms:
            return (
                "latency",
                f"Latency {snapshot.latency_ms}ms exceeds max {self._max_latency_ms}ms",
            )

        age_ms = _book_age_ms(snapshot)
        if age_ms > self._max_book_age_ms:
            return (
                "stale_book",
                f"Book age {age_ms:.1f}ms exceeds max {self._max_book_age_ms}ms",
            )
        return None

    def _build_candidate(
        self,
        buy_snap: MarketSnapshot,
        sell_snap: MarketSnapshot,
        *,
        equity: Decimal | None = None,
    ) -> ArbitrageCandidate | None:
        assert buy_snap.order_book is not None
        assert sell_snap.order_book is not None
        assert buy_snap.exchange is not None
        assert sell_snap.exchange is not None

        buy_depth = _side_depth(buy_snap.order_book.asks)
        sell_depth = _side_depth(sell_snap.order_book.bids)
        quantity_cap = self._max_quantity
        if equity is not None and equity > 0 and self._position_pct > 0:
            # Scale trade size with equity while respecting the hard max quantity cap.
            max_notional = equity * (self._position_pct / Decimal("100"))
            ref_ask = buy_snap.order_book.asks[0].price if buy_snap.order_book.asks else _ZERO
            if ref_ask > 0:
                quantity_cap = min(quantity_cap, max_notional / ref_ask)
        quantity = min(buy_depth, sell_depth, quantity_cap)
        if quantity < self._min_liquidity:
            self._reject(
                buy_snap.symbol,
                "insufficient_overlapping_liquidity",
                (
                    f"Overlap qty {quantity} below min_liquidity {self._min_liquidity} "
                    f"(buy={buy_snap.exchange} ask_depth={buy_depth}, "
                    f"sell={sell_snap.exchange} bid_depth={sell_depth})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        buy_fill = walk_book(buy_snap.order_book.asks, quantity)
        sell_fill = walk_book(sell_snap.order_book.bids, quantity)
        if not buy_fill.sufficient or not sell_fill.sufficient:
            self._reject(
                buy_snap.symbol,
                "insufficient_depth_for_quantity",
                (
                    f"Cannot fill quantity {quantity} on depth "
                    f"(buy_filled={buy_fill.filled_quantity}, "
                    f"sell_filled={sell_fill.filled_quantity})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        # Ticker top-of-book alone is not enough; require depth VWAP edge.
        if sell_fill.vwap <= buy_fill.vwap:
            self._reject(
                buy_snap.symbol,
                "no_depth_edge",
                (
                    f"No executable cross-exchange edge after depth: "
                    f"buy_vwap={buy_fill.vwap} on {buy_snap.exchange}, "
                    f"sell_vwap={sell_fill.vwap} on {sell_snap.exchange}"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        return ArbitrageCandidate(
            symbol=buy_snap.symbol.upper(),
            buy_exchange=buy_snap.exchange,
            sell_exchange=sell_snap.exchange,
            quantity=quantity,
            buy_vwap=buy_fill.vwap,
            sell_vwap=sell_fill.vwap,
            buy_depth=buy_depth,
            sell_depth=sell_depth,
            buy_snapshot=buy_snap,
            sell_snapshot=sell_snap,
        )

    async def _gate_candidate(
        self,
        candidate: ArbitrageCandidate,
    ) -> TradeOpportunity | None:
        opportunity = TradeOpportunity(
            strategy_name=self.name,
            symbol=candidate.symbol,
            side=OpportunitySide.BUY,
            quantity=candidate.quantity,
            entry_price=candidate.buy_vwap,
            expected_exit_price=candidate.sell_vwap,
            confidence=0.6,
            rationale=(
                f"Buy {candidate.quantity} on {candidate.buy_exchange} @ VWAP "
                f"{candidate.buy_vwap}; sell on {candidate.sell_exchange} @ VWAP "
                f"{candidate.sell_vwap}"
            ),
            # Depth is already in VWAP prices — strip books so slippage does not
            # re-apply single-venue impact against the wrong exit book.
            market=candidate.buy_snapshot.model_copy(update={"order_book": None}),
            entry_fee_role=FeeRole.TAKER,
            exit_fee_role=FeeRole.TAKER,
            funding_periods=_ZERO,  # spot cross-exchange arb
            metadata={
                "buy_exchange": candidate.buy_exchange,
                "sell_exchange": candidate.sell_exchange,
                "buy_vwap": str(candidate.buy_vwap),
                "sell_vwap": str(candidate.sell_vwap),
                "buy_depth": str(candidate.buy_depth),
                "sell_depth": str(candidate.sell_depth),
                "buy_taker_fee_rate": str(venue_taker_fee(candidate.buy_exchange)),
                "sell_taker_fee_rate": str(venue_taker_fee(candidate.sell_exchange)),
                "pricing": "order_book_depth_vwap",
                "quote_currency": "EUR",
                "round_trip": True,
            },
        )

        result = await self._profitability.evaluate(
            opportunity,
            buy_fee_rate=venue_taker_fee(candidate.buy_exchange),
            sell_fee_rate=venue_taker_fee(candidate.sell_exchange),
        )
        net = result.net_profit_usd
        net_return = result.net_return

        if not result.trade_allowed:
            reasons = (
                result.estimate.disallow_reasons
                if result.estimate is not None
                else ["profitability engine rejected"]
            )
            self._reject(
                candidate.symbol,
                "profitability",
                "; ".join(reasons) or "NET profit below profitability thresholds",
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_profit_eur=str(net),
                net_return=str(net_return),
            )
            return None

        if net < self._min_profit_eur:
            self._reject(
                candidate.symbol,
                "min_profit_eur",
                f"NET profit {net} EUR below minimum {self._min_profit_eur} EUR",
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_profit_eur=str(net),
            )
            return None

        if net_return < self._min_profit_pct:
            self._reject(
                candidate.symbol,
                "min_profit_pct",
                f"NET return {net_return} below minimum {self._min_profit_pct}",
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_return=str(net_return),
            )
            return None

        logger.info(
            "arbitrage opportunity accepted symbol=%s buy=%s sell=%s qty=%s "
            "net_profit_eur=%s net_return=%s",
            candidate.symbol,
            candidate.buy_exchange,
            candidate.sell_exchange,
            candidate.quantity,
            net,
            net_return,
        )
        return opportunity.model_copy(
            update={
                "confidence": min(0.95, 0.5 + float(net_return) * 10.0),
                "metadata": {
                    **opportunity.metadata,
                    "net_profit_eur": str(net),
                    "net_return": str(net_return),
                    "gross_profit_eur": str(result.gross_profit_usd),
                },
            }
        )

    def _reject(
        self,
        symbol: str,
        code: str,
        reason: str,
        **context: object,
    ) -> None:
        self._scan_rejections += 1
        self._reject_counts[code] = self._reject_counts.get(code, 0) + 1
        extras = " ".join(f"{key}={value}" for key, value in context.items() if value is not None)
        logger.debug(
            "arbitrage opportunity rejected symbol=%s code=%s reason=%s%s",
            symbol,
            code,
            reason,
            f" {extras}" if extras else "",
        )

    def scan_stats(self) -> dict[str, object]:
        """Cumulative opportunity-scan funnel for dashboards."""
        return {
            "pairs_evaluated": self._pairs_evaluated,
            "depth_edges_found": self._depth_edges_found,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": dict(sorted(self._reject_counts.items())),
        }


def walk_book(levels: Sequence[OrderBookLevel], quantity: Decimal) -> DepthFill:
    """Walk order-book levels to compute executable VWAP for ``quantity``."""
    if quantity <= 0:
        return DepthFill(
            vwap=_ZERO,
            filled_quantity=_ZERO,
            depth_available=_ZERO,
            levels_consumed=0,
            sufficient=False,
        )

    remaining = quantity
    notional = _ZERO
    filled = _ZERO
    depth_available = _side_depth(levels)
    levels_consumed = 0

    for level in levels:
        if remaining <= 0:
            break
        if level.amount <= 0:
            continue
        take = min(remaining, level.amount)
        notional += take * level.price
        filled += take
        remaining -= take
        levels_consumed += 1

    if filled <= 0:
        return DepthFill(
            vwap=_ZERO,
            filled_quantity=_ZERO,
            depth_available=depth_available,
            levels_consumed=0,
            sufficient=False,
        )

    return DepthFill(
        vwap=notional / filled,
        filled_quantity=filled,
        depth_available=depth_available,
        levels_consumed=levels_consumed,
        sufficient=remaining <= 0,
    )


def _side_depth(levels: Sequence[OrderBookLevel]) -> Decimal:
    return sum((level.amount for level in levels), _ZERO)


def _book_age_ms(snapshot: MarketSnapshot) -> float:
    """Age of the book/snapshot relative to now (milliseconds)."""
    ts = snapshot.timestamp
    if snapshot.order_book is not None:
        ts = snapshot.order_book.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age = datetime.now(UTC) - ts
    return max(0.0, age.total_seconds() * 1000.0)


def top_of_book_snapshot(
    *,
    exchange: str,
    symbol: str,
    order_book: OrderBook,
    latency_ms: float | None = 10.0,
) -> MarketSnapshot:
    """Helper to build a ``MarketSnapshot`` from a synthetic order book (tests)."""
    best_bid = order_book.bids[0].price if order_book.bids else _ZERO
    best_ask = order_book.asks[0].price if order_book.asks else _ZERO
    mid = (best_bid + best_ask) / Decimal("2") if best_bid and best_ask else _ZERO
    return MarketSnapshot(
        symbol=symbol,
        bid=best_bid,
        ask=best_ask,
        last=mid,
        order_book=order_book,
        exchange=exchange,
        latency_ms=latency_ms,
        timestamp=order_book.timestamp,
    )
