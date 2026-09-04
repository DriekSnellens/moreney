"""AlphaI dynamic price confirmation + pick outcomes (coin-agnostic)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bot.integrations.alphai.parse import AlphaIRegimeState
from bot.integrations.alphai.pick_outcomes import PickOutcomeStore, sync_pick_outcomes
from bot.integrations.alphai.price_confirm import (
    adaptive_lag_threshold,
    build_price_check,
    classify_price_lags,
    confirm_scale_from_excess,
    enrich_daily_with_price_check,
)
from bot.integrations.alphai.signals import build_trading_signals


def test_confirm_scale_is_continuous_and_coin_agnostic() -> None:
    assert confirm_scale_from_excess(-2.0, lag_pp=1.5) == 0.0
    assert confirm_scale_from_excess(0.5, lag_pp=1.5, full_pp=0.0) == 1.0
    mid = confirm_scale_from_excess(-0.75, lag_pp=1.5, full_pp=0.0)
    assert 0.4 < mid < 0.7


def test_lag_classification_follows_tape_not_coin_name() -> None:
    """Whoever underperforms gets demoted — swap returns, swap lagging set."""
    day_a = {"BTC": -2.0, "AAA": -5.0, "BBB": -1.0, "CCC": -2.1}
    day_b = {"BTC": -2.0, "AAA": -1.0, "BBB": -5.0, "CCC": -2.1}
    picks = ["AAA", "BBB", "CCC"]
    lag_a = classify_price_lags(day_a, lag_vs_btc_pp=1.5, pick_bases=picks)
    lag_b = classify_price_lags(day_b, lag_vs_btc_pp=1.5, pick_bases=picks)
    assert "AAA" in lag_a and "BBB" not in lag_a
    assert "BBB" in lag_b and "AAA" not in lag_b


def test_build_price_check_exposes_confirm_scales() -> None:
    check = build_price_check(
        ["AAA", "BBB", "CCC", "DDD"],
        {"BTC": -2.18, "AAA": -4.25, "BBB": -1.97, "CCC": -1.81, "DDD": -0.75},
        lag_vs_btc_pp=1.5,
    )
    assert check["confirm_scales"]["AAA"] < 0.45
    assert check["confirm_scales"]["DDD"] == 1.0
    assert "AAA" in check["lagging"]
    assert "DDD" in check["confirmed"]


def test_price_lag_demotes_strong_from_scales() -> None:
    state = AlphaIRegimeState(enabled=True, macro_reduce_only=True)
    daily = {
        "picks": [
            {"base": "AAA", "score": 60.0, "rank": 1},
            {"base": "BBB", "score": 57.0, "rank": 2},
            {"base": "CCC", "score": 39.0, "rank": 3},
        ],
        "price_check": {
            "lagging": ["AAA"],
            "confirmed": ["BBB", "CCC"],
            "confirm_scales": {"AAA": 0.2, "BBB": 0.9, "CCC": 0.8},
        },
    }
    sig = build_trading_signals(state, daily)
    assert sig.is_bullish_buy("AAA") is True
    assert sig.is_strong_bullish_buy("AAA") is False
    assert sig.is_weak_bullish_hold("AAA") is True
    assert sig.inventory_build("AAA") is False
    assert sig.price_confirm_scale("AAA") == pytest.approx(0.2)
    assert sig.is_strong_bullish_buy("BBB") is True
    assert sig.entry_size_multiplier("AAA") < sig.entry_size_multiplier("BBB")


def test_base_reliability_from_outcomes_is_per_base_dynamic(tmp_path: Path) -> None:
    store = PickOutcomeStore()
    # AAA repeatedly lags; BBB repeatedly beats — names are arbitrary.
    for i in range(4):
        store.sessions.append(
            {
                "session_id": f"s{i}",
                "generated_at": f"2026-09-0{i+1}T12:00:00+00:00",
                "settled": True,
                "outcomes": [
                    {
                        "base": "AAA",
                        "rank": 1,
                        "vs_btc_pp": -2.0,
                        "beat_btc": False,
                        "lagging": True,
                    },
                    {
                        "base": "BBB",
                        "rank": 2,
                        "vs_btc_pp": 1.5,
                        "beat_btc": True,
                        "lagging": False,
                    },
                ],
            }
        )
    rel = store.base_reliability(min_n=3)
    assert rel["AAA"] < rel["BBB"]
    assert rel["AAA"] < 0.85
    assert rel["BBB"] >= 0.95


def test_reliability_haircut_flows_into_signals() -> None:
    state = AlphaIRegimeState(enabled=True)
    daily = {
        "picks": [
            {"base": "AAA", "score": 60.0, "rank": 1},
            {"base": "BBB", "score": 55.0, "rank": 2},
        ],
        "price_confirm_scales": {"AAA": 1.0, "BBB": 1.0},
        "base_reliability": {"AAA": 0.55, "BBB": 1.05},
    }
    sig = build_trading_signals(state, daily)
    assert sig.is_strong_bullish_buy("AAA") is False  # reliability < 0.70
    assert sig.is_strong_bullish_buy("BBB") is True
    assert sig.base_reliability_mult("AAA") < sig.base_reliability_mult("BBB")
    # After reliability, weak history must not outrank strong history on size.
    assert sig.entry_size_multiplier("AAA") <= sig.entry_size_multiplier("BBB")
    assert sig.is_weak_bullish_hold("AAA") is True


def test_adaptive_lag_threshold_uses_history_not_default() -> None:
    # Mild historical laggards → threshold near 1.0
    mild = [-0.9, -1.0, -1.1, -0.8, -1.2] * 5 + [0.3, 0.5, 0.2] * 3
    thr = adaptive_lag_threshold(mild, default_pp=1.5, min_samples=20)
    assert 0.75 <= thr <= 1.5
    # Too few samples → keep default
    assert adaptive_lag_threshold([-2.0, -1.0], default_pp=1.5, min_samples=20) == 1.5


def test_pick_outcomes_scorecard_settles_vs_btc(tmp_path: Path) -> None:
    path = tmp_path / "pick_outcomes.json"
    report = {
        "session_id": "2026-09-04T19:00",
        "generated_at": "2026-09-04T17:00:00+00:00",
        "macro_caution": True,
        "picks": [
            {"base": "AAA", "score": 60.0, "rank": 1},
            {"base": "BBB", "score": 57.0, "rank": 2},
            {"base": "CCC", "score": 39.0, "rank": 3},
            {"base": "DDD", "score": 21.0, "rank": 4},
        ],
    }
    day = {"BTC": -2.18, "AAA": -4.25, "BBB": -1.97, "CCC": -1.81, "DDD": -0.75}
    summary = sync_pick_outcomes(report, path, day_returns_pct=day)
    assert summary is not None
    assert summary["pick_rows"] == 4
    assert summary["beat_btc_rate"] == pytest.approx(0.75)
    assert summary["lag_rate"] == pytest.approx(0.25)
    assert summary["rank1_lag_rate"] == pytest.approx(1.0)
    assert "rank1=AAA" in (summary["latest_lesson"] or "")


def test_enrich_daily_with_price_check_copy() -> None:
    daily = {"session_id": "x", "picks": [{"base": "AAA", "score": 60}]}
    enriched = enrich_daily_with_price_check(
        daily,
        {"BTC": -2.0, "AAA": -5.0},
        lag_vs_btc_pp=1.5,
    )
    assert enriched is not None
    assert "price_check" in enriched
    assert "AAA" in enriched["price_check"]["lagging"]
    assert enriched["price_confirm_scales"]["AAA"] < 0.45
    assert "price_check" not in daily
