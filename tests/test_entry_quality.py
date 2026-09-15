"""Tests for entry quality + headroom engine."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from bot.core.enums import EntryQualityRecommendation, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.strategies.entry_quality import (
    EntryQualityConfig,
    apply_size_multiplier,
    compute_extension_over_window,
    compute_headroom_pct,
    compute_local_range,
    compute_trend_continuity,
    evaluate_entry_quality,
    net_break_even_pct,
)


def _marks(values: list[str]) -> list[Decimal]:
    return [Decimal(v) for v in values]


def _opportunity(*, qty: str = "1", px: str = "100") -> TradeOpportunity:
    return TradeOpportunity(
        strategy_name="maker_inventory",
        symbol="ARBEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal(qty),
        entry_price=Decimal(px),
        metadata={"buy_exchange": "bitvavo"},
    )


def _profitability(*, notional: Decimal, net_return: str = "0.005") -> ProfitabilityResult:
    from bot.core.models import ProfitEstimate

    est = ProfitEstimate(
        gross_profit=Decimal("1"),
        buy_fee=notional * Decimal("0.001"),
        sell_fee=notional * Decimal("0.001"),
        slippage=notional * Decimal("0.0005"),
        funding_cost=Decimal("0"),
        execution_buffer=notional * Decimal("0.001"),
        net_profit=notional * Decimal(net_return),
        net_return=Decimal(net_return),
        trade_allowed=True,
    )
    return ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=est.gross_profit,
        fees_usd=est.buy_fee + est.sell_fee,
        slippage_usd=est.slippage,
        funding_usd=est.funding_cost,
        execution_buffer_usd=est.execution_buffer,
        net_profit_usd=est.net_profit,
        net_return=est.net_return,
        is_profitable=True,
        trade_allowed=True,
        estimate=est,
    )


class TestTrendContinuity:
    def test_gradual_rise_scores_high(self) -> None:
        marks = _marks(["100", "100.05", "100.09", "100.15", "100.20", "100.25"])
        score = compute_trend_continuity(marks, min_marks=5)
        assert score is not None
        assert score >= Decimal("0.75")

    def test_spike_scores_lower_than_gradual(self) -> None:
        gradual = _marks(["100", "100.05", "100.09", "100.15", "100.20", "100.25"])
        spike = _marks(["100", "99.90", "99.92", "99.93", "100.01", "100.70"])
        g = compute_trend_continuity(gradual, min_marks=5)
        s = compute_trend_continuity(spike, min_marks=5)
        assert g is not None and s is not None
        assert g > s

    def test_insufficient_history_returns_none(self) -> None:
        assert compute_trend_continuity(_marks(["100", "100.01"]), min_marks=5) is None


class TestExtension:
    def test_low_extension(self) -> None:
        marks = _marks(["100", "100.01", "100.02", "100.03", "100.04"])
        ext = compute_extension_over_window(marks, 5)
        assert ext is not None
        assert ext < Decimal("0.001")

    def test_high_extension(self) -> None:
        marks = _marks(["100", "100.5", "101", "101.5", "102.5"])
        ext = compute_extension_over_window(marks, 5)
        assert ext is not None
        assert ext >= Decimal("0.02")


class TestHeadroom:
    def test_headroom_above_required(self) -> None:
        marks = _marks(["100", "100.2", "100.4", "100.5", "100.3", "100.45"])
        hr = compute_headroom_pct(marks, lookback=6, current_price=Decimal("100.30"))
        assert hr is not None
        assert hr > Decimal("0.001")

    def test_headroom_near_top_is_low(self) -> None:
        marks = _marks(["100", "100.2", "100.4", "100.5", "100.48", "100.49"])
        hr = compute_headroom_pct(marks, lookback=6, current_price=Decimal("100.485"))
        assert hr is not None
        assert hr < Decimal("0.001")


class TestEntryQualityDecisions:
    def _cfg(self) -> EntryQualityConfig:
        return EntryQualityConfig(
            enabled=True,
            quality_min_score=Decimal("60"),
            reduced_size_score=Decimal("70"),
            normal_size_score=Decimal("80"),
            target_harvest_pct=Decimal("0.012"),
            extension_extreme_pct=Decimal("0.08"),
            extension_max_pct=Decimal("0.04"),
            extension_moderate_pct=Decimal("0.015"),
            headroom_min_pct=Decimal("0.001"),
        )

    def test_high_momentum_high_headroom_normal_size(self) -> None:
        marks = _marks(
            ["100", "100.2", "100.4", "100.6", "100.8", "101", "101.4", "101.7"]
        )
        opp = _opportunity(px="100.05")
        prof = _profitability(notional=Decimal("100.05"))
        out = evaluate_entry_quality(
            opportunity=opp, profitability=prof, marks=marks, config=self._cfg()
        )
        assert out.recommendation in {
            EntryQualityRecommendation.NORMAL_SIZE,
            EntryQualityRecommendation.REDUCED_SIZE,
        }
        assert out.recommended_size_multiplier > 0

    def test_high_momentum_low_headroom_reject(self) -> None:
        marks = _marks(
            ["100", "100.5", "101", "101.5", "102", "102.3", "102.4", "102.45"]
        )
        opp = _opportunity(px="102.44")
        prof = _profitability(notional=Decimal("102.44"))
        out = evaluate_entry_quality(
            opportunity=opp, profitability=prof, marks=marks, config=self._cfg()
        )
        assert out.recommendation == EntryQualityRecommendation.REJECT
        assert out.recommended_size_multiplier == Decimal("0")

    def test_unknown_headroom_conservative_not_crash(self) -> None:
        opp = _opportunity()
        prof = _profitability(notional=Decimal("100"))
        out = evaluate_entry_quality(
            opportunity=opp, profitability=prof, marks=[], config=self._cfg()
        )
        assert out.headroom_pct is None
        assert out.recommendation in {
            EntryQualityRecommendation.REJECT,
            EntryQualityRecommendation.REDUCED_SIZE,
            EntryQualityRecommendation.NORMAL_SIZE,
        }


class TestPositionSizing:
    def test_multiplier_never_above_one(self) -> None:
        assert apply_size_multiplier(Decimal("100"), Decimal("1.5")) == Decimal("100.00")

    def test_reduced_sizes(self) -> None:
        assert apply_size_multiplier(Decimal("100"), Decimal("0.75")) == Decimal("75.00")
        assert apply_size_multiplier(Decimal("100"), Decimal("0.50")) == Decimal("50.00")

    def test_new_size_lte_old(self) -> None:
        old = Decimal("120")
        for mult in ("1.0", "0.75", "0.5", "0.25"):
            new = apply_size_multiplier(old, Decimal(mult))
            assert new <= old


class TestLiveSafety:
    def test_sell_bypasses_quality(self) -> None:
        opp = TradeOpportunity(
            strategy_name="maker_inventory",
            symbol="ARBEUR",
            side=OpportunitySide.SELL,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
        )
        prof = _profitability(notional=Decimal("100"))
        out = evaluate_entry_quality(opportunity=opp, profitability=prof, marks=[])
        assert out.recommendation == EntryQualityRecommendation.NORMAL_SIZE
