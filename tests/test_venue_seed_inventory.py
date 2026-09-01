"""Venue inventory seeding keeps tradeable sizes (total budget, not per-coin compound)."""

from decimal import Decimal

from bot.core.config import Settings
from bot.portfolio.portfolio import PaperPortfolio
from bot.portfolio.venue_ledger import VenueLedger


def test_seed_uses_total_budget_split_across_allowlisted_assets() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_starting_eur=300.0,
        paper_quote_asset="EUR",
        paper_venue_inventory=True,
        paper_seed_inventory_pct=30.0,
        paper_seed_max_assets=3,
        paper_seed_symbols="ATOMEUR,DOTEUR,SOLEUR",
        market_data_symbols="BTCEUR,ATOMEUR,DOTEUR,SOLEUR,XRPEUR",
    )
    portfolio = PaperPortfolio(settings, starting_eur=Decimal("300"))
    portfolio.init_venue_ledger(["okx", "binance", "bitvavo"], starting_quote=Decimal("300"))
    assert portfolio.venue_ledger is not None
    ledger = portfolio.venue_ledger

    portfolio.maybe_seed_inventory("BTCEUR", Decimal("50000"))  # not allowlisted
    assert ledger.seeded_assets == set()

    portfolio.maybe_seed_inventory("ATOMEUR", Decimal("1"))
    portfolio.maybe_seed_inventory("DOTEUR", Decimal("1"))
    portfolio.maybe_seed_inventory("SOLEUR", Decimal("10"))
    portfolio.maybe_seed_inventory("XRPEUR", Decimal("1"))  # over max / not allowlisted

    assert ledger.seeded_assets == {"ATOM", "DOT", "SOL"}
    # 30% of 100 EUR/venue = 30 EUR inventory total → 10 EUR per asset per venue
    assert ledger.available("okx", "ATOM") == Decimal("10")
    assert ledger.available("okx", "DOT") == Decimal("10")
    assert ledger.available("okx", "SOL") == Decimal("1")
    # ~70% quote cash remains for buys
    assert ledger.available("okx", "EUR") == Decimal("70")


def test_legacy_pct_seed_still_available_on_ledger() -> None:
    ledger = VenueLedger(["okx"], quote="EUR", starting_quote=Decimal("100"))
    moved = ledger.seed_asset("BTC", price=Decimal("50"), pct=Decimal("10"))
    assert moved
    assert ledger.available("okx", "EUR") == Decimal("90")
    assert ledger.available("okx", "BTC") == Decimal("0.2")


def test_ensure_venues_adds_and_funds_new_exchange() -> None:
    ledger = VenueLedger(["okx", "binance"], quote="EUR", starting_quote=Decimal("200"))
    ledger.seed_asset("XRP", price=Decimal("1"), quote_budget=Decimal("20"))
    added = ledger.ensure_venues(["okx", "binance", "coinbase"], fee_bps=Decimal("0"))
    assert added == ["coinbase"]
    assert "coinbase" in ledger.venues
    assert ledger.available("coinbase", "EUR") > 0
    assert ledger.available("coinbase", "XRP") > 0
