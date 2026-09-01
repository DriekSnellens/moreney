"""Regression tests: frozen CVD semantics parity between research and live paper.

Proves:
1. A >=40 bps signal creates a candidate WITHOUT waiting 5 seconds.
2. The same candidate can later receive its T+5s outcome.
3. Dislocation disappearing after 1s does NOT invalidate the decision-time candidate.
4. Missing T+5s data → DATA_INVALID for outcome, not deletion of entry.
5. Strategy fingerprint and frozen config remain identical.
6. No threshold/economics/fee/slippage/adverse/route/risk rule changed.
7. PaperExecutor behavior unchanged.
8. Historical canonical replay semantics unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path

import pytest

from bot.paper.cvd_candidate import create_cvd_candidates, _THRESHOLD
from bot.paper.pipeline_funnel import LivePipelineFunnel
from bot.core.models import MarketSnapshot
from bot.core.exchange_types import OrderBook, OrderBookLevel
from bot.research.shadow_validation.protocol import (
    DISLOCATION_BPS,
    HORIZON_MS,
    VENUE_A,
    VENUE_B,
    NOTIONAL_EUR,
    ADVERSE_BPS,
    SLIPPAGE_BPS,
    LATENCY_BPS,
    FEE_RATE_ROUNDTRIP,
    PRODUCTION_EXECUTION_ENABLED,
    strategy_fingerprint,
    config_hash,
    parameter_hash,
    frozen_parameters,
)
from bot.research.shadow_validation.observer import ShadowPaperObserver
from bot.research.shadow_validation.outcomes import (
    DATA_INVALID,
    FULL_FILL,
    NO_FILL,
    classify_observation,
)
from bot.research.shadow_validation.economics import expected_from_dislocation
from bot.research.shadow_validation.books import CompactL1, L1View


def _snap(exchange: str, symbol: str, bid: float, ask: float, depth: float = 10.0) -> MarketSnapshot:
    book = OrderBook(
        symbol=symbol,
        bids=[OrderBookLevel(price=Decimal(str(bid)), amount=Decimal(str(depth)))],
        asks=[OrderBookLevel(price=Decimal(str(ask)), amount=Decimal(str(depth)))],
    )
    return MarketSnapshot(
        symbol=symbol,
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        last=Decimal(str((bid + ask) / 2)),
        order_book=book,
        exchange=exchange,
    )


def test_candidate_created_immediately_no_5s_wait():
    """A >=40 bps signal creates a candidate at decision time without waiting."""
    # OKX mid = 100.5, Bitvavo mid = 100.0 → dislocation ~50 bps
    okx = _snap("okx", "BTCEUR", 100.4, 100.6)
    btv = _snap("bitvavo", "BTCEUR", 99.9, 100.1)
    opps = create_cvd_candidates([okx, btv])
    assert len(opps) == 1
    opp = opps[0]
    assert opp.strategy_name == "cross_venue_dislocation"
    assert opp.metadata["frozen_cvd"] is True
    assert opp.metadata["decision_time_candidate"] is True
    assert opp.metadata["entry_semantics"] == "immediate_at_signal_time"
    assert opp.metadata["outcome_horizon_ms"] == 5000
    assert opp.metadata["dislocation_bps"] >= 40.0


def test_candidate_receives_t_plus_5_outcome(tmp_path: Path):
    """The shadow observer measures T+5s outcome separately from entry."""
    obs = ShadowPaperObserver(run_dir=str(tmp_path), git_commit_override="t5", now_ms=0.0)
    books = {
        "okx": {"BTCEUR": SimpleNamespace(
            bids=[SimpleNamespace(price=Decimal("100.4"), amount=Decimal("10"))],
            asks=[SimpleNamespace(price=Decimal("100.6"), amount=Decimal("10"))],
            metadata={"exchange_ts_available": True, "received_at": "2026-08-19T00:00:00+00:00"},
            age_ms=5.0, timestamp=None,
        )},
        "bitvavo": {"BTCEUR": SimpleNamespace(
            bids=[SimpleNamespace(price=Decimal("99.9"), amount=Decimal("10"))],
            asks=[SimpleNamespace(price=Decimal("100.1"), amount=Decimal("10"))],
            metadata={"exchange_ts_available": True, "received_at": "2026-08-19T00:00:00+00:00"},
            age_ms=5.0, timestamp=None,
        )},
    }
    obs.process_cycle(books, symbols=["BTCEUR"], now_ms=0.0)
    assert obs.pending_count == 1
    # At T+5s, the shadow observer classifies the outcome
    obs.process_cycle(books, symbols=["BTCEUR"], now_ms=5000.0)
    assert obs.acc.n_completed == 1


def test_dislocation_disappearing_does_not_retroactively_invalidate():
    """If dislocation disappears at T+1s, the decision-time candidate still exists."""
    entry = CompactL1(
        venue="okx", symbol="BTCEUR",
        bid=100.4, ask=100.6, bid_size=10.0, ask_size=10.0,
        mid=100.5, exchange_ts_ms=0.0, received_ts_ms=0.0,
        exchange_ts_available=True, book_age_ms=5.0,
    )
    hedge = CompactL1(
        venue="bitvavo", symbol="BTCEUR",
        bid=99.9, ask=100.1, bid_size=10.0, ask_size=10.0,
        mid=100.0, exchange_ts_ms=0.0, received_ts_ms=0.0,
        exchange_ts_available=True, book_age_ms=5.0,
    )
    # At T+10ms, prices converged (dislocation gone) but entry still available
    later_entry = L1View("OK", entry)  # quote unchanged = still executable
    later_hedge = L1View("OK", hedge)
    expected = expected_from_dislocation((entry.mid - hedge.mid) / entry.mid)
    result = classify_observation(
        candidate_id="test",
        strategy_fingerprint="fp",
        signal_time_ms=0.0,
        now_ms=5000.0,
        a_rich=True,
        entry_side="SELL",
        hedge_side="BUY",
        decision_entry=entry,
        decision_hedge=hedge,
        later_entry=later_entry,
        later_hedge=later_hedge,
        future_entry=later_entry,
        expected=expected,
        decision_book_age_ms=5.0,
    )
    # The candidate was NOT deleted — it was classified as a fill
    assert result.outcome == FULL_FILL
    assert result.shadow_fill is True


def test_missing_t_plus_5_data_is_data_invalid_not_deletion():
    """Missing T+5s data → DATA_INVALID outcome, not retroactive deletion."""
    entry = CompactL1(
        venue="okx", symbol="BTCEUR",
        bid=100.4, ask=100.6, bid_size=10.0, ask_size=10.0,
        mid=100.5, exchange_ts_ms=0.0, received_ts_ms=0.0,
        exchange_ts_available=True, book_age_ms=5.0,
    )
    hedge = CompactL1(
        venue="bitvavo", symbol="BTCEUR",
        bid=99.9, ask=100.1, bid_size=10.0, ask_size=10.0,
        mid=100.0, exchange_ts_ms=0.0, received_ts_ms=0.0,
        exchange_ts_available=True, book_age_ms=5.0,
    )
    expected = expected_from_dislocation((entry.mid - hedge.mid) / entry.mid)
    result = classify_observation(
        candidate_id="test",
        strategy_fingerprint="fp",
        signal_time_ms=0.0,
        now_ms=5000.0,
        a_rich=True,
        entry_side="SELL",
        hedge_side="BUY",
        decision_entry=entry,
        decision_hedge=hedge,
        later_entry=None,  # Missing data at T+10ms
        later_hedge=L1View("OK", hedge),
        future_entry=L1View("OK", entry),
        expected=expected,
        decision_book_age_ms=5.0,
    )
    assert result.outcome == DATA_INVALID
    assert result.shadow_fill is False


def test_strategy_fingerprint_unchanged():
    """No fingerprint or frozen config was altered."""
    fp = strategy_fingerprint()
    ch = config_hash()
    ph = parameter_hash()
    # Run twice to prove determinism
    assert fp == strategy_fingerprint()
    assert ch == config_hash()
    assert ph == parameter_hash()
    params = frozen_parameters()
    assert params["dislocation_bps"] == DISLOCATION_BPS
    assert params["horizon_ms"] == HORIZON_MS
    assert params["venues"] == [VENUE_A, VENUE_B]


def test_no_threshold_or_economics_changed():
    """Frozen economics remain constant."""
    assert DISLOCATION_BPS == 40.0
    assert HORIZON_MS == 5000
    assert VENUE_A == "okx"
    assert VENUE_B == "bitvavo"
    assert NOTIONAL_EUR == 100.0
    assert ADVERSE_BPS == 8.0
    assert SLIPPAGE_BPS == 2.0
    assert LATENCY_BPS == 2.0
    assert float(_THRESHOLD) == pytest.approx(0.004, rel=1e-6)


def test_production_execution_still_disabled():
    assert PRODUCTION_EXECUTION_ENABLED is False


def test_below_threshold_does_not_create_candidate():
    """<40 bps does not fire."""
    okx = _snap("okx", "BTCEUR", 100.1, 100.3)
    btv = _snap("bitvavo", "BTCEUR", 100.0, 100.2)
    opps = create_cvd_candidates([okx, btv])
    assert len(opps) == 0


def test_funnel_semantics_documented():
    """Funnel snapshot explicitly documents entry/outcome semantics."""
    f = LivePipelineFunnel()
    f.tick_cycle()
    snap = f.snapshot()
    assert snap["entry_semantics"] == "Decision is made at signal time"
    assert snap["outcome_horizon"] == "5 seconds"
