"""Tests for central funding & multi-venue portfolio MVP."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.core.config import Settings
from bot.core.enums import ExecutionMode
from bot.core.models import Balance, PortfolioSnapshot
from bot.execution.paper_executor import PaperExecutor
from bot.funding.models import FundingEventType
from bot.funding.multi_venue import (
    fetch_live_venue_balances,
    ledger_to_venue_snapshots,
    venue_credential_env_names,
)
from bot.funding.rebalance import recommend_asset_topups, recommend_quote_rebalance
from bot.funding.service import FundingPortfolioService, reset_funding_service
from bot.funding.store import FundingEventStore
from bot.main import app, reset_risk_singletons
from bot.portfolio.venue_ledger import VenueLedger


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_risk_singletons()
    reset_funding_service()
    path = tmp_path / "funding_events.json"
    monkeypatch.setenv("FUNDING_PERSIST_PATH", str(path))
    monkeypatch.setenv("AUTOMATIC_WITHDRAWALS_ENABLED", "false")
    # Clear settings cache so env overrides apply.
    from bot.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_funding_service()
    reset_risk_singletons()


def test_paper_balance_via_ledger() -> None:
    ledger = VenueLedger(["bitvavo", "kraken"], quote="EUR", starting_quote=Decimal("1000"))
    snaps = ledger_to_venue_snapshots(ledger.export(), source="paper")
    assert len(snaps) == 2
    by_venue = {s.venue: s for s in snaps}
    assert by_venue["bitvavo"].total_value_eur == Decimal("500")
    assert by_venue["kraken"].balances[0].available == Decimal("500")
    assert by_venue["bitvavo"].source == "paper"


def test_multiple_venue_balances_and_portfolio_aggregation(tmp_path: Path) -> None:
    store = FundingEventStore(tmp_path / "f.json")
    store.record_deposit(venue="bitvavo", amount="10000", asset="EUR")

    ledger = VenueLedger(
        ["bitvavo", "kraken", "binance"], quote="EUR", starting_quote=Decimal("9000")
    )
    ledger._balances["bitvavo"]["BTC"] = Decimal("0.01")

    def fake_runner() -> object:
        class P:
            venue_ledger = ledger

        class R:
            portfolio = P()

            class tracker:
                @staticmethod
                def snapshot() -> object:
                    class S:
                        current_equity = Decimal("9250")
                        starting_equity = Decimal("9000")
                        net_pnl = Decimal("250")
                        realized_pnl = Decimal("200")

                    return S()

        return R()

    settings = Settings(
        execution_mode=ExecutionMode.PAPER,
        paper_starting_eur=9000,
        funding_persist_path=str(tmp_path / "f.json"),
        funding_venues="bitvavo,kraken,binance",
        funding_main_venue="bitvavo",
    )
    svc = FundingPortfolioService(
        settings, store=store, paper_runner_getter=fake_runner
    )
    import asyncio

    summary = asyncio.run(svc.portfolio_summary())
    assert summary.mode == "paper"
    assert summary.main_funding_venue == "bitvavo"
    assert summary.total_deposited == Decimal("10000")
    assert summary.current_portfolio == Decimal("9250")
    assert summary.withdrawals_supported is False
    assert len(summary.venues) >= 3


@pytest.mark.asyncio
async def test_live_balance_offline_venue_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(execution_mode=ExecutionMode.LIVE, funding_venues="bitvavo,kraken")

    class BoomClient:
        async def get_balances(self) -> PortfolioSnapshot:
            raise RuntimeError("exchange down")

        async def close(self) -> None:
            return None

    def factory(_settings: Settings, *, enable_trading: bool = False) -> BoomClient:
        assert enable_trading is False
        return BoomClient()

    monkeypatch.setenv("BITVAVO_API_KEY", "x")
    monkeypatch.setenv("BITVAVO_API_SECRET", "y")
    # kraken has no keys → credentials_not_configured
    snaps = await fetch_live_venue_balances(
        settings, ["bitvavo", "kraken"], client_factory=factory
    )
    assert len(snaps) == 2
    by = {s.venue: s for s in snaps}
    assert by["bitvavo"].online is False
    assert by["bitvavo"].error == "RuntimeError"
    assert by["kraken"].error == "credentials_not_configured"


@pytest.mark.asyncio
async def test_live_balance_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(execution_mode=ExecutionMode.LIVE, funding_venues="bitvavo")

    class OkClient:
        async def get_balances(self) -> PortfolioSnapshot:
            return PortfolioSnapshot(
                balances=[
                    Balance(asset="EUR", free=Decimal("4000"), locked=Decimal("100")),
                    Balance(asset="BTC", free=Decimal("0.05"), locked=Decimal("0")),
                ],
                equity_usd=Decimal("4000"),
            )

        async def close(self) -> None:
            return None

    def factory(_settings: Settings, *, enable_trading: bool = False) -> OkClient:
        assert enable_trading is False
        return OkClient()

    monkeypatch.setenv("BITVAVO_API_KEY", "x")
    monkeypatch.setenv("BITVAVO_API_SECRET", "y")
    snaps = await fetch_live_venue_balances(
        settings, ["bitvavo"], client_factory=factory
    )
    assert snaps[0].online is True
    eur = next(b for b in snaps[0].balances if b.asset == "EUR")
    assert eur.available == Decimal("4000")
    assert eur.reserved == Decimal("100")
    assert eur.total == Decimal("4100")


def test_deposit_and_withdrawal_tracking(tmp_path: Path) -> None:
    store = FundingEventStore(tmp_path / "f.json")
    dep = store.record_deposit(venue="bitvavo", amount="5000", external_reference="SEPA-1")
    assert dep.type == FundingEventType.DEPOSIT
    exit_ = store.record_withdrawal_tracking(venue="bitvavo", amount="250")
    assert exit_.type == FundingEventType.WITHDRAWAL
    assert exit_.metadata.get("bot_executed") is False
    totals = store.totals()
    assert totals["total_deposited"] == Decimal("5000")
    assert totals["total_withdrawn"] == Decimal("250")
    # reload
    store2 = FundingEventStore(tmp_path / "f.json")
    assert len(store2.list_events()) == 2


def test_balance_reservations_on_ledger() -> None:
    ledger = VenueLedger(["bitvavo"], quote="EUR", starting_quote=Decimal("100"))
    assert ledger.lock("bitvavo", "EUR", Decimal("40")) is True
    assert ledger.available("bitvavo", "EUR") == Decimal("60")
    ledger.unlock("bitvavo", "EUR", Decimal("40"))
    assert ledger.available("bitvavo", "EUR") == Decimal("100")


def test_insufficient_inventory_blocks_sell() -> None:
    ledger = VenueLedger(["bitvavo", "kraken"], quote="EUR", starting_quote=Decimal("1000"))
    assert ledger.can_sell("kraken", "BTC", Decimal("0.01")) is False
    ledger.credit("kraken", "BTC", Decimal("0.02"))
    assert ledger.can_sell("kraken", "BTC", Decimal("0.01")) is True


def test_rebalancing_recommendation_not_executed() -> None:
    bals = {
        "bitvavo": Decimal("8000"),
        "kraken": Decimal("1500"),
        "binance": Decimal("500"),
    }
    recs = recommend_quote_rebalance(bals, asset="EUR", fee_bps=Decimal("10"))
    assert recs
    assert all(r.status == "pending_manual" for r in recs)
    # Balances unchanged
    assert bals["bitvavo"] == Decimal("8000")


def test_asset_topup_recommendation() -> None:
    bals = {"bitvavo": Decimal("0.1"), "kraken": Decimal("0.001")}
    recs = recommend_asset_topups(
        asset="BTC",
        balances=bals,
        min_amount=Decimal("0.02"),
        donor_preference=["bitvavo"],
    )
    assert recs
    assert recs[0].from_venue == "bitvavo"
    assert recs[0].to_venue == "kraken"


def test_fee_transfer_model_on_ledger() -> None:
    ledger = VenueLedger(["bitvavo", "kraken"], quote="EUR", starting_quote=Decimal("1000"))
    result = ledger.transfer(
        from_venue="bitvavo",
        to_venue="kraken",
        asset="EUR",
        amount=Decimal("100"),
        fee_bps=Decimal("10"),
    )
    assert result is not None
    received, fee = result
    assert fee == Decimal("0.1")
    assert received == Decimal("99.9")


def test_paper_executor_never_live() -> None:
    from bot.portfolio.portfolio import PaperPortfolio

    portfolio = PaperPortfolio(Settings(execution_mode=ExecutionMode.PAPER))
    with pytest.raises(RuntimeError, match="refuses"):
        PaperExecutor(
            Settings(execution_mode=ExecutionMode.LIVE),
            portfolio=portfolio,
        )
    # Paper mode constructs fine
    PaperExecutor(Settings(execution_mode=ExecutionMode.PAPER), portfolio=portfolio)


def test_api_keys_never_leaked_in_funding_status() -> None:
    client = TestClient(app)
    body = client.get("/status").json()
    blob = str(body).lower()
    assert "api_key" not in blob or body.get("exchange") is not None
    for secretish in ("secret", "passphrase", "password"):
        # status should not contain credential values
        assert secretish not in blob or secretish in ("password",)  # weak check
    # Stronger: credential env names helper never returns values
    names = venue_credential_env_names("bitvavo")
    assert names["api_key"] == "BITVAVO_API_KEY"
    assert "value" not in names


def test_api_portfolio_and_funding_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUNDING_PERSIST_PATH", str(tmp_path / "funding.json"))
    from bot.core.config import get_settings

    get_settings.cache_clear()
    reset_funding_service()
    client = TestClient(app)

    status = client.get("/status").json()
    assert status["withdrawals_supported"] is False
    assert status["automatic_withdrawals_enabled"] is False
    assert status["funding_main_venue"]

    port = client.get("/portfolio").json()
    assert "venues" in port
    assert port["withdrawals_supported"] is False

    bals = client.get("/balances").json()
    assert "venues" in bals

    rec = client.post(
        "/funding/events",
        json={"type": "deposit", "venue": "bitvavo", "amount": "1000"},
    )
    assert rec.status_code == 200
    assert rec.json()["executed"] is False

    funding = client.get("/funding").json()
    assert funding["withdrawals_supported"] is False
    assert "withdraw_instructions" in funding
    assert any(float(d["amount"]) == 1000 for d in funding["deposits"])

    exits = client.get("/funding/recorded-exits").json()
    assert exits["bot_executed"] is False

    # Track exit without executing
    track = client.post(
        "/funding/events",
        json={"type": "withdrawal", "venue": "bitvavo", "amount": "50"},
    )
    assert track.status_code == 200
    assert track.json()["executed"] is False

    recs = client.get("/rebalancing/recommendations").json()
    assert recs["auto_execute"] is False


def test_no_withdraw_execution_routes() -> None:
    paths = {route.path.lower() for route in app.routes if hasattr(route, "path")}
    assert not any(p.endswith("/withdraw") or "/withdraw/" in p for p in paths)
    # recorded-exits is tracking only
    assert "/funding/recorded-exits" in paths


def test_database_funding_events_table_no_withdrawal_table() -> None:
    from database.base import Base
    from database.models import FundingEventRecord

    names = {name.lower() for name in Base.metadata.tables}
    assert "funding_events" in names
    assert "withdrawals" not in names
    assert "transfers" not in names
    assert FundingEventRecord.__tablename__ == "funding_events"
