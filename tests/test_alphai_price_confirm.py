"""AlphaI price confirmation + pick outcomes learning."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bot.integrations.alphai.parse import AlphaIRegimeState
from bot.integrations.alphai.pick_outcomes import PickOutcomeStore, sync_pick_outcomes
from bot.integrations.alphai.price_confirm import (
    build_price_check,
    classify_price_lags,
    enrich_daily_with_price_check,
)
from bot.integrations.alphai.signals import build_trading_signals


def test_classify_price_lags_sep4_lesson() -> None:
    """2026-09-04: XRP #1 pick lagged BTC; LINK/BNB did not."""
    day = {
        "BTC": -2.18,
        "XRP": -4.25,
        "ETH": -1.97,
        "LINK": -1.81,
        "BNB": -0.75,
    }
    lags = classify_price_lags(day, lag_vs_btc_pp=1.5)
    assert "XRP" in lags
    assert "ETH" not in lags
    assert "LINK" not in lags
    assert "BNB" not in lags


def test_build_price_check_marks_confirmed_and_lagging() -> None:
    check = build_price_check(
        ["XRP", "ETH", "LINK", "BNB"],
        {"BTC": -2.18, "XRP": -4.25, "ETH": -1.97, "LINK": -1.81, "BNB": -0.75},
        lag_vs_btc_pp=1.5,
    )
    assert check["btc_day_pct"] == pytest.approx(-2.18)
    assert "XRP" in check["lagging"]
    assert "BNB" in check["confirmed"]
    assert check["picks"]["XRP"]["lagging"] is True
    assert check["picks"]["XRP"]["vs_btc_pp"] == pytest.approx(-2.07, abs=0.01)


def test_price_lag_demotes_strong_and_inventory_build() -> None:
    state = AlphaIRegimeState(enabled=True, macro_reduce_only=True)
    daily = {
        "picks": [
            {"base": "XRP", "score": 60.0, "rank": 1},
            {"base": "ETH", "score": 57.0, "rank": 2},
            {"base": "LINK", "score": 39.0, "rank": 3},
            {"base": "BNB", "score": 21.0, "rank": 4},
        ],
        "price_check": {
            "lagging": ["XRP"],
            "confirmed": ["ETH", "LINK", "BNB"],
        },
    }
    sig = build_trading_signals(state, daily)
    assert sig.is_bullish_buy("XRP") is True
    assert sig.is_strong_bullish_buy("XRP") is False
    assert sig.is_weak_bullish_hold("XRP") is True
    assert sig.inventory_build("XRP") is False
    assert sig.momentum_floor_scale("XRP") == Decimal("1")
    # Non-lagging top pick still strong.
    assert sig.is_strong_bullish_buy("ETH") is True
    assert sig.is_weak_bullish_hold("ETH") is False
    assert sig.entry_size_multiplier("XRP") < sig.entry_size_multiplier("ETH")


def test_pick_outcomes_scorecard_settles_vs_btc(tmp_path: Path) -> None:
    path = tmp_path / "pick_outcomes.json"
    report = {
        "session_id": "2026-09-04T19:00",
        "generated_at": "2026-09-04T17:00:00+00:00",
        "macro_caution": True,
        "picks": [
            {"base": "XRP", "score": 60.0, "rank": 1},
            {"base": "ETH", "score": 57.0, "rank": 2},
            {"base": "LINK", "score": 39.0, "rank": 3},
            {"base": "BNB", "score": 21.0, "rank": 4},
        ],
    }
    day = {"BTC": -2.18, "XRP": -4.25, "ETH": -1.97, "LINK": -1.81, "BNB": -0.75}
    summary = sync_pick_outcomes(report, path, day_returns_pct=day)
    assert summary is not None
    assert summary["pick_rows"] == 4
    assert summary["beat_btc_rate"] == pytest.approx(0.75)  # ETH/LINK/BNB
    assert summary["lag_rate"] == pytest.approx(0.25)  # XRP only
    assert summary["rank1_lag_rate"] == pytest.approx(1.0)
    assert "rank1=XRP" in (summary["latest_lesson"] or "")

    store = PickOutcomeStore.load(path)
    assert len(store.sessions) == 1
    assert store.sessions[0]["settled"] is True


def test_enrich_daily_with_price_check_copy() -> None:
    daily = {"session_id": "x", "picks": [{"base": "XRP", "score": 60}]}
    enriched = enrich_daily_with_price_check(
        daily,
        {"BTC": -2.0, "XRP": -5.0},
        lag_vs_btc_pp=1.5,
    )
    assert enriched is not None
    assert "price_check" in enriched
    assert "XRP" in enriched["price_check"]["lagging"]
    assert "price_check" not in daily  # original untouched
