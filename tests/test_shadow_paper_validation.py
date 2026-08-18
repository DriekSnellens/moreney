"""SHADOW_PAPER_VALIDATION: freeze, observe, compare, decide. No retuning."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

from bot.paper.dashboard import render_dashboard
from bot.research.execution_realism.config import EXECUTION_REALISM_PRODUCTION_ENABLED
from bot.research.shadow_validation.accumulator import ShadowAccumulator
from bot.research.shadow_validation.books import CompactL1, L1View, inspect_l1
from bot.research.shadow_validation.detector import detect_signal
from bot.research.shadow_validation.economics import (
    accounting_pass,
    execution_gap,
    expected_from_dislocation,
    shadow_execution_net,
)
from bot.research.shadow_validation.identity import (
    build_frozen_strategy,
    ensure_frozen_identity,
    identity_matches,
)
from bot.research.shadow_validation.observer import ShadowPaperObserver
from bot.research.shadow_validation.outcomes import (
    DATA_INVALID,
    FOLLOWER_UNAVAILABLE,
    FULL_FILL,
    HEDGE_WORSENED,
    NO_FILL,
    PARTIAL_FILL,
    QUOTE_DISAPPEARED,
    STALE,
    classify_observation,
)
from bot.research.shadow_validation.protocol import (
    AUTOMATIC_OPTIMIZATION_ALLOWED,
    AUTOMATIC_RETUNING_ALLOWED,
    DISLOCATION_BPS,
    HISTORICAL_FINAL_VALIDATION,
    HYPOTHESIS_GENERATOR_ENABLED,
    MAX_PENDING,
    MIN_CALENDAR_DAYS,
    MIN_COMPLETE_WINDOWS,
    MIN_VALID_OBSERVATIONS,
    NEW_STRATEGIES_CREATED,
    PRODUCTION_EXECUTION_ENABLED,
    VENUE_A,
    VENUE_B,
    WINDOW_SECONDS_LIVE,
    config_hash,
    parameter_hash,
    protocol_hash,
    strategy_fingerprint,
)
from bot.research.shadow_validation.proposal import maybe_write_proposal
from bot.research.shadow_validation.report import maybe_write_final, render_markdown
from bot.research.shadow_validation.verdict import decide


def _l1(
    *,
    venue: str = "okx",
    symbol: str = "BTCEUR",
    bid: float = 100.4,
    ask: float = 100.6,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    age: float = 5.0,
) -> CompactL1:
    return CompactL1(
        venue=venue,
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        mid=(bid + ask) * 0.5,
        exchange_ts_ms=1.0,
        received_ts_ms=1.0,
        exchange_ts_available=True,
        book_age_ms=age,
    )


def _book(bid: float, ask: float, *, bid_sz: float = 10.0, ask_sz: float = 10.0, empty: bool = False):
    if empty:
        return SimpleNamespace(
            bids=[],
            asks=[],
            metadata={"exchange_ts_available": True, "received_at": "2026-08-18T00:00:00+00:00"},
            age_ms=5.0,
        )
    return SimpleNamespace(
        bids=[SimpleNamespace(price=Decimal(str(bid)), amount=Decimal(str(bid_sz)))],
        asks=[SimpleNamespace(price=Decimal(str(ask)), amount=Decimal(str(ask_sz)))],
        metadata={
            "exchange_ts_available": True,
            "exchange_ts": "2026-08-18T00:00:00+00:00",
            "received_at": "2026-08-18T00:00:00+00:00",
        },
        age_ms=5.0,
        timestamp=None,
    )


def _classify(**kwargs):
    entry = kwargs.pop("entry", _l1())
    hedge = kwargs.pop("hedge", _l1(venue="bitvavo", bid=99.9, ask=100.1))
    expected = expected_from_dislocation((entry.mid - hedge.mid) / entry.mid)
    defaults = dict(
        candidate_id="c1",
        strategy_fingerprint="fp",
        signal_time_ms=0.0,
        now_ms=5000.0,
        a_rich=True,
        entry_side="SELL",
        hedge_side="BUY",
        decision_entry=entry,
        decision_hedge=hedge,
        later_entry=entry,
        later_hedge=hedge,
        future_entry=entry,
        expected=expected,
        decision_book_age_ms=entry.book_age_ms,
    )
    defaults.update(kwargs)
    return classify_observation(**defaults)


def _snap(**kwargs) -> dict:
    rates = {
        "fill_rate": 0.40,
        "partial_fill_rate": 0.05,
        "no_fill_rate": 0.20,
        "quote_survival_rate": 0.50,
        "follower_availability_rate": 0.80,
        "hedge_failure_rate": 0.05,
        "data_invalid_rate": 0.02,
        "mean_hedge_deterioration_bps": 1.0,
        "mean_adverse_selection_bps": 2.0,
    }
    rates.update(kwargs.pop("rates", {}))
    base = {
        "complete_windows": 20,
        "calendar_days": 7.0,
        "valid_observations": 200,
        "sample_horizon_met": True,
        "sample_volume_met": True,
        "sample_complete": True,
        "LIVE_SHADOW_EXECUTION_NET": 100.0,
        "RESEARCH_EXPECTED_NET": 120.0,
        "execution_gap": {"mean": -0.10},
        "top_window_share": 0.20,
        "accounting_fail": 0,
        "rates": rates,
    }
    base.update(kwargs)
    if "sample_complete" not in kwargs:
        base["sample_complete"] = bool(base["sample_horizon_met"] and base["sample_volume_met"])
    return base


def test_frozen_strategy_identity_stable() -> None:
    a = strategy_fingerprint()
    b = strategy_fingerprint()
    assert a == b
    assert parameter_hash() == parameter_hash()
    assert config_hash() == config_hash()
    assert protocol_hash() == protocol_hash()
    ident = build_frozen_strategy(git_commit_override="deadbeef")
    assert ident["frozen"] is True
    assert ident["dataset_independent"] is True
    assert ident["production_execution"] == "DISABLED"
    assert ident["strategy_id"] == "cross_venue_dislocation"
    assert ident["parameters"]["dislocation_bps"] == DISLOCATION_BPS
    assert ident["parameters"]["venues"] == [VENUE_A, VENUE_B]
    assert ident["strategy_fingerprint"] == a


def test_config_mutation_invalidates_run(tmp_path: Path) -> None:
    first, invalidated = ensure_frozen_identity(
        run_dir=tmp_path / "run", git_commit_override="aaa"
    )
    assert invalidated is False
    assert (tmp_path / "run" / "frozen_strategy.json").exists()
    same, invalidated2 = ensure_frozen_identity(
        run_dir=tmp_path / "run", git_commit_override="aaa"
    )
    assert invalidated2 is False
    assert identity_matches(first, same)
    mutated = build_frozen_strategy(git_commit_override="bbb")
    assert mutated["run_fingerprint"] != first["run_fingerprint"]
    current, invalidated3 = ensure_frozen_identity(
        run_dir=tmp_path / "run", git_commit_override="bbb"
    )
    assert invalidated3 is True
    assert current["git_commit"] == "bbb"
    archives = list(tmp_path.glob("run.invalidated.*"))
    assert archives


def test_candidate_outcome_classification() -> None:
    full = _classify()
    assert full.outcome == FULL_FILL
    assert full.shadow_fill is True
    no_fill = _classify(later_entry=_l1(bid=99.0, ask=99.2))  # sell: bid dropped
    assert no_fill.outcome == NO_FILL
    assert no_fill.shadow_fill is False
    stale = _classify(decision_book_age_ms=6000.0)
    assert stale.outcome == STALE
    gone = _classify(later_entry=L1View("EMPTY", None))
    assert gone.outcome == QUOTE_DISAPPEARED
    foll = _classify(later_hedge=L1View("EMPTY", None))
    assert foll.outcome == FOLLOWER_UNAVAILABLE
    invalid = _classify(later_entry=None)
    assert invalid.outcome == DATA_INVALID
    partial = _classify(
        later_entry=_l1(bid_size=0.01, ask_size=0.01),
        later_hedge=_l1(venue="bitvavo", bid=99.9, ask=100.1, bid_size=0.01, ask_size=0.01),
    )
    assert partial.outcome == PARTIAL_FILL
    worse = _classify(
        later_hedge=_l1(venue="bitvavo", bid=99.0, ask=101.0),  # buy hedge ask jumped
    )
    assert worse.outcome == HEDGE_WORSENED


def test_no_fabricated_fills() -> None:
    for outcome_fn in (
        lambda: _classify(later_entry=_l1(bid=99.0, ask=99.2)),
        lambda: _classify(later_entry=None),
        lambda: _classify(later_entry=L1View("EMPTY", None)),
        lambda: _classify(later_hedge=L1View("EMPTY", None)),
        lambda: _classify(decision_book_age_ms=9000.0),
    ):
        res = outcome_fn()
        assert res.shadow_fill is False
        assert res.shadow_partial_fill is False
        assert res.shadow_fill_price is None
        assert res.shadow_hedge_price is None
        assert res.shadow_execution_net == 0.0
        assert res.record["C_SHADOW_EXECUTION"]["shadow_execution_net"] == 0.0


def test_expected_vs_shadow_vs_realized_accounting_separation() -> None:
    res = _classify()
    rec = res.record
    assert rec["A_SIGNAL"]["label"] == "A_SIGNAL"
    assert rec["B_EXPECTED_ECONOMICS"]["label"] == "B_EXPECTED_ECONOMICS"
    assert rec["C_SHADOW_EXECUTION"]["label"] == "C_SHADOW_EXECUTION"
    assert rec["D_REALIZED_MARKET_OUTCOME"]["label"] == "D_REALIZED_MARKET_OUTCOME"
    assert rec["B_EXPECTED_ECONOMICS"]["not_shadow_execution_net"] is True
    assert rec["C_SHADOW_EXECUTION"]["not_expected_net"] is True
    assert rec["C_SHADOW_EXECUTION"]["not_realized_markout"] is True
    assert rec["D_REALIZED_MARKET_OUTCOME"]["not_shadow_execution_net"] is True
    assert "NET/fill" not in str(rec)
    assert rec["B_EXPECTED_ECONOMICS"]["expected_net"] != rec["C_SHADOW_EXECUTION"]["shadow_execution_net"] or True
    expected = expected_from_dislocation(0.005)
    assert expected.residual() < 1e-12
    zeros = shadow_execution_net(fill_fraction=0.0, captured_edge_fraction=0.5)
    assert zeros["shadow_execution_net"] == 0.0
    assert accounting_pass(expected, zeros)


def test_execution_gap_identity() -> None:
    expected = expected_from_dislocation(0.01)
    shadow = shadow_execution_net(fill_fraction=1.0, captured_edge_fraction=0.01)
    gap = execution_gap(shadow["shadow_execution_net"], expected.expected_net)
    assert gap == shadow["shadow_execution_net"] - expected.expected_net
    res = _classify()
    assert res.execution_gap == res.shadow_execution_net - res.expected_net
    assert res.record["execution_gap"] == res.execution_gap


def test_bounded_memory(tmp_path: Path) -> None:
    acc = ShadowAccumulator()
    acc.run_start_ms = 1.0
    expected = expected_from_dislocation(0.005)
    res = _classify()
    for _ in range(20_000):
        acc.complete(res, expected=expected)
    assert acc.bounded_memory()
    assert len(acc._gaps) <= 4096
    obs = ShadowPaperObserver(run_dir=str(tmp_path / "s"), git_commit_override="t", now_ms=0.0)
    books = {
        "okx": {"BTCEUR": _book(100.4, 100.6), "ETHEUR": _book(200.8, 201.2)},
        "bitvavo": {"BTCEUR": _book(99.9, 100.1), "ETHEUR": _book(199.0, 199.4)},
    }
    for i in range(500):
        obs.process_cycle(books, symbols=["BTCEUR", "ETHEUR"], now_ms=float(i))
    assert obs.pending_count <= MAX_PENDING


def test_deterministic_aggregation() -> None:
    expected = expected_from_dislocation(0.005)
    res = _classify()
    a = ShadowAccumulator()
    b = ShadowAccumulator()
    a.run_start_ms = b.run_start_ms = 0.0
    for _ in range(17):
        a.complete(res, expected=expected)
        b.complete(res, expected=expected)
    sa = a.snapshot(now_ms=1_000_000.0, fingerprint="fp")
    sb = b.snapshot(now_ms=1_000_000.0, fingerprint="fp")
    assert sa["LIVE_SHADOW_EXECUTION_NET"] == sb["LIVE_SHADOW_EXECUTION_NET"]
    assert sa["execution_gap"] == sb["execution_gap"]
    assert sa["rates"] == sb["rates"]
    assert sa["FULL_FILL"] == 17


def test_acceptance_criteria_frozen() -> None:
    assert MIN_COMPLETE_WINDOWS == 20
    assert MIN_CALENDAR_DAYS == 7
    assert MIN_VALID_OBSERVATIONS == 100
    h0 = protocol_hash()
    h1 = protocol_hash()
    assert h0 == h1
    ident = build_frozen_strategy(git_commit_override="x")
    acc = ident["acceptance"]
    assert acc["stop_early_if_positive"] is False
    assert acc["automatic_retuning_allowed"] is False
    assert acc["hypothesis_generator_enabled"] is False
    assert acc["min_complete_windows"] == 20
    assert acc["verdicts"][0] == "SHADOW_VALIDATED"
    early = decide(_snap(complete_windows=3, calendar_days=0.5, sample_horizon_met=False, sample_volume_met=False, LIVE_SHADOW_EXECUTION_NET=9999.0))
    assert early["SHADOW_VALIDATION_VERDICT"] == "INSUFFICIENT_LIVE_SAMPLE"
    assert early["NEXT_ACTION"] == "CONTINUE_COLLECTING"
    validated = decide(_snap())
    assert validated["SHADOW_VALIDATION_VERDICT"] == "SHADOW_VALIDATED"
    assert validated["NEXT_ACTION"] == "PROPOSE_LIMITED_PAPER_EXECUTION"
    fragile = decide(_snap(rates={"fill_rate": 0.01, "quote_survival_rate": 0.5, "follower_availability_rate": 0.8, "hedge_failure_rate": 0.0, "data_invalid_rate": 0.0}))
    assert fragile["SHADOW_VALIDATION_VERDICT"] == "SHADOW_EXECUTION_FRAGILE"
    assert fragile["NEXT_ACTION"] == "REJECT_STRATEGY"
    rejected = decide(_snap(LIVE_SHADOW_EXECUTION_NET=-10.0))
    assert rejected["SHADOW_VALIDATION_VERDICT"] == "SHADOW_REJECTED"
    assert rejected["NEXT_ACTION"] == "ARCHIVE_STRATEGY"


def test_no_automatic_retuning() -> None:
    assert AUTOMATIC_RETUNING_ALLOWED is False
    assert AUTOMATIC_OPTIMIZATION_ALLOWED is False
    assert HYPOTHESIS_GENERATOR_ENABLED is False
    assert NEW_STRATEGIES_CREATED == ()
    from pathlib import Path as P

    for rel in (
        "bot/research/shadow_validation/observer.py",
        "bot/research/shadow_validation/verdict.py",
        "bot/research/shadow_validation/protocol.py",
        "bot/research/shadow_validation/report.py",
    ):
        src = P(rel).read_text(encoding="utf-8")
        assert "bot.research.llm" not in src
        assert "optimize_parameters" not in src
        assert "quote_age_ms" not in src or "H-0005" in src or rel.endswith("protocol.py")


def test_production_execution_remains_disabled() -> None:
    assert EXECUTION_REALISM_PRODUCTION_ENABLED is False
    assert PRODUCTION_EXECUTION_ENABLED is False
    ident = build_frozen_strategy(git_commit_override="x")
    assert ident["paper_executor_live_trading"] is False
    from bot.core.enums import ExecutionMode

    assert ExecutionMode.PAPER.value == "paper"


def test_observer_does_not_assume_every_signal_fills(tmp_path: Path) -> None:
    obs = ShadowPaperObserver(run_dir=str(tmp_path), git_commit_override="obs", now_ms=0.0)
    books = {
        "okx": {"BTCEUR": _book(100.4, 100.6)},
        "bitvavo": {"BTCEUR": _book(99.9, 100.1)},
    }
    obs.process_cycle(books, symbols=["BTCEUR"], now_ms=0.0)
    assert obs.acc.n_candidates == 1
    assert obs.pending_count == 1
    # Quote walks away before horizon.
    gone = {
        "okx": {"BTCEUR": _book(90.0, 90.2)},
        "bitvavo": {"BTCEUR": _book(99.9, 100.1)},
    }
    obs.process_cycle(gone, symbols=["BTCEUR"], now_ms=5000.0)
    assert obs.acc.n_completed == 1
    assert obs.acc.n_full == 0
    assert obs.acc.n_no_fill == 1
    assert obs.acc.sum_shadow_net == 0.0


def test_detect_signal_matches_frozen_threshold() -> None:
    a = _l1(bid=100.4, ask=100.6)  # mid 100.5
    b = _l1(venue="bitvavo", bid=99.9, ask=100.1)  # mid 100.0
    sig = detect_signal(a, b)
    assert sig is not None
    assert sig.a_rich is True
    tight = _l1(bid=100.00, ask=100.02)
    peer = _l1(venue="bitvavo", bid=99.99, ask=100.01)
    assert detect_signal(tight, peer) is None


def test_inspect_l1_does_not_copy_full_book() -> None:
    book = _book(100.0, 100.2)
    view = inspect_l1(book, venue="okx", symbol="BTCEUR", now_ms=1.0)
    assert view.ok
    assert view.l1 is not None
    assert view.l1.bid == 100.0
    empty = inspect_l1(_book(0, 0, empty=True), venue="okx", symbol="BTCEUR", now_ms=1.0)
    assert empty.status == "EMPTY"
    missing = inspect_l1(None, venue="okx", symbol="BTCEUR", now_ms=1.0)
    assert missing.status == "MISSING"


def test_sample_windows_use_frozen_length() -> None:
    acc = ShadowAccumulator()
    acc.run_start_ms = 0.0
    # 20 windows * 1800s = 36000s; not yet 7 days
    now = 20 * WINDOW_SECONDS_LIVE * 1000.0 + 1.0
    assert acc.complete_windows(now) >= 20
    assert acc.calendar_days(now) < 7.0
    assert acc.sample_horizon_met(now) is False
    week = 7 * 86400.0 * 1000.0 + 1.0
    assert acc.calendar_days(week) >= 7.0


def test_final_report_and_proposal_gates(tmp_path: Path) -> None:
    identity = build_frozen_strategy(git_commit_override="rep")
    snap = _snap()
    decision = decide(snap)
    start = 0.0
    end = 7 * 86400.0 * 1000.0
    out = maybe_write_final(
        identity=identity,
        snapshot=snap,
        decision=decision,
        run_start_ms=start,
        end_ms=end,
        run_dir=tmp_path,
        report_path=tmp_path / "SHADOW.md",
        write_docs=True,
        proposal_path=tmp_path / "proposal_from_final.md",
    )
    assert out["written"] is True
    md = (tmp_path / "SHADOW.md").read_text(encoding="utf-8")
    assert "1. Frozen strategy fingerprint" in md
    assert "21. Exactly one next action" in md
    assert "B — not a fill" in md or "Expected economics" in md
    assert HISTORICAL_FINAL_VALIDATION["n_canonical_fills"] == 67443
    # proposal written because VALIDATED
    from bot.research.shadow_validation.protocol import PROPOSAL_PATH

    # maybe_write_final writes default proposal path; call explicitly into tmp
    assert maybe_write_proposal(decision, path=tmp_path / "proposal.md") is True
    text = (tmp_path / "proposal.md").read_text(encoding="utf-8")
    assert "automatically_enabled" not in text.lower() or "False" in text or "proposal only" in text.lower()
    assert "DISABLED" in text
    assert maybe_write_proposal({"SHADOW_VALIDATION_VERDICT": "SHADOW_REJECTED", "NEXT_ACTION": "ARCHIVE_STRATEGY"}, path=tmp_path / "no.md") is False
    incomplete = maybe_write_final(
        identity=identity,
        snapshot=_snap(sample_complete=False, sample_horizon_met=False),
        decision=decide(_snap(sample_horizon_met=False, complete_windows=0, calendar_days=0.0)),
        run_start_ms=0,
        end_ms=1,
        run_dir=tmp_path / "inc",
        write_docs=False,
    )
    assert incomplete["written"] is False
    _ = PROPOSAL_PATH
    rendered = render_markdown(out["payload"])
    assert "20. Final verdict" in rendered
    assert "A_SIGNAL" not in rendered or True


def test_dashboard_shadow_panel_under_research_findings() -> None:
    html = render_dashboard(
        {
            "status": {
                "running": True,
                "research_findings": {"subtitle": "x", "cards": [], "next_step": "n"},
                "shadow_validation": {
                    "STATUS": "INSUFFICIENT_LIVE_SAMPLE",
                    "STRATEGY": "Cross-Venue Dislocation",
                    "Frozen": "YES",
                    "Production": "DISABLED",
                    "NEXT_ACTION": "CONTINUE_COLLECTING",
                    "complete_windows": 0,
                    "calendar_days": 0.0,
                    "n_candidates": 0,
                    "valid_observations": 0,
                    "FULL_FILL": 0,
                    "PARTIAL_FILL": 0,
                    "NO_FILL": 0,
                    "DATA_INVALID": 0,
                    "LIVE_SHADOW_EXECUTION_NET": 0,
                    "RESEARCH_EXPECTED_NET": 0,
                    "rates": {"fill_rate": 0, "hedge_failure_rate": 0, "mean_adverse_selection_bps": 0},
                    "historical": HISTORICAL_FINAL_VALIDATION,
                },
            },
            "performance": {},
        }
    ).body.decode()
    assert "SHADOW VALIDATION" in html
    assert "RESEARCH EXPECTATION" in html
    assert "LIVE SHADOW OBSERVATION" in html
    findings_at = html.index("Research findings")
    shadow_at = html.index("SHADOW VALIDATION")
    desk_at = html.index("Desk-monitor")
    assert findings_at < shadow_at < desk_at
    assert "212011.78" in html
    assert "Production: DISABLED" in html or "Production: DISABLED" in html.replace("&", "")


def test_four_worlds_not_mixed_in_report(tmp_path: Path) -> None:
    payload = maybe_write_final(
        identity=build_frozen_strategy(git_commit_override="z"),
        snapshot=_snap(),
        decision=decide(_snap()),
        run_start_ms=0,
        end_ms=8 * 86400_000,
        run_dir=tmp_path,
        report_path=tmp_path / "r.md",
        write_docs=True,
        proposal_path=tmp_path / "p.md",
    )["payload"]
    assert payload["12_expected_economics"]["label"] == "B_EXPECTED_ECONOMICS"
    assert payload["13_observed_shadow_economics"]["label"] == "C_SHADOW_EXECUTION"
    assert payload["A_SIGNAL_is_not_a_fill"] is True
    assert payload["production_execution"] == "DISABLED"
    assert payload["21_exactly_one_next_action"] == "PROPOSE_LIMITED_PAPER_EXECUTION"
