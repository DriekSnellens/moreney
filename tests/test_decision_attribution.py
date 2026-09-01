"""Deterministic tests for four-way decision attribution."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from bot.opportunity.causal_walkforward import CONFIGS, CausalBeliefModel, walk_forward
from bot.opportunity.decision_attribution import (
    build_comparison_rows,
    classify_decisions,
    mechanism_overlap,
    run_independent_replays,
)


def _ts(i: int) -> str:
    base = datetime(2026, 8, 15, 11, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=i)).isoformat()


def _trade(i: int, *, real: str = "-2", exp: str = "1", adv: str = "4") -> dict:
    return {
        "timestamp": _ts(i),
        "opportunity_id": f"opp-{i}",
        "strategy": "maker_inventory",
        "symbol": "XRPEUR",
        "buy_exchange": "bitvavo",
        "sell_exchange": "bitvavo",
        "expected_net_profit": exp,
        "realized_net_profit": real,
        "expected_adverse": "0.5",
        "realized_adverse": adv,
    }


def test_decision_matrix_categories() -> None:
    assert classify_decisions("take", "take", "take", "take") == "ALL_TAKE"
    assert classify_decisions("take", "reject", "take", "reject") == "EARLY_STOP_ONLY_BLOCK"
    assert classify_decisions("take", "take", "reject", "reject") == "CONDITIONAL_EV_ONLY_BLOCK"
    assert classify_decisions("take", "reject", "reject", "reject") == "BOTH_BLOCK"
    assert classify_decisions("reject", "reject", "reject", "reject") == "BASELINE_REJECT"
    # Path dependence: B rejects, C/D still take — category follows A/B/C only.
    assert classify_decisions("take", "reject", "take", "take") == "EARLY_STOP_ONLY_BLOCK"
    assert classify_decisions("take", "take", "take", "reject") == "OTHER"


def test_configs_have_independent_causal_state() -> None:
    trades = [_trade(i) for i in range(12)]
    replays = run_independent_replays(trades)
    a_events = replays["A_BASELINE"]["events"]
    b_events = replays["B_EARLY_STOP_ONLY"]["events"]
    assert a_events is not b_events
    assert any(e["decision"] == "reject" for e in b_events)
    assert all(e["decision"] == "take" for e in a_events)


def test_rejected_config_does_not_share_state_with_others() -> None:
    trades = [_trade(i, real="-2") for i in range(12)]
    b = walk_forward(trades, config=CONFIGS["B_EARLY_STOP_ONLY"], model=CausalBeliefModel())
    c = walk_forward(trades, config=CONFIGS["C_CONDITIONAL_EV_ONLY"], model=CausalBeliefModel())
    assert b["events"] is not c["events"]
    assert b["total_realized_net"] != c["total_realized_net"] or b["rejected_opportunities"] != c[
        "rejected_opportunities"
    ]


def test_ex_post_label_is_evaluation_only() -> None:
    trades = [_trade(i) for i in range(10)]
    rows = build_comparison_rows(trades, run_independent_replays(trades))
    for r in rows:
        assert r["ex_post_label"] == "EX-POST COUNTERFACTUAL OUTCOME"
        assert r["category"] in {
            "ALL_TAKE",
            "EARLY_STOP_ONLY_BLOCK",
            "CONDITIONAL_EV_ONLY_BLOCK",
            "BOTH_BLOCK",
            "BASELINE_REJECT",
            "OTHER",
        }


def test_overlap_deterministic() -> None:
    trades = [_trade(i) for i in range(12)]
    rows = build_comparison_rows(trades, run_independent_replays(trades))
    assert mechanism_overlap(rows) == mechanism_overlap(deepcopy(rows))


def test_combined_internal_gate_consistency() -> None:
    trades = [_trade(i) for i in range(12)]
    replays = run_independent_replays(trades)
    for e in replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]["events"]:
        if e["decision"] == "reject":
            assert e["decision_reason"] in {
                "early_stop_historical",
                "conditional_ev_non_positive",
            }
        else:
            assert e["decision_reason"] == "approved"


def test_path_dependence_can_make_d_equal_c() -> None:
    trades = [_trade(i) for i in range(12)]
    replays = run_independent_replays(trades)
    assert (
        replays["C_CONDITIONAL_EV_ONLY"]["total_realized_net"]
        == replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]["total_realized_net"]
    )
    d_es = sum(
        1
        for e in replays["D_CONDITIONAL_EV_PLUS_EARLY_STOP"]["events"]
        if e.get("decision_reason") == "early_stop_historical"
    )
    assert d_es == 0
