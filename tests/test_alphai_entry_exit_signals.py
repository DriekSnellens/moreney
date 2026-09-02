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
