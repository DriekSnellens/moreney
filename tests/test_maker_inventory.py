"""Maker inventory strategy: quote bid/ask when NET edge clears maker fees."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import FeeRole, OrderStatus
from bot.core.venue_fees import set_fee_tier
from bot.strategies.arbitrage import top_of_book_snapshot
from bot.strategies.maker_inventory import MakerInventoryStrategy
from bot.core.exchange_types import OrderBook, OrderBookLevel


def _maker_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "execution_mode": "paper",
        "paper_maker_enabled": True,
        "paper_maker_min_profit_eur": 0.02,
        "paper_maker_min_net_return": 0.0001,
        "paper_maker_min_notional_eur": 0.5,
        "paper_maker_min_spread_bps": 2.0,
        "paper_maker_max_edge_bps": 200.0,
        "paper_maker_max_fee_bps": 40.0,
        "paper_maker_adverse_bps": 0.0,
        "paper_maker_fair_value": False,
        "paper_maker_venues": "okx,binance,bitvavo",
        "paper_maker_same_venue": True,
        "arbitrage_min_profit_pct": 0.0001,
        "arbitrage_min_liquidity_base": 0.01,
        "arbitrage_max_quantity": 1.0,
        "arbitrage_position_pct": 0.0,
        "arbitrage_opportunity_cooldown_ms": 0.0,
        "arbitrage_max_emits_per_cycle": 4,
        "arbitrage_max_latency_ms": 5000.0,
        "arbitrage_max_book_age_ms": 5000.0,
        "profitability_apply_funding": False,
        "paper_quote_asset": "EUR",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _book(bid: str, ask: str, qty: str = "2") -> OrderBook:
    return OrderBook(
        symbol="BTCEUR",
        bids=[OrderBookLevel(price=Decimal(bid), amount=Decimal(qty))],
        asks=[OrderBookLevel(price=Decimal(ask), amount=Decimal(qty))],
    )


def _retail_level1_atom(*, depth: str = "500") -> OrderBook:
    """Same-venue ATOM book whose *level-1* quote clears retail maker fees.

    Touch (~30 bps) is eaten by Binance 20 bps RT + buffers. Level 1 is ~68 bps,
    under the 80 bps stale cap used on Realistic.
    """
    qty = Decimal(depth)
    return OrderBook(
        symbol="ATOMEUR",
        bids=[
            OrderBookLevel(price=Decimal("1.330"), amount=Decimal("80")),
            OrderBookLevel(price=Decimal("1.326"), amount=qty),
        ],
        asks=[
            OrderBookLevel(price=Decimal("1.334"), amount=Decimal("80")),
            OrderBookLevel(price=Decimal("1.335"), amount=qty),
        ],
    )


@pytest.mark.asyncio
async def test_maker_emits_when_ask_minus_bid_clears_fees() -> None:
    strategy = MakerInventoryStrategy(_maker_settings())
    snaps = [
        top_of_book_snapshot(
            exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.20")
        ),
        top_of_book_snapshot(
            exchange="binance", symbol="BTCEUR", order_book=_book("100.80", "101.20")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps
    best = opps[0]
    assert best.strategy_name == "maker_inventory"
    assert best.entry_fee_role == FeeRole.MAKER
    assert best.exit_fee_role == FeeRole.MAKER
    assert best.metadata["post_only"] is True
    assert best.entry_price == Decimal("100")
    assert best.expected_exit_price == Decimal("101.20")
    assert Decimal(str(best.metadata["net_profit_eur"])) > 0


@pytest.mark.asyncio
async def test_maker_rejects_stale_wide_edge() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(paper_maker_max_edge_bps=30, paper_maker_same_venue=False)
    )
    snaps = [
        top_of_book_snapshot(
            exchange="binance", symbol="BTCEUR", order_book=_book("100", "100.05")
        ),
        top_of_book_snapshot(
            exchange="bitvavo", symbol="BTCEUR", order_book=_book("102", "103")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []
    assert strategy.scan_stats()["reject_counts"].get("stale_edge", 0) > 0  # type: ignore[union-attr]
    strategy = MakerInventoryStrategy(
        _maker_settings(paper_maker_min_profit_eur=1.0, paper_maker_same_venue=True)
    )
    snaps = [
        top_of_book_snapshot(
            exchange="binance", symbol="BTCEUR", order_book=_book("100.00", "100.01")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []
    stats = strategy.scan_stats()
    assert int(stats["scan_rejections"]) > 0  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_maker_does_not_stale_reject_retail_width_alts() -> None:
    """A 32 bps Bitvavo book is the retail fee hurdle, not a stale BTC dislocation."""
    set_fee_tier("retail")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_max_edge_bps=30,
            paper_maker_same_venue=True,
            paper_maker_min_profit_eur=0.0001,
            paper_fee_tier="retail",
        )
    )
    snaps = [
        top_of_book_snapshot(
            exchange="bitvavo", symbol="BNBEUR", order_book=_book("100", "100.32")
        ),
    ]
    await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    counts = strategy.scan_stats()["reject_counts"]
    assert counts.get("stale_edge", 0) == 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_maker_level1_quotes_when_touch_fees_eat_edge() -> None:
    set_fee_tier("retail")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_book_level=1,
            paper_maker_max_edge_bps=80,
            paper_maker_same_venue=True,
            paper_maker_min_spread_bps=2,
            paper_maker_min_profit_eur=0.0001,
            paper_maker_adverse_bps=0,
            paper_fee_tier="retail",
        )
    )
    deep = OrderBook(
        symbol="ADAEUR",
        bids=[
            OrderBookLevel(price=Decimal("1.0000"), amount=Decimal("10")),
            OrderBookLevel(price=Decimal("0.9970"), amount=Decimal("10")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("1.0012"), amount=Decimal("10")),
            OrderBookLevel(price=Decimal("1.0032"), amount=Decimal("10")),
        ],
    )
    snaps = [
        top_of_book_snapshot(exchange="okx", symbol="ADAEUR", order_book=deep),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps
    assert opps[0].entry_price == Decimal("0.9970")
    assert opps[0].expected_exit_price == Decimal("1.0032")


@pytest.mark.asyncio
async def test_maker_rejects_when_fees_eat_edge() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(paper_maker_max_edge_bps=40, paper_maker_same_venue=True)
    )
    # Bitvavo maker RT ≈ 30 bps; 12 bps spread leaves no NET room.
    snaps = [
        top_of_book_snapshot(
            exchange="bitvavo", symbol="BTCEUR", order_book=_book("100", "100.12")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []
    assert strategy.scan_stats()["reject_counts"].get("fees_eat_edge", 0) > 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_maker_rejects_expensive_venue_pair() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_venues="okx,binance,bitvavo,kraken",
            paper_maker_max_fee_bps=20,
            paper_maker_same_venue=False,
            paper_maker_max_edge_bps=80,
        )
    )
    snaps = [
        top_of_book_snapshot(
            exchange="binance", symbol="BTCEUR", order_book=_book("100", "100.10")
        ),
        top_of_book_snapshot(
            exchange="kraken", symbol="BTCEUR", order_book=_book("100.40", "100.50")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []
    assert strategy.scan_stats()["reject_counts"].get("fee_too_high", 0) > 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_maker_ignores_venues_outside_allowlist() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(paper_maker_venues="okx,binance", paper_maker_same_venue=False)
    )
    snaps = [
        top_of_book_snapshot(
            exchange="kraken", symbol="BTCEUR", order_book=_book("100", "100.05")
        ),
        top_of_book_snapshot(
            exchange="bitvavo", symbol="BTCEUR", order_book=_book("100.40", "100.50")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []


@pytest.mark.asyncio
async def test_maker_same_venue_when_spread_is_wide() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(paper_maker_min_profit_eur=0.01, paper_maker_min_spread_bps=2)
    )
    snaps = [
        top_of_book_snapshot(
            exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.40")
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert len(opps) == 1
    assert opps[0].metadata["buy_exchange"] == "okx"
    assert opps[0].metadata["sell_exchange"] == "okx"


@pytest.mark.asyncio
async def test_maker_skips_usdt_pairs_on_eur_account() -> None:
    strategy = MakerInventoryStrategy(_maker_settings())
    snaps = [
        top_of_book_snapshot(
            exchange="binance",
            symbol="BTCUSDT",
            order_book=OrderBook(
                symbol="BTCUSDT",
                bids=[OrderBookLevel(price=Decimal("100"), amount=Decimal("2"))],
                asks=[OrderBookLevel(price=Decimal("102"), amount=Decimal("2"))],
            ),
        ),
        top_of_book_snapshot(
            exchange="okx",
            symbol="BTCUSDT",
            order_book=OrderBook(
                symbol="BTCUSDT",
                bids=[OrderBookLevel(price=Decimal("101"), amount=Decimal("2"))],
                asks=[OrderBookLevel(price=Decimal("103"), amount=Decimal("2"))],
            ),
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []


@pytest.mark.asyncio
async def test_orchestrator_places_post_only_legs_without_taker_hedge() -> None:
    from bot.engine.orchestrator import TradingEngine
    from bot.execution.paper_executor import PaperExecutor
    from bot.portfolio.models import AssetBalance
    from bot.portfolio.portfolio import PaperPortfolio
    from bot.profitability.engine import DefaultProfitabilityEngine
    from bot.risk.engine import DefaultRiskEngine

    settings = _maker_settings(
        paper_starting_eur=10_000.0,
        paper_simulated_latency_ms=0.0,
        paper_maker_rest_ms=0.0,
        paper_maker_queue_fill_pct=0.0,
        paper_maker_trade_through_fill_pct=1.0,
        paper_fee_rate=0.001,
        paper_slippage_mode="order_book",
        risk_max_position_usd=50_000.0,
        max_position_percent=50.0,
        max_slippage_percent=5.0,
        max_market_data_age_ms=60_000.0,
        profitability_min_net_profit_usd=0.02,
        paper_maker_min_profit_eur=0.02,
    )
    okx = top_of_book_snapshot(
        exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.20")
    )
    binance = top_of_book_snapshot(
        exchange="binance", symbol="BTCEUR", order_book=_book("100.80", "101.20")
    )

    class VenueProvider:
        async def get_venue_snapshots(self, symbol: str):
            return [okx, binance]

        async def get_snapshot(self, symbol: str):
            return okx

        def get_order_book(self, exchange: str, symbol: str):
            if exchange == "okx":
                return okx.order_book
            if exchange == "binance":
                return binance.order_book
            return None

    portfolio = PaperPortfolio(settings, starting_eur=Decimal("10000"))
    portfolio.state.balances["BTC"] = AssetBalance(
        asset="BTC", available=Decimal("1"), reserved=Decimal("0")
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    engine = TradingEngine(
        market_data=VenueProvider(),
        strategy=MakerInventoryStrategy(settings),
        profitability=DefaultProfitabilityEngine(settings),
        risk=DefaultRiskEngine(settings),
        portfolio=portfolio,
        executor=executor,
    )
    result = await engine.run_once("BTCEUR")
    assert result.opportunities
    assert result.executions
    open_or_resting = [ex for ex in result.executions if ex.status == OrderStatus.OPEN]
    assert open_or_resting
    assert all(ex.status != OrderStatus.FILLED for ex in result.executions)
    assert all((o.metadata or {}).get("post_only") for o in result.orders)

    books = {
        "okx": {
            "BTCEUR": _book("99.5", "100.20")  # bid traded through buy @ 100
        },
        "binance": {
            "BTCEUR": _book("100.80", "101.50")  # ask traded through sell @ 101.20
        },
    }
    fills = executor.match_resting(books)
    assert len(fills) == 2
    assert {f.status for f in fills} == {OrderStatus.FILLED}


@pytest.mark.asyncio
async def test_maker_rejects_toxic_buy_above_usdt_fair_value() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_fair_value=True,
            paper_maker_adverse_bps=0.0,
            paper_maker_same_venue=True,
            paper_maker_max_edge_bps=80,
        )
    )
    # EUR book: bid 102 / ask 102.30. USDT fair = 100000/1000 = 100 EUR.
    snaps = [
        top_of_book_snapshot(
            exchange="okx", symbol="BTCEUR", order_book=_book("102", "102.30")
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="BTCUSDT",
            order_book=OrderBook(
                symbol="BTCUSDT",
                bids=[OrderBookLevel(price=Decimal("99900"), amount=Decimal("2"))],
                asks=[OrderBookLevel(price=Decimal("100100"), amount=Decimal("2"))],
            ),
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="EURUSDT",
            order_book=OrderBook(
                symbol="EURUSDT",
                bids=[OrderBookLevel(price=Decimal("999"), amount=Decimal("100"))],
                asks=[OrderBookLevel(price=Decimal("1001"), amount=Decimal("100"))],
            ),
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps == []
    assert strategy.scan_stats()["reject_counts"].get("toxic_buy_vs_fv", 0) > 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_maker_emits_when_eur_book_brackets_usdt_fair_value() -> None:
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_fair_value=True,
            paper_maker_adverse_bps=0.0,
            paper_maker_same_venue=True,
            paper_maker_min_profit_eur=0.01,
            paper_maker_max_edge_bps=80,
        )
    )
    # Fair value ≈ 100. EUR bid 99.80 / ask 100.40 brackets FV.
    snaps = [
        top_of_book_snapshot(
            exchange="okx", symbol="BTCEUR", order_book=_book("99.80", "100.40")
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="BTCUSDT",
            order_book=OrderBook(
                symbol="BTCUSDT",
                bids=[OrderBookLevel(price=Decimal("99900"), amount=Decimal("2"))],
                asks=[OrderBookLevel(price=Decimal("100100"), amount=Decimal("2"))],
            ),
        ),
        top_of_book_snapshot(
            exchange="binance",
            symbol="EURUSDT",
            order_book=OrderBook(
                symbol="EURUSDT",
                bids=[OrderBookLevel(price=Decimal("999"), amount=Decimal("100"))],
                asks=[OrderBookLevel(price=Decimal("1001"), amount=Decimal("100"))],
            ),
        ),
    ]
    opps = await strategy.evaluate_markets(snaps, equity=Decimal("10000"))
    assert opps
    assert opps[0].metadata.get("fair_value_aligned") is True


@pytest.mark.asyncio
async def test_realistic_dust_max_quantity_cannot_clear_min_profit() -> None:
    """€25k Realistic was capped at 0.02 coins → every alt quote died in profitability."""
    set_fee_tier("retail")
    deep = _retail_level1_atom()
    snaps = [top_of_book_snapshot(exchange="binance", symbol="ATOMEUR", order_book=deep)]
    dust = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_book_level=1,
            paper_maker_max_edge_bps=80,
            paper_maker_min_profit_eur=0.001,
            paper_maker_adverse_bps=4,
            paper_fee_tier="retail",
            arbitrage_max_quantity=0.02,
            arbitrage_position_pct=8,
            arbitrage_min_liquidity_base=0.0001,
        )
    )
    assert await dust.evaluate_markets(snaps, equity=Decimal("25000")) == []
    rejects = dust.scan_stats()["reject_counts"]  # type: ignore[index]
    assert (
        int(rejects.get("min_profit_eur", 0))
        + int(rejects.get("profitability", 0))
        + int(rejects.get("dust_or_net_floor", 0))
    ) > 0

    sized = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_book_level=1,
            paper_maker_max_edge_bps=80,
            paper_maker_min_profit_eur=0.001,
            paper_maker_adverse_bps=4,
            paper_fee_tier="retail",
            arbitrage_max_quantity=10000,
            arbitrage_position_pct=8,
            arbitrage_min_liquidity_base=0.0001,
        )
    )
    opps = await sized.evaluate_markets(snaps, equity=Decimal("25000"))
    assert opps
    assert opps[0].quantity > Decimal("1")
    assert Decimal(str(opps[0].metadata["net_profit_eur"])) >= Decimal("0.001")


@pytest.mark.asyncio
async def test_retail_same_venue_roundtrip_is_net_positive_after_trade_through() -> None:
    """When size is tradeable, a same-venue maker roundtrip beats retail fees."""
    from bot.execution.paper_executor import PaperExecutor
    from bot.portfolio.models import AssetBalance
    from bot.portfolio.portfolio import PaperPortfolio

    set_fee_tier("retail")
    settings = _maker_settings(
        paper_starting_eur=25_000.0,
        paper_simulated_latency_ms=0.0,
        paper_maker_queue_fill_pct=0.0,
        paper_maker_trade_through_fill_pct=1.0,
        paper_maker_book_level=1,
        paper_maker_max_edge_bps=80,
        paper_maker_min_profit_eur=0.001,
        paper_maker_adverse_bps=4,
        paper_fee_tier="retail",
        paper_fee_rate=0.001,
        arbitrage_max_quantity=10000,
        arbitrage_position_pct=8,
        arbitrage_min_liquidity_base=0.0001,
        paper_venue_inventory=False,
    )
    deep = _retail_level1_atom()
    snap = top_of_book_snapshot(exchange="binance", symbol="ATOMEUR", order_book=deep)
    strategy = MakerInventoryStrategy(settings)
    opps = await strategy.evaluate_markets([snap], equity=Decimal("25000"))
    assert opps
    opp = opps[0]
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("25000"))
    portfolio.state.balances["ATOM"] = AssetBalance(
        asset="ATOM", available=Decimal("5000"), reserved=Decimal("0")
    )
    executor = PaperExecutor(settings, portfolio=portfolio)
    from bot.core.models import OrderRequest
    from bot.core.enums import OpportunitySide

    buy_req = OrderRequest(
        opportunity_id=opp.id,
        symbol="ATOMEUR",
        side=OpportunitySide.BUY,
        quantity=opp.quantity,
        limit_price=opp.entry_price,
        metadata={"venue": "binance", "post_only": True, "fee_role": "maker"},
    )
    sell_req = OrderRequest(
        opportunity_id=opp.id,
        symbol="ATOMEUR",
        side=OpportunitySide.SELL,
        quantity=opp.quantity,
        limit_price=opp.expected_exit_price,
        metadata={"venue": "binance", "post_only": True, "fee_role": "maker"},
    )
    buy_ex = await executor.execute(buy_req, order_book=deep, strategy="maker_inventory")
    sell_ex = await executor.execute(sell_req, order_book=deep, strategy="maker_inventory")
    assert buy_ex.status == OrderStatus.OPEN
    assert sell_ex.status == OrderStatus.OPEN

    through = OrderBook(
        symbol="ATOMEUR",
        bids=[OrderBookLevel(price=Decimal("1.320"), amount=Decimal("500"))],
        asks=[OrderBookLevel(price=Decimal("1.345"), amount=Decimal("500"))],
    )
    fills = executor.match_resting({"binance": {"ATOMEUR": through}})
    assert len(fills) == 2
    fees = sum((f.fees_usd for f in fills), Decimal("0"))
    buy_px = opp.entry_price
    sell_px = opp.expected_exit_price
    gross = (sell_px - buy_px) * opp.quantity
    assert gross - fees > 0


@pytest.mark.asyncio
async def test_picks_fatter_book_level_when_euro_edge_is_larger() -> None:
    """Profit-max: size × spread at level 1 beats a tiny touch quote."""
    set_fee_tier("retail")
    book = OrderBook(
        symbol="ATOMEUR",
        bids=[
            OrderBookLevel(price=Decimal("1.330"), amount=Decimal("50")),
            OrderBookLevel(price=Decimal("1.328"), amount=Decimal("400")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("1.336"), amount=Decimal("50")),
            OrderBookLevel(price=Decimal("1.337"), amount=Decimal("400")),
        ],
    )
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_book_level=1,
            paper_maker_max_edge_bps=80,
            paper_maker_min_profit_eur=0.001,
            paper_maker_adverse_bps=4,
            paper_fee_tier="retail",
            arbitrage_max_quantity=10000,
            arbitrage_position_pct=8,
            arbitrage_min_liquidity_base=0.0001,
        )
    )
    opps = await strategy.evaluate_markets(
        [top_of_book_snapshot(exchange="binance", symbol="ATOMEUR", order_book=book)],
        equity=Decimal("25000"),
    )
    assert opps
    assert opps[0].entry_price == Decimal("1.328")
    assert opps[0].expected_exit_price == Decimal("1.337")
    assert opps[0].quantity > Decimal("50")


@pytest.mark.asyncio
async def test_joins_touch_when_deeper_euro_is_similar() -> None:
    """When both levels pay about the same euro, stay at the touch (faster fill)."""
    set_fee_tier("retail")
    book = OrderBook(
        symbol="ATOMEUR",
        bids=[
            OrderBookLevel(price=Decimal("1.330"), amount=Decimal("500")),
            OrderBookLevel(price=Decimal("1.329"), amount=Decimal("20")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("1.336"), amount=Decimal("500")),
            OrderBookLevel(price=Decimal("1.337"), amount=Decimal("20")),
        ],
    )
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_book_level=1,
            paper_maker_max_edge_bps=80,
            paper_maker_min_profit_eur=0.001,
            paper_maker_adverse_bps=4,
            paper_fee_tier="retail",
            arbitrage_max_quantity=10000,
            arbitrage_position_pct=8,
            arbitrage_min_liquidity_base=0.0001,
        )
    )
    opps = await strategy.evaluate_markets(
        [top_of_book_snapshot(exchange="binance", symbol="ATOMEUR", order_book=book)],
        equity=Decimal("25000"),
    )
    assert opps
    assert opps[0].entry_price == Decimal("1.330")
    assert opps[0].expected_exit_price == Decimal("1.336")


@pytest.mark.asyncio
async def test_equity_bps_floor_rejects_dust_on_large_book() -> None:
    set_fee_tier("retail")
    book = _book("100", "100.20", qty="0.05")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_min_profit_eur=0.001,
            paper_maker_min_profit_equity_bps=0.2,
            paper_maker_same_venue=True,
            arbitrage_max_quantity=10000,
        )
    )
    opps = await strategy.evaluate_markets(
        [top_of_book_snapshot(exchange="okx", symbol="BTCEUR", order_book=book)],
        equity=Decimal("25000"),
    )
    assert opps == []
    rejects = strategy.scan_stats()["reject_counts"]  # type: ignore[index]
    assert (
        int(rejects.get("min_profit_eur", 0))
        + int(rejects.get("dust_or_net_floor", 0))
    ) > 0


@pytest.mark.asyncio
async def test_keeps_only_quotes_near_best_net() -> None:
    set_fee_tier("retail")
    fat = _book("100", "100.80", qty="5")
    thin = _book("1.00", "1.004", qty="1")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_keep_vs_best_frac=0.35,
            paper_maker_max_edge_bps=200,
            paper_maker_min_spread_bps=2,
            paper_maker_venues="okx",
            arbitrage_max_emits_per_cycle=4,
            arbitrage_max_quantity=100,
            paper_maker_fair_value=False,
        )
    )
    opps = await strategy.evaluate_markets(
        [
            top_of_book_snapshot(exchange="okx", symbol="BTCEUR", order_book=fat),
            top_of_book_snapshot(exchange="okx", symbol="ATOMEUR", order_book=thin),
        ],
        equity=Decimal("25000"),
    )
    assert opps
    symbols = {o.symbol for o in opps}
    assert "BTCEUR" in symbols
    assert "ATOMEUR" not in symbols


@pytest.mark.asyncio
async def test_cooldown_replaces_quote_when_net_improves() -> None:
    set_fee_tier("retail")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            arbitrage_opportunity_cooldown_ms=60_000,
            paper_maker_replace_improve_frac=0.25,
            paper_maker_max_edge_bps=200,
            paper_maker_venues="okx",
            arbitrage_max_quantity=10,
        )
    )
    snap = top_of_book_snapshot(
        exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.40", qty="2")
    )
    first = await strategy.evaluate_markets([snap], equity=Decimal("25000"))
    assert first
    wider = top_of_book_snapshot(
        exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.90", qty="2")
    )
    second = await strategy.evaluate_markets([wider], equity=Decimal("25000"))
    assert second
    assert Decimal(str(second[0].metadata["net_profit_eur"])) > Decimal(
        str(first[0].metadata["net_profit_eur"])
    )


@pytest.mark.asyncio
async def test_overweight_inventory_emits_sell_only() -> None:
    from bot.portfolio.models import AssetBalance, PortfolioState, PositionState

    set_fee_tier("retail")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_max_edge_bps=200,
            paper_maker_venues="okx",
            paper_maker_min_notional_eur=1,
            paper_maker_min_profit_eur=0.01,
            paper_maker_min_net_return=0.0001,
            arbitrage_max_quantity=10,
            paper_inventory_ask_improve_bps=8,
        )
    )
    state = PortfolioState(
        balances={
            "EUR": AssetBalance(asset="EUR", available=Decimal("6000")),
            "BTC": AssetBalance(asset="BTC", available=Decimal("40")),
        },
        positions={
            "BTCEUR": PositionState(
                symbol="BTCEUR",
                quantity=Decimal("40"),
                average_entry_price=Decimal("100"),
            )
        },
        quote_asset="EUR",
        mark_prices={"BTCEUR": Decimal("100")},
    )

    class _Inv:
        def available(self, venue: str, asset: str) -> Decimal:
            if asset == "BTC":
                return Decimal("5")
            if asset == "EUR":
                return Decimal("5000")
            return Decimal("0")

    snap = top_of_book_snapshot(
        exchange="okx", symbol="BTCEUR", order_book=_book("100", "100.40", qty="2")
    )
    opps = await strategy.evaluate_markets(
        [snap],
        equity=Decimal("10000"),
        inventory=_Inv(),
        portfolio_state=state,
    )
    assert opps
    assert opps[0].metadata.get("sell_only") is True
    assert strategy.active_skew is not None
    assert strategy.active_skew.sell_only is True


@pytest.mark.asyncio
async def test_dust_notional_floor_rejects_tiny_quotes() -> None:
    set_fee_tier("retail")
    strategy = MakerInventoryStrategy(
        _maker_settings(
            paper_maker_max_edge_bps=200,
            paper_maker_venues="okx",
            paper_maker_min_notional_eur=50,
            paper_maker_min_profit_eur=0.01,
            arbitrage_max_quantity=0.05,
            arbitrage_min_liquidity_base=0.01,
        )
    )
    snap = top_of_book_snapshot(
        exchange="okx",
        symbol="BTCEUR",
        order_book=_book("100", "100.40", qty="0.05"),
    )
    opps = await strategy.evaluate_markets([snap], equity=Decimal("25000"))
    assert opps == []
    assert strategy.scan_stats()["reject_counts"].get("dust_or_net_floor", 0) > 0


def test_balanced_emits_give_each_venue_a_slot() -> None:
    """Bitvavo-ranked edges must not monopolize a tiny emit budget."""
    from bot.core.enums import OpportunitySide
    from bot.core.models import TradeOpportunity
    from uuid import uuid4

    strategy = MakerInventoryStrategy(
        _maker_settings(arbitrage_max_emits_per_cycle=2)
    )
    def _opp(venue: str, net: str) -> TradeOpportunity:
        return TradeOpportunity(
            id=uuid4(),
            strategy_name="maker_inventory",
            symbol="ADAEUR",
            side=OpportunitySide.BUY,
            quantity=Decimal("10"),
            entry_price=Decimal("1"),
            expected_exit_price=Decimal("1.01"),
            confidence=0.5,
            rationale="test",
            metadata={
                "buy_exchange": venue,
                "sell_exchange": venue,
                "net_profit_eur": net,
                "post_only": True,
            },
        )

    # Three Bitvavo edges rank above the sole OKX edge.
    ranked = [
        _opp("bitvavo", "3.0"),
        _opp("bitvavo", "2.5"),
        _opp("bitvavo", "2.0"),
        _opp("okx", "1.5"),
    ]
    selected = strategy._select_balanced_emits(ranked)  # noqa: SLF001
    venues = {
        (o.metadata or {}).get("buy_exchange") for o in selected
    }
    assert venues == {"bitvavo", "okx"}
    assert len(selected) == 2

