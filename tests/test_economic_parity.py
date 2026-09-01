"""Research-to-live economic parity audit tests for frozen CVD."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.core.config import Settings
from bot.core.enums import FeeRole, OpportunitySide
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.core.models import MarketSnapshot
from bot.core.venue_fees import set_fee_tier
from bot.paper.cvd_candidate import create_cvd_candidates
from bot.research.economic_parity.evaluator import (
    evaluate_frozen_cvd_immutable,
    evaluate_frozen_research_economics,
    evaluate_live_profitability_economics,
    frozen_to_profitability_result,
)
from bot.research.economic_parity.formulas import (
    breakeven_dislocation_bps,
    synthetic_economics_table,
)
from bot.research.economic_parity.store import EconomicParityStore
from bot.research.shadow_validation.protocol import (
    DISLOCATION_BPS,
    FEE_RATE_ROUNDTRIP,
    NOTIONAL_EUR,
    PRODUCTION_EXECUTION_ENABLED,
    strategy_fingerprint,
)


@pytest.fixture(autouse=True)
def _fees() -> None:
    set_fee_tier("retail")


def _snap(exchange: str, symbol: str, bid: float, ask: float) -> MarketSnapshot:
    book = OrderBook(
        symbol=symbol,
        bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal("10"))],
    )
    return MarketSnapshot(
        symbol=symbol,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        last=Decimal(str((bid + ask) / 2)),
        order_book=book,
        exchange=exchange,
    )


def _gate_settings() -> Settings:
    return Settings(
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.15,
        paper_maker_min_net_return=0.0025,
        paper_maker_adverse_bps=4.0,
        paper_maker_gate_buffer_bps=1.0,
        profitability_apply_funding=False,
        profitability_slippage_bps=0.0,
    )


def test_frozen_research_equals_live_when_using_frozen_gate():
    """Frozen research economics is what the CVD gate must use."""
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.9, 100.1)
    opp = create_cvd_candidates([okx, btv])[0]
    research = evaluate_frozen_research_economics(opp)
    prof = frozen_to_profitability_result(opp, research)
    assert prof.trade_allowed == research.profitable
    assert float(prof.net_profit_usd) == pytest.approx(research.expected_net_eur, rel=1e-9)


def test_mutating_live_prices_cannot_change_frozen_profitability():
    """Decision-time snapshot in metadata is immutable for frozen economics."""
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.9, 100.1)
    opp = create_cvd_candidates([okx, btv])[0]
    a, b = evaluate_frozen_cvd_immutable(opp, settings=_gate_settings())
    assert a.expected_net_eur == pytest.approx(b.expected_net_eur, rel=1e-12)
    assert a.profitable == b.profitable


def test_fees_applied_once_research():
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.5, 99.7)
    opp = create_cvd_candidates([okx, btv])[0]
    r = evaluate_frozen_research_economics(opp)
    assert r.fees_eur == pytest.approx(NOTIONAL_EUR * FEE_RATE_ROUNDTRIP, rel=1e-9)
    assert r.leader_fee_eur + r.follower_fee_eur == pytest.approx(r.fees_eur, rel=1e-9)


def test_slippage_and_adverse_applied_once_research():
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.0, 99.2)
    opp = create_cvd_candidates([okx, btv])[0]
    r = evaluate_frozen_research_economics(opp)
    recon = (
        r.gross_eur - r.fees_eur - r.slippage_eur - r.adverse_eur - r.latency_eur
    )
    assert r.expected_net_eur == pytest.approx(recon, rel=1e-9)


def test_breakeven_and_40bps_deterministic():
    be = breakeven_dislocation_bps()
    assert be == pytest.approx(47.0, rel=1e-6)
    table = {row["dislocation_bps"]: row for row in synthetic_economics_table()}
    assert table[40.0]["expected_net_eur"] < 0
    assert table[40.0]["profitable_research"] is False
    assert table[50.0]["expected_net_eur"] > 0
    assert table[100.0]["expected_net_eur"] > table[50.0]["expected_net_eur"]
    assert table[200.0]["expected_net_eur"] > table[100.0]["expected_net_eur"]


def test_100bps_economics_deterministic():
    table = {row["dislocation_bps"]: row for row in synthetic_economics_table()}
    expected = 100.0 * (100.0 / 10000.0) - 100.0 * (
        FEE_RATE_ROUNDTRIP + 2 / 10000 + 8 / 10000 + 2 / 10000
    )
    assert table[100.0]["expected_net_eur"] == pytest.approx(expected, rel=1e-9)


def test_live_netprofit_diverges_from_research_at_threshold():
    """Documents parity failure mode: maker NetProfitCalculator vs frozen research."""
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.9, 100.1)
    opp = create_cvd_candidates([okx, btv])[0]
    research = evaluate_frozen_research_economics(opp)
    live = evaluate_live_profitability_economics(opp, settings=_gate_settings())
    assert research.profitable is True
    assert live.profitable is False
    assert live.gross_eur < 0


def test_candidate_level_replay_identity():
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.0, 99.2)
    opp = create_cvd_candidates([okx, btv])[0]
    store = EconomicParityStore()
    research = evaluate_frozen_research_economics(opp)
    live = evaluate_live_profitability_economics(opp, settings=_gate_settings())
    row = store.record(opp, research=research, live=live)
    assert row["research_expected_net"] == pytest.approx(research.expected_net_eur, rel=1e-9)
    assert row["live_expected_net"] == pytest.approx(live.expected_net_eur, rel=1e-9)
    assert row["parity_mismatch"] is (research.profitable != live.profitable)


def test_strategy_fingerprint_unchanged():
    fp = strategy_fingerprint()
    assert fp == strategy_fingerprint()
    assert DISLOCATION_BPS == 40.0


def test_production_execution_disabled():
    assert PRODUCTION_EXECUTION_ENABLED is False


def test_store_summary_verdict_on_mismatch():
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.9, 100.1)
    opp = create_cvd_candidates([okx, btv])[0]
    store = EconomicParityStore()
    store.record(
        opp,
        research=evaluate_frozen_research_economics(opp),
        live=evaluate_live_profitability_economics(opp, settings=_gate_settings()),
    )
    summary = store.summary()
    assert summary["LIVE_CANDIDATES_ANALYZED"] == 1
    assert summary["PARITY_MISMATCHES"] == 1
    assert summary["ECONOMIC_PARITY"] == "ECONOMIC_PARITY_FAIL"
    assert summary["ROOT_CAUSE"] == "DIFFERENT_PRICE_SELECTION"
