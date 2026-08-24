"""Capital-velocity policy: inventory skew, dust floors, dump guard, holding time."""

from decimal import Decimal

from bot.paper.capital_policy import (
    HoldingTimeController,
    InventorySkewPolicy,
    NetProfitDustFilter,
    VolatilityDumpGuard,
)
from bot.portfolio.models import AssetBalance, PortfolioState, PositionState


def _state(
    *,
    equity_cash: str = "7000",
    alt_qty: str = "0",
    alt_mark: str = "1",
    quote: str = "EUR",
) -> PortfolioState:
    cash = Decimal(equity_cash)
    qty = Decimal(alt_qty)
    mark = Decimal(alt_mark)
    balances = {
        quote: AssetBalance(asset=quote, available=cash),
    }
    marks: dict[str, Decimal] = {}
    positions: dict[str, PositionState] = {}
    if qty > 0:
        balances["ATOM"] = AssetBalance(asset="ATOM", available=qty)
        marks["ATOMEUR"] = mark
        positions["ATOMEUR"] = PositionState(
            symbol="ATOMEUR",
            quantity=qty,
            average_entry_price=mark,
        )
    return PortfolioState(
        balances=balances,
        positions=positions,
        quote_asset=quote,
        mark_prices=marks,
    )


def test_inventory_skew_overweight_blocks_buys_and_improves_ask() -> None:
    # 40% alt → sell-only + tighter ask.
    state = _state(equity_cash="6000", alt_qty="4000", alt_mark="1")
    policy = InventorySkewPolicy(
        max_alt_pct=Decimal("30"),
        min_alt_pct=Decimal("10"),
        overweight_ask_improve_bps=Decimal("10"),
    )
    skew = policy.skew(state)
    assert skew.sell_only is True
    assert skew.allow_buy is False
    assert skew.mode == "overweight_sell_only"
    buy, sell = policy.apply_prices(
        buy_price=Decimal("1.00"),
        sell_price=Decimal("1.02"),
        skew=skew,
        best_bid=Decimal("1.00"),
        best_ask=Decimal("1.02"),
    )
    assert sell < Decimal("1.02")
    assert buy == Decimal("1.00")


def test_inventory_skew_underweight_demands_deeper_buy() -> None:
    state = _state(equity_cash="9500", alt_qty="500", alt_mark="1")
    policy = InventorySkewPolicy(
        max_alt_pct=Decimal("30"),
        min_alt_pct=Decimal("10"),
        underweight_buy_extra_bps=Decimal("20"),
    )
    skew = policy.skew(state)
    assert skew.allow_buy is True
    assert skew.buy_extra_edge_bps == Decimal("20")
    max_buy = policy.max_buy_vs_fair(Decimal("1.00"), skew)
    assert max_buy is not None
    assert max_buy < Decimal("1.00")


def test_dust_filter_blocks_stofjes_and_thin_net() -> None:
    filt = NetProfitDustFilter(
        min_net_profit_eur=Decimal("0.15"),
        min_net_return=Decimal("0.0025"),
        min_notional_eur=Decimal("10"),
    )
    assert filt.reject_reason(
        quantity=Decimal("1"),
        buy_price=Decimal("5"),
        net_profit_eur=Decimal("1"),
        net_return=Decimal("0.01"),
    )
    assert filt.reject_reason(
        quantity=Decimal("20"),
        buy_price=Decimal("1"),
        net_profit_eur=Decimal("0.05"),
        net_return=Decimal("0.01"),
    )
    assert filt.reject_reason(
        quantity=Decimal("20"),
        buy_price=Decimal("1"),
        net_profit_eur=Decimal("0.50"),
        net_return=Decimal("0.001"),
    )
    assert (
        filt.reject_reason(
            quantity=Decimal("20"),
            buy_price=Decimal("1"),
            net_profit_eur=Decimal("0.50"),
            net_return=Decimal("0.01"),
        )
        is None
    )


def test_inventory_skew_per_venue_independent() -> None:
    from bot.portfolio.venue_ledger import VenueLedger

    policy = InventorySkewPolicy(
        max_alt_pct=Decimal("30"),
        min_alt_pct=Decimal("10"),
    )
    ledger = VenueLedger(["bitvavo", "okx"], quote="EUR", starting_quote=Decimal("0"))
    ledger.replace_balances(
        "bitvavo",
        {
            "EUR": Decimal("800"),
            "APT": Decimal("280"),
            "SOL": Decimal("3"),
        },
    )
    ledger.replace_balances("okx", {"EUR": Decimal("1600"), "SOL": Decimal("1")})
    marks = {
        "APTEUR": Decimal("0.54"),
        "SOLEUR": Decimal("80"),
    }
    bv = policy.skew_venue(ledger, "bitvavo", mark_prices=marks)
    okx = policy.skew_venue(ledger, "okx", mark_prices=marks)
    assert bv.mode == "overweight_sell_only"
    assert bv.sell_only is True
    assert okx.mode == "underweight_selective_buy"
    assert okx.sell_only is False
    guard = VolatilityDumpGuard(
        move_pct=Decimal("1.5"),
        window_sec=300.0,
        cool_down_sec=120.0,
    )
    t0 = 1_000.0
    guard.observe("ATOMEUR", Decimal("100"), now=t0)
    guard.observe("ATOMEUR", Decimal("98"), now=t0 + 60)  # -2%
    assert guard.is_dump("ATOMEUR", now=t0 + 61) is True
    assert guard.is_dump("ATOMEUR", now=t0 + 200) is False


def test_holding_time_recycles_flat_or_losing() -> None:
    ctrl = HoldingTimeController(max_holding_sec=100.0)
    t0 = 5_000.0
    balances = {"ATOM": Decimal("10"), "EUR": Decimal("1000")}
    ctrl.note_balances(balances, now=t0)
    assert (
        ctrl.overdue(
            balances,
            mark_prices={"ATOMEUR": Decimal("1.00")},
            entry_prices={"ATOMEUR": Decimal("1.00")},
            now=t0 + 50,
        )
        == []
    )
    overdue = ctrl.overdue(
        balances,
        mark_prices={"ATOMEUR": Decimal("0.99")},
        entry_prices={"ATOMEUR": Decimal("1.00")},
        now=t0 + 150,
    )
    assert overdue == [("ATOM", Decimal("10"))]
    # Winners are kept.
    assert (
        ctrl.overdue(
            balances,
            mark_prices={"ATOMEUR": Decimal("1.05")},
            entry_prices={"ATOMEUR": Decimal("1.00")},
            now=t0 + 150,
        )
        == []
    )
