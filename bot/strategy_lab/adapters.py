"""Strategy adapters for the research lab."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.venue_fees import venue_maker_fee, venue_taker_fee
from bot.strategies.maker_inventory import MakerInventoryStrategy
from bot.strategies.arbitrage import top_of_book_snapshot
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.strategy_lab.adapter import StrategyResearchAdapter, empty_reject
from bot.strategy_lab.economics import (
    CommonEconomics,
    draft_opportunity,
    executable_vwap,
    refuse_midpoint_execution,
)
from bot.strategy_lab.types import (
    CostBreakdown,
    CycleSnapshot,
    DecisionAction,
    MarketEventView,
    StrategyDecision,
)

_ZERO = Decimal("0")
_BPS = Decimal("10000")


def _book_from_view(v: MarketEventView) -> OrderBook:
    bids = [
        OrderBookLevel(price=p, amount=q) for p, q in (v.bid_levels or ((v.bid, v.bid_size),))
    ]
    asks = [
        OrderBookLevel(price=p, amount=q) for p, q in (v.ask_levels or ((v.ask, v.ask_size),))
    ]
    return OrderBook(symbol=v.symbol, bids=bids or [], asks=asks or [])


class MakerInventoryAdapter(StrategyResearchAdapter):
    """Wrap existing MakerInventoryStrategy — do not rewrite strategy logic."""

    strategy_id = "maker_inventory"
    strategy_version = "wrap_existing_v1"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._inner = MakerInventoryStrategy(self._settings)

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        snaps = []
        for v in cycle.books:
            book = _book_from_view(v)
            if not book.bids or not book.asks:
                continue
            snaps.append(
                top_of_book_snapshot(
                    exchange=v.venue, symbol=v.symbol, order_book=book, latency_ms=5.0
                )
            )
        if not snaps:
            return []
        # Sync bridge: evaluate_markets is async — run via helper
        import asyncio

        equity = self._capital.budget_for(self.strategy_id)

        async def _run():
            return await self._inner.evaluate_markets(snaps, equity=equity)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Nested: use a dedicated loop in a thread would be heavy; call sync path
            # by creating opportunities via temporary new loop only when none running.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                opps = pool.submit(lambda: asyncio.run(_run())).result()
        else:
            opps = asyncio.run(_run())

        out: list[StrategyDecision] = []
        for opp in opps:
            meta = opp.metadata or {}
            buy_ex = str(meta.get("buy_exchange") or "")
            sell_ex = str(meta.get("sell_exchange") or "")
            costs = CostBreakdown(
                gross_edge_eur=Decimal(str(meta.get("gross_profit_eur") or 0)),
                fees_eur=_ZERO,  # embedded in NET from strategy
                slippage_eur=_ZERO,
                adverse_latency_eur=Decimal(str(meta.get("adverse_bps") or 0))
                * opp.quantity
                * opp.entry_price
                / _BPS,
                funding_eur=_ZERO,
                hedge_other_eur=_ZERO,
                net_eur=Decimal(str(meta.get("net_profit_eur") or 0)),
                conservative_net_eur=Decimal(str(meta.get("net_profit_eur") or 0)),
            )
            # Recompute fees via common economics for waterfall consistency
            buy_fee = venue_maker_fee(buy_ex)
            sell_fee = venue_maker_fee(sell_ex)
            eco = self._economics.estimate_opportunity(
                opp, buy_fee_rate=buy_fee, sell_fee_rate=sell_fee
            )
            capital = opp.quantity * opp.entry_price
            out.append(
                StrategyDecision(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    cycle_id=cycle.cycle_id,
                    ts_ns=cycle.ts_ns,
                    symbol=opp.symbol,
                    venue=buy_ex,
                    route=f"{buy_ex}->{sell_ex}",
                    action=DecisionAction.ACCEPT,
                    reject_reason=None,
                    expected_edge_eur=eco.gross_edge_eur,
                    costs=eco,
                    capital_required_eur=capital,
                    estimated_capital_lock_ms=float(
                        getattr(self._settings, "paper_maker_rest_ms", 2500) or 2500
                    ),
                    uncertainty=Decimal("0.5"),
                    metadata={
                        "source": "maker_inventory",
                        "net_profit_eur": str(eco.conservative_net_eur),
                        "scan_stats": None,
                    },
                )
            )
        # Also count rejects via scan for participation denominator elsewhere
        return out


class ExecutableCrossVenueArbAdapter(StrategyResearchAdapter):
    """Taker-taker executable dislocation using depth VWAP (never mid)."""

    strategy_id = "executable_cross_venue_arb"
    strategy_version = "v1"

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        by_sym: dict[str, list[MarketEventView]] = {}
        for b in cycle.books:
            if b.bid > 0 and b.ask > b.bid:
                by_sym.setdefault(b.symbol, []).append(b)
        out: list[StrategyDecision] = []
        qty_cap = Decimal(str(getattr(self._settings, "arbitrage_max_quantity", 1) or 1))
        min_net = Decimal(str(getattr(self._settings, "arbitrage_min_profit_eur", 0.01) or 0.01))
        for symbol, venues in by_sym.items():
            for buy in venues:
                for sell in venues:
                    if buy.venue == sell.venue:
                        continue
                    if not refuse_midpoint_execution(
                        bids=sell.bid_levels or ((sell.bid, sell.bid_size),),
                        asks=buy.ask_levels or ((buy.ask, buy.ask_size),),
                    ):
                        out.append(
                            empty_reject(
                                strategy_id=self.strategy_id,
                                strategy_version=self.strategy_version,
                                cycle=cycle,
                                symbol=symbol,
                                venue=buy.venue,
                                route=f"{buy.venue}->{sell.venue}",
                                reason="no_depth_for_executable_vwap",
                            )
                        )
                        continue
                    qty = min(
                        qty_cap,
                        buy.ask_size or Decimal("1"),
                        sell.bid_size or Decimal("1"),
                        Decimal("1"),
                    )
                    buy_px, buy_filled, buy_ok, _ = executable_vwap(
                        "buy",
                        bids=buy.bid_levels,
                        asks=buy.ask_levels or ((buy.ask, buy.ask_size),),
                        quantity=qty,
                    )
                    sell_px, sell_filled, sell_ok, _ = executable_vwap(
                        "sell",
                        bids=sell.bid_levels or ((sell.bid, sell.bid_size),),
                        asks=sell.ask_levels,
                        quantity=qty,
                    )
                    if not buy_ok or not sell_ok or buy_filled <= 0 or sell_filled <= 0:
                        out.append(
                            empty_reject(
                                strategy_id=self.strategy_id,
                                strategy_version=self.strategy_version,
                                cycle=cycle,
                                symbol=symbol,
                                venue=buy.venue,
                                route=f"{buy.venue}->{sell.venue}",
                                reason="insufficient_executable_depth",
                            )
                        )
                        continue
                    # Hedge must be modeled: second leg is the sell; if sell VWAP
                    # not available we already rejected. Additional conservative
                    # latency haircut for dual-leg risk.
                    qty = min(buy_filled, sell_filled)
                    buy_fee = venue_taker_fee(buy.venue)
                    sell_fee = venue_taker_fee(sell.venue)
                    latency_haircut = buy_px * qty * Decimal("0.0002")  # 2 bps
                    safety = buy_px * qty * Decimal("0.0001")
                    costs = self._economics.from_legs(
                        quantity=qty,
                        buy_vwap=buy_px,
                        sell_vwap=sell_px,
                        buy_fee_rate=buy_fee,
                        sell_fee_rate=sell_fee,
                        adverse_eur=latency_haircut,
                        safety_margin_eur=safety,
                    )
                    route = f"{buy.venue}->{sell.venue}"
                    if costs.conservative_net_eur < min_net:
                        out.append(
                            StrategyDecision(
                                strategy_id=self.strategy_id,
                                strategy_version=self.strategy_version,
                                cycle_id=cycle.cycle_id,
                                ts_ns=cycle.ts_ns,
                                symbol=symbol,
                                venue=buy.venue,
                                route=route,
                                action=DecisionAction.REJECT,
                                reject_reason="conservative_net_below_min",
                                expected_edge_eur=costs.gross_edge_eur,
                                costs=costs,
                                capital_required_eur=buy_px * qty,
                                estimated_capital_lock_ms=500.0,
                                uncertainty=Decimal("0.4"),
                            )
                        )
                        continue
                    out.append(
                        StrategyDecision(
                            strategy_id=self.strategy_id,
                            strategy_version=self.strategy_version,
                            cycle_id=cycle.cycle_id,
                            ts_ns=cycle.ts_ns,
                            symbol=symbol,
                            venue=buy.venue,
                            route=route,
                            action=DecisionAction.ACCEPT,
                            reject_reason=None,
                            expected_edge_eur=costs.gross_edge_eur,
                            costs=costs,
                            capital_required_eur=buy_px * qty,
                            estimated_capital_lock_ms=500.0,
                            uncertainty=Decimal("0.4"),
                            metadata={
                                "buy_vwap": str(buy_px),
                                "sell_vwap": str(sell_px),
                                "used_midpoint": False,
                            },
                        )
                    )
        return out


class LeadLagAdapter(StrategyResearchAdapter):
    """Reuse bot.opportunity.lead_lag — shadow/causal only."""

    strategy_id = "lead_lag"
    strategy_version = "reuse_existing_v1"

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        from bot.opportunity.lead_lag.economics import build_shadow_opportunity
        from bot.opportunity.lead_lag.types import LeadLagSignal
        from bot.core.exchange_types import OrderBookLevel

        by_sym: dict[str, list[MarketEventView]] = {}
        for b in cycle.books:
            by_sym.setdefault(b.symbol, []).append(b)
        out: list[StrategyDecision] = []
        qty = Decimal("0.1")
        for symbol, venues in by_sym.items():
            if len(venues) < 2:
                continue
            # Simple causal signal: leader = first venue by name with up move proxy
            # using L1 imbalance as proxy signed lead (no future).
            ordered = sorted(venues, key=lambda v: v.venue)
            for i, leader in enumerate(ordered):
                for follower in ordered:
                    if leader.venue == follower.venue:
                        continue
                    l1_imb = _l1_imbalance(leader)
                    if abs(l1_imb) < Decimal("0.2"):
                        continue
                    pred_bps = l1_imb * Decimal("5")  # interpretable small scale
                    signal = LeadLagSignal(
                        decision_timestamp_ms=float(cycle.ts_ns // 1_000_000),
                        symbol=symbol,
                        leader_venue=leader.venue,
                        follower_venue=follower.venue,
                        horizon_ms=500,
                        predicted_follower_move_bps=pred_bps,
                        uncertainty_bps=Decimal("2"),
                        signal_strength=abs(l1_imb),
                        model_version="LAB_L1_IMBALANCE_PROXY_v1",
                        evidence_sample_count=0,
                        leader_return_bps=l1_imb * Decimal("3"),
                    )
                    fb = tuple(
                        OrderBookLevel(price=p, amount=q)
                        for p, q in (follower.bid_levels or ((follower.bid, follower.bid_size),))
                    )
                    fa = tuple(
                        OrderBookLevel(price=p, amount=q)
                        for p, q in (follower.ask_levels or ((follower.ask, follower.ask_size),))
                    )
                    lb = tuple(
                        OrderBookLevel(price=p, amount=q)
                        for p, q in (leader.bid_levels or ((leader.bid, leader.bid_size),))
                    )
                    la = tuple(
                        OrderBookLevel(price=p, amount=q)
                        for p, q in (leader.ask_levels or ((leader.ask, leader.ask_size),))
                    )
                    opp = build_shadow_opportunity(
                        signal,
                        follower_bids=fb,
                        follower_asks=fa,
                        leader_bids=lb,
                        leader_asks=la,
                        quantity=qty,
                        latency_ms=50.0,
                        hedge_mode="FULLY_HEDGED",
                    )
                    admitted = str(opp.state) == "SHADOW_ADMITTED" and opp.conservative_net_eur > 0
                    if not admitted:
                        out.append(
                            empty_reject(
                                strategy_id=self.strategy_id,
                                strategy_version=self.strategy_version,
                                cycle=cycle,
                                symbol=symbol,
                                venue=follower.venue,
                                route=f"{leader.venue}->{follower.venue}",
                                reason=str(opp.first_gate or opp.state),
                            )
                        )
                        continue
                    costs = CostBreakdown(
                        gross_edge_eur=opp.gross_predicted_edge_eur,
                        fees_eur=opp.fees_eur,
                        slippage_eur=opp.slippage_eur,
                        adverse_latency_eur=opp.latency_haircut_eur,
                        funding_eur=_ZERO,
                        hedge_other_eur=opp.hedge_haircut_eur + opp.other_costs_eur,
                        net_eur=opp.conservative_net_eur,
                        conservative_net_eur=opp.conservative_net_eur,
                    )
                    out.append(
                        StrategyDecision(
                            strategy_id=self.strategy_id,
                            strategy_version=self.strategy_version,
                            cycle_id=cycle.cycle_id,
                            ts_ns=cycle.ts_ns,
                            symbol=symbol,
                            venue=follower.venue,
                            route=f"{leader.venue}->{follower.venue}",
                            action=DecisionAction.ACCEPT,
                            reject_reason=None,
                            expected_edge_eur=costs.gross_edge_eur,
                            costs=costs,
                            capital_required_eur=opp.capital_required_eur,
                            estimated_capital_lock_ms=opp.estimated_capital_lock_ms,
                            uncertainty=Decimal("0.7"),
                            metadata={"hedge_required": True, "shadow": True},
                        )
                    )
        return out


class OrderBookImbalanceAdapter(StrategyResearchAdapter):
    """Simple interpretable OBI features — no deep learning / feature mining."""

    strategy_id = "order_book_imbalance"
    strategy_version = "l1_l5_v1"

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        out: list[StrategyDecision] = []
        qty = Decimal("0.2")
        for book in cycle.books:
            feats = micro_features(book)
            # Trade only when L1 imbalance is strong AND microprice agrees
            imb = feats["l1_imbalance"]
            micro_vs_mid = feats["microprice_vs_mid_bps"]
            if abs(imb) < Decimal("0.35") or abs(micro_vs_mid) < Decimal("1"):
                out.append(
                    empty_reject(
                        strategy_id=self.strategy_id,
                        strategy_version=self.strategy_version,
                        cycle=cycle,
                        symbol=book.symbol,
                        venue=book.venue,
                        route=f"{book.venue}|obi",
                        reason="weak_imbalance",
                    )
                )
                continue
            # Predicted move proportional to imbalance (interpretable)
            pred_bps = imb * Decimal("8")
            side = "buy" if pred_bps > 0 else "sell"
            if not refuse_midpoint_execution(
                bids=book.bid_levels or ((book.bid, book.bid_size),),
                asks=book.ask_levels or ((book.ask, book.ask_size),),
            ):
                out.append(
                    empty_reject(
                        strategy_id=self.strategy_id,
                        strategy_version=self.strategy_version,
                        cycle=cycle,
                        symbol=book.symbol,
                        venue=book.venue,
                        route=f"{book.venue}|obi",
                        reason="no_depth",
                    )
                )
                continue
            entry_px, filled, ok, _ = executable_vwap(
                side,
                bids=book.bid_levels or ((book.bid, book.bid_size),),
                asks=book.ask_levels or ((book.ask, book.ask_size),),
                quantity=qty,
            )
            if not ok or filled <= 0:
                out.append(
                    empty_reject(
                        strategy_id=self.strategy_id,
                        strategy_version=self.strategy_version,
                        cycle=cycle,
                        symbol=book.symbol,
                        venue=book.venue,
                        route=f"{book.venue}|obi",
                        reason="not_executable",
                    )
                )
                continue
            # Exit assume mid move of pred_bps (conservative half)
            move = entry_px * (pred_bps / _BPS) * Decimal("0.5")
            exit_px = entry_px + move if side == "buy" else entry_px - move
            fee = venue_taker_fee(book.venue)
            if side == "buy":
                costs = self._economics.from_legs(
                    quantity=filled,
                    buy_vwap=entry_px,
                    sell_vwap=exit_px,
                    buy_fee_rate=fee,
                    sell_fee_rate=fee,
                    adverse_eur=entry_px * filled * Decimal("0.0003"),
                )
            else:
                costs = self._economics.from_legs(
                    quantity=filled,
                    buy_vwap=exit_px,
                    sell_vwap=entry_px,
                    buy_fee_rate=fee,
                    sell_fee_rate=fee,
                    adverse_eur=entry_px * filled * Decimal("0.0003"),
                )
            action = (
                DecisionAction.ACCEPT
                if costs.conservative_net_eur > Decimal("0.02")
                else DecisionAction.REJECT
            )
            out.append(
                StrategyDecision(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    cycle_id=cycle.cycle_id,
                    ts_ns=cycle.ts_ns,
                    symbol=book.symbol,
                    venue=book.venue,
                    route=f"{book.venue}|obi",
                    action=action,
                    reject_reason=None if action == DecisionAction.ACCEPT else "net_too_small",
                    expected_edge_eur=costs.gross_edge_eur,
                    costs=costs,
                    capital_required_eur=entry_px * filled,
                    estimated_capital_lock_ms=1000.0,
                    uncertainty=Decimal("0.8"),
                    metadata={"features": {k: str(v) for k, v in feats.items()}},
                )
            )
        return out


class FundingBasisAdapter(StrategyResearchAdapter):
    """Adapt funding_basis; INSUFFICIENT_DATA when no funding_rate on books."""

    strategy_id = "funding_basis"
    strategy_version = "wrap_existing_v1"

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        out: list[StrategyDecision] = []
        any_funding = False
        for book in cycle.books:
            if book.funding_rate is None:
                continue
            any_funding = True
            rate = book.funding_rate
            funding_bps = abs(rate) * _BPS
            min_bps = Decimal(str(getattr(self._settings, "global_min_funding_bps", 3) or 3))
            if funding_bps < min_bps:
                out.append(
                    empty_reject(
                        strategy_id=self.strategy_id,
                        strategy_version=self.strategy_version,
                        cycle=cycle,
                        symbol=book.symbol,
                        venue=book.venue,
                        route=f"{book.venue}|funding",
                        reason="funding_below_min",
                    )
                )
                continue
            qty = Decimal("0.1")
            side = OpportunitySide.SHORT if rate > 0 else OpportunitySide.LONG
            entry = book.ask if side == OpportunitySide.LONG else book.bid
            opp = draft_opportunity(
                strategy_name=self.strategy_id,
                symbol=book.symbol,
                side=side,
                quantity=qty,
                entry_price=entry,
                exit_price=entry,
                entry_role=FeeRole.MAKER,
                exit_role=FeeRole.MAKER,
                funding_periods=Decimal("1"),
                metadata={
                    "funding_rate": str(rate),
                    "profitability_apply_funding": True,
                },
            )
            # Need funding-aware settings
            from bot.profitability.engine import DefaultProfitabilityEngine

            funded = DefaultProfitabilityEngine(
                self._settings.model_copy(update={"profitability_apply_funding": True})
            )
            est = funded.estimate_sync(opp)
            costs = CostBreakdown(
                gross_edge_eur=est.gross_profit,
                fees_eur=est.buy_fee + est.sell_fee,
                slippage_eur=est.slippage,
                adverse_latency_eur=est.execution_buffer,
                funding_eur=est.funding_cost,
                hedge_other_eur=_ZERO,
                net_eur=est.net_profit,
                conservative_net_eur=est.net_profit,
            )
            action = (
                DecisionAction.ACCEPT
                if est.trade_allowed
                else DecisionAction.REJECT
            )
            out.append(
                StrategyDecision(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    cycle_id=cycle.cycle_id,
                    ts_ns=cycle.ts_ns,
                    symbol=book.symbol,
                    venue=book.venue or "",
                    route=f"{book.venue}|funding",
                    action=action,
                    reject_reason=None if action == DecisionAction.ACCEPT else "funding_not_trade_allowed",
                    expected_edge_eur=costs.gross_edge_eur,
                    costs=costs,
                    capital_required_eur=entry * qty,
                    estimated_capital_lock_ms=8 * 3600 * 1000.0,  # funding period lock
                    uncertainty=Decimal("0.6"),
                )
            )
        if not any_funding:
            # Explicit insufficient-data marker decision (not a trade)
            out.append(
                StrategyDecision(
                    strategy_id=self.strategy_id,
                    strategy_version=self.strategy_version,
                    cycle_id=cycle.cycle_id,
                    ts_ns=cycle.ts_ns,
                    symbol="*",
                    venue="",
                    route="funding",
                    action=DecisionAction.SKIP,
                    reject_reason="INSUFFICIENT_DATA",
                    expected_edge_eur=_ZERO,
                    costs=CostBreakdown(),
                    capital_required_eur=_ZERO,
                    estimated_capital_lock_ms=0.0,
                    uncertainty=Decimal("1"),
                    metadata={"verdict_hint": "INSUFFICIENT_DATA"},
                )
            )
        return out


class ControlNoTradeAdapter(StrategyResearchAdapter):
    """CONTROL_NO_TRADE — always zero participation."""

    strategy_id = "control_no_trade"
    strategy_version = "v1"

    def generate_decisions(self, cycle: CycleSnapshot) -> list[StrategyDecision]:
        return [
            StrategyDecision(
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                cycle_id=cycle.cycle_id,
                ts_ns=cycle.ts_ns,
                symbol="*",
                venue="",
                route="control",
                action=DecisionAction.CONTROL,
                reject_reason="control_no_trade",
                expected_edge_eur=_ZERO,
                costs=CostBreakdown(),
                capital_required_eur=_ZERO,
                estimated_capital_lock_ms=0.0,
                uncertainty=_ZERO,
            )
        ]


def _l1_imbalance(book: MarketEventView) -> Decimal:
    bid_sz = book.bid_size or (book.bid_levels[0][1] if book.bid_levels else _ZERO)
    ask_sz = book.ask_size or (book.ask_levels[0][1] if book.ask_levels else _ZERO)
    denom = bid_sz + ask_sz
    if denom <= 0:
        return _ZERO
    return (bid_sz - ask_sz) / denom


def micro_features(book: MarketEventView) -> dict[str, Decimal]:
    """Interpretable features available at decision time only."""
    bid_sz = book.bid_size or _ZERO
    ask_sz = book.ask_size or _ZERO
    l1 = _l1_imbalance(book)
    # L5 depth imbalance
    bid5 = sum((q for _, q in book.bid_levels[:5]), _ZERO) or bid_sz
    ask5 = sum((q for _, q in book.ask_levels[:5]), _ZERO) or ask_sz
    denom5 = bid5 + ask5
    l5 = (bid5 - ask5) / denom5 if denom5 > 0 else _ZERO
    mid = book.mid if book.mid and book.mid > 0 else (book.bid + book.ask) / 2
    micro = (
        (book.ask * bid_sz + book.bid * ask_sz) / (bid_sz + ask_sz)
        if (bid_sz + ask_sz) > 0
        else mid
    )
    micro_bps = ((micro - mid) / mid * _BPS) if mid > 0 else _ZERO
    spread = book.ask - book.bid
    spread_bps = (spread / mid * _BPS) if mid > 0 else _ZERO
    return {
        "l1_imbalance": l1,
        "l5_imbalance": l5,
        "microprice_vs_mid_bps": micro_bps,
        "spread_bps": spread_bps,
    }


def build_all_adapters(
    *,
    economics: CommonEconomics,
    capital: Any,
    settings: Any,
) -> list[StrategyResearchAdapter]:
    kwargs = {"economics": economics, "capital": capital, "settings": settings}
    return [
        MakerInventoryAdapter(**kwargs),
        ExecutableCrossVenueArbAdapter(**kwargs),
        LeadLagAdapter(**kwargs),
        OrderBookImbalanceAdapter(**kwargs),
        FundingBasisAdapter(**kwargs),
        ControlNoTradeAdapter(**kwargs),
    ]
