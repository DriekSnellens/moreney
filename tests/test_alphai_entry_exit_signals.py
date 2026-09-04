"""Tests for AlphaI entry/exit signal weaving."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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


def test_priority_buy_bases_and_slot_penalty() -> None:
    signals = build_trading_signals(
        None,
        {
            "picks": [
                {"base": "XRP", "score": 54.0},
                {"base": "LINK", "score": 39.0},
                {"base": "UNI", "score": 36.0},
                {"base": "ETH", "score": 18.0},
            ],
            "avoid": [],
        },
    )
    assert signals.priority_buy_bases() == frozenset({"XRP", "LINK", "UNI", "ETH"})
    assert signals.unheld_priority_buys({"LINK"}) == frozenset({"XRP", "UNI", "ETH"})
    assert signals.is_slot_priority_buy("XRP")
    assert signals.non_pick_slot_penalty("XRP", {"LINK"}) == Decimal("0")
    assert signals.non_pick_slot_penalty("SOL", {"LINK"}) == Decimal("-0.60")
    assert signals.non_pick_slot_penalty("SOL", {"XRP", "LINK", "UNI", "ETH"}) == Decimal(
        "0"
    )


def test_entry_size_multiplier_scales_high_alpha_scores() -> None:
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"ETH"})),
        {
            "picks": [
                {"base": "ETH", "score": 96.0, "rank": 1},
                {"base": "XRP", "score": 53.5, "rank": 2},
                {"base": "ADA", "score": 2.0, "rank": 3},
            ],
            "avoid": [{"base": "SOL", "score": -5.0}],
        },
    )
    eth_mult = signals.entry_size_multiplier("ETH")
    xrp_mult = signals.entry_size_multiplier("XRP")
    ada_mult = signals.entry_size_multiplier("ADA")
    assert eth_mult > xrp_mult >= ada_mult
    assert eth_mult <= Decimal("1.50")
    assert eth_mult >= Decimal("1.40")
    assert signals.entry_size_multiplier("SOL") == Decimal("0.75")


def test_alphai_buy_clip_cap_prefers_strong_picks(tmp_path: Path) -> None:
    from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor
    from bot.live.micro_engine import LiveMicroEngine
    from bot.portfolio.portfolio import PaperPortfolio

    settings = Settings(
        execution_mode="paper",
        live_trading_enabled=True,
        live_micro_enabled=True,
        live_orders_unlocked=True,
        live_allow_without_research_unlock=True,
        live_micro_venues="bitvavo",
        live_micro_first_clip_eur=140.0,
        live_micro_add_clip_eur=200.0,
        live_micro_alphai_priority_clip_eur=220.0,
        live_micro_alphai_strong_clip_eur=280.0,
        live_micro_alphai_winner_add_only=True,
        live_micro_winner_add_enabled=True,
        live_micro_bridge_persist_path=str(tmp_path / "clip.json"),
        alphai_require_bullish_new_buys=True,
        alphai_bullish_buy_enabled=True,
    )
    bridge = MicroBudgetLiveExecutor(
        settings,
        portfolio=PaperPortfolio(settings, starting_eur=Decimal("2000")),
        live_engine=LiveMicroEngine(settings),
        budget_eur=Decimal("2000"),
        live_maker=True,
    )
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"ETH"})),
        {
            "picks": [
                {"base": "ETH", "score": 96.0, "rank": 1},
                {"base": "XRP", "score": 2.5, "rank": 2},
                {"base": "LINK", "score": 2.0, "rank": 3},
            ],
            "avoid": [],
        },
    )
    bridge._alphai_signals = signals  # noqa: SLF001
    assert bridge._buy_clip_cap_eur("bitvavo", "ETH") == Decimal("280")  # noqa: SLF001
    # Priority pick with score < 3 → priority clip (not strong).
    assert bridge._buy_clip_cap_eur("bitvavo", "XRP") == Decimal("220")  # noqa: SLF001
    # Non-AlphaI falls back to first clip.
    assert bridge._buy_clip_cap_eur("bitvavo", "DOT") == Decimal("140")  # noqa: SLF001
    # Winner-add requires AlphaI when alphai_winner_add_only.
    assert bridge._winner_add_eligible("bitvavo", "DOT") is False  # noqa: SLF001


def test_alphai_stance_drives_buy_and_sell() -> None:
    signals = build_trading_signals(
        AlphaIRegimeState(
            bullish_bases=frozenset({"UNI"}),
            blocked_bases=frozenset({"ETH"}),
        ),
        {
            "picks": [
                {"base": "XRP", "score": 54.0},
                {"base": "UNI", "score": 36.0},
            ],
            "avoid": [{"base": "SOL", "score": -8.0}],
        },
    )
    assert signals.is_bearish("SOL")
    assert signals.is_bearish("ETH")
    assert signals.allows_new_buy("XRP")
    assert signals.allows_new_buy("UNI")
    assert not signals.allows_new_buy("SOL")
    assert not signals.allows_new_buy("DOT")
    assert signals.recycle_sell_only("DOT")
    assert signals.exit_urgency("SOL")
    assert signals.exit_urgency("DOT")  # neutral recycle → faster BE harvest
    assert not signals.exit_urgency("XRP")
    assert signals.be_harvest_gain_scale("DOT") < signals.be_harvest_gain_scale("XRP")
    assert signals.maker_rank_boost("XRP", is_buy=True) > signals.maker_rank_boost(
        "DOT", is_buy=True
    )
    assert signals.maker_rank_boost("SOL", is_buy=False) > signals.maker_rank_boost(
        "XRP", is_buy=False
    )


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
    assert signals.is_strong_bullish_buy("SOL")  # live headline bullish
    assert not signals.is_strong_bullish_buy("AVAX")  # weak score → priority only
    assert not signals.is_bullish_buy("ETH")
    strong = build_trading_signals(
        None,
        {
            "picks": [
                {"base": "ETH", "score": 96.0, "rank": 1},
                {"base": "DOGE", "score": 18.0, "rank": 2},
            ]
        },
    )
    assert strong.is_strong_bullish_buy("ETH")
    assert not strong.is_strong_bullish_buy("DOGE")
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


def test_openai_copyright_does_not_trigger_macro() -> None:
    row = {
        "original": {
            "uid": "oai1",
            "title": "Trump Administration Sides With OpenAI in Publishers’ Copyright Lawsuits",
            "time_published": "2026-09-02T20:00:00Z",
        },
        "enrichment": {
            "relevance_score": 8,
            "category": "regulation",
            "tickers": ["MSFT"],
            "ai_trading_insights": {
                "ticker_analysis": [
                    {
                        "ticker": "MSFT",
                        "impact_analysis": {"sentiment": "negative"},
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
        focus_bases={"SOL", "ETH"},
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


def test_require_bullish_new_buys_sell_only_for_neutral() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=0.5,
        paper_maker_venues="bitvavo",
        alphai_require_bullish_new_buys=True,
    )
    maker = MakerInventoryStrategy(settings)
    signals = build_trading_signals(
        AlphaIRegimeState(bullish_bases=frozenset({"UNI"})),
        {"picks": [{"base": "XRP", "score": 54.0}], "avoid": [{"base": "SOL", "score": -5.0}]},
    )
    maker.apply_alphai_signals(signals)
    assert maker._symbol_sell_only("XRPEUR") is False
    assert maker._symbol_sell_only("UNIEUR") is False
    assert maker._symbol_sell_only("SOLEUR") is True
    assert maker._symbol_sell_only("DOTEUR") is True
    assert maker._alphai_ring_fallback_active() is False


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
