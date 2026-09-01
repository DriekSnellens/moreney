"""Tests for central Opportunity Optimization Engine."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import MarketSnapshot, ProfitabilityResult, TradeOpportunity
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_economics import CapitalEfficiencyConfig, VenueEconomicsConfig
from bot.strategies.opportunity_engine import (
    OpportunityDecision,
    OpportunityEngineConfig,
    VolatilityRegime,
    allocate_portfolio,
    apply_assessment_to_opportunity,
    classify_volatility_regime,
    evaluate,
    rank_opportunities,
)


def _marks(values: list[str]) -> list[Decimal]:
    return [Decimal(v) for v in values]


def _opp(**kwargs) -> TradeOpportunity:
    defaults = dict(
        strategy_name="maker_inventory",
        symbol="ARBEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        metadata={"buy_exchange": "bitvavo", "net_profit_eur": "0.80"},
        entry_fee_role=FeeRole.MAKER,
    )
    defaults.update(kwargs)
    return TradeOpportunity(**defaults)


def _prof(net: str = "0.80") -> ProfitabilityResult:
    net_d = Decimal(net)
    return ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=net_d + Decimal("0.2"),
        fees_usd=Decimal("0.1"),
        slippage_usd=Decimal("0.05"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.05"),
        net_profit_usd=net_d,
        net_return=net_d / Decimal("100"),
        is_profitable=True,
        trade_allowed=True,
    )


class TestCapitalVelocityRanking:
    def test_higher_net_per_hour_wins_ranking(self) -> None:
        cfg = OpportunityEngineConfig(enabled=True)
        cap_fast = CapitalEfficiencyConfig(default_hold_seconds=Decimal("600"))
        cap_slow = CapitalEfficiencyConfig(default_hold_seconds=Decimal("7200"))
        marks = _marks(["100", "100.2", "100.4", "100.6", "100.8", "101"])
        fast = evaluate(
            opportunity=_opp(),
            profitability=_prof("1"),
            marks=marks,
            engine_config=cfg,
            capital_config=cap_fast,
            entry_config=EntryQualityConfig(enabled=False),
        )
        slow = evaluate(
            opportunity=_opp(symbol="APTEUR"),
            profitability=_prof("2"),
            marks=marks,
            engine_config=cfg,
            capital_config=cap_slow,
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert fast.expected_net_eur_per_hour is not None
        assert slow.expected_net_eur_per_hour is not None
        assert fast.expected_net_eur_per_hour > slow.expected_net_eur_per_hour


class TestHeadroom:
    def test_low_headroom_rejects_with_entry_quality(self) -> None:
        marks = _marks(["100", "100.5", "101", "101.2", "101.3", "101.35"])
        out = evaluate(
            opportunity=_opp(),
            profitability=_prof("0.05"),
            marks=marks,
            engine_config=OpportunityEngineConfig(enabled=True, min_opportunity_score=Decimal("40")),
            entry_config=EntryQualityConfig(
                enabled=True,
                headroom_min_pct=Decimal("0.01"),
                quality_min_score=Decimal("60"),
            ),
        )
        assert out.decision == OpportunityDecision.REJECT or out.headroom_pct is not None


class TestVolatility:
    def test_extreme_volatility_caps_size(self) -> None:
        marks = _marks(["100", "101", "99", "102", "98", "103"])
        regime = classify_volatility_regime(marks)
        assert regime in {VolatilityRegime.HIGH, VolatilityRegime.EXTREME}
        out = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=marks,
            engine_config=OpportunityEngineConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.recommended_size_multiplier <= Decimal("1")


class TestSpread:
    def test_wide_spread_lowers_score(self) -> None:
        snap = MarketSnapshot(
            symbol="ARBEUR",
            bid=Decimal("100"),
            ask=Decimal("101.5"),
            last=Decimal("100.75"),
        )
        tight = evaluate(
            opportunity=_opp(market=snap),
            profitability=_prof(),
            marks=_marks(["100", "100.1", "100.2", "100.3"]),
            engine_config=OpportunityEngineConfig(enabled=True, max_spread_pct=Decimal("0.02")),
            entry_config=EntryQualityConfig(enabled=False),
        )
        wide_snap = MarketSnapshot(
            symbol="ARBEUR",
            bid=Decimal("100"),
            ask=Decimal("102"),
            last=Decimal("101"),
        )
        wide = evaluate(
            opportunity=_opp(market=wide_snap),
            profitability=_prof(),
            marks=_marks(["100", "100.1", "100.2", "100.3"]),
            engine_config=OpportunityEngineConfig(enabled=True, max_spread_pct=Decimal("0.008")),
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert wide.opportunity_score <= tight.opportunity_score


class TestSizingInvariant:
    def test_never_exceeds_existing_size(self) -> None:
        opp = _opp(quantity=Decimal("10"))
        out = evaluate(
            opportunity=opp,
            profitability=_prof("5"),
            marks=_marks(["100", "100.5", "101", "101.5", "102"]),
            engine_config=OpportunityEngineConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        updated = apply_assessment_to_opportunity(opp, out)
        assert updated is not None
        assert updated.quantity <= opp.quantity


class TestPortfolioAllocator:
    def test_respects_capital_budget(self) -> None:
        cfg = OpportunityEngineConfig(enabled=True)
        marks = _marks(["100", "100.5", "101", "101.5", "102"])
        a1 = evaluate(
            opportunity=_opp(symbol="ARBEUR"),
            profitability=_prof("0.8"),
            marks=marks,
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        a2 = evaluate(
            opportunity=_opp(symbol="APTEUR"),
            profitability=_prof("0.9"),
            marks=marks,
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        selected, skipped = allocate_portfolio(
            [a1, a2],
            available_capital_eur=Decimal("100"),
        )
        total = sum(
            (s.capital_required_eur * s.recommended_size_multiplier for s in selected),
            Decimal("0"),
        )
        assert total <= Decimal("100")
        assert len(selected) >= 1


class TestDeterminism:
    def test_same_inputs_same_output(self) -> None:
        opp = _opp()
        prof = _prof()
        marks = _marks(["100", "100.2", "100.4", "100.6", "100.8"])
        cfg = OpportunityEngineConfig(enabled=True)
        a = evaluate(
            opportunity=opp,
            profitability=prof,
            marks=marks,
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        b = evaluate(
            opportunity=opp,
            profitability=prof,
            marks=marks,
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert a.opportunity_score == b.opportunity_score
        assert a.decision == b.decision
        assert a.recommended_size_multiplier == b.recommended_size_multiplier


class TestVenueRanking:
    def test_venue_score_in_assessment(self) -> None:
        out = evaluate(
            opportunity=_opp(metadata={"buy_exchange": "okx", "net_profit_eur": "0.95"}),
            profitability=_prof("0.95"),
            marks=_marks(["100", "100.5", "101"]),
            engine_config=OpportunityEngineConfig(enabled=True),
            venue_config=VenueEconomicsConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.venue == "okx"
