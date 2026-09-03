"""Tests for AlphaI scored features, freshness, and adverse×news timing."""

from __future__ import annotations

from decimal import Decimal

from bot.integrations.alphai.features import (
    AlphaIFeatureConfig,
    compute_alphai_feature,
    freshness_factor,
)
from bot.integrations.alphai.parse import AlphaIRegimeState
from bot.integrations.alphai.signals import build_trading_signals
from bot.strategies.opportunity_engine import OpportunityEngineConfig, evaluate
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_economics import CapitalEfficiencyConfig, VenueEconomicsConfig
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from uuid import uuid4


def _signals():
    return build_trading_signals(
        AlphaIRegimeState(
            enabled=True,
            bullish_bases=frozenset({"ARB"}),
            blocked_bases=frozenset({"SOL"}),
        ),
        {
            "picks": [{"base": "ARB", "score": 5.0}, {"base": "APT", "score": 3.0}],
            "avoid": [{"base": "NEAR", "score": -2.0}],
            "watch": [{"base": "OP", "score": 1.0}],
        },
    )


def test_freshness_decay():
    full = freshness_factor(
        signal_age_hours=Decimal("0"),
        half_life_hours=Decimal("4"),
        max_hours=Decimal("24"),
    )
    half = freshness_factor(
        signal_age_hours=Decimal("4"),
        half_life_hours=Decimal("4"),
        max_hours=Decimal("24"),
    )
    dead = freshness_factor(
        signal_age_hours=Decimal("24"),
        half_life_hours=Decimal("4"),
        max_hours=Decimal("24"),
    )
    assert full == Decimal("1")
    assert half == Decimal("0.5")
    assert dead == Decimal("0")


def test_avoid_beats_stale_bullish():
    sig = _signals()
    # NEAR is avoid
    feat = compute_alphai_feature(
        "NEAR",
        sig,
        signal_age_hours_value=Decimal("1"),
        config=AlphaIFeatureConfig(),
    )
    assert feat.feature_score <= Decimal("0.20")
    assert "alphai_avoid" in feat.reasons


def test_adverse_bullish_wait():
    sig = _signals()
    feat = compute_alphai_feature(
        "ARB",
        sig,
        adverse_score=Decimal("0.70"),
        signal_age_hours_value=Decimal("1"),
        config=AlphaIFeatureConfig(),
    )
    assert feat.entry_timing == "WAIT"
    assert feat.size_multiplier < Decimal("1")


def test_capital_preference_boost_top_pick():
    sig = _signals()
    feat = compute_alphai_feature(
        "ARB",
        sig,
        signal_age_hours_value=Decimal("1"),
        config=AlphaIFeatureConfig(),
    )
    assert feat.capital_preference > Decimal("1")


def test_opportunity_engine_includes_alphai_score():
    sig = _signals()
    opp = TradeOpportunity(
        strategy_name="maker_inventory",
        symbol="ARBEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("1"),
        metadata={"buy_exchange": "bitvavo", "net_profit_eur": "0.08"},
        entry_fee_role=FeeRole.MAKER,
    )
    prof = ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=Decimal("0.10"),
        fees_usd=Decimal("0.01"),
        slippage_usd=Decimal("0.005"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.005"),
        net_profit_usd=Decimal("0.08"),
        net_return=Decimal("0.008"),
        is_profitable=True,
        trade_allowed=True,
    )
    out = evaluate(
        opportunity=opp,
        profitability=prof,
        marks=[Decimal("1"), Decimal("1.01"), Decimal("1.02")],
        entry_config=EntryQualityConfig(enabled=False),
        capital_config=CapitalEfficiencyConfig(enabled=True),
        venue_config=VenueEconomicsConfig(enabled=True),
        engine_config=OpportunityEngineConfig(
            enabled=True,
            alphai_feature_enabled=True,
            weight_alphai=Decimal("0.10"),
        ),
        alphai_signals=sig,
        alphai_feature_config=AlphaIFeatureConfig(enabled=True, shadow_only=True),
        alphai_signal_age_hours=Decimal("1"),
    )
    assert out.alphai_feature_score is not None
    assert out.alphai_feature_score > Decimal("0.5")
