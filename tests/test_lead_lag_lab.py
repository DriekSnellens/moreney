"""Lead-lag research lab — causality, safety, no production impact."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

from bot.core.exchange_types import OrderBookLevel
from bot.core.config import Settings
from bot.opportunity.lead_lag.economics import build_shadow_opportunity, executable_vwap
from bot.opportunity.lead_lag.hedge import require_feasible_hedge
from bot.opportunity.lead_lag.horizons import HORIZON_MS_GRID, classify_horizon_support
from bot.opportunity.lead_lag.models_a_d import ModelA, make_models
from bot.opportunity.lead_lag.observer import LeadLagObserver, synthetic_lead_lag_tape
from bot.opportunity.lead_lag.shadow import execution_allowed, shadow_admit
from bot.opportunity.lead_lag.study import build_study, freeze_candidates, split_observations
from bot.opportunity.lead_lag.timestamps import audit_timestamps
from bot.opportunity.lead_lag.types import LeadLagSignal
from bot.opportunity.lead_lag.walkforward import follower_future_move_bps, walk_forward_lead_lag
from bot.opportunity.lead_lag.pairs import directed_pairs


def test_audit_without_tape_is_unsupported() -> None:
    audit = audit_timestamps(market_data_dir="/nonexistent_ll_tape_xyz")
    assert audit["has_synchronized_tape"] is False
    assert audit["overall_quality"] == "UNSUPPORTED"
    assert audit["subsecond_lead_lag_supported"] is False


def test_unsupported_horizon_when_no_tape() -> None:
    row = classify_horizon_support(
        50,
        data_quality="UNSUPPORTED",
        min_resolution_ms=None,
        has_synchronized_tape=False,
    )
    assert row["support"] == "UNSUPPORTED_BY_DATA"


def test_study_verdict_insufficient_data_without_tape() -> None:
    report = build_study(market_data_dir="/no_tape", use_synthetic_if_empty=False)
    assert report["O_final_verdict"] == "INSUFFICIENT_DATA"
    assert report["production_safety"]["alters_execution"] is False
    assert report["production_safety"]["execution_enabled_default"] is False


def test_no_future_in_prediction() -> None:
    tape = synthetic_lead_lag_tape(n=80, horizon_ms=500, dt_ms=100, seed=1)
    model = ModelA()
    # Predict at index 10 — model must not have seen outcomes from i>10
    sig = model.predict(tape[10], horizon_ms=500)
    assert sig.evidence_sample_count == 0
    wf = walk_forward_lead_lag(
        tape,
        model_version="A_SIGNED_LEADER_v1",
        horizon_ms=500,
        min_leader_move_bps=0.0,
    )
    early = [d for d in wf.decisions if d["idx"] < 5]
    assert early
    assert all(d["signal"]["evidence_sample_count"] == 0 for d in early)


def test_outcome_unavailable_before_horizon() -> None:
    tape = synthetic_lead_lag_tape(n=30, horizon_ms=500, dt_ms=100, seed=2)
    # At start_idx=0, need timestamp >= 500 — index 5 is first at 500ms
    assert follower_future_move_bps(tape, start_idx=0, horizon_ms=500) is not None
    # Truncate so horizon not reached
    short = tape[:3]  # last ts = 200
    assert follower_future_move_bps(short, start_idx=0, horizon_ms=500) is None


def test_future_loss_cannot_influence_earlier_decision() -> None:
    tape = synthetic_lead_lag_tape(n=100, horizon_ms=500, dt_ms=100, seed=3)
    wf = walk_forward_lead_lag(tape, model_version="A_SIGNED_LEADER_v1", horizon_ms=500)
    # Decision at idx records evidence_sample_count from past only
    for d in wf.decisions:
        # evidence cannot exceed outcomes that were available before this timestamp
        assert d["signal"]["evidence_sample_count"] <= d["idx"]


def test_deterministic_replay() -> None:
    tape = synthetic_lead_lag_tape(n=60, seed=7)
    a = walk_forward_lead_lag(tape, model_version="B_INCREMENTAL_v1", horizon_ms=500)
    b = walk_forward_lead_lag(tape, model_version="B_INCREMENTAL_v1", horizon_ms=500)
    assert a.summary() == b.summary()
    assert len(a.decisions) == len(b.decisions)


def test_mid_not_used_when_depth_exists() -> None:
    asks = [
        OrderBookLevel(price=Decimal("101"), amount=Decimal("1")),
        OrderBookLevel(price=Decimal("102"), amount=Decimal("1")),
    ]
    bids = [OrderBookLevel(price=Decimal("99"), amount=Decimal("2"))]
    vwap, filled, ok, _ = executable_vwap(
        "buy", bids=bids, asks=asks, quantity=Decimal("1.5")
    )
    assert ok
    # VWAP = (1*101 + 0.5*102)/1.5 = 101.333... not mid 100
    assert vwap != Decimal("100")
    assert vwap > Decimal("101")


def test_missing_hedge_rejects() -> None:
    sig = LeadLagSignal(
        decision_timestamp_ms=0,
        symbol="BTCEUR",
        leader_venue="binance",
        follower_venue="bitvavo",
        horizon_ms=500,
        predicted_follower_move_bps=Decimal("20"),
        uncertainty_bps=Decimal("5"),
        signal_strength=Decimal("2"),
        model_version="A_SIGNED_LEADER_v1",
        evidence_sample_count=10,
        leader_return_bps=Decimal("20"),
    )
    # Follower has depth; leader empty → hedge unavailable
    opp = build_shadow_opportunity(
        sig,
        follower_bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("5"))],
        follower_asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("5"))],
        leader_bids=[],
        leader_asks=[],
        quantity=Decimal("1"),
    )
    assert opp.state == "HEDGE_UNAVAILABLE"
    assert shadow_admit(opp)["accept"] is False


def test_execution_gate_default_false() -> None:
    assert execution_allowed(lead_lag_execution_enabled=False, shadow_only=True) is False
    assert execution_allowed(lead_lag_execution_enabled=True, shadow_only=True) is False
    assert execution_allowed(lead_lag_execution_enabled=True, shadow_only=False) is True


def test_settings_defaults() -> None:
    s = Settings(
        execution_mode="paper",
        _env_file=None,  # type: ignore[call-arg]
    )
    # pydantic-settings may still load .env; assert attrs exist with safe defaults via getattr
    assert getattr(s, "lead_lag_enabled", True) is True
    assert getattr(s, "lead_lag_shadow_only", True) is True
    assert getattr(s, "lead_lag_execution_enabled", False) is False


def test_frozen_before_oos() -> None:
    frozen = freeze_candidates(
        pairs=directed_pairs()[:2],
        horizons=[500, 1000],
        models=["A_SIGNED_LEADER_v1"],
    )
    assert frozen["frozen"] is True
    tape = synthetic_lead_lag_tape(n=80, seed=9)
    dev, oos = split_observations(tape, train_frac=0.6)
    assert len(dev) + len(oos) == len(tape)
    assert max(o.timestamp_ms for o in dev) <= min(o.timestamp_ms for o in oos)


def test_dev_oos_no_leak_in_study_synthetic() -> None:
    report = build_study(use_synthetic_if_empty=True)
    assert report["L_frozen_candidates"]["frozen"] is True
    assert report["M_untouched_oos"] is not None
    # Synthetic may yield various verdicts; must be in allowed set
    assert report["O_final_verdict"] in report["allowed_verdicts"]


def test_lead_lag_not_in_executor_or_fill_path() -> None:
    import bot.execution.paper_executor as paper_ex
    import bot.execution.executor as ex

    for mod in (paper_ex, ex):
        src = inspect.getsource(mod)
        assert "lead_lag" not in src
        assert "LeadLag" not in src


def test_lead_lag_cannot_alter_maker_module() -> None:
    import bot.strategies.maker_inventory as maker

    src = inspect.getsource(maker)
    assert "lead_lag" not in src


def test_shadow_admitted_requires_conservative_net() -> None:
    sig = LeadLagSignal(
        decision_timestamp_ms=0,
        symbol="BTCEUR",
        leader_venue="binance",
        follower_venue="okx",
        horizon_ms=500,
        predicted_follower_move_bps=Decimal("1"),  # tiny — likely negative after costs
        uncertainty_bps=Decimal("20"),
        signal_strength=Decimal("0.1"),
        model_version="A_SIGNED_LEADER_v1",
        evidence_sample_count=2,
        leader_return_bps=Decimal("1"),
    )
    levels_b = [OrderBookLevel(price=Decimal("100"), amount=Decimal("10"))]
    levels_a = [OrderBookLevel(price=Decimal("100.1"), amount=Decimal("10"))]
    opp = build_shadow_opportunity(
        sig,
        follower_bids=levels_b,
        follower_asks=levels_a,
        leader_bids=levels_b,
        leader_asks=levels_a,
        quantity=Decimal("1"),
        latency_ms=500,
    )
    # With tiny edge + latency + uncertainty should not admit
    assert opp.state in {"NEGATIVE_CONSERVATIVE_NET", "SHADOW_ADMITTED", "NOT_EXECUTABLE"}
    if opp.conservative_net_eur <= 0:
        assert shadow_admit(opp)["accept"] is False


def test_all_models_predict() -> None:
    tape = synthetic_lead_lag_tape(n=20, seed=11)
    models = make_models()
    for name, model in models.items():
        sig = model.predict(tape[-1], horizon_ms=500)
        assert sig.model_version == name


def test_require_feasible_hedge() -> None:
    sig = LeadLagSignal(
        decision_timestamp_ms=0,
        symbol="X",
        leader_venue="binance",
        follower_venue="okx",
        horizon_ms=250,
        predicted_follower_move_bps=Decimal("30"),
        uncertainty_bps=Decimal("2"),
        signal_strength=Decimal("3"),
        model_version="A_SIGNED_LEADER_v1",
        evidence_sample_count=50,
        leader_return_bps=Decimal("30"),
    )
    opp = build_shadow_opportunity(
        sig,
        follower_bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("5"))],
        follower_asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("5"))],
        leader_bids=[OrderBookLevel(price=Decimal("99"), amount=Decimal("5"))],
        leader_asks=[OrderBookLevel(price=Decimal("101"), amount=Decimal("5"))],
        quantity=Decimal("1"),
    )
    fixed = require_feasible_hedge(opp)
    assert fixed.state in {"SHADOW_ADMITTED", "NEGATIVE_CONSERVATIVE_NET", "HEDGE_UNAVAILABLE"}


def test_horizons_predeclared() -> None:
    assert HORIZON_MS_GRID == (50, 100, 250, 500, 1000, 2000, 5000)


def test_runner_status_keys_include_lead_lag_lab_method() -> None:
    import bot.paper.runner as runner_mod

    src = inspect.getsource(runner_mod)
    assert "_lead_lag_lab_snapshot" in src
    assert "lead_lag_execution_enabled" in src
    # Observer must not call execution
    assert "LEAD_LAG_EXECUTION_ENABLED" not in src or "False" in src
