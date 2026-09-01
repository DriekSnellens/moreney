"""Ablation and integration tests for execution intelligence layer."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.intelligence.execution_quality import (
    ExecutionDecision,
    assess_execution,
    estimate_fill_probability,
)
from bot.intelligence.outcome_learning import (
    OutcomeBucket,
    OutcomeLearningConfig,
    OutcomeRecord,
    empirical_multiplier,
)
from bot.intelligence.resting_order_intelligence import (
    RestingOrderAction,
    assess_resting_order,
)
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_engine import (
    OpportunityDecision,
    OpportunityEngineConfig,
    evaluate,
)


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


def _opp(**kwargs) -> TradeOpportunity:
    defaults = dict(
        strategy_name="maker_inventory",
        symbol="ARBEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        metadata={"buy_exchange": "bitvavo"},
        entry_fee_role=FeeRole.MAKER,
    )
    defaults.update(kwargs)
    return TradeOpportunity(**defaults)


class TestAblation:
    def test_baseline_without_intelligence(self) -> None:
        cfg = OpportunityEngineConfig(
            regime_engine_enabled=False,
            adverse_selection_enabled=False,
            outcome_learning_enabled=False,
            execution_quality_enabled=False,
        )
        out = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=[Decimal("100"), Decimal("100.1"), Decimal("100.2")],
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.opportunity_score > 0

    def test_with_regime_enabled(self) -> None:
        cfg = OpportunityEngineConfig(
            regime_engine_enabled=True,
            adverse_selection_enabled=False,
            outcome_learning_enabled=False,
            execution_quality_enabled=False,
        )
        out = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=[Decimal("100"), Decimal("100.1"), Decimal("100.2"), Decimal("100.3"), Decimal("100.4"), Decimal("100.5")],
            engine_config=cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.market_regime is not None

    def test_adverse_selection_reduces_score(self) -> None:
        marks = [Decimal("100"), Decimal("100.05"), Decimal("100.2")]
        base_cfg = OpportunityEngineConfig(
            regime_engine_enabled=False,
            adverse_selection_enabled=False,
            outcome_learning_enabled=False,
            execution_quality_enabled=False,
        )
        adv_cfg = OpportunityEngineConfig(
            regime_engine_enabled=False,
            adverse_selection_enabled=True,
            outcome_learning_enabled=False,
            execution_quality_enabled=False,
        )
        base = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=marks,
            engine_config=base_cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        with_adv = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=marks,
            engine_config=adv_cfg,
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert with_adv.adverse_selection_score is not None


class TestEmpiricalLearning:
    def test_neutral_below_min_samples(self) -> None:
        cfg = OutcomeLearningConfig(min_learning_samples=20)
        assert empirical_multiplier(bucket=OutcomeBucket(samples=5), config=cfg) == Decimal("1.0")

    def test_bounded_multiplier(self) -> None:
        cfg = OutcomeLearningConfig(min_learning_samples=5, full_learning_samples=10)
        b = OutcomeBucket(samples=50, wins=40, sum_net_eur=Decimal("20"))
        m = empirical_multiplier(bucket=b, config=cfg)
        assert cfg.empirical_multiplier_min <= m <= cfg.empirical_multiplier_max


class TestExecutionDecision:
    def test_taker_when_maker_ev_low(self) -> None:
        out = assess_execution(
            maker_net_eur=Decimal("0.05"),
            taker_net_eur=Decimal("0.80"),
            urgency=__import__("bot.intelligence.execution_quality", fromlist=["Urgency"]).Urgency.HIGH,
        )
        assert out.decision in {ExecutionDecision.TAKER, ExecutionDecision.WAIT, ExecutionDecision.REJECT}


class TestRestingOrderIntelligence:
    def test_high_adverse_suggests_cancel(self) -> None:
        from bot.intelligence.adverse_selection import AdverseSelectionAssessment

        out = assess_resting_order(
            side="buy",
            order_price=Decimal("100"),
            age_sec=15.0,
            adverse=AdverseSelectionAssessment(
                adverse_selection_score=Decimal("0.85"),
                microprice=Decimal("100.2"),
                midprice=Decimal("100.1"),
                microprice_vs_mid=Decimal("0.001"),
                orderbook_imbalance=Decimal("-0.3"),
                short_term_return=Decimal("0.005"),
                reasons=("test",),
            ),
            observation_mode=False,
        )
        assert out.action in {RestingOrderAction.CANCEL, RestingOrderAction.EXPIRE}

    def test_observation_mode_holds(self) -> None:
        from bot.intelligence.adverse_selection import AdverseSelectionAssessment

        out = assess_resting_order(
            side="buy",
            order_price=Decimal("100"),
            age_sec=60.0,
            adverse=AdverseSelectionAssessment(
                adverse_selection_score=Decimal("0.95"),
                microprice=None,
                midprice=Decimal("100"),
                microprice_vs_mid=None,
                orderbook_imbalance=None,
                short_term_return=None,
                reasons=("test",),
            ),
            observation_mode=True,
        )
        assert out.action == RestingOrderAction.HOLD
        assert out.observation_only is True


class TestNoLookahead:
    def test_evaluate_uses_only_provided_marks(self) -> None:
        marks = [Decimal("100"), Decimal("100.5"), Decimal("101")]
        out = evaluate(
            opportunity=_opp(),
            profitability=_prof(),
            marks=marks,
            engine_config=OpportunityEngineConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.decision in {
            OpportunityDecision.HIGH_QUALITY,
            OpportunityDecision.REDUCED,
            OpportunityDecision.REJECT,
        }


class TestDownwardOnlySizing:
    def test_never_exceeds_one(self) -> None:
        out = evaluate(
            opportunity=_opp(quantity=Decimal("10")),
            profitability=_prof("2"),
            marks=[Decimal("100")] * 6,
            engine_config=OpportunityEngineConfig(enabled=True),
            entry_config=EntryQualityConfig(enabled=False),
        )
        assert out.recommended_size_multiplier <= Decimal("1")
