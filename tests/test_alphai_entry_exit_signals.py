"""Tests for AlphaI entry/exit signal weaving."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.integrations.alphai.parse import (
    AlphaIRegimeState,
    build_regime_from_headlines,
    parse_news_row,
)
from bot.integrations.alphai.signals import AlphaITradingSignals, build_trading_signals
from bot.strategies.maker_inventory import MakerInventoryStrategy


def _bullish_article(*, uid: str = "bull1") -> dict:
    return {
        "original": {
            "uid": uid,
            "title": "SOL ecosystem growth accelerates",
            "time_published": "2026-09-02T10:00:00Z",
        },
        "enrichment": {
            "relevance_score": 8,
            "category": "crypto",
            "tickers": ["SOL-USD"],
            "ai_trading_insights": {
                "ticker_analysis": [
                    {
                        "ticker": "SOL-USD",
                        "impact_analysis": {"sentiment": "bullish"},
                    }
                ]
            },
        },
    }


def test_build_regime_tracks_bullish_bases() -> None:
    headline = parse_news_row(_bullish_article())
    assert headline is not None
    state = build_regime_from_headlines(
        [headline],
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"SOL", "BTC"},
        observation_mode=False,
    )
    assert "SOL" in state.bullish_bases
    assert "SOL" not in state.blocked_bases


def test_build_trading_signals_merges_daily_and_regime() -> None:
    state = AlphaIRegimeState(
        enabled=True,
        bullish_bases=frozenset({"SOL"}),
        blocked_bases=frozenset({"ETH"}),
        macro_reduce_only=False,
    )
    daily = {
        "picks": [
            {"base": "AVAX", "score": 32.5, "rank": 1},
            {"base": "SOL", "score": 17.5, "rank": 2},
        ],
        "avoid": [{"base": "XRP", "score": -8.0}],
    }
    signals = build_trading_signals(state, daily)
    assert signals.pick_score("AVAX") == pytest.approx(32.5)
    assert signals.is_top_pick("AVAX")
    assert "SOL" in signals.bullish_bases
    assert "XRP" in signals.avoid_bases
    assert "ETH" in signals.blocked_bases
    assert signals.entry_size_multiplier("AVAX") > Decimal("1")
    assert signals.exit_urgency("XRP") is True


def test_maker_rank_prefers_daily_pick() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=0.5,
        paper_maker_venues="bitvavo",
        live_micro_focus_bases="AVAX,SOL,ETH",
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"SOL"})),
        {
            "picks": [
                {"base": "AVAX", "score": 20.0},
                {"base": "SOL", "score": 10.0},
            ],
            "avoid": [],
        },
    )
    maker.apply_alphai_signals(signals)
    avax_boost = signals.maker_rank_boost("AVAX", is_buy=True)
    eth_boost = signals.maker_rank_boost("ETH", is_buy=True)
    assert avax_boost > eth_boost


def test_is_bullish_buy_daily_and_headline() -> None:
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"SOL"})),
        {"picks": [{"base": "AVAX", "score": 20.0}], "avoid": []},
    )
    assert signals.is_bullish_buy("SOL")
    assert signals.is_bullish_buy("AVAX")
    assert signals.is_strong_bullish_buy("SOL")
    assert signals.is_strong_bullish_buy("AVAX")
    assert not signals.is_bullish_buy("ETH")
    blocked = build_trading_signals(
        AlphaIRegimeState(blocked_bases=frozenset({"SOL"})),
        {"picks": [{"base": "SOL", "score": 20.0}], "avoid": []},
    )
    assert not blocked.is_bullish_buy("SOL")


def test_observation_mode_soft_avoid_only() -> None:
    state = AlphaIRegimeState(
        blocked_bases=frozenset(),
        blocked_detail={"BTC": "SEC probe"},
        observation_mode=True,
    )
    signals = build_trading_signals(state, None)
    assert "BTC" not in signals.blocked_bases
    assert "BTC" in signals.avoid_bases


def test_enforced_blocks_not_soft_avoid() -> None:
    state = AlphaIRegimeState(
        blocked_bases=frozenset({"BTC"}),
        blocked_detail={"BTC": "SEC probe"},
        observation_mode=False,
    )
    signals = build_trading_signals(state, None)
    assert "BTC" in signals.blocked_bases


def test_inventory_build_flag() -> None:
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"AVAX"})),
        {"picks": [{"base": "AVAX", "score": 20.0}], "avoid": []},
    )
    assert signals.inventory_build("AVAX")
    assert not signals.inventory_build("ETH")


def test_ring_fallback_opens_watch_and_focus() -> None:
    signals = build_trading_signals(
        AlphaIRegimeState(macro_reduce_only=True),
        {
            "picks": [{"base": "LINK", "score": 32.5}],
            "avoid": [{"base": "ETH", "score": -10.0}],
            "watch": [{"base": "ADA", "score": 2.0}],
        },
    )
    assert signals.all_bullish_held({"LINK"})
    assert not signals.inventory_build("ADA")
    assert signals.inventory_build("ADA", ring_fallback=True)
    assert signals.is_bullish_buy("ADA", ring_fallback=True)
    assert not signals.is_bullish_buy("ETH", ring_fallback=True)
    assert signals.inventory_build("DOT", ring_fallback=True)  # unscored non-avoid
    assert not signals.inventory_build("ETH", ring_fallback=True)


def test_starbucks_regulation_does_not_trigger_macro() -> None:
    row = {
        "original": {
            "uid": "sbux1",
            "title": "Starbucks dress code didn’t violate NYC workers’ labor rights, US court rules",
            "time_published": "2026-09-02T20:00:00Z",
        },
        "enrichment": {
            "relevance_score": 8,
            "category": "regulation",
            "tickers": ["SBUX"],
            "ai_trading_insights": {
                "ticker_analysis": [
                    {
                        "ticker": "SBUX",
                        "impact_analysis": {"sentiment": "neutral"},
                    }
                ]
            },
        },
    }
    headline = parse_news_row(row)
    assert headline is not None
    state = build_regime_from_headlines(
        [headline],
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"SOL", "ETH", "LINK"},
        observation_mode=False,
    )
    assert state.macro_reduce_only is False


def test_fed_regulation_still_triggers_macro() -> None:
    row = {
        "original": {
            "uid": "fed1",
            "title": "SEC crypto enforcement wave threatens ETF inflows",
            "time_published": "2026-09-02T20:00:00Z",
        },
        "enrichment": {
            "relevance_score": 9,
            "category": "regulation",
            "tickers": ["BTC-USD", "ETH-USD"],
            "ai_trading_insights": {
                "ticker_analysis": [
                    {
                        "ticker": "BTC-USD",
                        "impact_analysis": {"sentiment": "bearish"},
                    }
                ]
            },
        },
    }
    headline = parse_news_row(row)
    assert headline is not None
    state = build_regime_from_headlines(
        [headline],
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"BTC", "ETH"},
        observation_mode=False,
    )
    assert state.macro_reduce_only is True


def test_maker_avoid_base_sell_only() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=0.5,
        paper_maker_venues="bitvavo",
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        None,
        {"picks": [], "avoid": [{"base": "XRP", "score": -5.0}]},
    )
    maker.apply_alphai_signals(signals)
    assert maker._symbol_sell_only("XRPEUR") is True
    assert maker._symbol_sell_only("SOLEUR") is False


@pytest.mark.asyncio
async def test_alphai_no_bijkoop_when_held_anywhere() -> None:
    """Daily policy: if already in portfolio on any venue, do not buy more."""
    from decimal import Decimal

    from bot.core.exchange_types import OrderBook, OrderBookLevel
    from bot.core.models import MarketSnapshot
    from bot.portfolio.models import PortfolioState

    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=55.0,
        paper_maker_allow_buy_only=True,
        paper_maker_same_venue=True,
        paper_maker_venues="bitvavo,okx",
        alphai_bullish_inventory_build_enabled=True,
        live_micro_active_ring_eur=1000.0,
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"AVAX"}), macro_reduce_only=True),
        {"picks": [{"base": "AVAX", "score": 32.5}], "avoid": []},
    )
    maker.apply_alphai_signals(signals)
    maker.set_alphai_macro_caution(True)

    def snap(ex: str, sym: str, bid: float, ask: float) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=sym,
            bid=Decimal(str(bid)),
            ask=Decimal(str(ask)),
            last=Decimal(str((bid + ask) / 2)),
            order_book=OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal("100"))],
                asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal("100"))],
            ),
            exchange=ex,
            latency_ms=50,
        )

    avax_bid, avax_ask, eurusdt = 24.50, 24.55, 1.08
    avaxusdt = avax_bid * eurusdt
    snaps = [
        snap("bitvavo", "AVAXEUR", avax_bid, avax_ask),
        snap("okx", "AVAXEUR", avax_bid - 0.01, avax_ask - 0.01),
        snap("bitvavo", "AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("okx", "AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("bitvavo", "EURUSDT", eurusdt, eurusdt + 0.001),
        snap("okx", "EURUSDT", eurusdt, eurusdt + 0.001),
    ]

    class Ledger:
        venues = ["bitvavo", "okx"]

        def available(self, venue: str, asset: str) -> Decimal:
            if asset == "EUR":
                return Decimal("2000")
            if asset == "AVAX" and venue == "okx":
                return Decimal("10")  # already held on OKX
            return Decimal("0")

    state = PortfolioState(
        cash_usd=Decimal("4000"),
        mark_prices={"AVAXEUR": Decimal("24.5")},
    )
    opps = await maker.evaluate_markets(
        snaps, equity=Decimal("4000"), inventory=Ledger(), portfolio_state=state
    )
    buys = [
        o
        for o in opps
        if str(o.side.value if hasattr(o.side, "value") else o.side).lower().startswith("b")
        and o.symbol == "AVAXEUR"
    ]
    assert buys == []
    assert maker.scan_stats()["reject_counts"].get("held_base_no_new_buy", 0) > 0


@pytest.mark.asyncio
async def test_alphai_picks_best_venue_when_missing() -> None:
    """Missing daily pick → emit only the better venue (cash + NET)."""
    from decimal import Decimal

    from bot.core.exchange_types import OrderBook, OrderBookLevel
    from bot.core.models import MarketSnapshot
    from bot.portfolio.models import PortfolioState

    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=55.0,
        paper_maker_allow_buy_only=True,
        paper_maker_same_venue=True,
        paper_maker_venues="bitvavo,okx",
        alphai_bullish_inventory_build_enabled=True,
        live_micro_active_ring_eur=1000.0,
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"AVAX"}), macro_reduce_only=True),
        {"picks": [{"base": "AVAX", "score": 32.5}], "avoid": []},
    )
    maker.apply_alphai_signals(signals)
    maker.set_alphai_macro_caution(True)

    def snap(ex: str, sym: str, bid: float, ask: float) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=sym,
            bid=Decimal(str(bid)),
            ask=Decimal(str(ask)),
            last=Decimal(str((bid + ask) / 2)),
            order_book=OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal("100"))],
                asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal("100"))],
            ),
            exchange=ex,
            latency_ms=50,
        )

    avax_bid, avax_ask, eurusdt = 24.50, 24.55, 1.08
    avaxusdt = avax_bid * eurusdt
    snaps = [
        snap("bitvavo", "AVAXEUR", avax_bid, avax_ask),
        snap("okx", "AVAXEUR", avax_bid - 0.05, avax_ask - 0.05),  # cheaper on OKX
        snap("bitvavo", "AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("okx", "AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("bitvavo", "EURUSDT", eurusdt, eurusdt + 0.001),
        snap("okx", "EURUSDT", eurusdt, eurusdt + 0.001),
    ]

    class Ledger:
        venues = ["bitvavo", "okx"]

        def available(self, venue: str, asset: str) -> Decimal:
            if asset == "EUR":
                return Decimal("2000")
            return Decimal("0")

    state = PortfolioState(
        cash_usd=Decimal("4000"),
        mark_prices={"AVAXEUR": Decimal("24.5")},
    )
    opps = await maker.evaluate_markets(
        snaps, equity=Decimal("4000"), inventory=Ledger(), portfolio_state=state
    )
    avax_buys = [
        o
        for o in opps
        if o.symbol == "AVAXEUR"
        and str(o.side.value if hasattr(o.side, "value") else o.side).lower().startswith("b")
    ]
    assert len(avax_buys) == 1
    assert (avax_buys[0].metadata or {}).get("buy_exchange") == "okx"


@pytest.mark.asyncio
async def test_alphai_same_venue_sizes_from_clip_not_touch() -> None:
    """AlphaI deploy uses min_notional clip, not thin top-of-book touch."""
    from decimal import Decimal

    from bot.core.exchange_types import OrderBook, OrderBookLevel
    from bot.core.models import MarketSnapshot

    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=55.0,
        paper_maker_allow_buy_only=True,
        paper_maker_same_venue=True,
        paper_maker_venues="bitvavo",
        alphai_bullish_inventory_build_enabled=True,
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"AVAX"}), macro_reduce_only=True),
        {"picks": [{"base": "AVAX", "score": 32.5}], "avoid": []},
    )
    maker.apply_alphai_signals(signals)
    maker.set_alphai_macro_caution(True)

    def snap(sym: str, bid: float, ask: float, qty: float = 0.5) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=sym,
            bid=Decimal(str(bid)),
            ask=Decimal(str(ask)),
            last=Decimal(str((bid + ask) / 2)),
            order_book=OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal(str(qty)))],
                asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal(str(qty)))],
            ),
            exchange="bitvavo",
            latency_ms=50,
        )

    avax_bid, avax_ask, eurusdt = 24.50, 24.55, 1.08
    avaxusdt = avax_bid * eurusdt
    snaps = [
        snap("AVAXEUR", avax_bid, avax_ask),
        snap("AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("EURUSDT", eurusdt, eurusdt + 0.001),
    ]

    class Ledger:
        def venues(self) -> list[str]:
            return ["bitvavo"]

        def available(self, venue: str, asset: str) -> Decimal:
            return Decimal("2000") if asset == "EUR" else Decimal("0")

    opps = await maker.evaluate_markets(
        snaps, equity=Decimal("4000"), inventory=Ledger()
    )
    assert len(opps) == 1
    meta = opps[0].metadata or {}
    assert meta.get("buy_only") is True
    assert opps[0].quantity * opps[0].entry_price >= Decimal("55")


@pytest.mark.asyncio
async def test_alphai_same_venue_skips_toxic_fv_premium() -> None:
    """Local EUR premium vs USDT fair must not block AlphaI same-venue deploy."""
    from decimal import Decimal

    from bot.core.exchange_types import OrderBook, OrderBookLevel
    from bot.core.models import MarketSnapshot

    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=55.0,
        paper_maker_allow_buy_only=True,
        paper_maker_same_venue=True,
        paper_maker_venues="bitvavo",
        alphai_bullish_inventory_build_enabled=True,
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"AVAX"}), macro_reduce_only=True),
        {"picks": [{"base": "AVAX", "score": 32.5}], "avoid": []},
    )
    maker.apply_alphai_signals(signals)
    maker.set_alphai_macro_caution(True)

    def snap(sym: str, bid: float, ask: float, qty: float = 100.0) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=sym,
            bid=Decimal(str(bid)),
            ask=Decimal(str(ask)),
            last=Decimal(str((bid + ask) / 2)),
            order_book=OrderBook(
                symbol=sym,
                bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal(str(qty)))],
                asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal(str(qty)))],
            ),
            exchange="bitvavo",
            latency_ms=50,
        )

    avax_bid, avax_ask, eurusdt = 24.75, 24.80, 1.08
    avaxusdt = 24.50 * eurusdt  # USDT fair below local EUR book
    snaps = [
        snap("AVAXEUR", avax_bid, avax_ask),
        snap("AVAXUSDT", avaxusdt, avaxusdt + 0.05),
        snap("EURUSDT", eurusdt, eurusdt + 0.001),
    ]

    class Ledger:
        def venues(self) -> list[str]:
            return ["bitvavo"]

        def available(self, venue: str, asset: str) -> Decimal:
            return Decimal("2000") if asset == "EUR" else Decimal("0")

    opps = await maker.evaluate_markets(
        snaps, equity=Decimal("4000"), inventory=Ledger()
    )
    assert len(opps) == 1
    assert "toxic_buy_vs_fv" not in maker.scan_stats().get("reject_counts", {})
