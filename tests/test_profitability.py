"""Extensive tests for the profitability engine and submodules."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel, TradingFee
from bot.core.models import MarketSnapshot, TradeOpportunity
from bot.profitability.engine import DefaultProfitabilityEngine
from bot.profitability.fee_calculator import FeeCalculator
from bot.profitability.net_profit import NetProfitCalculator
from bot.profitability.slippage import SlippageModel


def _settings(**overrides: object) -> Settings:
    base = dict(
        app_env="development",
        execution_mode="paper",
        profitability_fee_rate=0.001,
        profitability_maker_fee_rate=0.0008,
        profitability_taker_fee_rate=0.001,
        profitability_slippage_bps=5.0,
        profitability_market_impact_factor=1.0,
        profitability_thin_book_penalty_bps=25.0,
        profitability_funding_rate=0.0001,
        profitability_apply_funding=True,
        profitability_execution_buffer_bps=10.0,
        profitability_min_net_profit_usd=1.0,
        profitability_min_net_return=0.001,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _opp(
    *,
    side: OpportunitySide = OpportunitySide.BUY,
    qty: str = "1",
    entry: str = "100",
    exit: str | None = "110",
    funding_periods: str = "1",
    entry_role: FeeRole = FeeRole.TAKER,
    exit_role: FeeRole = FeeRole.TAKER,
    funding_rate: str | None = "0.0001",
    order_book: OrderBook | None = None,
) -> TradeOpportunity:
    market = MarketSnapshot(
        symbol="BTCUSDT",
        bid=Decimal("99.9"),
        ask=Decimal("100.1"),
        last=Decimal("100"),
        funding_rate=Decimal(funding_rate) if funding_rate is not None else None,
        order_book=order_book,
    )
    return TradeOpportunity(
        strategy_name="test",
        symbol="BTCUSDT",
        side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        expected_exit_price=Decimal(exit) if exit is not None else None,
        entry_fee_role=entry_role,
        exit_fee_role=exit_role,
        funding_periods=Decimal(funding_periods),
        market=market,
    )


def _book(
    *,
    ask_qty: str = "10",
    bid_qty: str = "10",
    ask_px: str = "100.1",
    bid_px: str = "99.9",
) -> OrderBook:
    return OrderBook(
        symbol="BTCUSDT",
        asks=[OrderBookLevel(price=Decimal(ask_px), amount=Decimal(ask_qty))],
        bids=[OrderBookLevel(price=Decimal(bid_px), amount=Decimal(bid_qty))],
    )


# ---------------------------------------------------------------------------
# Fee calculator
# ---------------------------------------------------------------------------


def test_fee_calculator_uses_maker_and_taker_rates() -> None:
    calc = FeeCalculator(_settings())
    assert calc.maker_rate == Decimal("0.0008")
    assert calc.taker_rate == Decimal("0.001")
    assert calc.rate_for(FeeRole.MAKER) == Decimal("0.0008")
    assert calc.rate_for(FeeRole.TAKER) == Decimal("0.001")


def test_fee_calculator_buy_opportunity_buy_then_sell() -> None:
    calc = FeeCalculator(_settings())
    fees = calc.calculate(_opp(entry="100", exit="110"), exit_price=Decimal("110"))
    assert fees.buy_fee == Decimal("100") * Decimal("0.001")
    assert fees.sell_fee == Decimal("110") * Decimal("0.001")
    assert fees.total_fees == fees.buy_fee + fees.sell_fee


def test_fee_calculator_maker_entry_reduces_buy_fee() -> None:
    calc = FeeCalculator(_settings())
    taker = calc.calculate(_opp(entry_role=FeeRole.TAKER), exit_price=Decimal("110"))
    maker = calc.calculate(_opp(entry_role=FeeRole.MAKER), exit_price=Decimal("110"))
    assert maker.buy_fee < taker.buy_fee
    assert maker.buy_fee == Decimal("100") * Decimal("0.0008")


def test_fee_calculator_short_maps_sell_on_entry() -> None:
    calc = FeeCalculator(_settings())
    fees = calc.calculate(
        _opp(side=OpportunitySide.SHORT, entry="100", exit="90"),
        exit_price=Decimal("90"),
    )
    # Short: sell entry notional 100, buy cover notional 90
    assert fees.sell_fee == Decimal("100") * Decimal("0.001")
    assert fees.buy_fee == Decimal("90") * Decimal("0.001")


def test_fee_calculator_trading_fee_override() -> None:
    calc = FeeCalculator(
        _settings(),
        trading_fee=TradingFee(symbol="BTCUSDT", maker=Decimal("0.0001"), taker=Decimal("0.0002")),
    )
    fees = calc.calculate(_opp(entry_role=FeeRole.MAKER), exit_price=Decimal("110"))
    assert fees.buy_fee_rate == Decimal("0.0001")


def test_fee_calculator_falls_back_to_flat_fee_rate() -> None:
    calc = FeeCalculator(
        _settings(profitability_maker_fee_rate=None, profitability_taker_fee_rate=None)
    )
    assert calc.maker_rate == Decimal("0.001")
    assert calc.taker_rate == Decimal("0.001")


# ---------------------------------------------------------------------------
# Slippage / market impact
# ---------------------------------------------------------------------------


def test_slippage_without_book_uses_base_bps_only() -> None:
    model = SlippageModel(_settings())
    opp = _opp(entry="100", exit="110", order_book=None)
    opp.market = MarketSnapshot(
        symbol="BTCUSDT",
        bid=Decimal("99.9"),
        ask=Decimal("100.1"),
        last=Decimal("100"),
        order_book=None,
    )
    est = model.estimate(opp, exit_price=Decimal("110"))
    expected_base = (Decimal("100") + Decimal("110")) * Decimal("5") / Decimal("10000")
    assert est.base_slippage == expected_base
    assert est.market_impact == Decimal("0")
    assert est.thin_book_penalty == Decimal("0")
    assert est.total_slippage == expected_base


def test_slippage_includes_market_impact_from_depth() -> None:
    model = SlippageModel(_settings())
    # Ask far from entry reference → measurable impact on entry leg.
    book = OrderBook(
        symbol="BTCUSDT",
        asks=[
            OrderBookLevel(price=Decimal("101"), amount=Decimal("0.5")),
            OrderBookLevel(price=Decimal("102"), amount=Decimal("0.5")),
        ],
        bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("10"))],
    )
    opp = _opp(entry="100", exit="110", order_book=book)
    est = model.estimate(opp, exit_price=Decimal("110"), order_book=book)
    assert est.market_impact > 0
    assert est.total_slippage > est.base_slippage
    assert est.vwap is not None
    assert est.levels_consumed >= 1


def test_slippage_thin_book_penalty_when_depth_insufficient() -> None:
    model = SlippageModel(_settings())
    book = _book(ask_qty="0.1", bid_qty="0.1")  # qty needed = 1
    opp = _opp(qty="1", entry="100", exit="110", order_book=book)
    est = model.estimate(opp, exit_price=Decimal("110"), order_book=book)
    assert est.thin_book_penalty > 0
    assert est.depth_consumed_ratio == Decimal("1")


def test_slippage_empty_book_applies_full_penalty() -> None:
    model = SlippageModel(_settings())
    book = OrderBook(symbol="BTCUSDT", asks=[], bids=[])
    opp = _opp(entry="100", exit="110")
    est = model.estimate(opp, exit_price=Decimal("110"), order_book=book)
    assert est.thin_book_penalty > 0
    assert est.vwap is None


def test_market_impact_factor_scales_impact() -> None:
    low = SlippageModel(_settings(profitability_market_impact_factor=0.0))
    high = SlippageModel(_settings(profitability_market_impact_factor=2.0))
    book = OrderBook(
        symbol="BTCUSDT",
        asks=[OrderBookLevel(price=Decimal("105"), amount=Decimal("1"))],
        bids=[OrderBookLevel(price=Decimal("95"), amount=Decimal("1"))],
    )
    opp = _opp(entry="100", exit="110")
    low_est = low.estimate(opp, exit_price=Decimal("110"), order_book=book)
    high_est = high.estimate(opp, exit_price=Decimal("110"), order_book=book)
    assert low_est.market_impact == Decimal("0")
    assert high_est.market_impact > 0


# ---------------------------------------------------------------------------
# Net profit calculator
# ---------------------------------------------------------------------------


def test_net_equals_gross_minus_all_cost_components() -> None:
    calc = NetProfitCalculator(_settings())
    est = calc.estimate(_opp(entry="100", exit="110"))
    expected = (
        est.gross_profit
        - est.buy_fee
        - est.sell_fee
        - est.slippage
        - est.funding_cost
        - est.execution_buffer
    )
    assert est.net_profit == expected
    assert est.net_return == est.net_profit / Decimal("100")


def test_profitable_opportunity_is_trade_allowed() -> None:
    # Large edge clears min absolute + min return after costs.
    calc = NetProfitCalculator(_settings())
    est = calc.estimate(_opp(entry="100", exit="120"))
    assert est.gross_profit == Decimal("20")
    assert est.net_profit > Decimal("1")
    assert est.trade_allowed is True
    assert est.disallow_reasons == []


def test_gross_positive_but_net_negative_not_allowed() -> None:
    # Tiny gross edge wiped out by fees/slippage/buffer.
    calc = NetProfitCalculator(
        _settings(
            profitability_min_net_profit_usd=0.0,
            profitability_min_net_return=0.0,
            profitability_fee_rate=0.01,
            profitability_maker_fee_rate=0.01,
            profitability_taker_fee_rate=0.01,
            profitability_slippage_bps=50,
            profitability_execution_buffer_bps=50,
        )
    )
    est = calc.estimate(_opp(entry="100", exit="100.10"))
    assert est.gross_profit > 0
    assert est.net_profit < 0
    assert est.trade_allowed is False
    assert any("gross" in r.lower() for r in est.disallow_reasons)


def test_never_allows_on_gross_spread_alone() -> None:
    calc = NetProfitCalculator(
        _settings(
            profitability_min_net_profit_usd=0,
            profitability_min_net_return=0,
        )
    )
    # Flat after costs still has zero net even if we lie about "spread" via gross.
    est = calc.estimate(_opp(entry="100", exit="100"))
    assert est.gross_profit == Decimal("0")
    assert est.trade_allowed is False


def test_minimum_absolute_profit_blocks_small_net() -> None:
    calc = NetProfitCalculator(
        _settings(
            profitability_min_net_profit_usd=50.0,
            profitability_min_net_return=0.0,
            profitability_fee_rate=0,
            profitability_maker_fee_rate=0,
            profitability_taker_fee_rate=0,
            profitability_slippage_bps=0,
            profitability_execution_buffer_bps=0,
            profitability_apply_funding=False,
        )
    )
    est = calc.estimate(_opp(entry="100", exit="110"))  # net == 10
    assert est.net_profit == Decimal("10")
    assert est.trade_allowed is False
    assert any("absolute" in r.lower() for r in est.disallow_reasons)


def test_minimum_percentage_return_blocks_low_return() -> None:
    calc = NetProfitCalculator(
        _settings(
            profitability_min_net_profit_usd=0.0,
            profitability_min_net_return=0.50,  # 50%
            profitability_fee_rate=0,
            profitability_maker_fee_rate=0,
            profitability_taker_fee_rate=0,
            profitability_slippage_bps=0,
            profitability_execution_buffer_bps=0,
            profitability_apply_funding=False,
        )
    )
    est = calc.estimate(_opp(entry="100", exit="110"))  # 10% return
    assert est.net_return == Decimal("0.1")
    assert est.trade_allowed is False
    assert any("return" in r.lower() for r in est.disallow_reasons)


def test_funding_cost_applied_for_longs() -> None:
    calc = NetProfitCalculator(_settings(profitability_apply_funding=True))
    with_funding = calc.estimate(_opp(side=OpportunitySide.BUY, funding_periods="2"))
    no_funding = NetProfitCalculator(
        _settings(profitability_apply_funding=False)
    ).estimate(_opp(side=OpportunitySide.BUY, funding_periods="2"))
    assert with_funding.funding_cost == Decimal("100") * Decimal("0.0001") * Decimal("2")
    assert no_funding.funding_cost == Decimal("0")
    assert with_funding.net_profit < no_funding.net_profit


def test_funding_credit_for_shorts_when_rate_positive() -> None:
    calc = NetProfitCalculator(_settings())
    est = calc.estimate(
        _opp(side=OpportunitySide.SHORT, entry="100", exit="90", funding_periods="1")
    )
    # Positive funding rate → shorts receive funding ⇒ negative cost.
    assert est.funding_cost < 0


def test_zero_funding_periods_skips_funding() -> None:
    calc = NetProfitCalculator(_settings())
    est = calc.estimate(_opp(funding_periods="0"))
    assert est.funding_cost == Decimal("0")


def test_missing_exit_price_assumes_flat_not_allowed() -> None:
    calc = NetProfitCalculator(_settings())
    est = calc.estimate(_opp(exit=None))
    assert est.gross_profit == Decimal("0")
    assert est.assumptions["exit_price"] == "100"
    assert est.trade_allowed is False


def test_sell_side_gross_profit() -> None:
    calc = NetProfitCalculator(
        _settings(
            profitability_min_net_profit_usd=0,
            profitability_min_net_return=0,
            profitability_fee_rate=0,
            profitability_maker_fee_rate=0,
            profitability_taker_fee_rate=0,
            profitability_slippage_bps=0,
            profitability_execution_buffer_bps=0,
            profitability_apply_funding=False,
        )
    )
    est = calc.estimate(_opp(side=OpportunitySide.SELL, entry="100", exit="90"))
    assert est.gross_profit == Decimal("10")
    assert est.trade_allowed is True


def test_order_book_depth_reduces_net_vs_no_book() -> None:
    calc = NetProfitCalculator(_settings())
    deep = calc.estimate(_opp(entry="100", exit="115"))
    adverse_book = OrderBook(
        symbol="BTCUSDT",
        asks=[OrderBookLevel(price=Decimal("103"), amount=Decimal("1"))],
        bids=[OrderBookLevel(price=Decimal("97"), amount=Decimal("1"))],
    )
    with_impact = calc.estimate(
        _opp(entry="100", exit="115", order_book=adverse_book),
        order_book=adverse_book,
    )
    assert with_impact.slippage > deep.slippage
    assert with_impact.net_profit < deep.net_profit


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_maps_estimate_to_profitability_result() -> None:
    engine = DefaultProfitabilityEngine(_settings())
    result = await engine.evaluate(_opp(entry="100", exit="120"))
    assert result.estimate is not None
    assert result.buy_fee_usd == result.estimate.buy_fee
    assert result.sell_fee_usd == result.estimate.sell_fee
    assert result.fees_usd == result.buy_fee_usd + result.sell_fee_usd
    assert result.net_profit_usd == result.estimate.net_profit
    assert result.net_return == result.estimate.net_return
    assert result.trade_allowed == result.estimate.trade_allowed
    assert result.is_profitable == result.trade_allowed
    assert result.assumptions["gross_alone_never_allows_trade"] is True


@pytest.mark.asyncio
async def test_engine_rejects_positive_gross_spread_alone() -> None:
    engine = DefaultProfitabilityEngine(
        _settings(
            profitability_min_net_profit_usd=0,
            profitability_min_net_return=0,
            profitability_taker_fee_rate=0.02,
            profitability_maker_fee_rate=0.02,
            profitability_slippage_bps=100,
            profitability_execution_buffer_bps=100,
        )
    )
    result = await engine.evaluate(_opp(entry="100", exit="100.5"))
    assert result.gross_profit_usd > 0
    assert result.net_profit_usd < 0
    assert result.trade_allowed is False
    assert result.is_profitable is False


@pytest.mark.asyncio
async def test_engine_profitable_case(settings: Settings) -> None:
    engine = DefaultProfitabilityEngine(settings)
    result = await engine.evaluate(_opp(entry="100", exit="120"))
    assert result.trade_allowed is True
    assert result.is_profitable is True


@pytest.mark.asyncio
async def test_flat_exit_is_not_profitable_after_costs(settings: Settings) -> None:
    engine = DefaultProfitabilityEngine(settings)
    result = await engine.evaluate(_opp(entry="100", exit="100"))
    assert result.gross_profit_usd == Decimal("0")
    assert result.net_profit_usd < 0
    assert result.is_profitable is False
    assert result.trade_allowed is False


@pytest.mark.asyncio
async def test_net_profit_subtracts_all_costs(settings: Settings, opportunity: TradeOpportunity) -> None:
    engine = DefaultProfitabilityEngine(settings)
    result = await engine.evaluate(opportunity)
    assert result.opportunity_id == opportunity.id
    assert result.buy_fee_usd > 0
    assert result.sell_fee_usd > 0
    assert result.fees_usd == result.buy_fee_usd + result.sell_fee_usd
    assert result.slippage_usd > 0
    assert result.funding_usd > 0
    assert result.execution_buffer_usd > 0
    expected_net = (
        result.gross_profit_usd
        - result.fees_usd
        - result.slippage_usd
        - result.funding_usd
        - result.execution_buffer_usd
    )
    assert result.net_profit_usd == expected_net
    # is_profitable follows trade_allowed (thresholds), not mere net > 0
    assert result.is_profitable == result.trade_allowed


@pytest.mark.asyncio
async def test_edge_case_zero_quantity_rejected_by_model() -> None:
    with pytest.raises(Exception):
        TradeOpportunity(
            strategy_name="x",
            symbol="BTCUSDT",
            side=OpportunitySide.BUY,
            quantity=Decimal("0"),
            entry_price=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_edge_case_very_small_notional_fails_min_return() -> None:
    engine = DefaultProfitabilityEngine(
        _settings(
            profitability_min_net_profit_usd=0.0,
            profitability_min_net_return=0.01,
            profitability_fee_rate=0,
            profitability_maker_fee_rate=0,
            profitability_taker_fee_rate=0,
            profitability_slippage_bps=0,
            profitability_execution_buffer_bps=0,
            profitability_apply_funding=False,
        )
    )
    # 0.5% gross edge < 1% min return
    result = await engine.evaluate(_opp(entry="100", exit="100.5"))
    assert result.net_return == Decimal("0.005")
    assert result.trade_allowed is False
