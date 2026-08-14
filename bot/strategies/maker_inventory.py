"""Maker inventory quoting: capture bid/ask instead of paying taker-taker.

Taker-taker cross-exchange arb pays both spreads and both taker fees, so it is
structurally unprofitable on public books. This strategy rests post-only:

* buy at the best bid (maker) on the cheaper venue
* sell at the best ask (maker) on the richer venue

Coins never teleport: size is capped by per-venue EUR and pre-funded inventory.
Never places orders — only emits ``TradeOpportunity`` objects.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.interfaces import ProfitabilityEngine
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.core.venue_fees import venue_maker_fee
from bot.paper.capital_policy import (
    InventorySkewPolicy,
    NetProfitDustFilter,
    QuoteSkew,
    VolatilityDumpGuard,
)
from bot.portfolio.models import PortfolioState
from bot.portfolio.venue_ledger import infer_base_asset, infer_quote_asset
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.strategies.arbitrage import _book_age_ms, _side_depth
from bot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class MakerCandidate:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    buy_snapshot: MarketSnapshot
    sell_snapshot: MarketSnapshot


class MakerInventoryStrategy(BaseStrategy):
    """Post-only bid/ask capture across (or on) venues with pre-funded inventory."""

    name = "maker_inventory"

    def __init__(
        self,
        settings: Settings,
        *,
        profitability: ProfitabilityEngine | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self._settings = settings
        self._min_profit_eur = Decimal(str(settings.paper_maker_min_profit_eur))
        self._min_profit_equity_bps = Decimal(
            str(getattr(settings, "paper_maker_min_profit_equity_bps", 0) or 0)
        )
        self._keep_vs_best_frac = Decimal(
            str(getattr(settings, "paper_maker_keep_vs_best_frac", 0) or 0)
        )
        self._replace_improve_frac = Decimal(
            str(getattr(settings, "paper_maker_replace_improve_frac", 0.25) or 0)
        )
        self._min_profit_pct = Decimal(
            str(
                getattr(settings, "paper_maker_min_net_return", None)
                or settings.arbitrage_min_profit_pct
            )
        )
        self._dust = NetProfitDustFilter(
            min_net_profit_eur=self._min_profit_eur,
            min_net_return=self._min_profit_pct,
            min_notional_eur=Decimal(
                str(getattr(settings, "paper_maker_min_notional_eur", 10) or 0)
            ),
        )
        self._skew_policy = InventorySkewPolicy(
            max_alt_pct=Decimal(
                str(getattr(settings, "paper_max_alt_inventory_pct", 30) or 30)
            ),
            min_alt_pct=Decimal(
                str(getattr(settings, "paper_min_alt_inventory_pct", 10) or 10)
            ),
            overweight_ask_improve_bps=Decimal(
                str(getattr(settings, "paper_inventory_ask_improve_bps", 4) or 0)
            ),
            underweight_buy_extra_bps=Decimal(
                str(getattr(settings, "paper_inventory_buy_dip_bps", 8) or 0)
            ),
        )
        self._vol_guard = VolatilityDumpGuard(
            move_pct=Decimal(str(getattr(settings, "paper_vol_move_pct", 1.5) or 0)),
            window_sec=float(getattr(settings, "paper_vol_window_sec", 300) or 300),
            cool_down_sec=float(getattr(settings, "paper_vol_cooldown_sec", 120) or 0),
        )
        self._min_liquidity = Decimal(str(settings.arbitrage_min_liquidity_base))
        self._max_quantity = Decimal(str(settings.arbitrage_max_quantity))
        self._position_pct = Decimal(str(settings.arbitrage_position_pct))
        self._cooldown_ms = float(settings.arbitrage_opportunity_cooldown_ms)
        self._max_emits = int(settings.arbitrage_max_emits_per_cycle)
        self._max_latency_ms = settings.arbitrage_max_latency_ms
        self._max_book_age_ms = settings.arbitrage_max_book_age_ms
        self._min_spread_bps = Decimal(str(settings.paper_maker_min_spread_bps))
        self._max_edge_bps = Decimal(str(settings.paper_maker_max_edge_bps))
        self._max_fee_bps = Decimal(str(settings.paper_maker_max_fee_bps))
        self._same_venue = bool(settings.paper_maker_same_venue)
        self._adverse_bps = Decimal(str(getattr(settings, "paper_maker_adverse_bps", 0) or 0))
        self._spread_fee_buffer_bps = Decimal(
            str(getattr(settings, "paper_maker_spread_fee_buffer_bps", 1) or 1)
        )
        self._fair_value_enabled = bool(getattr(settings, "paper_maker_fair_value", True))
        self._fx_symbol = str(
            getattr(settings, "paper_maker_fx_symbol", "EURUSDT") or "EURUSDT"
        ).upper()
        self._quote = settings.paper_quote_asset.upper()
        self._maker_venues = {
            part.strip().lower()
            for part in str(getattr(settings, "paper_maker_venues", "") or "").split(",")
            if part.strip()
        }
        self._profitability = profitability or self._build_profitability_engine(settings)
        self._pairs_evaluated = 0
        self._depth_edges_found = 0
        self._scan_rejections = 0
        self._opportunities_emitted = 0
        self._reject_counts: dict[str, int] = {}
        self._last_emit: dict[str, tuple[float, Decimal]] = {}
        self._fair_values: dict[str, Decimal] = {}
        self._active_skew: QuoteSkew | None = None
        self._portfolio_state: PortfolioState | None = None
        self._external_reduce_only = False
        self._hmm_regime_id: int | None = None
        self._hmm_uptrend_ask_improve_bps = Decimal(
            str(getattr(settings, "paper_hmm_uptrend_ask_improve_bps", 0) or 0)
        )

    def set_reduce_only(self, enabled: bool) -> None:
        """External guardrail (HMM toxic flow / operator): block new BUY quotes."""
        self._external_reduce_only = bool(enabled)

    def set_hmm_regime(self, regime_id: int | None, *, is_toxic: bool = False) -> None:
        """Apply HMM regime: toxic → REDUCE_ONLY; up-trend → tighter asks."""
        self._hmm_regime_id = regime_id
        self._external_reduce_only = bool(is_toxic)

    @property
    def reduce_only(self) -> bool:
        return self._external_reduce_only

    @property
    def active_skew(self) -> QuoteSkew | None:
        return self._active_skew

    @property
    def vol_guard(self) -> VolatilityDumpGuard:
        return self._vol_guard

    def dump_symbols(self) -> list[str]:
        """Symbols currently in dump cool-off (bids must be pulled)."""
        return self._vol_guard.active_symbols()

    def update_adverse_bps(self, adverse_bps: Decimal) -> None:
        """Rebuild NET gate with a new adverse-selection haircut."""
        self._adverse_bps = Decimal(str(adverse_bps))
        updated = self._settings.model_copy(
            update={"paper_maker_adverse_bps": float(self._adverse_bps)}
        )
        self._settings = updated
        self._profitability = self._build_profitability_engine(updated)

    @staticmethod
    def _build_profitability_engine(settings: Settings) -> DefaultProfitabilityEngine:
        adverse = float(getattr(settings, "paper_maker_adverse_bps", 0) or 0)
        maker_settings = settings.model_copy(
            update={
                "profitability_min_net_profit_usd": settings.paper_maker_min_profit_eur,
                "profitability_min_net_return": float(
                    getattr(
                        settings,
                        "paper_maker_min_net_return",
                        settings.arbitrage_min_profit_pct,
                    )
                    or settings.arbitrage_min_profit_pct
                ),
                "profitability_apply_funding": False,
                "profitability_slippage_bps": 0.0,
                "profitability_thin_book_penalty_bps": 0.0,
                # 1 bp exec buffer + expected adverse selection when hit.
                "profitability_execution_buffer_bps": 1.0 + adverse,
            }
        )
        return DefaultProfitabilityEngine(maker_settings)

    async def evaluate(self, snapshot: MarketSnapshot) -> list[TradeOpportunity]:
        self._reject(
            snapshot.symbol,
            "single_snapshot",
            "Maker quoting needs at least one venue book with bid and ask",
            exchange=snapshot.exchange,
        )
        return []

    async def evaluate_markets(
        self,
        snapshots: Sequence[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
        portfolio_state: PortfolioState | None = None,
    ) -> list[TradeOpportunity]:
        self._portfolio_state = portfolio_state
        if portfolio_state is not None:
            self._active_skew = self._skew_policy.skew(portfolio_state)
        else:
            self._active_skew = None

        by_symbol = self._group_valid_snapshots(snapshots)
        self._fair_values = self._build_fair_values(by_symbol)
        # Feed mid history for dump detection before quoting.
        for symbol, venues in by_symbol.items():
            mid = self._median_mid(venues)
            if mid is not None:
                self._vol_guard.observe(symbol, mid)

        opportunities: list[TradeOpportunity] = []
        for symbol, venues in by_symbol.items():
            if infer_quote_asset(symbol, self._quote) != self._quote:
                # USDT/FX books are reference-only for fair value, not quote legs.
                continue
            opportunities.extend(
                await self._evaluate_symbol(
                    symbol,
                    venues,
                    equity=equity,
                    inventory=inventory,
                )
            )
        opportunities.sort(key=self._rank_opportunity, reverse=True)
        selected = opportunities[: self._max_emits]
        selected = self._drop_small_vs_best(selected)
        for opp in selected:
            self._opportunities_emitted += 1
            self._mark_emitted(opp)
        return selected

    def _symbol_sell_only(self, symbol: str) -> bool:
        """True when inventory cap, dump guard, or HMM toxic forbids new BUY exposure."""
        if self._external_reduce_only:
            return True
        skew = self._active_skew
        if skew is not None and skew.sell_only:
            return True
        return self._vol_guard.is_dump(symbol)

    async def _evaluate_symbol(
        self,
        symbol: str,
        venues: list[MarketSnapshot],
        *,
        equity: Decimal | None = None,
        inventory: object = None,
    ) -> list[TradeOpportunity]:
        if infer_quote_asset(symbol, self._quote) != self._quote:
            self._reject(
                symbol,
                "quote_mismatch",
                f"Skip {symbol}: quote is not {self._quote}",
            )
            return []

        sell_only = self._symbol_sell_only(symbol)
        if sell_only and self._vol_guard.is_dump(symbol):
            self._reject(
                symbol,
                "vol_dump_sell_only",
                "Mid dumped faster than vol guard threshold; bids pulled",
            )

        ranked: list[TradeOpportunity] = []
        fair_value = self._fair_values.get(infer_base_asset(symbol, self._quote))
        for buy_snap in venues:
            if not self._venue_allowed(buy_snap.exchange):
                continue
            for sell_snap in venues:
                if not self._venue_allowed(sell_snap.exchange):
                    continue
                same = buy_snap.exchange == sell_snap.exchange
                if same and not self._same_venue:
                    continue
                self._pairs_evaluated += 1
                candidate = self._build_candidate(
                    buy_snap,
                    sell_snap,
                    equity=equity,
                    inventory=inventory,
                    fair_value=fair_value,
                    sell_only=sell_only,
                )
                if candidate is None:
                    continue
                self._depth_edges_found += 1
                opportunity = await self._gate_candidate(
                    candidate,
                    inventory=inventory,
                    equity=equity,
                    sell_only=sell_only,
                )
                if opportunity is None:
                    continue
                if self._in_cooldown(opportunity):
                    self._reject(
                        symbol,
                        "cooldown",
                        "Pair recently quoted; waiting for cooldown",
                        buy_exchange=candidate.buy_exchange,
                        sell_exchange=candidate.sell_exchange,
                    )
                    continue
                ranked.append(opportunity)

        return ranked

    def _rank_opportunity(self, opportunity: TradeOpportunity) -> Decimal:
        meta = opportunity.metadata or {}
        net = Decimal(str(meta.get("net_profit_eur", "0")))
        skew = Decimal(str(meta.get("inventory_skew_score", "0")))
        fv_bonus = Decimal("1") if meta.get("fair_value_aligned") else Decimal("0")
        same_venue = str(meta.get("buy_exchange") or "") == str(meta.get("sell_exchange") or "")
        # Same-venue can complete without a transfer; prefer it when NET is close.
        venue_bonus = Decimal("0.05") if same_venue else _ZERO
        return net + (skew * Decimal("0.01")) + (fv_bonus * Decimal("0.001")) + venue_bonus

    def _effective_min_profit(self, equity: Decimal | None) -> Decimal:
        floor = self._min_profit_eur
        if equity is None or equity <= 0 or self._min_profit_equity_bps <= 0:
            return floor
        scaled = equity * self._min_profit_equity_bps / _BPS
        return max(floor, scaled)

    def _build_fair_values(
        self, by_symbol: dict[str, list[MarketSnapshot]]
    ) -> dict[str, Decimal]:
        if not self._fair_value_enabled:
            return {}
        fx_mid = self._median_mid(by_symbol.get(self._fx_symbol) or [])
        if fx_mid is None or fx_mid <= 0:
            return {}
        values: dict[str, Decimal] = {}
        for symbol, snaps in by_symbol.items():
            if not symbol.endswith("USDT") or symbol == self._fx_symbol:
                continue
            base = symbol[: -len("USDT")]
            if not base:
                continue
            usdt_mid = self._median_mid(snaps)
            if usdt_mid is None or usdt_mid <= 0:
                continue
            # EURUSDT = USDT per 1 EUR ⇒ BASEEUR fair = BASEUSDT / EURUSDT.
            values[base] = usdt_mid / fx_mid
        return values

    @staticmethod
    def _median_mid(snaps: list[MarketSnapshot]) -> Decimal | None:
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

    def _venue_allowed(self, exchange: str | None) -> bool:
        if not self._maker_venues:
            return True
        return str(exchange or "").strip().lower() in self._maker_venues

    def _in_cooldown(self, opportunity: TradeOpportunity) -> bool:
        if self._cooldown_ms <= 0:
            return False
        last = self._last_emit.get(self._pair_key(opportunity))
        if last is None:
            return False
        last_ts, last_net = last
        if (time.monotonic() - last_ts) * 1000.0 >= self._cooldown_ms:
            return False
        if self._replace_improve_frac <= 0:
            return True
        net = Decimal(str((opportunity.metadata or {}).get("net_profit_eur", "0") or "0"))
        hurdle = last_net * (Decimal("1") + self._replace_improve_frac)
        return net < hurdle

    def _mark_emitted(self, opportunity: TradeOpportunity) -> None:
        net = Decimal(str((opportunity.metadata or {}).get("net_profit_eur", "0") or "0"))
        self._last_emit[self._pair_key(opportunity)] = (time.monotonic(), net)

    def _drop_small_vs_best(
        self, opportunities: list[TradeOpportunity]
    ) -> list[TradeOpportunity]:
        """Skip dust next to a much larger NET-euro quote in the same cycle."""
        if self._keep_vs_best_frac <= 0 or len(opportunities) < 2:
            return opportunities
        nets = [
            Decimal(str((o.metadata or {}).get("net_profit_eur", "0") or "0"))
            for o in opportunities
        ]
        best = max(nets)
        if best <= 0:
            return opportunities
        floor = best * self._keep_vs_best_frac
        kept = [o for o, net in zip(opportunities, nets, strict=True) if net >= floor]
        return kept or opportunities

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
        if book.bids[0].price >= book.asks[0].price:
            return ("crossed_book", "Bid is at or through the ask; post-only would take")
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
        inventory: object = None,
        fair_value: Decimal | None = None,
        sell_only: bool = False,
    ) -> MakerCandidate | None:
        assert buy_snap.order_book is not None
        assert sell_snap.order_book is not None
        assert buy_snap.exchange is not None
        assert sell_snap.exchange is not None

        # Pick the book level with the most NET euro. A slightly worse fill
        # rate is fine when size × spread is much larger (profit, not trade count).
        max_level = int(getattr(self._settings, "paper_maker_book_level", 0) or 0)
        seen: set[tuple[Decimal, Decimal]] = set()
        best: tuple[Decimal, int, MakerCandidate] | None = None
        for level in range(0, max_level + 1):
            quoted = self._depth_quote(buy_snap.order_book, sell_snap.order_book, level)
            if quoted is None:
                continue
            buy_price, sell_price, buy_touch, sell_touch = quoted
            key = (buy_price, sell_price)
            if key in seen:
                continue
            seen.add(key)
            candidate = self._candidate_from_quote(
                buy_snap,
                sell_snap,
                buy_price=buy_price,
                sell_price=sell_price,
                buy_touch=buy_touch,
                sell_touch=sell_touch,
                equity=equity,
                inventory=inventory,
                fair_value=fair_value,
                sell_only=sell_only,
            )
            if candidate is None:
                continue
            euro = (candidate.sell_price - candidate.buy_price) * candidate.quantity
            # Touch fills more often; require extra euro at deeper levels.
            score = euro / (Decimal("1") + Decimal(level) * Decimal("0.5"))
            if best is None or score > best[0]:
                best = (score, level, candidate)
        return None if best is None else best[2]

    @staticmethod
    def _depth_quote(
        buy_book: object,
        sell_book: object,
        level: int,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
        bids = getattr(buy_book, "bids", None) or []
        asks = getattr(sell_book, "asks", None) or []
        if not bids or not asks:
            return None
        buy_lvl = min(level, len(bids) - 1)
        sell_lvl = min(level, len(asks) - 1)
        buy_price = bids[buy_lvl].price
        sell_price = asks[sell_lvl].price
        buy_touch = sum((lvl.amount for lvl in bids[: buy_lvl + 1]), _ZERO)
        sell_touch = sum((lvl.amount for lvl in asks[: sell_lvl + 1]), _ZERO)
        return buy_price, sell_price, buy_touch, sell_touch

    def _candidate_from_quote(
        self,
        buy_snap: MarketSnapshot,
        sell_snap: MarketSnapshot,
        *,
        buy_price: Decimal,
        sell_price: Decimal,
        buy_touch: Decimal,
        sell_touch: Decimal,
        equity: Decimal | None = None,
        inventory: object = None,
        fair_value: Decimal | None = None,
        sell_only: bool = False,
    ) -> MakerCandidate | None:
        assert buy_snap.order_book is not None
        assert sell_snap.order_book is not None

        skew = self._active_skew
        if skew is not None:
            buy_price, sell_price = self._skew_policy.apply_prices(
                buy_price=buy_price,
                sell_price=sell_price,
                skew=skew,
                best_bid=buy_snap.order_book.bids[0].price,
                best_ask=sell_snap.order_book.asks[0].price,
            )
        # HMM up-trend: harvest EUR with a slightly tighter ask.
        if (
            self._hmm_regime_id == 1
            and self._hmm_uptrend_ask_improve_bps > 0
            and not sell_only
        ):
            improved = sell_price * (
                Decimal("1") - self._hmm_uptrend_ask_improve_bps / _BPS
            )
            floor = buy_snap.order_book.bids[0].price * Decimal("1.00005")
            sell_price = max(improved, floor)
            if sell_price >= buy_snap.order_book.asks[0].price:
                sell_price = buy_snap.order_book.asks[0].price

        if sell_price <= buy_price:
            self._reject(
                buy_snap.symbol,
                "no_maker_edge",
                (
                    f"Sell ask {sell_price} on {sell_snap.exchange} is not above "
                    f"buy bid {buy_price} on {buy_snap.exchange}"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        spread_bps = (sell_price - buy_price) / buy_price * _BPS
        if buy_snap.exchange == sell_snap.exchange and spread_bps < self._min_spread_bps:
            self._reject(
                buy_snap.symbol,
                "tight_spread",
                f"Same-venue spread {spread_bps} bps below min {self._min_spread_bps}",
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        if fair_value is not None and fair_value > 0 and not sell_only:
            # Require buy below fair value and sell above it — otherwise the
            # "edge" is a one-sided stale book vs global USDT fair value.
            max_buy = fair_value
            if skew is not None:
                dipped = self._skew_policy.max_buy_vs_fair(fair_value, skew)
                if dipped is not None:
                    max_buy = dipped
            if buy_price > max_buy:
                self._reject(
                    buy_snap.symbol,
                    "toxic_buy_vs_fv" if max_buy == fair_value else "selective_buy_dip",
                    (
                        f"Buy bid {buy_price} is above max allowed {max_buy} "
                        f"(fair={fair_value}, underweight dip gate)"
                        if max_buy != fair_value
                        else (
                            f"Buy bid {buy_price} is above USDT fair value {fair_value} "
                            f"({self._fx_symbol} bridge)"
                        )
                    ),
                    buy_exchange=buy_snap.exchange,
                    sell_exchange=sell_snap.exchange,
                )
                return None
            if sell_price < fair_value:
                self._reject(
                    buy_snap.symbol,
                    "toxic_sell_vs_fv",
                    (
                        f"Sell ask {sell_price} is below USDT fair value {fair_value} "
                        f"({self._fx_symbol} bridge)"
                    ),
                    buy_exchange=buy_snap.exchange,
                    sell_exchange=sell_snap.exchange,
                )
                return None
        elif fair_value is not None and fair_value > 0 and sell_only:
            # Recycle sells may sit at/through fair value; still refuse absurd asks.
            if sell_price < fair_value * Decimal("0.97"):
                self._reject(
                    buy_snap.symbol,
                    "toxic_sell_vs_fv",
                    (
                        f"Sell-only ask {sell_price} far below fair value {fair_value}"
                    ),
                    buy_exchange=buy_snap.exchange,
                    sell_exchange=sell_snap.exchange,
                )
                return None

        fee_bps = (
            venue_maker_fee(buy_snap.exchange) + venue_maker_fee(sell_snap.exchange)
        ) * _BPS
        if self._max_fee_bps > 0 and fee_bps > self._max_fee_bps:
            self._reject(
                buy_snap.symbol,
                "fee_too_high",
                (
                    f"Combined maker fees {fee_bps} bps exceed max {self._max_fee_bps} bps "
                    f"({buy_snap.exchange}->{sell_snap.exchange})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None
        cost_bps = fee_bps + self._spread_fee_buffer_bps
        # Sell-only recycle: allow thinner edge so capital velocity wins.
        if not sell_only and cost_bps >= spread_bps:
            self._reject(
                buy_snap.symbol,
                "fees_eat_edge",
                (
                    f"Maker fees {fee_bps} bps (+{self._spread_fee_buffer_bps} bps buffer) leave no NET room in "
                    f"{spread_bps} bps gross edge "
                    f"(adverse {self._adverse_bps} bps applied later in NET gate)"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        # Retail alt books often need 20–40 bps just to clear maker fees. A flat
        # 30 bps "stale" cap (calibrated to tight BTC books) would reject the
        # only Realistic-viable quotes. Never mark stale below 2× round-trip fees.
        stale_cap = self._max_edge_bps
        if fee_bps > 0:
            stale_cap = max(stale_cap, fee_bps * Decimal("2"))
        if stale_cap > 0 and spread_bps > stale_cap:
            self._reject(
                buy_snap.symbol,
                "stale_edge",
                (
                    f"Gross edge {spread_bps} bps above stale cap {stale_cap} bps "
                    f"(max_edge={self._max_edge_bps}, 2x_fees={fee_bps * Decimal('2')})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        quantity_cap = self._max_quantity
        if equity is not None and equity > 0 and self._position_pct > 0:
            max_notional = equity * (self._position_pct / Decimal("100"))
            if buy_price > 0:
                quantity_cap = min(quantity_cap, max_notional / buy_price)
        quantity = min(buy_touch, sell_touch, quantity_cap)
        quantity = self._cap_to_inventory(
            quantity,
            buy_snap=buy_snap,
            sell_snap=sell_snap,
            buy_price=buy_price,
            inventory=inventory,
            sell_only=sell_only,
        )
        if quantity < self._min_liquidity:
            self._reject(
                buy_snap.symbol,
                "insufficient_overlapping_liquidity",
                (
                    f"Quote size {quantity} below min_liquidity {self._min_liquidity} "
                    f"(buy={buy_snap.exchange} bid_touch={buy_touch}, "
                    f"sell={sell_snap.exchange} ask_touch={sell_touch})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
            return None

        return MakerCandidate(
            symbol=buy_snap.symbol.upper(),
            buy_exchange=buy_snap.exchange,
            sell_exchange=sell_snap.exchange,
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_snapshot=buy_snap,
            sell_snapshot=sell_snap,
        )

    def _cap_to_inventory(
        self,
        quantity: Decimal,
        *,
        buy_snap: MarketSnapshot,
        sell_snap: MarketSnapshot,
        buy_price: Decimal,
        inventory: object,
        sell_only: bool = False,
    ) -> Decimal:
        if inventory is None or quantity <= 0:
            return quantity
        available = getattr(inventory, "available", None)
        if not callable(available):
            return quantity
        base = infer_base_asset(buy_snap.symbol, self._quote)
        sell_coins = available(sell_snap.exchange, base)
        if sell_only:
            capped = min(quantity, sell_coins)
            if capped < quantity:
                self._reject(
                    buy_snap.symbol,
                    "venue_inventory",
                    (
                        f"Sell-only size capped by {base} on {sell_snap.exchange} "
                        f"(have={sell_coins})"
                    ),
                    buy_exchange=buy_snap.exchange,
                    sell_exchange=sell_snap.exchange,
                )
            return capped
        buy_fee = Decimal("1") + venue_maker_fee(buy_snap.exchange)
        quote_cash = available(buy_snap.exchange, self._quote)
        max_buy = quote_cash / (buy_price * buy_fee) if buy_price > 0 else _ZERO
        capped = min(quantity, max_buy, sell_coins)
        if capped < quantity:
            self._reject(
                buy_snap.symbol,
                "venue_inventory",
                (
                    f"Size capped by per-exchange balances "
                    f"(buy_{self._quote}={quote_cash} on {buy_snap.exchange}, "
                    f"sell_{base}={sell_coins} on {sell_snap.exchange})"
                ),
                buy_exchange=buy_snap.exchange,
                sell_exchange=sell_snap.exchange,
            )
        return capped

    async def _gate_candidate(
        self,
        candidate: MakerCandidate,
        *,
        inventory: object = None,
        equity: Decimal | None = None,
        sell_only: bool = False,
    ) -> TradeOpportunity | None:
        base = infer_base_asset(candidate.symbol, self._quote)
        fair_value = self._fair_values.get(base)
        fair_aligned = bool(
            fair_value is not None
            and fair_value > 0
            and candidate.buy_price < fair_value < candidate.sell_price
        )
        skew = self._inventory_skew_score(
            candidate, inventory=inventory, base=base
        )
        skew_meta = self._active_skew
        opportunity = TradeOpportunity(
            strategy_name=self.name,
            symbol=candidate.symbol,
            side=OpportunitySide.SELL if sell_only else OpportunitySide.BUY,
            quantity=candidate.quantity,
            entry_price=candidate.buy_price,
            expected_exit_price=candidate.sell_price,
            confidence=0.55,
            rationale=(
                (
                    f"Sell-only recycle {candidate.quantity} on {candidate.sell_exchange} "
                    f"@ ask {candidate.sell_price}"
                    if sell_only
                    else (
                        f"Maker buy {candidate.quantity} on {candidate.buy_exchange} @ bid "
                        f"{candidate.buy_price}; maker sell on {candidate.sell_exchange} @ ask "
                        f"{candidate.sell_price}"
                    )
                )
                + (
                    f"; fair_value_eur={fair_value}"
                    if fair_value is not None
                    else ""
                )
            ),
            market=candidate.buy_snapshot.model_copy(update={"order_book": None}),
            entry_fee_role=FeeRole.MAKER,
            exit_fee_role=FeeRole.MAKER,
            funding_periods=_ZERO,
            metadata={
                "buy_exchange": candidate.buy_exchange,
                "sell_exchange": candidate.sell_exchange,
                "buy_vwap": str(candidate.buy_price),
                "sell_vwap": str(candidate.sell_price),
                "buy_maker_fee_rate": str(venue_maker_fee(candidate.buy_exchange)),
                "sell_maker_fee_rate": str(venue_maker_fee(candidate.sell_exchange)),
                "pricing": "maker_touch",
                "quote_currency": self._quote,
                "round_trip": not sell_only,
                "post_only": True,
                "sell_only": sell_only,
                "fair_value_eur": str(fair_value) if fair_value is not None else None,
                "fair_value_aligned": fair_aligned,
                "inventory_skew_score": str(skew),
                "inventory_mode": skew_meta.mode if skew_meta is not None else "unknown",
                "alt_inventory_pct": (
                    str(skew_meta.alt_fraction * Decimal("100"))
                    if skew_meta is not None
                    else None
                ),
                "adverse_bps": str(self._adverse_bps),
                "hmm_regime_id": self._hmm_regime_id,
                "reduce_only": sell_only or self._external_reduce_only,
            },
        )
        result = await self._profitability.evaluate(
            opportunity,
            buy_fee_rate=venue_maker_fee(candidate.buy_exchange),
            sell_fee_rate=venue_maker_fee(candidate.sell_exchange),
        )
        net = result.net_profit_usd
        net_return = result.net_return
        # Hard dust / NET floors (stofjes + thin margins).
        dust_reason = self._dust.reject_reason(
            quantity=candidate.quantity,
            buy_price=candidate.buy_price,
            net_profit_eur=net,
            net_return=net_return,
        )
        if dust_reason is not None and not sell_only:
            self._reject(
                candidate.symbol,
                "dust_or_net_floor",
                dust_reason,
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_profit_eur=str(net),
                net_return=str(net_return),
            )
            return None
        if not result.trade_allowed and not sell_only:
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
        min_eur = self._effective_min_profit(equity)
        if not sell_only and net < min_eur:
            self._reject(
                candidate.symbol,
                "min_profit_eur",
                f"NET profit {net} EUR below minimum {min_eur} EUR",
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_profit_eur=str(net),
            )
            return None
        if not sell_only and net_return < self._min_profit_pct:
            self._reject(
                candidate.symbol,
                "min_profit_pct",
                f"NET return {net_return} below minimum {self._min_profit_pct}",
                buy_exchange=candidate.buy_exchange,
                sell_exchange=candidate.sell_exchange,
                net_return=str(net_return),
            )
            return None
        # Sell-only still needs a positive notional (no stofjes) even if edge is thin.
        if sell_only:
            notional = candidate.quantity * candidate.sell_price
            if (
                self._dust.min_notional_eur > 0
                and notional < self._dust.min_notional_eur
            ):
                self._reject(
                    candidate.symbol,
                    "dust_or_net_floor",
                    (
                        f"sell-only notional {notional} EUR below dust floor "
                        f"{self._dust.min_notional_eur} EUR"
                    ),
                    buy_exchange=candidate.buy_exchange,
                    sell_exchange=candidate.sell_exchange,
                )
                return None
        logger.info(
            "maker quote accepted symbol=%s buy=%s sell=%s qty=%s "
            "net_profit_eur=%s net_return=%s fair_value=%s skew=%s sell_only=%s",
            candidate.symbol,
            candidate.buy_exchange,
            candidate.sell_exchange,
            candidate.quantity,
            net,
            net_return,
            fair_value,
            skew,
            sell_only,
        )
        return opportunity.model_copy(
            update={
                "confidence": min(0.95, 0.45 + float(net_return) * 10.0),
                "metadata": {
                    **opportunity.metadata,
                    "net_profit_eur": str(net),
                    "net_return": str(net_return),
                    "gross_profit_eur": str(result.gross_profit_usd),
                },
            }
        )

    def _inventory_skew_score(
        self,
        candidate: MakerCandidate,
        *,
        inventory: object,
        base: str,
    ) -> Decimal:
        if inventory is None:
            return _ZERO
        available = getattr(inventory, "available", None)
        if not callable(available):
            return _ZERO
        sell_coins = available(candidate.sell_exchange, base)
        buy_cash = available(candidate.buy_exchange, self._quote)
        # Prefer quotes that unload heavy inventory into scarce quote cash.
        return sell_coins * candidate.sell_price + buy_cash

    def _reject(self, symbol: str, code: str, reason: str, **context: object) -> None:
        self._scan_rejections += 1
        self._reject_counts[code] = self._reject_counts.get(code, 0) + 1
        extras = " ".join(
            f"{key}={value}" for key, value in context.items() if value is not None
        )
        logger.debug(
            "maker quote rejected symbol=%s code=%s reason=%s%s",
            symbol,
            code,
            reason,
            f" {extras}" if extras else "",
        )

    def scan_stats(self) -> dict[str, object]:
        skew = self._active_skew
        return {
            "pairs_evaluated": self._pairs_evaluated,
            "depth_edges_found": self._depth_edges_found,
            "scan_rejections": self._scan_rejections,
            "opportunities_emitted": self._opportunities_emitted,
            "reject_counts": dict(sorted(self._reject_counts.items())),
            "inventory_mode": skew.mode if skew is not None else None,
            "alt_inventory_pct": (
                float(skew.alt_fraction * Decimal("100")) if skew is not None else None
            ),
            "dump_symbols": self.dump_symbols(),
            "reduce_only": self._external_reduce_only,
            "hmm_regime_id": self._hmm_regime_id,
        }
