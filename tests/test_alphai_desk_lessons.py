"""Desk lesson ledger: record → settle → capped feedback."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from bot.integrations.alphai.desk_lessons import (
    DeskLessonStore,
    sync_desk_lessons_day_returns,
)
from bot.live.micro_bridge_executor import MicroBudgetLiveExecutor


def test_missed_deploy_settles_and_raises_deploy_bias(tmp_path: Path) -> None:
    path = tmp_path / "desk_lessons.json"
    store = DeskLessonStore.load(path)
    store.auto_apply = True
    row = store.record_missed_deploy(
        sleeve_bases=["BNB", "ADA"],
        free_cash_eur=1800.0,
        held_non_picks=["ETH", "XRP"],
        playbook="FLAT",
        min_free_eur=150.0,
    )
    assert row is not None
    assert row["settled"] is False

    n = store.settle_open_with_day_returns({"BNB": 6.0, "ADA": 4.0, "ETH": -1.0})
    assert n >= 1
    assert store.lessons[0]["settled"] is True
    assert store.lessons[0]["outcome"]["positive_miss"] is True
    store.recompute_feedback()
    fb = store.applied_feedback()
    assert fb.deploy_urgency_bias > 1.0
    assert fb.deploy_urgency_bias <= 1.25
    store.save(path)

    loaded = DeskLessonStore.load(path)
    assert loaded.summary()["settled_count"] == 1
    assert loaded.applied_feedback().deploy_urgency_bias > 1.0


def test_early_harvest_immediate_settle_raises_harvest_floor(tmp_path: Path) -> None:
    store = DeskLessonStore.load(tmp_path / "desk.json")
    store.auto_apply = True
    row = store.record_early_harvest(
        base="BNB",
        venue="bitvavo",
        exit_gain_pct=0.0008,
        peak_gain_pct=0.012,
        notional_eur=400.0,
        soft_arm_pct=0.015,
        reason="trail_be_harvest",
    )
    assert row is not None
    settled = store.settle_early_harvest_immediate(str(row["id"]))
    assert settled is not None
    assert settled["settled"] is True
    assert settled["outcome"]["missed_eur"] > 0
    store.recompute_feedback()
    assert store.applied_feedback().harvest_floor_scale > 1.0
    assert store.applied_feedback().harvest_floor_scale <= 1.35


def test_avoid_vs_sleeve_tightens_recycle_age(tmp_path: Path) -> None:
    store = DeskLessonStore.load(tmp_path / "desk.json")
    store.auto_apply = True
    store.record_avoid_vs_sleeve(
        avoid_bases=["ETH", "XRP"],
        sleeve_bases=["BNB"],
        avoid_notional_eur=900.0,
        free_cash_eur=200.0,
        playbook="TREND",
    )
    store.settle_open_with_day_returns({"BNB": 5.0, "ETH": -0.5, "XRP": 0.2})
    store.recompute_feedback()
    fb = store.applied_feedback()
    assert fb.avoid_recycle_age_scale < 1.0
    assert fb.avoid_recycle_age_scale >= 0.55


def test_auto_apply_false_keeps_identity_multipliers(tmp_path: Path) -> None:
    store = DeskLessonStore.load(tmp_path / "desk.json")
    store.auto_apply = False
    store.record_missed_deploy(
        sleeve_bases=["BNB"],
        free_cash_eur=2000.0,
        held_non_picks=["ETH"],
    )
    store.settle_open_with_day_returns({"BNB": 8.0})
    store.recompute_feedback()
    assert store.feedback.deploy_urgency_bias > 1.0  # shadow computed
    assert store.applied_feedback().deploy_urgency_bias == 1.0  # not applied


def test_sync_desk_lessons_day_returns(tmp_path: Path) -> None:
    path = tmp_path / "desk.json"
    store = DeskLessonStore.load(path)
    store.record_missed_deploy(
        sleeve_bases=["ADA"],
        free_cash_eur=500.0,
        held_non_picks=[],
    )
    store.save(path)
    summary = sync_desk_lessons_day_returns(
        path, {"ADA": 3.5}, enabled=True, auto_apply=False
    )
    assert summary is not None
    assert summary["settled_count"] >= 1


def test_bridge_harvest_scale_uses_desk_feedback() -> None:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    store = DeskLessonStore()
    store.auto_apply = True
    store.feedback.harvest_floor_scale = 1.20
    b._desk_lessons = store
    b._alphai_feature_for = (  # type: ignore[method-assign]
        lambda base: SimpleNamespace(be_harvest_gain_scale=Decimal("1.0"))
    )
    scale = b._alphai_be_harvest_gain_scale("BNB")
    assert scale == Decimal("1.20")


def test_bridge_deploy_bias_and_avoid_age_helpers() -> None:
    b = MicroBudgetLiveExecutor.__new__(MicroBudgetLiveExecutor)
    store = DeskLessonStore()
    store.auto_apply = True
    store.feedback.deploy_urgency_bias = 1.15
    store.feedback.avoid_recycle_age_scale = 0.70
    b._desk_lessons = store
    assert b._desk_lesson_deploy_bias() == Decimal("1.15")
    assert b._desk_lesson_avoid_age_scale() == 0.70


def test_debounce_missed_deploy_same_day(tmp_path: Path) -> None:
    store = DeskLessonStore.load(tmp_path / "desk.json")
    a = store.record_missed_deploy(
        sleeve_bases=["BNB"],
        free_cash_eur=400.0,
        held_non_picks=["ETH"],
        day="2026-09-05",
    )
    b = store.record_missed_deploy(
        sleeve_bases=["BNB", "ADA"],
        free_cash_eur=900.0,
        held_non_picks=["ETH", "XRP"],
        day="2026-09-05",
    )
    assert a is not None and b is not None
    assert a["id"] == b["id"]
    open_n = sum(
        1
        for x in store.lessons
        if x.get("kind") == "missed_deploy" and not x.get("settled")
    )
    assert open_n == 1
    assert float(b["free_cash_eur"]) >= 900.0


def test_missed_deploy_records_when_capital_deadlocked(tmp_path: Path) -> None:
    store = DeskLessonStore.load(tmp_path / "desk_deadlock.json")
    row = store.record_missed_deploy(
        sleeve_bases=["BNB"],
        free_cash_eur=20.0,
        held_non_picks=["SOL"],
        playbook="FLAT",
        min_free_eur=150.0,
        capital_deadlocked=True,
        locked_eur=280.0,
    )
    assert row is not None
    assert row.get("capital_deadlocked") is True
    assert float(row.get("locked_eur") or 0) >= 280.0
