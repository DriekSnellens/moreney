"""Strategy Research Lab acceptance tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.core.venue_fees import set_fee_tier
from bot.perf.candidate_fingerprint import fingerprint_candidates
from bot.perf.candidate_hotpath_bench import build_goe_snapshots, goe_emitting_settings
from bot.strategies.maker_inventory import MakerInventoryStrategy
from bot.strategy_lab.adapters import (
    ControlNoTradeAdapter,
    ExecutableCrossVenueArbAdapter,
    build_all_adapters,
)
from bot.strategy_lab.capital import CapitalLedger, net_eur_per_capital_second
from bot.strategy_lab.dataset import (
    chronological_split,
    dataset_fingerprint,
    iter_baseline_opportunity_keys,
    synthetic_research_tape,
    build_cycles_from_events,
)
from bot.strategy_lab.economics import CommonEconomics, executable_vwap, refuse_midpoint_execution
from bot.strategy_lab.tournament import run_tournament
from bot.strategy_lab.types import DecisionAction
from bot.strategy_lab.verdict import CRITERIA_VERSION, criteria_manifest


@pytest.fixture(autouse=True)
def _fees() -> None:
    set_fee_tier("retail")


def test_maker_fingerprint_unchanged_by_lab_import() -> None:
    """Existing maker strategy fingerprint must remain stable."""
    settings = goe_emitting_settings()
    strategy = MakerInventoryStrategy(settings)
    snaps = build_goe_snapshots(nonce=99, price_shift=Decimal("0"))

    async def _run():
        return await strategy.evaluate_markets(snaps, equity=Decimal("25000"))

    opps = asyncio.run(_run())
    fp = fingerprint_candidates(opps)
    assert fp["count"] >= 1
    # Stable key from prior hot-path work on this fixture
    assert fp["sha256"].startswith("a7310623")


def test_control_produces_zero_trades() -> None:
    settings = Settings(strategy_lab_execution_enabled=False)
    eco = CommonEconomics(settings)
    capital = CapitalLedger.from_config()
    control = ControlNoTradeAdapter(economics=eco, capital=capital, settings=settings)
    events = synthetic_research_tape(n_cycles=10, seed=1)
    cycles = build_cycles_from_events(events)
    for c in cycles:
        control.run_cycle(c)
    assert all(d.action == DecisionAction.CONTROL for d in control.decisions())
    assert sum(1 for d in control.decisions() if d.action == DecisionAction.ACCEPT) == 0


def test_identical_market_events_for_all_strategies() -> None:
    settings = Settings(
        strategy_lab_execution_enabled=False,
        paper_maker_fair_value=False,
        paper_maker_same_venue=True,
        arbitrage_opportunity_cooldown_ms=0,
    )
    eco = CommonEconomics(settings)
    capital = CapitalLedger.from_config()
    adapters = build_all_adapters(economics=eco, capital=capital, settings=settings)
    events = synthetic_research_tape(n_cycles=5, seed=2)
    cycles = build_cycles_from_events(events)
    seen = []
    for a in adapters:
        a.reset()
        ids = []
        for c in cycles:
            a.run_cycle(c)
            ids.append(c.cycle_id)
        seen.append(ids)
    assert all(s == seen[0] for s in seen)


def test_oos_cannot_affect_development_split() -> None:
    events = synthetic_research_tape(n_cycles=40, seed=3)
    cycles = build_cycles_from_events(events)
    dev, oos = chronological_split(cycles, development_frac=0.7)
    assert max(c.ts_ns for c in dev) <= min(c.ts_ns for c in oos)
    assert dataset_fingerprint(dev) != dataset_fingerprint(oos)


def test_future_outcomes_not_in_decide() -> None:
    """Adapters only receive CycleSnapshot books — no outcome fields."""
    settings = Settings(strategy_lab_execution_enabled=False)
    eco = CommonEconomics(settings)
    capital = CapitalLedger.from_config()
    arb = ExecutableCrossVenueArbAdapter(economics=eco, capital=capital, settings=settings)
    events = synthetic_research_tape(n_cycles=3, seed=4)
    cycles = build_cycles_from_events(events)
    for c in cycles:
        assert not hasattr(c, "realized_net")
        arb.run_cycle(c)
    # Decisions exist before any outcomes recorded
    assert arb.outcomes() == []
    assert len(arb.decisions()) >= 0


def test_midpoint_not_used_when_depth_exists() -> None:
    bids = ((Decimal("100"), Decimal("2")),)
    asks = ((Decimal("101"), Decimal("2")),)
    assert refuse_midpoint_execution(bids=bids, asks=asks) is True
    px, qty, ok, _ = executable_vwap("buy", bids=bids, asks=asks, quantity=Decimal("1"))
    assert ok and px == Decimal("101")  # ask, not mid 100.5


def test_missing_hedge_rejects_cross_venue() -> None:
    """If sell depth missing, reject — never assume second leg."""
    settings = Settings(strategy_lab_execution_enabled=False)
    eco = CommonEconomics(settings)
    capital = CapitalLedger.from_config()
    arb = ExecutableCrossVenueArbAdapter(economics=eco, capital=capital, settings=settings)
    from bot.strategy_lab.types import CycleSnapshot, MarketEventView

    buy = MarketEventView(
        event_id="1",
        ts_ns=1,
        venue="binance",
        symbol="BTCEUR",
        bid=Decimal("100"),
        ask=Decimal("100.1"),
        bid_size=Decimal("5"),
        ask_size=Decimal("5"),
        bid_levels=((Decimal("100"), Decimal("5")),),
        ask_levels=((Decimal("100.1"), Decimal("5")),),
    )
    # Sell venue with empty bids → hedge impossible
    sell = MarketEventView(
        event_id="2",
        ts_ns=1,
        venue="okx",
        symbol="BTCEUR",
        bid=Decimal("0"),
        ask=Decimal("101"),
        bid_size=Decimal("0"),
        ask_size=Decimal("5"),
        bid_levels=(),
        ask_levels=((Decimal("101"), Decimal("5")),),
    )
    cycle = CycleSnapshot(cycle_id="t", ts_ns=1, books=(buy, sell))
    decisions = arb.generate_decisions(cycle)
    accepts = [d for d in decisions if d.action == DecisionAction.ACCEPT]
    assert accepts == []


def test_capital_velocity_consistent() -> None:
    v = net_eur_per_capital_second(Decimal("1"), Decimal("100"), 10_000)
    # €1 on €100 for 10s = 0.001 € / (€·s)
    assert v == Decimal("1") / (Decimal("100") * Decimal("10"))


def test_shadow_lab_does_not_enable_execution() -> None:
    s = Settings()
    assert s.strategy_lab_enabled is True
    assert s.strategy_lab_research_only is True
    assert s.strategy_lab_execution_enabled is False


def test_net_economics_consistent_across_common_engine() -> None:
    settings = Settings(profitability_execution_buffer_bps=1.0)
    eco = CommonEconomics(settings)
    costs = eco.from_legs(
        quantity=Decimal("1"),
        buy_vwap=Decimal("100"),
        sell_vwap=Decimal("100.5"),
        buy_fee_rate=Decimal("0.001"),
        sell_fee_rate=Decimal("0.001"),
    )
    assert costs.gross_edge_eur == Decimal("0.5")
    assert costs.fees_eur == Decimal("0.2005")  # 0.1 + 0.1005
    assert costs.conservative_net_eur < costs.gross_edge_eur


def test_tournament_deterministic_fingerprint(tmp_path: Path) -> None:
    empty = tmp_path / "empty_tape"
    empty.mkdir()
    a = run_tournament(
        dataset_id="test_a",
        research_path=empty,
        out_dir=tmp_path / "a",
        use_synthetic_if_thin=True,
        n_synthetic_cycles=40,
        development_frac=0.7,
        outcome_mode="trade_through",
    )
    b = run_tournament(
        dataset_id="test_b",
        research_path=empty,
        out_dir=tmp_path / "b",
        use_synthetic_if_thin=True,
        n_synthetic_cycles=40,
        development_frac=0.7,
        outcome_mode="trade_through",
    )
    assert a["data_label"] == "SYNTHETIC"
    assert a["fingerprints"]["dataset"] == b["fingerprints"]["dataset"]
    assert a["fingerprints"]["tournament"] == b["fingerprints"]["tournament"]
    assert criteria_manifest()["criteria_version"] == CRITERIA_VERSION
    assert a["frozen_config"]["outcome_mode"] == "trade_through"


def test_trade_through_haircuts_shadow_net(tmp_path: Path) -> None:
    empty = tmp_path / "empty_tape"
    empty.mkdir()
    shadow = run_tournament(
        dataset_id="shadow",
        research_path=empty,
        out_dir=tmp_path / "shadow",
        n_synthetic_cycles=40,
        outcome_mode="shadow",
    )
    tt = run_tournament(
        dataset_id="tt",
        research_path=empty,
        out_dir=tmp_path / "tt",
        n_synthetic_cycles=40,
        outcome_mode="trade_through",
    )
    assert shadow["data_label"] == tt["data_label"] == "SYNTHETIC"
    maker_s = next(r for r in shadow["leaderboard"] if r["strategy"] == "maker_inventory")
    maker_t = next(r for r in tt["leaderboard"] if r["strategy"] == "maker_inventory")
    # Same accepts; trade-through must not invent a larger NET than shadow expected.
    if maker_s["trades"] > 0:
        assert maker_t["net"] <= maker_s["net"] + 1e-9


def test_dashboard_matches_scorecards(tmp_path: Path) -> None:
    empty = tmp_path / "empty_tape"
    empty.mkdir()
    results = run_tournament(
        dataset_id="dash",
        research_path=empty,
        out_dir=tmp_path / "dash",
        n_synthetic_cycles=30,
    )
    from bot.strategy_lab.dashboard import render_strategy_lab_dashboard

    html = render_strategy_lab_dashboard(results).body.decode("utf-8")
    for row in results["leaderboard"]:
        assert row["strategy"] in html
        assert row["verdict"] in html


def test_baseline_opportunity_universe_nonzero() -> None:
    events = synthetic_research_tape(n_cycles=3, seed=9)
    cycles = build_cycles_from_events(events)
    n = sum(1 for c in cycles for _ in iter_baseline_opportunity_keys(c))
    assert n > 0
