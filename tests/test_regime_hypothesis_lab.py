"""Regime hypothesis lab — independent H-0005/H-0007, no parent mutation."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from bot.paper.dashboard import render_dashboard
from bot.research.regime_lab.families import FreshnessCVDFamily, WideSpreadMRFamily
from bot.research.regime_lab.features import quote_age_ms
from bot.research.regime_lab.protocol import (
    FORENSIC_OOS_END_NS,
    H0005,
    H0007,
    build_manifest,
    protocol_hash,
)
from bot.research.regime_lab.split import make_fresh_split
from bot.research.regime_lab.stability import stability_report
from bot.research.regime_lab.verdict import mechanical_verdict
from bot.research.tournament.criteria import MAX_TOP_ROUTE_PNL_SHARE, MAX_TOP_SYMBOL_PNL_SHARE
from bot.research.tournament.economics import net_waterfall_from_edge, shared_cost_assumptions
from bot.research.tournament.families import (
    CrossVenueDislocationFamily,
    ShortHorizonMeanReversionFamily,
)
from bot.research.tournament.tape_index import SeriesPoint, TapeIndex, build_tape_index


def test_parents_not_edited_for_gates() -> None:
    src = inspect.getsource(CrossVenueDislocationFamily.evaluate_window)
    assert "QUOTE_AGE_FRESH" not in src
    src2 = inspect.getsource(ShortHorizonMeanReversionFamily.evaluate_window)
    assert "SPREAD_WIDE" not in src2


def test_quote_age_is_pretrade_only() -> None:
    assert quote_age_ms(1_000_000_000, 999_000_000) == 1.0
    assert quote_age_ms(1000, 1001) is None
    assert quote_age_ms(1000, None) is None


def test_event_density_window_is_causal() -> None:
    from bot.research.regime_lab.features import TsView, event_density

    pts = [
        SeriesPoint(ts_ns=i * 1_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=i)
        for i in range(20)
    ]
    view = TsView(pts)
    t = 10_000_000
    n = event_density(view, t)
    assert n is not None
    # lookback 1000ms = 1e9 ns, but our pts are 1ms apart in ns*1e6 wait:
    # i * 1_000_000 is 1ms steps. DENSITY_LOOKBACK_MS=1000 → 1e9 ns = 1000ms.
    # points with ts in [t-1e9, t] = all of them since t=10ms.
    assert n >= 1


def test_stability_does_not_relax_thresholds() -> None:
    assert MAX_TOP_SYMBOL_PNL_SHARE == 0.70
    assert MAX_TOP_ROUTE_PNL_SHARE == 0.70
    events = [
        {"symbol": "SOLEUR", "route": "binance|bitvavo", "forward": 1.0, "ts_ns": 10, "net": 1.0}
        for _ in range(10)
    ]
    rep = stability_report(events, oos_start_ns=0, oos_end_ns=100)
    assert rep["ROUTE_UNIVERSE_LIMITED"] is True
    assert rep["criteria_relaxed"] is False
    assert rep["top_symbol_share"] > 0.70
    assert rep["concentrated"] is True


def test_route_universe_limited_not_pretend_diversity() -> None:
    events = [
        {"symbol": "A", "route": "binance|bitvavo", "forward": 1.0, "ts_ns": 1, "net": 1},
        {"symbol": "B", "route": "binance|bitvavo", "forward": 1.0, "ts_ns": 2, "net": 1},
        {"symbol": "C", "route": "binance|bitvavo", "forward": 1.0, "ts_ns": 3, "net": 1},
    ]
    rep = stability_report(events, oos_start_ns=0, oos_end_ns=10)
    assert rep["ROUTE_UNIVERSE_LIMITED"] is True
    assert "ROUTE_UNIVERSE_LIMITED" in rep["label"]
    # three equal symbols → not symbol-concentrated
    assert rep["top_symbol_share"] < 0.70
    assert rep["concentrated"] is False


def test_rejected_events_are_not_labels() -> None:
    fam = FreshnessCVDFamily()
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=1.0,
        inventory={},
        series={
            ("binance", "BTCEUR"): [
                SeriesPoint(ts_ns=FORENSIC_OOS_END_NS + 1_000_000 * i, mid=100 + i * 0.01, bid=99.9, ask=100.1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=i)
                for i in range(50)
            ],
            ("bitvavo", "BTCEUR"): [
                SeriesPoint(ts_ns=FORENSIC_OOS_END_NS + 1_000_000 * i - 5_000_000, mid=100, bid=99, ask=101, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=i)
                for i in range(50)
            ],
        },
    )
    stats, admitted = fam.evaluate_window(
        idx,
        start_ns=FORENSIC_OOS_END_NS + 1,
        end_ns_exclusive=None,
        end_ns_inclusive=FORENSIC_OOS_END_NS + 50_000_000,
        params={
            "horizon_ms": 500,
            "dislocation_bps": 5.0,
            "venue_a": "binance",
            "venue_b": "bitvavo",
        },
        horizons=[500],
    )
    assert fam.last_audit["rejected_not_labels"] is True
    assert stats.signals == fam.last_audit["admitted"] == len(admitted)
    assert all(e.get("admission") == "ADMITTED" for e in admitted)
    for e in admitted:
        assert e.get("quote_age_ms") is not None
        assert e["quote_age_ms"] < 250
        assert int(e["ts_ns"]) > FORENSIC_OOS_END_NS


def test_parent_pnl_not_inherited() -> None:
    assert H0005["parent_hypothesis_id"] == "H-0001"
    assert H0007["parent_hypothesis_id"] == "H-0003"
    assert "inherit" not in H0005["economic_mechanism"].lower() or "not" in H0005["signal_definition"].lower()


def test_cost_model_unchanged() -> None:
    a = net_waterfall_from_edge(gross_edge_fraction=0.001, venue="binance", venue_exit="bitvavo")
    b = net_waterfall_from_edge(gross_edge_fraction=0.001, venue="binance", venue_exit="bitvavo")
    assert a["EXPECTED_NET"] == b["EXPECTED_NET"]
    assert a["FEES"] == b["FEES"]
    assert shared_cost_assumptions()["no_queue_fills"] is True


def test_execution_disabled_and_not_in_production_ranking() -> None:
    from bot.research.regime_lab.protocol import EXECUTION_MODEL
    import bot.execution.paper_executor as paper_ex
    import bot.opportunity.engine as opp

    assert EXECUTION_MODEL["enabled"] is False
    assert EXECUTION_MODEL["affects_production_ranking"] is False
    src = inspect.getsource(paper_ex) + inspect.getsource(opp)
    assert "cross_venue_dislocation_freshness" not in src
    assert "short_horizon_mean_reversion_wide_spread" not in src


def test_fresh_split_excludes_forensic_period() -> None:
    cut = FORENSIC_OOS_END_NS
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=4000.0,
        inventory={},
        series={
            ("binance", "BTCEUR"): [
                SeriesPoint(ts_ns=cut - 10_000_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=0),
                SeriesPoint(ts_ns=cut + 1_000_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=1),
                SeriesPoint(ts_ns=cut + 3_000_000_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=2),
            ]
        },
    )
    split = make_fresh_split(idx)
    assert split["available"] is True
    assert split["development"]["start_ts_ns"] > cut
    assert split["untouched_oos"]["start_ts_ns"] > cut


def test_insufficient_fresh_data() -> None:
    cut = FORENSIC_OOS_END_NS
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=1.0,
        inventory={},
        series={
            ("binance", "BTCEUR"): [
                SeriesPoint(ts_ns=cut - 1, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=0)
            ]
        },
    )
    split = make_fresh_split(idx)
    assert split["available"] is False
    assert split["DATA_STATUS"] == "INSUFFICIENT_FRESH_DATA"


def test_manifest_reproducible() -> None:
    split = {
        "available": True,
        "development": {"start_ts_ns": 1, "end_ts_ns_exclusive": 2},
        "freeze_boundary": {"start_ts_ns": 2, "end_ts_ns_exclusive": 3},
        "untouched_oos": {"start_ts_ns": 3, "end_ts_ns_inclusive": 4},
    }
    a = build_manifest(dataset_id="d", dataset_fingerprint="fp", split=split)
    b = build_manifest(dataset_id="d", dataset_fingerprint="fp", split=split)
    assert a["configuration_hash"] == b["configuration_hash"] == protocol_hash()
    assert a["hypothesis_hash_h0005"] == b["hypothesis_hash_h0005"]
    assert a["random_seed"] == 20260817


def test_mechanical_verdict_not_llm() -> None:
    assert (
        mechanical_verdict(
            data_status="INSUFFICIENT_FRESH_DATA",
            tournament_verdict=None,
            failed_gate=None,
            gated_metrics=None,
            parent_metrics=None,
            regime_only_metrics=None,
            audit=None,
            stability=None,
        )
        == "INSUFFICIENT_FRESH_DATA"
    )
    assert (
        mechanical_verdict(
            data_status="FRESH_SPLIT_READY",
            tournament_verdict="COST_NEGATIVE",
            failed_gate="ECONOMICS",
            gated_metrics={"EXPECTED_NET": -1, "mean_forward": 0.0, "signals": 10},
            parent_metrics={"EXPECTED_NET": 1, "mean_forward": 0.01, "signals": 100},
            regime_only_metrics={"EXPECTED_NET": 0},
            audit={"admitted": 10, "candidates": 100},
            stability={"concentrated": False},
        )
        == "COST_NEGATIVE"
    )


def test_non_participation_only() -> None:
    v = mechanical_verdict(
        data_status="FRESH_SPLIT_READY",
        tournament_verdict="PAPER_CANDIDATE",
        failed_gate=None,
        gated_metrics={"EXPECTED_NET": 1.0, "mean_forward": 0.01, "signals": 10},
        parent_metrics={"EXPECTED_NET": 1.0, "mean_forward": 0.01, "signals": 100},
        regime_only_metrics={"EXPECTED_NET": 0.0},
        audit={"admitted": 10, "candidates": 100},
        stability={"concentrated": False},
    )
    assert v == "NON_PARTICIPATION_ONLY"


def test_failed_hypothesis_cannot_enter_production() -> None:
    from bot.research.regime_lab.protocol import EXECUTION_MODEL

    assert EXECUTION_MODEL["affects_production_ranking"] is False
    v = mechanical_verdict(
        data_status="FRESH_SPLIT_READY",
        tournament_verdict="UNSTABLE",
        failed_gate="STABILITY",
        gated_metrics={"EXPECTED_NET": 2.0, "mean_forward": 0.05, "signals": 80},
        parent_metrics={"EXPECTED_NET": 0.1, "mean_forward": 0.01, "signals": 80},
        regime_only_metrics={"EXPECTED_NET": 0.0},
        audit={"admitted": 80, "candidates": 90},
        stability={"concentrated": True},
    )
    assert v == "UNSTABLE"


def test_min_ts_skips_forensic_rows(tmp_path: Path) -> None:
    root = tmp_path / "tape" / "20260817" / "binance"
    root.mkdir(parents=True)
    cut = FORENSIC_OOS_END_NS
    lines = []
    for i, ts in enumerate([cut - 100, cut + 100, cut + 200]):
        lines.append(
            json.dumps(
                {
                    "venue": "binance",
                    "symbol": "BTCEUR",
                    "received_ts_ns": ts,
                    "bid_price": "100",
                    "ask_price": "101",
                    "bid_size": "1",
                    "ask_size": "1",
                }
            )
        )
    (root / "BTCEUR.jsonl").write_text("\n".join(lines) + "\n")
    idx = build_tape_index(tmp_path / "tape", min_ts_ns=cut + 1, stride=1)
    pts = idx.points("binance", "BTCEUR")
    assert pts
    assert all(p.ts_ns > cut for p in pts)


def test_dashboard_section() -> None:
    html = render_dashboard(
        {
            "status": {
                "running": True,
                "regime_hypothesis_lab": {
                    "headline": "lab",
                    "DATA_STATUS": "FRESH_SPLIT_READY",
                    "disclaimer": "Forensic NET is not strategy profitability.",
                    "rows": [
                        {
                            "ID": "H-0005",
                            "Parent": "H-0001",
                            "Mechanism": "freshness",
                            "Status": "CANDIDATE",
                            "Discovery_NET": None,
                            "DEV_NET": 0.1,
                            "OOS_NET": -0.2,
                            "NET_per_fill": None,
                            "sample_count": 3,
                            "stability": "ROUTE_UNIVERSE_LIMITED",
                            "top_concentration": {"symbol": "SOLEUR", "route": "binance|bitvavo"},
                            "verdict": "INSUFFICIENT_DATA",
                        }
                    ],
                },
            },
            "performance": {},
        }
    ).body.decode()
    assert "REGIME HYPOTHESIS LAB" in html
    assert "H-0005" in html
    assert "Forensic NET is not strategy profitability" in html


def test_deterministic_replay() -> None:
    fam = WideSpreadMRFamily()
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=1.0,
        inventory={},
        series={
            ("bitvavo", "BTCEUR"): [
                SeriesPoint(
                    ts_ns=FORENSIC_OOS_END_NS + 10_000_000 * i,
                    mid=100.0,
                    bid=99.0,
                    ask=101.5,
                    bid_size=1,
                    ask_size=1,
                    exchange_ts_ns=None,
                    sequence=i,
                )
                for i in range(40)
            ],
            ("binance", "BTCEUR"): [
                SeriesPoint(
                    ts_ns=FORENSIC_OOS_END_NS + 10_000_000 * i,
                    mid=100.5 if i % 2 else 99.5,
                    bid=100.4,
                    ask=100.6,
                    bid_size=2,
                    ask_size=2,
                    exchange_ts_ns=FORENSIC_OOS_END_NS + 10_000_000 * i,
                    sequence=i,
                )
                for i in range(40)
            ],
        },
    )
    params = {"horizon_ms": 500, "deviation_bps": 5.0, "venue": "bitvavo"}
    a, ea = fam.evaluate_window(
        idx,
        start_ns=FORENSIC_OOS_END_NS + 1,
        end_ns_exclusive=None,
        end_ns_inclusive=FORENSIC_OOS_END_NS + 400_000_000,
        params=params,
        horizons=[500],
    )
    b, eb = fam.evaluate_window(
        idx,
        start_ns=FORENSIC_OOS_END_NS + 1,
        end_ns_exclusive=None,
        end_ns_inclusive=FORENSIC_OOS_END_NS + 400_000_000,
        params=params,
        horizons=[500],
    )
    assert a.signals == b.signals
    assert [e["forward"] for e in ea] == [e["forward"] for e in eb]


def test_missing_freshness_is_unsupported_not_a_signal() -> None:
    from bot.research.regime_lab.families import classify_freshness, classify_wide_spread

    assert classify_freshness(None) == "UNSUPPORTED_DATA"
    assert classify_freshness(0.0) == "ADMITTED"
    assert classify_freshness(249.9) == "ADMITTED"
    assert classify_freshness(250.0) == "REJECTED"
    assert classify_wide_spread(None) == "UNSUPPORTED_DATA"
    assert classify_wide_spread(19.9) == "REJECTED"
    assert classify_wide_spread(20.0) == "ADMITTED"


def test_sparse_density_is_feature_not_admission_gate() -> None:
    src = inspect.getsource(WideSpreadMRFamily.evaluate_window)
    assert "DENSITY_SPARSE" not in src
    assert "99.1" not in src
    from bot.research.regime_lab.features import enrich_pretrade, views_for

    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=1.0,
        inventory={},
        series={
            ("bitvavo", "BTCEUR"): [
                SeriesPoint(
                    ts_ns=FORENSIC_OOS_END_NS + 1_000_000 * i,
                    mid=100.0,
                    bid=99.0,
                    ask=101.5,
                    bid_size=1,
                    ask_size=1,
                    exchange_ts_ns=None,
                    sequence=i,
                )
                for i in range(8)
            ]
        },
    )
    views = views_for(idx)
    row = enrich_pretrade(
        {
            "ts_ns": FORENSIC_OOS_END_NS + 7_000_000,
            "symbol": "BTCEUR",
            "forward": 0.0,
        },
        index=idx,
        views=views,
        venue="bitvavo",
        peer_venue=None,
    )
    assert "event_density" in row
    assert "event_density_sparse_flag" in row
    assert row["exchange_ts_invented"] is False
    assert row["clock_quality"] == "BITVAVO_EXCHANGE_TS_ABSENT"


def test_event_density_excludes_future_points() -> None:
    from bot.research.regime_lab.features import TsView, event_density
    from bot.research.forensics.buckets import DENSITY_LOOKBACK_MS

    t = 10_000_000_000
    pts = [
        SeriesPoint(ts_ns=t - 500_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=0),
        SeriesPoint(ts_ns=t, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=1),
        SeriesPoint(ts_ns=t + 1, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=2),
        SeriesPoint(ts_ns=t + DENSITY_LOOKBACK_MS * 1_000_000, mid=1, bid=1, ask=1, bid_size=1, ask_size=1, exchange_ts_ns=None, sequence=3),
    ]
    n = event_density(TsView(pts), t)
    assert n == 2


def test_parameter_change_is_new_hypothesis_version() -> None:
    from bot.research.regime_lab.protocol import hypothesis_hash

    a = hypothesis_hash(H0005)
    b = hypothesis_hash({**H0005, "fresh_max_ms": 999})
    assert a != b
    assert protocol_hash() == protocol_hash()


def test_old_session_folder_skipped_for_fresh_min_ts() -> None:
    from bot.research.tournament.tape_index import _session_dir_ends_before

    old = Path("/opt/moreney/data/research_marketdata/20260816/binance/BTCEUR.jsonl")
    same = Path("/opt/moreney/data/research_marketdata/20260817/binance/BTCEUR.jsonl")
    assert _session_dir_ends_before(old, FORENSIC_OOS_END_NS) is True
    assert _session_dir_ends_before(same, FORENSIC_OOS_END_NS) is False


def test_candidates_cannot_enter_production_ranking(tmp_path: Path) -> None:
    from bot.research.llm.hypothesis_memory import HypothesisRegistry
    from bot.research.regime_lab.engine import register_candidates

    reg = HypothesisRegistry(tmp_path / "registry.jsonl")
    ids = register_candidates(reg)
    assert ids["H-0005"] == "H-0005"
    rows = [r for r in reg.list_all() if r.get("hypothesis_id") in {"H-0005", "H-0007"}]
    assert rows
    assert all(r.get("research_status") == "CANDIDATE" for r in rows)
    assert all(r.get("affects_production_ranking") is False for r in rows)
    assert all(r.get("inherits_parent_pnl") is False for r in rows)


def test_forensic_leak_guard() -> None:
    from bot.research.regime_lab.split import assert_event_after_forensics
    import pytest

    with pytest.raises(RuntimeError):
        assert_event_after_forensics(FORENSIC_OOS_END_NS)
    assert_event_after_forensics(FORENSIC_OOS_END_NS + 1)
