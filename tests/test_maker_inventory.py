"""Maker inventory strategy: quote bid/ask when NET edge clears maker fees."""

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import FeeRole, OrderStatus
from bot.strategies.arbitrage import top_of_book_snapshot
from bot.strategies.maker_inventory import MakerInventoryStrategy
from bot.core.exchange_types import OrderBook, OrderBookLevel


def _maker_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "execution_mode": "paper",
        "paper_maker_enabled": True,
        "paper_maker_min_profit_eur": 0.02,
        "paper_maker_min_spread_bps": 2.0,
        "paper_maker_max_edge_bps": 200.0,
        "paper_maker_max_fee_bps": 40.0,
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
