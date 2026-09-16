"""Route state, fill types, quote economics, early-stop observability."""

from decimal import Decimal
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import FeeRole, FillType, OpportunitySide, RouteState
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.opportunity.calibration import EvCalibrator, calibration_key, route_key
from bot.opportunity.quote_economics import (
    RouteBelief,
    combine_execution_economics,
    quote_age_bucket,
    quote_economics_from_profitability,
)
from bot.opportunity.waterfall import prediction_error
from bot.paper.tracker import PerformanceTracker


def _settings(**overrides: object) -> Settings:
    base = {"execution_mode": "paper"}
    base.update(overrides)
    return Settings(**base)


def _opp() -> TradeOpportunity:
    return TradeOpportunity(
        id=uuid4(),
        strategy_name="maker_inventory",
        symbol="XRPEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("100"),
        entry_price=Decimal("2"),
        expected_exit_price=Decimal("2.01"),
        entry_fee_role=FeeRole.MAKER,
        exit_fee_role=FeeRole.MAKER,
        metadata={
            "post_only": True,
            "buy_exchange": "bitvavo",
            "sell_exchange": "bitvavo",
        },
    )


def _prof(oid) -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=oid,
        gross_profit_usd=Decimal("1.00"),
        fees_usd=Decimal("0.40"),
        slippage_usd=Decimal("0"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal("0.20"),
        net_profit_usd=Decimal("0.40"),
        net_return=Decimal("0.002"),
        is_profitable=True,
        trade_allowed=True,
    )


def test_quote_age_buckets() -> None:
    assert quote_age_bucket(100) == "0_250ms"
    assert quote_age_bucket(800) == "250ms_1s"
    assert quote_age_bucket(2000) == "1s_4s"
    assert quote_age_bucket(8000) == "4s_10s"
    assert quote_age_bucket(30000) == "10s_60s"
    assert quote_age_bucket(90000) == "60s_plus"


def test_execution_economics_trade_through_conditions_net() -> None:
    opp = _opp()
    quote = quote_economics_from_profitability(opp, _prof(opp.id))
    belief = RouteBelief(
        p_fill=Decimal("1"),
        expected_adverse_bps_if_fill=Decimal("20"),
        fill_type=FillType.TRADE_THROUGH,
    )
    # already buffered ~5 bps on notional 200 → buffer ≈ 0.20 matches profitability
    exe = combine_execution_economics(
        quote,
        belief,
        already_buffered_bps=Decimal("5"),
        notional_eur=Decimal("200"),
    )
    assert exe.fill_conditioned is True
    # extra 15 bps on 200 = 0.30 → net_if_fill = 0.40 - 0.30 + relief0 = 0.10
    assert exe.net_if_fill_eur == Decimal("0.10")
    assert exe.ev_per_quote_eur == Decimal("0.10")


def test_route_state_early_stopped_overrides_positive_shrinkage() -> None:
    cal = EvCalibrator(
        prior_strength=40,
        min_samples=20,
        early_stop_samples=8,
        early_stop_capture=Decimal("-0.25"),
        early_stop_min_loss_eur=Decimal("5"),
    )
    opp = _opp()
    for _ in range(8):
        cal.observe(
            key=calibration_key(opp),
            route=route_key(opp),
            strategy=opp.strategy_name,
            expected_net=Decimal("1"),
            realized_net=Decimal("-2"),
        )
    status = cal.route_state(route_key(opp))
    assert status["state"] == RouteState.EARLY_STOPPED.value
    assert status["overrides_positive_shrinkage"] is True
    assert Decimal(status["shrunk_capture"]) > 0
    assert cal.hard_gate_negative(opp)


def test_prediction_error_decomposition() -> None:
    err = prediction_error(
        predicted_net_if_fill=Decimal("1.5"),
        realized_net=Decimal("-2"),
        predicted_gross=Decimal("3"),
        realized_gross_proxy=Decimal("-0.7"),
        predicted_fees=Decimal("1"),
        realized_fees=Decimal("1"),
        predicted_adverse=Decimal("0.5"),
        realized_adverse=Decimal("3.7"),
    )
    assert err["kind"] == "estimated"
    assert Decimal(err["total_error"]) == Decimal("-3.5")


def test_calibration_queue_drains_once() -> None:
    tracker = PerformanceTracker(starting_equity=Decimal("1000"))
    # Simulate register via private queue append path used by _register_trade
    tracker._calibration_queue.append(  # type: ignore[attr-defined]
        {
            "key": "a",
            "route": "bitvavo->bitvavo",
            "strategy": "maker_inventory",
            "expected_net": Decimal("1"),
            "realized_net": Decimal("-1"),
        }
    )
    rows = tracker.drain_calibration_observations()
    assert len(rows) == 1
    assert tracker.drain_calibration_observations() == []


def test_fill_type_enum_values() -> None:
    assert FillType.TRADE_THROUGH.value == "trade_through"
    assert FillType.QUEUE.value == "queue"
