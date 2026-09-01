"""Phase 2.1 tests — attribution, calibration, recommendations."""

from __future__ import annotations

from decimal import Decimal

from bot.intelligence.economic_attribution import (
    EconomicAttributionStore,
    net_per_capital_hour,
    net_per_hour,
)
from bot.intelligence.outcome_learning import OutcomeBucket, empirical_multiplier, learning_confidence
from bot.intelligence.parameter_recommendation import generate_recommendations


def test_net_per_capital_hour_no_division_by_zero() -> None:
    assert net_per_capital_hour(Decimal("1"), Decimal("0"), Decimal("100")) is None
    assert net_per_capital_hour(Decimal("1"), Decimal("100"), Decimal("0")) is None
    v = net_per_capital_hour(Decimal("2"), Decimal("100"), Decimal("3600"))
    assert v is not None
    assert v == Decimal("0.02")


def test_net_per_hour() -> None:
    assert net_per_hour(Decimal("1"), None) is None
    v = net_per_hour(Decimal("1"), Decimal("3600"))
    assert v == Decimal("1")


def test_attribution_record_opportunity_and_cancel() -> None:
    store = EconomicAttributionStore()
    rec = store.record_opportunity(
        record_id="r1",
        symbol="BTCEUR",
        venue="bitvavo",
        strategy="maker_inventory",
        side="buy",
        score_after=Decimal("72"),
        adverse_score=Decimal("0.81"),
        expected_net=Decimal("0.50"),
    )
    store.record_cancel(
        rec,
        reason="adverse_selection_high",
        avoided_loss=Decimal("0.37"),
        missed_opportunity=Decimal("0.09"),
    )
    snap = store.cancel_alpha_summary()
    assert snap["samples"] == 1
    assert Decimal(str(snap["total_cancel_alpha_eur"])) == Decimal("0.28")
    assert store.shadow_threshold_cancels.get("threshold_80_would_cancel", 0) >= 1


def test_adverse_calibration_buckets() -> None:
    store = EconomicAttributionStore()
    rec = store.record_opportunity(
        record_id="r1",
        symbol="BTCEUR",
        venue="bitvavo",
        strategy="maker_inventory",
        side="buy",
        adverse_score=Decimal("0.75"),
    )
    store.record_fill_outcome(
        rec,
        fill_price=Decimal("100"),
        realized_net=Decimal("-0.10"),
        toxic=True,
        adverse_move=Decimal("0.005"),
    )
    cal = store.adverse_calibration()
    assert len(cal) == 10
    assert any(int(row.get("toxic_fills") or 0) >= 1 for row in cal)


def test_learning_shrinkage_small_sample() -> None:
    bucket = OutcomeBucket(samples=3, wins=3, sum_net_eur=Decimal("3"))
    mult = empirical_multiplier(bucket=bucket, config=__import__(
        "bot.intelligence.outcome_learning", fromlist=["OutcomeLearningConfig"]
    ).OutcomeLearningConfig(min_learning_samples=20, full_learning_samples=50))
    assert mult == Decimal("1.0")


def test_learning_shrinkage_partial_wins() -> None:
    bucket = OutcomeBucket(samples=30, wins=20, sum_net_eur=Decimal("5"))
    cfg = __import__(
        "bot.intelligence.outcome_learning", fromlist=["OutcomeLearningConfig"]
    ).OutcomeLearningConfig(min_learning_samples=20, full_learning_samples=50)
    mult = empirical_multiplier(bucket=bucket, config=cfg)
    conf, n = learning_confidence(bucket, cfg)
    assert n == 30
    assert conf == "WEAK"
    assert Decimal("0.80") <= mult <= Decimal("1.20")


def test_parameter_recommendations_no_auto_apply() -> None:
    store = EconomicAttributionStore()
    recs = generate_recommendations(store, regime_scoring_enabled=False)
    assert all(r.auto_apply is False for r in recs)
    regime_rec = next(r for r in recs if r.parameter == "regime_scoring_enabled")
    assert regime_rec.recommended == "False"


def test_score_monotonicity_detection() -> None:
    store = EconomicAttributionStore()
    assert store.score_monotonicity_ok() is True


def test_no_lookahead_marks_causal() -> None:
    from bot.research.execution_intelligence_ablation import load_audit_candidates
    from pathlib import Path
    import json
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(
            json.dumps(
                {
                    "ts": "2026-08-21T08:47:20+00:00",
                    "type": "micro_order_result",
                    "payload": {
                        "venue": "bitvavo",
                        "symbol": "SOLEUR",
                        "side": "buy",
                        "quantity": "1",
                        "notional_eur": "100",
                    },
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": "2026-08-21T08:47:21+00:00",
                    "type": "micro_order_result",
                    "payload": {
                        "venue": "bitvavo",
                        "symbol": "SOLEUR",
                        "side": "buy",
                        "quantity": "1",
                        "notional_eur": "101",
                    },
                }
            )
            + "\n"
        )
        path = Path(f.name)
    cands = load_audit_candidates(path)
    assert len(cands[1].marks) == 1
    assert cands[1].marks[0] == cands[0].price
