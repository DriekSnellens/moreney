"""NET PnL ranking, EV calibration, markout 60s, missed-opportunity gates."""

from decimal import Decimal
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunityDecisionAction, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.opportunity.calibration import EvCalibrator, calibration_key, route_key
from bot.opportunity.economics import build_fill_economics
from bot.opportunity.ev_engine import ExpectedValueEngine
from bot.opportunity.missed import MissedOpportunityTracker
from bot.opportunity.models import OpportunityDecision, ScoredOpportunity
from bot.opportunity.portfolio_gate import PortfolioExposureGate, correlation_group_for_symbol
from bot.opportunity.ranker import OpportunityRanker
from bot.paper.markout import MarkoutTracker
from bot.paper.tracker import PerformanceTracker


def _settings(**overrides: object) -> Settings:
    base = {
        "execution_mode": "paper",
        "profitability_min_net_profit_usd": 0.001,
        "profitability_min_net_return": 0.0001,
    }
    base.update(overrides)
    return Settings(**base)


def _opp(**meta: object) -> TradeOpportunity:
    return TradeOpportunity(
        id=uuid4(),
        strategy_name="maker_inventory",
        symbol="ADAEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("100"),
        entry_price=Decimal("0.50"),
        expected_exit_price=Decimal("0.505"),
        entry_fee_role=FeeRole.MAKER,
        exit_fee_role=FeeRole.MAKER,
        metadata={
            "post_only": True,
            "buy_exchange": "bitvavo",
            "sell_exchange": "bitvavo",
            **{k: str(v) if not isinstance(v, (str, bool, int, float)) else v for k, v in meta.items()},
        },
    )


def _prof(oid, net: str = "0.40") -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=oid,
        gross_profit_usd=Decimal("1.00"),
        fees_usd=Decimal("0.40"),
        slippage_usd=Decimal("0"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.20"),
        net_profit_usd=Decimal(net),
        net_return=Decimal("0.008"),
        is_profitable=True,
        trade_allowed=True,
    )


def test_economics_include_capital_velocity() -> None:
    opp = _opp()
    eco = build_fill_economics(opp, _prof(opp.id), quote_max_age_ms=4000)
    assert eco.expected_net_eur > 0
    assert eco.capital_required_eur == Decimal("50")
    assert eco.expected_capital_time == Decimal("4")
    assert eco.expected_net_eur_per_capital_second > 0
    assert eco.expected_fee_eur == Decimal("0.40")


def test_inventory_relief_cannot_rescue_losing_trade() -> None:
    opp = _opp(inventory_skew_score="50000")
    eco = build_fill_economics(opp, _prof(opp.id, net="-0.10"))
    assert eco.inventory_relief_eur == 0
    assert eco.expected_net_eur <= 0


def test_inventory_relief_caps_at_half_of_positive_net() -> None:
    opp = _opp(inventory_skew_score="1000000")
    eco = build_fill_economics(opp, _prof(opp.id, net="0.40"))
    assert eco.inventory_relief_eur > 0
    assert eco.inventory_relief_eur <= Decimal("0.20")


def test_calibrator_shrinks_toward_prior_on_small_samples() -> None:
    cal = EvCalibrator(prior_strength=40, min_samples=20, prior_capture=Decimal("1"))
    opp = _opp()
    key = calibration_key(opp)
    for _ in range(5):
        cal.observe(
            key=key,
            route=route_key(opp),
            strategy=opp.strategy_name,
            expected_net=Decimal("1"),
            realized_net=Decimal("-1"),
        )
    ratio = cal.capture_ratio(key=key, route=route_key(opp), strategy=opp.strategy_name)
    # 5 samples vs k=40 → heavily shrunk toward 1.0, not raw -1.
    assert ratio > Decimal("0.5")
    assert ratio < Decimal("1")
    assert not cal.hard_gate_negative(opp)


def test_calibrator_hard_gate_after_min_samples() -> None:
    cal = EvCalibrator(prior_strength=10, min_samples=8, prior_capture=Decimal("1"))
    opp = _opp()
    key = calibration_key(opp)
    for _ in range(20):
        cal.observe(
            key=key,
            route=route_key(opp),
            strategy=opp.strategy_name,
            expected_net=Decimal("1"),
            realized_net=Decimal("-2"),
        )
    assert cal.hard_gate_negative(opp)
    calibrated = cal.calibrate(Decimal("1.00"), opp)
    assert calibrated < Decimal("0.5")


def test_ranker_prefers_calibrated_ev_then_velocity() -> None:
    settings = _settings()
    ranker = OpportunityRanker(settings)

    def scored(*, cal: str, vel: str, raw: str) -> ScoredOpportunity:
        oid = uuid4()
        opp = TradeOpportunity(
            id=oid,
            strategy_name="maker_inventory",
            symbol="BTCEUR",
            side=OpportunitySide.BUY,
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
        )
        item = ScoredOpportunity(
            opportunity=opp,
            profitability=_prof(oid),
            expected_value=Decimal(raw),
            calibrated_expected_value=Decimal(cal),
            expected_net_eur_per_capital_second=Decimal(vel),
            liquidity_score=1.0,
            execution_quality=1.0,
            regime_weight=Decimal("1"),
            risk_reward=Decimal("1"),
        )
        item.score = OpportunityRanker.compute_score(item)
        return item

    high_cal = scored(cal="1.0", vel="0.001", raw="1.0")
    low_cal_fast = scored(cal="0.2", vel="9.0", raw="2.0")
    ranked = ranker.rank([low_cal_fast, high_cal])
    assert ranked[0].calibrated_expected_value == Decimal("1.0")


def test_markout_tracks_60s_and_venue_buckets() -> None:
    tracker = MarkoutTracker(window=50)
    tracker.record_fill(
        fill_id="1",
        opportunity_id=None,
        symbol="ADAEUR",
        side="buy",
        fill_price=Decimal("1"),
        mid=Decimal("1"),
        venue="bitvavo",
    )
    tracker._pending[0].filled_at_ms -= 61000  # type: ignore[index]
    tracker.update({"ADAEUR": Decimal("1.02")})
    snap = tracker.snapshot()
    assert snap["avg_adverse_bps_60s"] != "0"
    assert snap["samples"] == 1
    suggested = tracker.suggested_adverse_bps(
        floor=Decimal("2"),
        ceiling=Decimal("30"),
        venue="bitvavo",
        symbol="ADAEUR",
        side="buy",
    )
    assert suggested >= Decimal("2")
    assert tracker.empirical_win_rate(min_samples=20) is None
    assert tracker.empirical_win_rate(min_samples=1) == 0.0


def test_ev_engine_ignores_markout_win_rate_until_min_samples() -> None:
    settings = _settings()
    ev = ExpectedValueEngine(
        settings,
        markout_win_rate=0.99,
        markout_samples=3,
        min_markout_samples=20,
    )
    opp = TradeOpportunity(
        strategy_name="test",
        symbol="BTCEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        expected_exit_price=Decimal("101"),
        confidence=0.0,
    )
    from bot.profitability.engine import DefaultProfitabilityEngine
    import asyncio

    async def _run() -> None:
        prof = await DefaultProfitabilityEngine(settings).evaluate(opp)
        data = ev.enrich(opp, prof)
        # Must not jump to 0.99 with only 3 samples.
        assert float(data["probability_profit"]) < 0.9

    asyncio.run(_run())


def test_correlation_group_matches_registry() -> None:
    assert correlation_group_for_symbol("BTCEUR") == "crypto_btc_beta"
    assert correlation_group_for_symbol("ADAEUR") == "crypto_alt"
    settings = _settings(global_max_correlation_exposure_pct=40.0)
    gate = PortfolioExposureGate(settings)
    from bot.core.enums import OpportunitySide
    from bot.core.models import PortfolioSnapshot, Position

    portfolio = PortfolioSnapshot(
        equity_usd=Decimal("1000"),
        positions=[
            Position(
                symbol="BTCEUR",
                quantity=Decimal("1"),
                average_entry_price=Decimal("400"),
                side=OpportunitySide.BUY,
            )
        ],
    )
    gate.sync_from_portfolio(portfolio)
    assert "crypto_btc_beta" in gate.snapshot()["correlation"]


def test_missed_tracker_records_first_gate() -> None:
    tracker = MissedOpportunityTracker()
    oid = uuid4()
    opp = TradeOpportunity(
        id=oid,
        strategy_name="maker_inventory",
        symbol="ADAEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("1"),
        metadata={"buy_exchange": "okx", "sell_exchange": "okx"},
    )
    scored = ScoredOpportunity(
        opportunity=opp,
        profitability=_prof(oid, net="0.50"),
        expected_value=Decimal("0.4"),
        calibrated_expected_value=Decimal("0.3"),
        expected_net_eur=Decimal("0.50"),
        capital_required=Decimal("10"),
        first_limiting_gate="portfolio",
        all_gates=["portfolio"],
    )
    decision = OpportunityDecision.from_scored(
        scored,
        action=OpportunityDecisionAction.REJECT,
        reason="Venue okx exposure 55% > 50%",
        stage="portfolio",
    )
    tracker.record_reject(scored, decision, first_gate="portfolio", all_gates=["portfolio"])
    why = tracker.why_not_trade()
    assert why["top_rejection_reasons"][0]["reason"] == "portfolio"
    assert why["top_rejection_reasons"][0]["count"] == 1


def test_tracker_snapshot_exposes_net_per_fill() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("1000"))
    oid = uuid4()
    opp = TradeOpportunity(
        id=oid,
        strategy_name="maker_inventory",
        symbol="ADAEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("1"),
        metadata={"buy_exchange": "okx", "sell_exchange": "bitvavo", "net_profit_eur": "1.5"},
    )
    tracked = tracker.record_detected(opp, _prof(oid, net="1.50"))
    tracked.realized_net_profit = Decimal("0.75")
    tracker._register_trade(tracked, Decimal("0.75"))  # noqa: SLF001
    snap = tracker.snapshot()
    assert snap.trade_count == 1
    assert snap.net_eur_per_fill == Decimal("0.75")
    assert snap.ev_capture == Decimal("0.5")
    rows = tracker.calibration_observations()
    assert rows[0]["route"] == "okx->bitvavo"


def test_limit_without_book_is_not_marketable() -> None:
    from bot.core.enums import OrderSide, OrderType
    from bot.execution.paper_executor import PaperExecutor
    from bot.portfolio.models import Order
    from bot.portfolio.portfolio import PaperPortfolio

    settings = _settings()
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("200"))
    executor = PaperExecutor(settings, portfolio=portfolio)
    order = Order(
        strategy="test",
        symbol="BTCEUR",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        requested_quantity=Decimal("0.01"),
        requested_price=Decimal("100"),
    )
    assert executor._limit_is_marketable(order, None) is False  # noqa: SLF001
