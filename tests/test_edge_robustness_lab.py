"""Edge robustness lab — interpretation, accounting, stress, no retune."""

from __future__ import annotations

import inspect
from pathlib import Path

from bot.paper.dashboard import render_dashboard
from bot.research.robustness.accounting import audit_card
from bot.research.robustness.decision import research_decision
from bot.research.robustness.interpretation import gate_selectivity, interpretation_verdict
from bot.research.robustness.magnitude import magnitude
from bot.research.robustness.protocol import (
    FROZEN_H0005_PARAMS,
    FROZEN_H0007_PARAMS,
    HISTORICAL_MECHANICAL,
    MIN_GATE_SELECTIVITY,
    protocol_hash,
)
from bot.research.robustness.stress import break_even_frontier, iter_stress_grid, stress_matrix
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.criteria import ADVERSE_BPS_DEFAULT, SLIPPAGE_BPS_DEFAULT
from bot.research.tournament.tape_index import SeriesPoint, TapeIndex


def test_mechanical_verdict_frozen() -> None:
    assert HISTORICAL_MECHANICAL["H-0005"] == "OOS_PASS"
    assert HISTORICAL_MECHANICAL["H-0007"] == "OOS_PASS"


def test_gate_inactive_when_admitted_equals_parent() -> None:
    sel = gate_selectivity(admitted=3370, candidates=3370, parent_signals=3370, rejected=0)
    assert sel["inactive"] is True
    assert sel["selectivity"] == 0.0
    v = interpretation_verdict(
        mechanical="OOS_PASS",
        selectivity=sel,
        regime_diversity={"required": True, "both_states": False},
        edge_to_uncertainty=10.0,
        incremental_positive=False,
        independent_windows=1,
        independently_positive=True,
    )
    assert v == "GATE_INACTIVE"


def test_h0005_promising_when_selective_but_not_incremental() -> None:
    sel = gate_selectivity(admitted=660, candidates=2277, parent_signals=2187, rejected=1617)
    assert sel["inactive"] is False
    assert sel["selectivity"] > MIN_GATE_SELECTIVITY
    v = interpretation_verdict(
        mechanical="OOS_PASS",
        selectivity=sel,
        regime_diversity={"required": False, "both_states": True},
        edge_to_uncertainty=10.0,
        incremental_positive=False,
        independent_windows=1,
        independently_positive=True,
    )
    assert v == "PROMISING_BUT_UNCONFIRMED"


def test_accounting_fail_when_net_per_fill_not_sum() -> None:
    card = {
        "NET/fill": 0.00503,
        "SAMPLE_COUNT": 660,
        "OOS_RESULT": {
            "NET": 2218.37,
            "EXPECTED_NET": 3.361,
            "EXECUTION_NET": 1.8266,
            "signals": 660,
            "completed_round_trips": 363,
            "gross": 2528.57,
            "fees": 231.0,
            "slippage": 13.2,
            "adverse": 52.8,
        },
    }
    acc = audit_card(card)
    assert acc["ACCOUNTING_AUDIT"] == "FAIL"
    assert acc["units"]["NET"]["unit"] == "EUR"
    assert acc["units"]["NET_per_fill_from_replay"]["unit"]
    assert acc["published_matches_sum_NET_over_fills"] is False
    assert acc["published_matches_replay"] is True


def test_accounting_pass_when_net_per_fill_is_sum() -> None:
    card = {
        "NET/fill": 2218.37 / 363,
        "SAMPLE_COUNT": 660,
        "OOS_RESULT": {
            "NET": 2218.37,
            "EXPECTED_NET": 2218.37 / 660,
            "EXECUTION_NET": 1.0,
            "signals": 660,
            "completed_round_trips": 363,
        },
    }
    acc = audit_card(card)
    assert acc["ACCOUNTING_AUDIT"] == "PASS"


def test_break_even_uses_existing_model() -> None:
    be = break_even_frontier(expected_net=3.361, venue="okx", venue_exit="bitvavo")
    # 3.361 EUR / 100 EUR notional * 10000 = 336.1 bps
    assert abs(be["BREAK_EVEN_ADVERSE_BPS"]["value"] - 336.1) < 0.05
    assert be["BREAK_EVEN_SLIPPAGE_BPS"]["value"] == be["BREAK_EVEN_ADVERSE_BPS"]["value"]


def test_stress_grid_is_full_cartesian_not_cherrypicked() -> None:
    n = sum(1 for _ in iter_stress_grid())
    assert n == 4 * 4 * 5 * 4 * 5 * 4
    m = stress_matrix(expected_net=3.361, venue="okx", venue_exit="bitvavo", signals=660)
    assert m["n_combinations"] == n
    assert m["n_positive"] + m["n_negative"] + m["n_zero"] == n
    assert m["worst"]["EXECUTION_NET"] <= m["best"]["EXECUTION_NET"]


def test_production_costs_unchanged() -> None:
    assert ADVERSE_BPS_DEFAULT == 8.0
    assert SLIPPAGE_BPS_DEFAULT == 2.0
    src = inspect.getsource(__import__("bot.research.tournament.economics", fromlist=["net_waterfall_from_edge"]))
    assert "robustness" not in src


def test_no_new_strategy_families() -> None:
    from bot.research.tournament.families import CrossVenueDislocationFamily, ShortHorizonMeanReversionFamily
    from bot.research.regime_lab.families import FreshnessCVDFamily, WideSpreadMRFamily

    assert CrossVenueDislocationFamily.strategy_id == "cross_venue_dislocation"
    assert ShortHorizonMeanReversionFamily.strategy_id == "short_horizon_mean_reversion"
    assert FreshnessCVDFamily.strategy_id == "cross_venue_dislocation_freshness"
    assert WideSpreadMRFamily.strategy_id == "short_horizon_mean_reversion_wide_spread"
    assert FROZEN_H0005_PARAMS["horizon_ms"] == 5000
    assert FROZEN_H0007_PARAMS["deviation_bps"] == 20.0


def test_execution_stays_disabled() -> None:
    from bot.research.robustness.protocol import protocol_payload
    from bot.research.regime_lab.protocol import EXECUTION_MODEL

    assert protocol_payload()["execution_enabled"] is False
    assert EXECUTION_MODEL["enabled"] is False


def test_robust_blocked_without_all_gates() -> None:
    d = research_decision(
        accounting_pass=False,
        interpretation="PROMISING_BUT_UNCONFIRMED",
        independent_windows=5,
        window_nets=[10.0, 10.0, 10.0, 10.0, 10.0],
        survives_reasonable_stress=True,
        gate_selective=True,
        parent_comparison_available=True,
        production_loosened=False,
        model_uncertainty_too_high=False,
        regime_diversity_ok=True,
        required_regime_diversity=False,
    )
    assert d != "ROBUST_PAPER_CANDIDATE"
    assert d == "PROMISING_REPLICATION_REQUIRED"


def test_h0007_gate_inactive_collect_more_when_regimes_exist() -> None:
    d = research_decision(
        accounting_pass=False,
        interpretation="GATE_INACTIVE",
        independent_windows=3,
        window_nets=[1.0, 1.0, 1.0],
        survives_reasonable_stress=True,
        gate_selective=False,
        parent_comparison_available=True,
        production_loosened=False,
        model_uncertainty_too_high=False,
        regime_diversity_ok=True,
        required_regime_diversity=True,
    )
    assert d == "COLLECT_MORE_DATA"
    d = research_decision(
        accounting_pass=False,
        interpretation="GATE_INACTIVE",
        independent_windows=3,
        window_nets=[1.0, 1.0, 1.0],
        survives_reasonable_stress=True,
        gate_selective=False,
        parent_comparison_available=True,
        production_loosened=False,
        model_uncertainty_too_high=False,
        regime_diversity_ok=False,
        required_regime_diversity=True,
    )
    assert d == "INSUFFICIENT_REGIME_DIVERSITY"


def test_magnitude_ratio_defined() -> None:
    mag = magnitude(expected_net=3.361, venue="okx", venue_exit="bitvavo", mean_forward=0.038)
    assert mag["EDGE_TO_COST_RATIO"] > 1
    assert mag["EDGE_TO_MODEL_UNCERTAINTY_RATIO"] > 1
    assert mag["NET_per_fill_primary"]["unit"] == "EUR_per_signal"


def test_windows_do_not_merge() -> None:
    idx = TapeIndex(
        root="x",
        dataset_id="d",
        content_fingerprint="f",
        duration_seconds=10000.0,
        inventory={},
        series={
            ("okx", "BTCEUR"): [
                SeriesPoint(
                    ts_ns=1_786_977_290_774_087_936 + 1_000_000_000 * i,
                    mid=1,
                    bid=1,
                    ask=1,
                    bid_size=1,
                    ask_size=1,
                    exchange_ts_ns=None,
                    sequence=i,
                )
                for i in range(4000)
            ]
        },
    )
    plan = sequential_windows(idx)
    ids = [w["WINDOW_ID"] for w in plan["windows"]]
    assert ids[0] == "W0_FIRST_OOS"
    assert len(ids) == len(set(ids))
    assert plan["windows"][0]["kind"] == "historical_first_oos"


def test_protocol_hash_stable() -> None:
    assert protocol_hash() == protocol_hash()


def test_dashboard_section() -> None:
    html = render_dashboard(
        {
            "status": {
                "running": True,
                "edge_robustness_lab": {
                    "headline": "lab",
                    "ACCOUNTING_AUDIT": "FAIL",
                    "disclaimer": "Mechanical OOS_PASS unchanged.",
                    "rows": [
                        {
                            "ID": "H-0005",
                            "mechanical_verdict": "OOS_PASS",
                            "interpretation_verdict": "PROMISING_BUT_UNCONFIRMED",
                            "NET_per_fill": 0.005,
                            "NET_per_fill_unit": "EUR_per_estimated_fill_of_mean_edge_replay",
                            "edge_to_cost": 8.1,
                            "edge_to_uncertainty": 20.0,
                            "break_even_adverse": 336.0,
                            "break_even_fee": 336.0,
                            "break_even_slippage": 336.0,
                            "worst_stress_NET": -1.0,
                            "independent_oos_windows": 1,
                            "gate_selectivity": 0.71,
                            "parent_comparison": {"positive": False},
                            "replication_status": "FIRST_OOS_ONLY",
                            "final_research_decision": "PROMISING_REPLICATION_REQUIRED",
                        }
                    ],
                },
            },
            "performance": {},
        }
    ).body.decode()
    assert "EDGE ROBUSTNESS LAB" in html
    assert "PROMISING_BUT_UNCONFIRMED" in html
    assert "Mechanical OOS_PASS unchanged" in html
