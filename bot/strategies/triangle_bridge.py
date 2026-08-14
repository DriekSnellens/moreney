"""EUR↔USDT triangle bridge: buy BASEUSDT, sell BASEEUR (or reverse) with FX.

Settlement is two maker legs on different symbols plus optional FX refill.
USDT inventory is pre-seeded / rebalanced from EUR via EURUSDT.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.portfolio.venue_ledger import infer_base_asset
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.arbitrage import _book_age_ms, top_of_book_snapshot
from bot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class TriangleCandidate:
    base: str
    buy_symbol: str
    sell_symbol: str
    buy_exchange: str
    sell_exchange: str
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    buy_snapshot: MarketSnapshot
    sell_snapshot: MarketSnapshot
    fx_mid: Decimal
    sell_eur_equivalent: Decimal
    direction: str  # usdt_to_eur | eur_to_usdt


class TriangleBridgeStrategy(BaseStrategy):
    """Cross-quote maker bridge using USDT as settlement currency."""

    name = "triangle_bridge"

    def __init__(self, settings: Settings, *, name: str | None = None) -> None:
        super().__init__(name=name)
        self._settings = settings
        self._enabled = bool(getattr(settings, "paper_triangle_enabled", True))
        self._fx_symbol = str(
            getattr(settings, "paper_maker_fx_symbol", "EURUSDT") or "EURUSDT"
        ).upper()
        self._min_profit = Decimal(
            str(getattr(settings, "paper_maker_min_profit_eur", 0.001) or 0.001)
        )
        self._min_return = Decimal(str(settings.arbitrage_min_profit_pct))
        self._min_liquidity = Decimal(str(settings.arbitrage_min_liquidity_base))
        self._max_quantity = Decimal(str(settings.arbitrage_max_quantity))
        self._position_pct = Decimal(str(settings.arbitrage_position_pct))
        self._max_emits = int(settings.arbitrage_max_emits_per_cycle)
        self._max_edge_bps = Decimal(
            str(getattr(settings, "paper_maker_max_edge_bps", 30) or 30)
        )
        self._adverse = Decimal(
            str(getattr(settings, "paper_maker_adverse_bps", 4) or 4)
        )
        self._max_book_age_ms = settings.arbitrage_max_book_age_ms
        self._maker_venues = {
            part.strip().lower()
            for part in str(getattr(settings, "paper_maker_venues", "") or "").split(",")
            if part.strip()
        }
        self._bases = {
            part.strip().upper()
            for part in str(
                getattr(settings, "paper_triangle_bases", "BTC,ETH,ATOM,DOT,XRP") or ""
            ).split(",")
            if part.strip()
        }
        self._profitability = DefaultProfitabilityEngine(
            settings.model_copy(
                update={
                    "profitability_min_net_profit_usd": float(self._min_profit),
                    "profitability_min_net_return": float(self._min_return),
                    "profitability_apply_funding": False,
                    "profitability_slippage_bps": 0.0,
                    "profitability_thin_book_penalty_bps": 0.0,
                    "profitability_execution_buffer_bps": 1.0 + float(self._adverse),
                }
            )
        )
        self._pairs_evaluated = 0
        self._depth_edges_found = 0
        self._scan_rejections = 0
        self._opportunities_emitted = 0
        self._reject_counts: dict[str, int] = {}
        self._last_emit: dict[str, float] = {}
        self._cooldown_ms = float(settings.arbitrage_opportunity_cooldown_ms)

    def update_adverse_bps(self, adverse_bps: Decimal) -> None:
        self._adverse = Decimal(str(adverse_bps))
        updated = self._settings.model_copy(
            update={"paper_maker_adverse_bps": float(self._adverse)}
        )
        self._settings = updated
        self._profitability = DefaultProfitabilityEngine(
            updated.model_copy(
                update={
                    "profitability_min_net_profit_usd": float(self._min_profit),
                    "profitability_min_net_return": float(self._min_return),
                    "profitability_apply_funding": False,
                    "profitability_slippage_bps": 0.0,
                    "profitability_thin_book_penalty_bps": 0.0,
                    "profitability_execution_buffer_bps": 1.0 + float(self._adverse),
                }
            )
        )

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
    ) -> list[TradeOpportunity]:
        if not self._enabled:
            return []
        by_symbol: dict[str, list[MarketSnapshot]] = {}
        for snap in snapshots:
            if snap.order_book is None or not snap.exchange:
                continue
            if not snap.order_book.bids or not snap.order_book.asks:
                continue
            if _book_age_ms(snap) > self._max_book_age_ms:
                continue
            by_symbol.setdefault(snap.symbol.upper(), []).append(snap)

        fx_mid = self._mid(by_symbol.get(self._fx_symbol) or [])
        if fx_mid is None or fx_mid <= 0:
            self._reject("FX", "missing_fx", f"No mid for {self._fx_symbol}")
            return []

        ranked: list[TradeOpportunity] = []
        for base in sorted(self._bases):
            eur_sym = f"{base}EUR"
            usdt_sym = f"{base}USDT"
            eur_snaps = [
                s
                for s in (by_symbol.get(eur_sym) or [])
                if self._venue_ok(s.exchange)
            ]
            usdt_snaps = [
                s
                for s in (by_symbol.get(usdt_sym) or [])
                if self._venue_ok(s.exchange)
            ]
            if not eur_snaps or not usdt_snaps:
                continue
            for buy_snap in usdt_snaps:
                for sell_snap in eur_snaps:
                    self._pairs_evaluated += 1
                    cand = self._build_usdt_to_eur(
                        base,
                        buy_snap,
                        sell_snap,
                        fx_mid=fx_mid,
                        equity=equity,
                        inventory=inventory,
                    )
                    if cand is None:
                        continue
                    self._depth_edges_found += 1
                    opp = await self._gate(cand)
                    if opp is not None:
                        ranked.append(opp)
            for buy_snap in eur_snaps:
                for sell_snap in usdt_snaps:
                    self._pairs_evaluated += 1
                    cand = self._build_eur_to_usdt(
                        base,
                        buy_snap,
                        sell_snap,
                        fx_mid=fx_mid,
                        equity=equity,
                        inventory=inventory,
                    )
                    if cand is None:
                        continue
                    self._depth_edges_found += 1
                    opp = await self._gate(cand)
                    if opp is not None:
                        ranked.append(opp)

        ranked.sort(
            key=lambda o: Decimal(str((o.metadata or {}).get("net_profit_eur", "0"))),
            reverse=True,
        )
        selected = ranked[: self._max_emits]
        for opp in selected:
            self._opportunities_emitted += 1
            key = (
                f"{opp.symbol}|{(opp.metadata or {}).get('buy_exchange')}|"
                f"{(opp.metadata or {}).get('sell_exchange')}|"
                f"{(opp.metadata or {}).get('direction')}"
            )
            self._last_emit[key] = time.monotonic()
        return selected

    def _venue_ok(self, exchange: str | None) -> bool:
        if not self._maker_venues:
            return True
        return str(exchange or "").strip().lower() in self._maker_venues

    def _mid(self, snaps: list[MarketSnapshot]) -> Decimal | None:
        mids: list[Decimal] = []
        for snap in snaps:
            book = snap.order_book
            if book is None or not book.bids or not book.asks:
                continue
            mids.append((book.bids[0].price + book.asks[0].price) / Decimal("2"))
        if not mids:
            return None
        mids.sort()
        return mids[len(mids) // 2]

    def _build_usdt_to_eur(
        self,
        base: str,
        buy_snap: MarketSnapshot,
        sell_snap: MarketSnapshot,
        *,
        fx_mid: Decimal,
        equity: Decimal | None,
        inventory: object,
    ) -> TriangleCandidate | None:
        assert buy_snap.order_book and sell_snap.order_book
        level = int(getattr(self._settings, "paper_maker_book_level", 0) or 0)
        buy_lvl = min(level, len(buy_snap.order_book.bids) - 1)
        sell_lvl = min(level, len(sell_snap.order_book.asks) - 1)
        buy_price = buy_snap.order_book.bids[buy_lvl].price  # maker buy USDT pair
        sell_price = sell_snap.order_book.asks[sell_lvl].price  # maker sell EUR pair
        buy_touch = buy_snap.order_book.bids[buy_lvl].amount
        sell_touch = sell_snap.order_book.asks[sell_lvl].amount
        # Convert buy USDT cost to EUR via FX mid.
        buy_eur = buy_price / fx_mid
        if sell_price <= buy_eur:
            self._reject(base, "no_triangle_edge", "EUR sell not above USDT-implied EUR buy")
            return None
        edge_bps = (sell_price - buy_eur) / buy_eur * _BPS
        if self._max_edge_bps > 0 and edge_bps > self._max_edge_bps:
            self._reject(base, "stale_edge", f"Triangle edge {edge_bps} bps too wide")
            return None
        fee_bps = (
            venue_maker_fee(buy_snap.exchange) + venue_maker_fee(sell_snap.exchange)
        ) * _BPS
        # FX refill later as taker on EURUSDT.
        fx_taker = venue_taker_fee(buy_snap.exchange) * _BPS
        if fee_bps + fx_taker + Decimal("1") + self._adverse >= edge_bps:
            self._reject(base, "fees_eat_edge", "Triangle fees+FX eat edge")
            return None
        qty = min(buy_touch, sell_touch, self._max_quantity)
        if equity and equity > 0 and self._position_pct > 0:
            qty = min(qty, (equity * self._position_pct / Decimal("100")) / buy_eur)
        qty = self._cap_inventory(
            qty,
            buy_venue=str(buy_snap.exchange),
            sell_venue=str(sell_snap.exchange),
            buy_quote="USDT",
            buy_price=buy_price,
            base=base,
            inventory=inventory,
        )
        if qty < self._min_liquidity:
            self._reject(base, "venue_inventory", "Triangle size below min liquidity")
            return None
        return TriangleCandidate(
            base=base,
            buy_symbol=f"{base}USDT",
            sell_symbol=f"{base}EUR",
            buy_exchange=str(buy_snap.exchange),
            sell_exchange=str(sell_snap.exchange),
            quantity=qty,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_snapshot=buy_snap,
            sell_snapshot=sell_snap,
            fx_mid=fx_mid,
            sell_eur_equivalent=sell_price,
            direction="usdt_to_eur",
        )

    def _build_eur_to_usdt(
        self,
        base: str,
        buy_snap: MarketSnapshot,
        sell_snap: MarketSnapshot,
        *,
        fx_mid: Decimal,
        equity: Decimal | None,
        inventory: object,
    ) -> TriangleCandidate | None:
        assert buy_snap.order_book and sell_snap.order_book
        level = int(getattr(self._settings, "paper_maker_book_level", 0) or 0)
        buy_lvl = min(level, len(buy_snap.order_book.bids) - 1)
        sell_lvl = min(level, len(sell_snap.order_book.asks) - 1)
        buy_price = buy_snap.order_book.bids[buy_lvl].price
        sell_usdt = sell_snap.order_book.asks[sell_lvl].price
        sell_eur = sell_usdt / fx_mid
        buy_touch = buy_snap.order_book.bids[buy_lvl].amount
        sell_touch = sell_snap.order_book.asks[sell_lvl].amount
        if sell_eur <= buy_price:
            self._reject(base, "no_triangle_edge", "USDT sell not above EUR buy")
            return None
        edge_bps = (sell_eur - buy_price) / buy_price * _BPS
        if self._max_edge_bps > 0 and edge_bps > self._max_edge_bps:
            self._reject(base, "stale_edge", f"Triangle edge {edge_bps} bps too wide")
            return None
        fee_bps = (
            venue_maker_fee(buy_snap.exchange) + venue_maker_fee(sell_snap.exchange)
        ) * _BPS
        fx_taker = venue_taker_fee(sell_snap.exchange) * _BPS
        if fee_bps + fx_taker + Decimal("1") + self._adverse >= edge_bps:
            self._reject(base, "fees_eat_edge", "Triangle fees+FX eat edge")
            return None
        qty = min(buy_touch, sell_touch, self._max_quantity)
        if equity and equity > 0 and self._position_pct > 0:
            qty = min(qty, (equity * self._position_pct / Decimal("100")) / buy_price)
        qty = self._cap_inventory(
            qty,
            buy_venue=str(buy_snap.exchange),
            sell_venue=str(sell_snap.exchange),
            buy_quote="EUR",
            buy_price=buy_price,
            base=base,
            inventory=inventory,
        )
        if qty < self._min_liquidity:
            self._reject(base, "venue_inventory", "Triangle size below min liquidity")
            return None
        return TriangleCandidate(
            base=base,
            buy_symbol=f"{base}EUR",
            sell_symbol=f"{base}USDT",
            buy_exchange=str(buy_snap.exchange),
            sell_exchange=str(sell_snap.exchange),
            quantity=qty,
            buy_price=buy_price,
            sell_price=sell_usdt,
            buy_snapshot=buy_snap,
            sell_snapshot=sell_snap,
            fx_mid=fx_mid,
            sell_eur_equivalent=sell_eur,
            direction="eur_to_usdt",
        )

    def _cap_inventory(
        self,
        quantity: Decimal,
        *,
        buy_venue: str,
        sell_venue: str,
        buy_quote: str,
        buy_price: Decimal,
        base: str,
        inventory: object,
    ) -> Decimal:
        if inventory is None or quantity <= 0:
            return quantity
        available = getattr(inventory, "available", None)
        if not callable(available):
            return quantity
        fee = Decimal("1") + venue_maker_fee(buy_venue)
        cash = available(buy_venue, buy_quote)
        coins = available(sell_venue, base)
        max_buy = cash / (buy_price * fee) if buy_price > 0 else _ZERO
        return min(quantity, max_buy, coins)

    async def _gate(self, candidate: TriangleCandidate) -> TradeOpportunity | None:
        key = (
            f"{candidate.base}|{candidate.buy_exchange}|{candidate.sell_exchange}|"
            f"{candidate.direction}"
        )
        last = self._last_emit.get(key)
        if (
            last is not None
            and self._cooldown_ms > 0
            and (time.monotonic() - last) * 1000.0 < self._cooldown_ms
        ):
            self._reject(candidate.base, "cooldown", "Triangle pair in cooldown")
            return None

        # Represent opportunity in EUR terms for risk/profit gates.
        entry_eur = (
            candidate.buy_price / candidate.fx_mid
            if candidate.direction == "usdt_to_eur"
            else candidate.buy_price
        )
        exit_eur = candidate.sell_eur_equivalent
        opportunity = TradeOpportunity(
            strategy_name=self.name,
            symbol=f"{candidate.base}EUR",
            side=OpportunitySide.BUY,
            quantity=candidate.quantity,
            entry_price=entry_eur,
            expected_exit_price=exit_eur,
            confidence=0.5,
            rationale=(
                f"Triangle {candidate.direction}: maker buy {candidate.buy_symbol} "
                f"@{candidate.buy_price} on {candidate.buy_exchange}; maker sell "
                f"{candidate.sell_symbol} @{candidate.sell_price} on {candidate.sell_exchange}"
            ),
            market=candidate.buy_snapshot.model_copy(update={"order_book": None}),
            entry_fee_role=FeeRole.MAKER,
            exit_fee_role=FeeRole.MAKER,
            funding_periods=_ZERO,
            metadata={
                "triangle": True,
                "direction": candidate.direction,
                "buy_exchange": candidate.buy_exchange,
                "sell_exchange": candidate.sell_exchange,
                "buy_symbol": candidate.buy_symbol,
                "sell_symbol": candidate.sell_symbol,
                "buy_vwap": str(candidate.buy_price),
                "sell_vwap": str(candidate.sell_price),
                "buy_maker_fee_rate": str(venue_maker_fee(candidate.buy_exchange)),
                "sell_maker_fee_rate": str(venue_maker_fee(candidate.sell_exchange)),
                "fx_symbol": self._fx_symbol,
                "fx_mid": str(candidate.fx_mid),
                "pricing": "triangle_maker",
                "quote_currency": "EUR",
                "round_trip": True,
                "post_only": True,
                "hybrid_hedge": True,
            },
        )
        result = await self._profitability.evaluate(
            opportunity,
            buy_fee_rate=venue_maker_fee(candidate.buy_exchange),
            sell_fee_rate=venue_maker_fee(candidate.sell_exchange),
        )
        if not result.trade_allowed or result.net_profit_usd < self._min_profit:
            self._reject(
                candidate.base,
                "profitability",
                f"Triangle NET {result.net_profit_usd} below gate",
            )
            return None
        return opportunity.model_copy(
            update={
                "metadata": {
                    **opportunity.metadata,
                    "net_profit_eur": str(result.net_profit_usd),
                    "net_return": str(result.net_return),
                    "gross_profit_eur": str(result.gross_profit_usd),
                }
            }
        )

    def _reject(self, symbol: str, code: str, reason: str) -> None:
        self._scan_rejections += 1
        self._reject_counts[code] = self._reject_counts.get(code, 0) + 1
        logger.debug("triangle rejected symbol=%s code=%s reason=%s", symbol, code, reason)

    def scan_stats(self) -> dict[str, object]:
        return {
            "pairs_evaluated": self._pairs_evaluated,
            "depth_edges_found": self._depth_edges_found,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": dict(sorted(self._reject_counts.items())),
        }


class CompositeDeskStrategy(BaseStrategy):
    """Maker inventory + triangle bridge in one universe evaluate."""

    name = "desk_composite"

    def __init__(
        self,
        settings: Settings,
        *,
        maker: BaseStrategy,
        triangle: BaseStrategy,
    ) -> None:
        super().__init__()
        self._maker = maker
        self._triangle = triangle
        self._max_emits = int(settings.arbitrage_max_emits_per_cycle)

    def update_adverse_bps(self, adverse_bps: Decimal) -> None:
        if hasattr(self._maker, "update_adverse_bps"):
            self._maker.update_adverse_bps(adverse_bps)  # type: ignore[attr-defined]
        if hasattr(self._triangle, "update_adverse_bps"):
            self._triangle.update_adverse_bps(adverse_bps)  # type: ignore[attr-defined]

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
        portfolio_state: object = None,
    ) -> list[TradeOpportunity]:
        kwargs: dict = {
            "equity": equity,
            "inventory": inventory,
            "portfolio_state": portfolio_state,
        }
        maker_opps = await self._maker.evaluate_markets(snapshots, **kwargs)
        try:
            tri_opps = await self._triangle.evaluate_markets(snapshots, **kwargs)
        except TypeError:
            tri_opps = await self._triangle.evaluate_markets(
                snapshots, equity=equity, inventory=inventory
            )
        combined = list(maker_opps) + list(tri_opps)
        combined.sort(
            key=lambda o: Decimal(str((o.metadata or {}).get("net_profit_eur", "0"))),
            reverse=True,
        )
        return combined[: self._max_emits]

    def scan_stats(self) -> dict[str, object]:
        maker = self._maker.scan_stats() if hasattr(self._maker, "scan_stats") else {}
        tri = self._triangle.scan_stats() if hasattr(self._triangle, "scan_stats") else {}
        reject: dict[str, int] = {}
        for src in (maker, tri):
            for k, v in (src.get("reject_counts") or {}).items():  # type: ignore[union-attr]
                reject[str(k)] = reject.get(str(k), 0) + int(v)
        return {
            "pairs_evaluated": int(maker.get("pairs_evaluated", 0) or 0)
            + int(tri.get("pairs_evaluated", 0) or 0),
            "depth_edges_found": int(maker.get("depth_edges_found", 0) or 0)
            + int(tri.get("depth_edges_found", 0) or 0),
            "scan_rejections": int(maker.get("scan_rejections", 0) or 0)
            + int(tri.get("scan_rejections", 0) or 0),
            "opportunities_emitted": int(maker.get("opportunities_emitted", 0) or 0)
            + int(tri.get("opportunities_emitted", 0) or 0),
            "reject_counts": dict(sorted(reject.items())),
            "maker": maker,
            "triangle": tri,
        }
