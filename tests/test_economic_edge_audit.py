"""Economic edge audit: waterfall identity, conditional EV, early stop, ownership."""

from decimal import Decimal
from uuid import uuid4

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.opportunity.calibration import EvCalibrator, calibration_key, route_key
from bot.opportunity.cost_ownership import components_used_in, ownership_table
from bot.opportunity.edge_decomposition import edge_decomposition
from bot.opportunity.ev_engine import ExpectedValueEngine
from bot.opportunity.toxicity import classify_markout_bps, toxicity_report
from bot.opportunity.waterfall import (
    assert_no_double_count,
    decompose_trade_row,
    expected_waterfall,
    realized_waterfall,
)


def _settings(**overrides: object) -> Settings:
    base = {
        "execution_mode": "paper",
        "paper_maker_trade_through_fill_pct": 1.0,
        "paper_maker_queue_fill_pct": 0.0,
        "profitability_execution_buffer_bps": 5.0,
    }
    base.update(overrides)
    return Settings(**base)


def _maker_opp(**meta: object) -> TradeOpportunity:
    return TradeOpportunity(
        id=uuid4(),
        strategy_name="maker_inventory",
        symbol="XRPEUR",
        side=OpportunitySide.BUY,
        quantity=Decimal("1000"),
        entry_price=Decimal("2"),
        expected_exit_price=Decimal("2.01"),
        entry_fee_role=FeeRole.MAKER,
        exit_fee_role=FeeRole.MAKER,
        metadata={
            "post_only": True,
            "buy_exchange": "bitvavo",
            "sell_exchange": "bitvavo",
            "adverse_bps": "4",
            **{k: str(v) if not isinstance(v, (str, bool, int, float)) else v for k, v in meta.items()},
        },
    )


def _prof(oid, net: str = "1.00", buffer: str = "1.00") -> ProfitabilityResult:
    return ProfitabilityResult(
        opportunity_id=oid,
        gross_profit_usd=Decimal("3.00"),
        fees_usd=Decimal("1.00"),
        slippage_usd=Decimal("0"),
        funding_usd=Decimal("0"),
        execution_buffer_usd=Decimal(buffer),
        net_profit_usd=Decimal(net),
        net_return=Decimal("0.0005"),
        is_profitable=True,
        trade_allowed=True,
    )


def test_waterfall_realized_identity_holds() -> None:
    wf = realized_waterfall(
        gross=Decimal("3"),
        buy_fee=Decimal("1"),
        sell_fee=Decimal("1"),
        slippage=Decimal("0.3"),
        realized_net=Decimal("-2"),
    )
    assert "identity_ok" in wf.notes
    assert wf.execution_buffer == 0
    assert wf.recomputed_net(include_buffer=False) == Decimal("-2")
    # adverse = 3 - 2.3 - (-2) = 2.7
    assert wf.adverse_selection == Decimal("2.7")


def test_expected_buffer_not_in_realized_double_count() -> None:
    exp = expected_waterfall(
        gross=Decimal("3"),
        buy_fee=Decimal("1"),
        sell_fee=Decimal("0.5"),
        slippage=Decimal("0"),
        execution_buffer=Decimal("0.5"),
        extra_adverse=Decimal("0"),
        net=Decimal("1"),
    )
    real = realized_waterfall(
        gross=Decimal("3"),
        buy_fee=Decimal("1"),
        sell_fee=Decimal("0.5"),
        slippage=Decimal("0.2"),
        realized_net=Decimal("-1"),
    )
    assert assert_no_double_count(exp, real) == []


def test_decompose_trade_row_separates_ev_gap_from_adverse() -> None:
    row = {
        "expected_gross": "3.0",
        "fees": "1.0",
        "slippage": "0.0",
        "expected_adverse": "0.5",
        "expected_inventory": "0",
        "expected_net_profit": "1.5",
        "realized_fees": "1.0",
        "realized_slippage": "0.3",
        "realized_net_profit": "-2.0",
    }
    parts = decompose_trade_row(row)
    # Adverse is residual closing identity on opportunity gross, not expected-realized.
    assert Decimal(parts["realized"]["adverse_selection"]) == Decimal("3.7")
    assert Decimal(parts["ev_gap"]) == Decimal("-3.5")


def test_conditional_ev_charges_extra_markout_beyond_buffer() -> None:
    settings = _settings()
    ev = ExpectedValueEngine(settings)
    opp = _maker_opp()
    # NET already includes ~5 bps buffer; conditional markout 20 bps ⇒ extra 15 bps.
    out = ev.enrich(
        opp,
        _prof(opp.id, net="1.00", buffer="1.00"),
        conditional_adverse_bps=Decimal("20"),
    )
    assert out["fill_conditioned"] is True
    assert out["p_fill"] == Decimal("1.0")
    # notional=2000; extra 15 bps = 3.0 → e_net = 1 - 3 = -2
    assert out["e_net_given_fill"] == Decimal("-2.00")
    assert out["expected_value"] < 0


def test_unconditional_p_fill_times_net_is_invalid_when_through_only() -> None:
    """Document the bug: p_fill×NET overstates EV under trade-through fills."""
    settings = _settings()
    ev = ExpectedValueEngine(settings)
    opp = _maker_opp()
    naive = Decimal("1.0") * Decimal("1.00")  # p_fill × NET
    out = ev.enrich(
        opp,
        _prof(opp.id, net="1.00"),
        conditional_adverse_bps=Decimal("25"),
    )
    assert out["expected_value"] < naive


def test_early_route_stop_fires_before_shrunk_gate() -> None:
    cal = EvCalibrator(
        prior_strength=40,
        min_samples=20,
        early_stop_samples=8,
        early_stop_capture=Decimal("-0.25"),
        early_stop_min_loss_eur=Decimal("5"),
    )
    opp = _maker_opp()
    key = calibration_key(opp)
    route = route_key(opp)
    for _ in range(8):
        cal.observe(
            key=key,
            route=route,
            strategy=opp.strategy_name,
            expected_net=Decimal("1"),
            realized_net=Decimal("-2"),
        )
    # Shrunk capture still positive (~0.6), but early stop must fire.
    assert cal.capture_ratio(key=key, route=route, strategy=opp.strategy_name) > 0
    assert cal.hard_gate_negative(opp)


def test_toxicity_buckets_and_corr() -> None:
    samples = [
        {"venue": "bitvavo", "side": "buy", "p_fill": "1.0", "markout_bps_5s": "30"},
        {"venue": "bitvavo", "side": "buy", "p_fill": "1.0", "markout_bps_5s": "12"},
        {"venue": "bitvavo", "side": "sell", "p_fill": "0.2", "markout_bps_5s": "-6"},
        {"venue": "okx", "side": "buy", "p_fill": "0.2", "markout_bps_5s": "1"},
    ]
    assert classify_markout_bps(Decimal("30")) == "very_toxic"
    assert classify_markout_bps(Decimal("-6")) == "very_favorable"
    report = toxicity_report(samples)
    assert report["buckets"]["very_toxic"]["n"] == 1
    assert report["sample_count"] == 4


def test_ownership_table_buffer_not_in_realized() -> None:
    rows = {r["component"]: r for r in ownership_table()}
    assert rows["execution_buffer"]["used_in_realized"] is False
    assert rows["execution_buffer"]["used_in_net"] is True
    assert "gross_opportunity" in components_used_in("net")


def test_edge_decomposition_sums() -> None:
    trades = [
        {
            "strategy": "maker_inventory",
            "symbol": "XRPEUR",
            "buy_exchange": "bitvavo",
            "sell_exchange": "bitvavo",
            "expected_gross": "3",
            "fees": "1",
            "slippage": "0",
            "expected_adverse": "0.5",
            "expected_inventory": "0",
            "expected_net_profit": "1.5",
            "realized_fees": "1",
            "realized_slippage": "0.3",
            "realized_net_profit": "-2",
        }
    ]
    dec = edge_decomposition(trades)
    assert dec["trade_count"] == 1
    assert dec["by_route"]["bitvavo->bitvavo"]["n"] == 1
    assert Decimal(dec["overall"]["realized_net"]) == Decimal("-2")


def test_inventory_relief_still_cannot_rescue_in_conditional_ev() -> None:
    settings = _settings()
    ev = ExpectedValueEngine(settings)
    opp = _maker_opp(inventory_skew_score="999999")
    out = ev.enrich(
        opp,
        _prof(opp.id, net="-0.50"),
        inventory_relief=Decimal("5"),
        conditional_adverse_bps=Decimal("10"),
    )
    assert out["e_net_given_fill"] <= 0
