"""Tests for capital velocity, venue economics, MFE analytics."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from bot.core.enums import EntryQualityRecommendation, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.strategies.entry_quality import EntryQualityConfig, apply_size_multiplier
from bot.strategies.opportunity_economics import (
    CapitalEfficiencyConfig,
    CapitalAllocator,
    VenueEconomicsConfig,
    adaptive_trail_should_hold,
    assess_capital_efficiency,
    assess_opportunity_economics,
    compute_mfe_record,
    compute_net_eur_per_capital_hour,
    compute_net_eur_per_hour,
    rank_venue_for_opportunity,
    select_best_buy_opportunities,
    underwater_recovery_metrics,
)


def _opp(*, symbol: str = "ARBEUR", qty: str = "1", px: str = "100", meta: dict | None = None):
    return TradeOpportunity(
        strategy_name="maker_inventory",
        symbol=symbol,
        side=OpportunitySide.BUY,
        quantity=Decimal(qty),
        entry_price=Decimal(px),
        metadata=meta or {"buy_exchange": "bitvavo", "net_profit_eur": "0.80"},
    )


def _prof(*, net: str = "0.80", notional: Decimal | None = None) -> ProfitabilityResult:
    n = notional or Decimal("100")
    net_d = Decimal(net)
    return ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=net_d + Decimal("0.5"),
        fees_usd=Decimal("0.25"),
        slippage_usd=Decimal("0.05"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.10"),
        net_profit_usd=net_d,
        net_return=net_d / n if n > 0 else Decimal("0"),
        is_profitable=net_d > 0,
        trade_allowed=True,
    )


class TestCapitalEfficiency:
    def test_faster_trade_has_higher_eur_per_hour(self) -> None:
        cfg = CapitalEfficiencyConfig(
            enabled=True,
            min_expected_net_profit_per_hour=Decimal("0.05"),
            default_hold_seconds=Decimal("600"),
        )
        fast = assess_capital_efficiency(
            opportunity=_opp(),
            profitability=_prof(net="1"),
            config=cfg,
        )
        slow_cfg = CapitalEfficiencyConfig(
            enabled=True,
            min_expected_net_profit_per_hour=Decimal("0.05"),
            default_hold_seconds=Decimal("7200"),
        )
        slow = assess_capital_efficiency(
            opportunity=_opp(),
            profitability=_prof(net="2"),
            config=slow_cfg,
        )
        assert fast.expected_net_profit_per_hour is not None
        assert slow.expected_net_profit_per_hour is not None
        assert fast.expected_net_profit_per_hour > slow.expected_net_profit_per_hour

    def test_low_efficiency_rejects(self) -> None:
        cfg = CapitalEfficiencyConfig(
            enabled=True,
            min_expected_net_profit_per_hour=Decimal("10"),
            default_hold_seconds=Decimal("3600"),
        )
        out = assess_capital_efficiency(
            opportunity=_opp(),
            profitability=_prof(net="0.01"),
            config=cfg,
        )
        assert out.recommendation == EntryQualityRecommendation.REJECT
        assert out.recommended_size_multiplier == Decimal("0")

    def test_multiplier_never_exceeds_one(self) -> None:
        cfg = CapitalEfficiencyConfig(enabled=True)
        out = assess_capital_efficiency(
            opportunity=_opp(),
            profitability=_prof(net="5"),
            config=cfg,
        )
        assert out.recommended_size_multiplier <= Decimal("1")


class TestNetEurMetrics:
    def test_net_eur_per_hour(self) -> None:
        v = compute_net_eur_per_hour(
            realized_net_eur=Decimal("6"),
            elapsed_seconds=Decimal("3600"),
        )
        assert v == Decimal("6")

    def test_net_eur_per_capital_hour(self) -> None:
        v = compute_net_eur_per_capital_hour(
            realized_net_eur=Decimal("6"),
            capital_deployed_eur=Decimal("1000"),
            elapsed_seconds=Decimal("3600"),
        )
        assert v == Decimal("6") / Decimal("1000")

    def test_zero_denominator_returns_none(self) -> None:
        assert compute_net_eur_per_hour(
            realized_net_eur=Decimal("1"), elapsed_seconds=Decimal("0")
        ) is None
        assert compute_net_eur_per_capital_hour(
            realized_net_eur=Decimal("1"),
            capital_deployed_eur=Decimal("0"),
            elapsed_seconds=Decimal("3600"),
        ) is None


class TestVenueRanking:
    def test_higher_net_wins(self) -> None:
        opp = _opp(meta={"buy_exchange": "bitvavo", "net_profit_eur": "0.80"})
        venue, scores = rank_venue_for_opportunity(
            opp,
            _prof(net="0.80"),
            venue_net={"bitvavo": Decimal("0.80"), "okx": Decimal("0.92")},
        )
        assert venue == "okx"
        assert len(scores) == 2
        assert scores[0].economic_score >= scores[1].economic_score

    def test_select_best_buy_dedupes_symbol(self) -> None:
        bv = _opp(meta={"buy_exchange": "bitvavo", "net_profit_eur": "0.70"})
        okx = _opp(meta={"buy_exchange": "okx", "net_profit_eur": "0.95"})
        out = select_best_buy_opportunities([bv, okx], config=VenueEconomicsConfig(enabled=True))
        buys = [o for o in out if str(o.side).lower().endswith("buy")]
        assert len(buys) == 1
        assert (buys[0].metadata or {}).get("buy_exchange") == "okx"


class TestMFE:
    def test_mfe_capture_ratio(self) -> None:
        rec = compute_mfe_record(
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            mfe_price=Decimal("102"),
            mae_price=Decimal("99.5"),
            cost_basis=Decimal("100"),
            realized_net_eur=Decimal("0.50"),
            notional=Decimal("100"),
            holding_seconds=Decimal("600"),
        )
        assert rec.mfe_pct == Decimal("0.02")
        assert rec.mae_pct == Decimal("-0.005")
        assert rec.mfe_capture_ratio is not None
        assert rec.mfe_capture_ratio > Decimal("0")

    def test_zero_mfe_capture_none(self) -> None:
        rec = compute_mfe_record(
            entry_price=Decimal("100"),
            exit_price=Decimal("99"),
            mfe_price=Decimal("100"),
            mae_price=Decimal("99"),
            cost_basis=Decimal("100"),
            realized_net_eur=Decimal("-1"),
            notional=Decimal("100"),
            holding_seconds=Decimal("60"),
        )
        assert rec.mfe_capture_ratio is None

    def test_negative_trade(self) -> None:
        rec = compute_mfe_record(
            entry_price=Decimal("100"),
            exit_price=Decimal("98"),
            mfe_price=Decimal("100.5"),
            mae_price=Decimal("98"),
            cost_basis=Decimal("100"),
            realized_net_eur=Decimal("-2"),
            notional=Decimal("100"),
            holding_seconds=Decimal("120"),
        )
        assert rec.realized_net_pct < Decimal("0")


class TestAdaptiveTrail:
    def test_strong_trend_holds(self) -> None:
        marks = [Decimal("100"), Decimal("100.5"), Decimal("101"), Decimal("101.2")]
        assert adaptive_trail_should_hold(
            symbol="X",
            marks=marks,
            extension_pct=Decimal("0.01"),
            continuity=Decimal("0.8"),
            headroom_pct=Decimal("0.01"),
            enabled=True,
        )

    def test_exhausted_trend_does_not_hold(self) -> None:
        marks = [Decimal("100"), Decimal("101"), Decimal("100.5"), Decimal("100.2")]
        assert not adaptive_trail_should_hold(
            symbol="X",
            marks=marks,
            extension_pct=Decimal("0.03"),
            continuity=Decimal("0.2"),
            headroom_pct=Decimal("0.001"),
            enabled=True,
        )


class TestSizingInvariants:
    def test_reduced_below_normal(self) -> None:
        base = Decimal("100")
        normal = apply_size_multiplier(base, Decimal("1"))
        reduced = apply_size_multiplier(base, Decimal("0.75"))
        reject = apply_size_multiplier(base, Decimal("0"))
        assert normal == base
        assert reduced < normal
        assert reject == Decimal("0")

    def test_economics_never_increases_size(self) -> None:
        cfg = EntryQualityConfig(enabled=False)
        cap = CapitalEfficiencyConfig(enabled=False)
        out = assess_opportunity_economics(
            opportunity=_opp(),
            profitability=_prof(),
            entry_config=cfg,
            capital_config=cap,
        )
        assert out.combined_multiplier <= Decimal("1")


class TestUnderwaterRecovery:
    def test_underwater_metrics(self) -> None:
        m = underwater_recovery_metrics(
            mark=Decimal("98"),
            break_even=Decimal("100"),
            notional_eur=Decimal("200"),
            age_seconds=Decimal("3600"),
            expected_hold_seconds=Decimal("1800"),
        )
        assert m.get("underwater_pct") is not None
        assert m.get("capital_locked_eur") == "200.00"


class TestCapitalAllocator:
    def test_rank_and_assess(self) -> None:
        alloc = CapitalAllocator(
            capital_config=CapitalEfficiencyConfig(enabled=False),
            venue_config=VenueEconomicsConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        bv = _opp(meta={"buy_exchange": "bitvavo", "net_profit_eur": "0.5"})
        okx = _opp(meta={"buy_exchange": "okx", "net_profit_eur": "1.0"})
        ranked = alloc.rank_opportunities([bv, okx])
        assert len(ranked) == 1
        econ = alloc.assess(opportunity=okx, profitability=_prof(net="1.0"))
        assert econ.combined_multiplier <= Decimal("1")
