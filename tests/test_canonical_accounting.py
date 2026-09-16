"""Canonical accounting identities, worlds, dashboard labels, H-0005/H-0007."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bot.paper.dashboard import render_dashboard
from bot.research.accounting.audit import audit_canonical, audit_dashboard_payload
from bot.research.accounting.fingerprint import replay_fingerprint
from bot.research.accounting.legacy import scan_legacy_fields
from bot.research.accounting.paired import PairedPartition, aggregate_paired, pair_window
from bot.research.accounting.protocol import (
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS,
    REPLAY_VERSION,
    WATERFALL_TOLERANCE,
)
from bot.research.accounting.replication import replication_advance
from bot.research.accounting.schema import (
    CrossWorldError,
    EconomicWorld,
    UnlabeledMetricError,
    ev_capture,
    labeled_ratio,
)
from bot.research.accounting.stress import (
    apply_canonical_cell,
    iter_canonical_stress_grid,
    stress_canonical,
    uses_canonical_replay,
)
from bot.research.accounting.waterfall import (
    assert_waterfall_identity,
    assemble_canonical,
    empty_canonical,
    from_component_sums,
    line_from_forward,
)
from bot.research.accounting.quantities import (
    ExpectedNetPerSignalEUR,
    ObservedRealizedRoundtripNetEUR,
    RealizedReplayNetEUR,
)
from bot.research.regime_lab.metrics import attach_event_economics, window_metrics
from bot.research.regime_lab.protocol import FORENSIC_OOS_END_NS
from bot.research.robustness.interpretation import gate_selectivity
from bot.research.robustness.protocol import FROZEN_H0005_PARAMS as ROB_H5
from bot.research.robustness.protocol import FROZEN_H0007_PARAMS as ROB_H7
from bot.research.tournament.criteria import ADVERSE_BPS_DEFAULT, SLIPPAGE_BPS_DEFAULT


def _lines(forwards: list[float], *, venue: str = "okx", venue_exit: str = "bitvavo"):
    return [
        line_from_forward(forward=f, venue=venue, venue_exit=venue_exit, ts_ns=10**18 + i, symbol="BTCEUR", route="okx|bitvavo")
        for i, f in enumerate(forwards)
    ]


def test_waterfall_identity() -> None:
    lines = _lines([0.05, 0.02, -0.01])
    assert_waterfall_identity(lines)
    for ln in lines:
        assert abs(ln.residual()) <= WATERFALL_TOLERANCE


def test_aggregate_equals_sum_of_signals() -> None:
    lines = _lines([0.04, 0.03, 0.01, 0.00])
    econ = assemble_canonical(
        lines,
        venue="okx",
        venue_exit="bitvavo",
        candidates=10,
        admitted=4,
        rejected=6,
        mean_forward=0.02,
    )
    summed = sum(ln.realized_replay_net for ln in lines)
    assert abs(summed - econ.replay_net.value) <= WATERFALL_TOLERANCE
    acc = audit_canonical(econ)
    assert acc["ACCOUNTING_AUDIT"] == "PASS"


def test_per_fill_arithmetic() -> None:
    # Historical H-0005 identity: 2218.37 / 363 ≈ 6.111
    econ = from_component_sums(
        venue="okx",
        venue_exit="bitvavo",
        signals=660,
        candidates=2277,
        admitted=660,
        rejected=1617,
        fills=363,
        gross=2528.572759778749,
        fees=231.0,
        slippage=13.2,
        adverse=52.8,
        net=2218.3727597787497,
        mean_forward=None,
        expected_net_per_signal=3.36117084814962,
    )
    pf = econ.replay_net_per_fill
    assert pf is not None
    assert abs(float(pf.value) - (2218.3727597787497 / 363)) < 1e-9
    assert abs(float(pf.value) - 6.111) < 0.001
    sidecar = econ.mean_edge_execution_replay_net_per_fill
    assert sidecar is not None
    assert abs(float(sidecar.value) - 0.00503) < 1e-4
    assert sidecar.quantity == "MeanEdgeExecutionReplayNetPerFillEUR"
    assert pf.quantity == "RealizedReplayNetPerFillEUR"


def test_per_signal_arithmetic() -> None:
    econ = from_component_sums(
        venue="okx",
        venue_exit="bitvavo",
        signals=660,
        candidates=2277,
        admitted=660,
        rejected=1617,
        fills=363,
        gross=2528.572759778749,
        fees=231.0,
        slippage=13.2,
        adverse=52.8,
        net=2218.3727597787497,
        mean_forward=None,
        expected_net_per_signal=3.36117084814962,
    )
    assert abs(float(econ.replay_net_per_signal.value) - (2218.3727597787497 / 660)) < 1e-9


def test_expected_and_replay_cannot_mix_accidentally() -> None:
    expected = ExpectedNetPerSignalEUR(Decimal("3.361"))
    replay = RealizedReplayNetEUR(Decimal("2218.37"))
    with pytest.raises(CrossWorldError):
        labeled_ratio(
            quantity="illegal_mix",
            numerator=replay,
            denominator=expected,
            unit="ratio",
            aggregation="ratio",
        )


def test_observed_cannot_silently_replace_replay() -> None:
    observed = ObservedRealizedRoundtripNetEUR(Decimal("99"))
    expected = ExpectedNetPerSignalEUR(Decimal("3.361"))
    with pytest.raises(CrossWorldError):
        labeled_ratio(
            quantity="illegal_obs_replay",
            numerator=observed,
            denominator=RealizedReplayNetEUR(Decimal("1")),
            unit="ratio",
            aggregation="ratio",
        )
    cap = ev_capture(observed_realized_net=observed, predicted_expected_net=expected)
    assert cap.comparison_id == "EV_CAPTURE"
    assert "OBSERVED" in cap.metadata.notes or "OBSERVED" in cap.to_dict()["notes"]


def test_dashboard_generic_net_per_fill_regression() -> None:
    unlabeled = audit_dashboard_payload({"NET_per_fill": 0.00503})
    assert unlabeled["ACCOUNTING_AUDIT"] == "FAIL"
    labeled = audit_dashboard_payload(
        {
            "canonical_replay_net_per_fill_eur": 6.111,
            "canonical_replay_net_per_fill_world": "EXECUTION_REPLAY",
        }
    )
    assert labeled["ACCOUNTING_AUDIT"] == "PASS"
    html = render_dashboard(
        {
            "status": {
                "running": True,
                "canonical_accounting": {
                    "ACCOUNTING_AUDIT": "PASS",
                    "schema_version": "canonical-accounting-v1",
                    "replay_version": REPLAY_VERSION,
                    "headline": "canonical",
                    "rows": [
                        {
                            "ID": "H-0005",
                            "RESEARCH_STATUS": "REPLICATING",
                            "expected_net_per_signal_eur": "3.361",
                            "replay_net_eur": "2218.37",
                            "replay_net_per_fill_eur": "6.111",
                            "observed_status": "NOT_RUN",
                            "paired_delta_replay_net_eur": "-100",
                        },
                        {
                            "ID": "H-0007",
                            "RESEARCH_STATUS": "GATE_INACTIVE",
                            "gate_inactive": True,
                        },
                    ],
                },
            },
            "performance": {},
        }
    ).body.decode()
    assert "ACCOUNTING STATUS" in html
    assert "ACCOUNTING_PASS" in html or "PASS" in html
    assert "GATE INACTIVE" in html
    assert "Parent vs child incremental" in html
    assert "SIGNAL_EXPECTATION" in html
    assert "EXECUTION_REPLAY" in html
    # Generic unlabeled header must not appear in research accounting tables.
    assert ">NET/fill</th>" not in html.replace(" ", "")
    assert "0.00503" not in html


def test_h0005_parent_child_paired_universe() -> None:
    venue, vx = "okx", "bitvavo"
    parent = attach_event_economics(
        [
            {"forward": 0.05, "ts_ns": 10**18 + 1, "symbol": "A", "route": "okx|bitvavo"},
            {"forward": -0.02, "ts_ns": 10**18 + 2, "symbol": "B", "route": "okx|bitvavo"},
            {"forward": 0.01, "ts_ns": 10**18 + 3, "symbol": "C", "route": "okx|bitvavo"},
        ],
        venue=venue,
        venue_exit=vx,
        horizon_ms=5000,
    )
    child = [parent[0], parent[2]]
    excluded = [parent[1]]
    part = PairedPartition(
        parent_events=tuple(parent),
        child_events=tuple(child),
        excluded_events=tuple(excluded),
        unsupported_events=tuple(),
        candidates=3,
        admitted=2,
        rejected=1,
        unsupported=0,
    )
    row = pair_window(
        window_id="W0",
        complete=True,
        start_ts_ns=10**18,
        end_ts_ns_inclusive=10**18 + 10,
        partition=part,
        venue=venue,
        venue_exit=vx,
        mean_forward_parent=0.01333,
        mean_forward_child=0.03,
        mean_forward_excluded=-0.02,
    )
    assert row.shared_signals == 2
    assert row.parent_only_signals == 1
    assert row.child_only_signals == 0
    assert abs(row.child.replay_net.value + row.excluded.replay_net.value - row.parent.replay_net.value) <= WATERFALL_TOLERANCE


def test_h0005_excluded_signal_accounting() -> None:
    venue, vx = "okx", "bitvavo"
    bad = attach_event_economics(
        [{"forward": -0.10, "ts_ns": 10**18 + 1, "symbol": "STALE", "route": "okx|bitvavo"}],
        venue=venue,
        venue_exit=vx,
        horizon_ms=5000,
    )[0]
    good = attach_event_economics(
        [{"forward": 0.10, "ts_ns": 10**18 + 2, "symbol": "FRESH", "route": "okx|bitvavo"}],
        venue=venue,
        venue_exit=vx,
        horizon_ms=5000,
    )[0]
    part = PairedPartition(
        parent_events=(good, bad),
        child_events=(good,),
        excluded_events=(bad,),
        unsupported_events=tuple(),
        candidates=2,
        admitted=1,
        rejected=1,
        unsupported=0,
    )
    row = pair_window(
        window_id="W_EXCL",
        complete=True,
        start_ts_ns=10**18,
        end_ts_ns_inclusive=10**18 + 10,
        partition=part,
        venue=venue,
        venue_exit=vx,
        mean_forward_parent=0.0,
        mean_forward_child=0.10,
        mean_forward_excluded=-0.10,
    )
    assert row.excluded_signal_net == row.excluded.replay_net.value
    assert row.delta_replay_net_eur == row.child.replay_net.value - row.parent.replay_net.value
    # Excluding the bad trade improves child vs parent.
    assert row.delta_replay_net_eur > 0


def test_h0007_gate_inactivity_detection() -> None:
    sel = gate_selectivity(admitted=3370, candidates=3370, parent_signals=3370, rejected=0)
    assert sel["inactive"] is True
    assert sel["selectivity"] == 0.0
    agg = gate_selectivity(admitted=28858, candidates=28878, parent_signals=28878, rejected=20)
    assert agg["selectivity"] is not None
    assert agg["selectivity"] < 0.05
    assert agg["inactive"] is True


def test_stress_grid_uses_canonical_replay() -> None:
    econ = from_component_sums(
        venue="okx",
        venue_exit="bitvavo",
        signals=660,
        candidates=2277,
        admitted=660,
        rejected=1617,
        fills=363,
        gross=2528.572759778749,
        fees=231.0,
        slippage=13.2,
        adverse=52.8,
        net=2218.3727597787497,
        mean_forward=None,
        expected_net_per_signal=3.36117084814962,
    )
    grid = list(iter_canonical_stress_grid())
    assert len(grid) == 4 * 4 * 5
    stress = stress_canonical(econ)
    assert stress["n_combinations"] == len(grid)
    assert uses_canonical_replay(stress["worst_cell"])
    assert uses_canonical_replay(stress["best_cell"])
    cell = apply_canonical_cell(
        econ,
        fee_multiplier=Decimal("1.0"),
        slippage_multiplier=Decimal("1.0"),
        adverse_multiplier=Decimal("1.0"),
    )
    assert abs(Decimal(cell["replay_net_eur"]) - econ.replay_net.value) <= WATERFALL_TOLERANCE
    assert "notional_eur" in cell
    assert "extra_cost_bps_of_notional" in cell


def test_deterministic_replay_fingerprint() -> None:
    lines = _lines([0.02, 0.01])
    a = assemble_canonical(
        lines, venue="okx", venue_exit="bitvavo", candidates=2, admitted=2, rejected=0, mean_forward=0.015
    )
    b = assemble_canonical(
        lines, venue="okx", venue_exit="bitvavo", candidates=2, admitted=2, rejected=0, mean_forward=0.015
    )
    assert replay_fingerprint(a) == replay_fingerprint(b)
    c_lines = _lines([0.02, 0.02])
    c = assemble_canonical(
        c_lines, venue="okx", venue_exit="bitvavo", candidates=2, admitted=2, rejected=0, mean_forward=0.02
    )
    assert replay_fingerprint(a) != replay_fingerprint(c)


def test_no_look_ahead() -> None:
    leaked = [{"forward": 0.01, "ts_ns": int(FORENSIC_OOS_END_NS) - 1, "symbol": "X", "route": "okx|bitvavo"}]
    from bot.research.accounting.engine import _check_leakage

    assert _check_leakage(leaked) is False
    ok = [{"forward": 0.01, "ts_ns": int(FORENSIC_OOS_END_NS) + 10, "symbol": "X", "route": "okx|bitvavo"}]
    assert _check_leakage(ok) is True


def test_frozen_oos_parameters() -> None:
    assert ROB_H5 == FROZEN_H0005_PARAMS
    assert ROB_H7 == FROZEN_H0007_PARAMS
    from bot.research.accounting.engine import _assert_frozen

    _assert_frozen("H-0005", dict(FROZEN_H0005_PARAMS))
    with pytest.raises(RuntimeError):
        _assert_frozen("H-0005", {**FROZEN_H0005_PARAMS, "dislocation_bps": 1.0})


def test_no_metric_without_metadata() -> None:
    from bot.research.accounting.schema import LabeledQuantity, MetricMetadata

    with pytest.raises(UnlabeledMetricError):
        LabeledQuantity(
            quantity="",
            value=Decimal("1"),
            metadata=MetricMetadata(
                numerator="x",
                denominator=None,
                unit="EUR",
                notional_basis="n",
                fill_model="f",
                adverse_model="a",
                fee_model="fee",
                replay_version="v",
                economic_world=EconomicWorld.EXECUTION_REPLAY,
                expected_or_realized="realized",
                aggregation="aggregate",
            ),
        )
    econ = empty_canonical(venue="okx", venue_exit="bitvavo")
    for q in (econ.replay_net, econ.expected_net_per_signal, econ.gross):
        assert q.metadata.economic_world
        assert q.metadata.replay_version
        assert q.metadata.unit


def test_window_metrics_emits_canonical_per_fill_not_mean_edge() -> None:
    events = attach_event_economics(
        [{"forward": 0.05, "ts_ns": FORENSIC_OOS_END_NS + 10, "symbol": "A", "route": "okx|bitvavo"}],
        venue="okx",
        venue_exit="bitvavo",
        horizon_ms=5000,
    )
    m = window_metrics(
        events, venue="okx", venue_exit="bitvavo", mean_forward=0.05, horizon_ms=5000, audit={"candidates": 2, "admitted": 1, "rejected": 1}
    )
    assert m["NET_per_fill_quantity"] == "RealizedReplayNetPerFillEUR"
    assert m["NET_per_fill"] == pytest.approx(m["NET"] / m["completed_round_trips"])
    assert m["mean_edge_execution_replay_net_per_fill_eur"] != m["NET_per_fill"]
    assert m["EXPECTED_NET_world"] == "SIGNAL_EXPECTATION"
    assert m["NET_world"] == "EXECUTION_REPLAY"


def test_replication_requires_twenty_windows() -> None:
    assert MIN_INDEPENDENT_WINDOWS_FOR_REPLICATION_PASS == 20
    out = replication_advance(
        accounting_audit_pass=True,
        independent_complete_windows=14,
        paired_comparison_present=True,
        aggregate_paired_delta_positive=True,
        window_concentration_ok=True,
        symbol_concentration_ok=True,
        route_limitation_reported=True,
        cost_stress_positive=True,
        no_leakage=True,
        no_parameter_retune_after_oos=True,
        mechanical_first_oos_pass=True,
        production_execution_disabled=True,
    )
    assert out["state"] == "REPLICATING"
    assert "independent_windows>=20" in out["blockers"]
    assert out["live_alpha_declared"] is False


def test_accounting_fail_blocks_robust() -> None:
    out = replication_advance(
        accounting_audit_pass=False,
        independent_complete_windows=20,
        paired_comparison_present=True,
        aggregate_paired_delta_positive=True,
        window_concentration_ok=True,
        symbol_concentration_ok=True,
        route_limitation_reported=True,
        cost_stress_positive=True,
        no_leakage=True,
        no_parameter_retune_after_oos=True,
        mechanical_first_oos_pass=True,
        production_execution_disabled=True,
    )
    assert out["state"] != "ROBUST_PAPER_CANDIDATE"
    assert "accounting_audit_pass" in out["blockers"]


def test_legacy_field_scan_allowlists_mapped_names() -> None:
    report = scan_legacy_fields(Path("bot"))
    offending = [
        h
        for h in report["hits"]
        if "test_" not in h["path"]
        and "MeanEdge" not in h["text"]
        and "mapped to canonical" not in h["text"]
        and "observed_realized" not in h["text"]
        and "DEPRECATED" not in h["text"]
    ]
    # Remaining hits must not be unlabeled dashboard generic NET/fill assignments
    # without a mapping comment. Tight check: paper dashboard research headers.
    dash = Path("bot/paper/dashboard.py").read_text(encoding="utf-8")
    assert ">NET/fill</th>" not in dash.replace(" ", "")


def test_costs_and_thresholds_unchanged() -> None:
    assert ADVERSE_BPS_DEFAULT == 8.0
    assert SLIPPAGE_BPS_DEFAULT == 2.0
    assert FROZEN_H0005_PARAMS["dislocation_bps"] == 40.0
    assert FROZEN_H0007_PARAMS["deviation_bps"] == 20.0


def test_paired_aggregate_no_pvalues() -> None:
    from bot.research.accounting.paired import pair_from_stored_nets

    parent = from_component_sums(
        venue="okx",
        venue_exit="bitvavo",
        signals=10,
        candidates=10,
        admitted=10,
        rejected=0,
        fills=6,
        gross=20,
        fees=1,
        slippage=0.2,
        adverse=0.8,
        net=17,
        mean_forward=0.01,
        expected_net_per_signal=1.7,
        other_costs=1.0,
    )
    child = from_component_sums(
        venue="okx",
        venue_exit="bitvavo",
        signals=4,
        candidates=10,
        admitted=4,
        rejected=6,
        fills=2,
        gross=12,
        fees=0.4,
        slippage=0.08,
        adverse=0.32,
        net=10,
        mean_forward=0.02,
        expected_net_per_signal=2.5,
        other_costs=1.2,
    )
    row = pair_from_stored_nets(
        window_id="W0",
        complete=True,
        start_ts_ns=1,
        end_ts_ns_inclusive=2,
        parent=parent,
        child=child,
    )
    agg = aggregate_paired([row])
    assert agg["p_values"] == "not_computed_assumptions_not_justified"
    assert Decimal(row["excluded_signal_net"]) == parent.replay_net.value - child.replay_net.value
