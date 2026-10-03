"""Smoke tests for moonshot preimage feature helpers."""

from __future__ import annotations

from bot.research.moonshot_preimage.engine import _features, _wilson, to_markdown


def test_wilson_bounds() -> None:
    assert 0.0 <= _wilson(0, 100) <= 0.05
    assert 0.4 <= _wilson(50, 100) <= 0.6


def test_features_coil_and_xs() -> None:
    # flat then small up — synthetic path
    closes = [100.0] * 50 + [101.0, 102.0, 103.0, 105.0, 108.0]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    vols = [1_000_000.0] * len(closes)
    btc = [50_000.0] * len(closes)
    # make alt outperform last 10d
    for i in range(10):
        closes[-10 + i] = 100.0 * (1.03**i)
        highs[-10 + i] = closes[-10 + i] * 1.01
        lows[-10 + i] = closes[-10 + i] * 0.99
    feats = _features(closes, highs, lows, vols, btc)
    assert feats is not None
    assert "xs10" in feats
    assert feats["above_sma50"] in (True, False)


def test_markdown_smoke() -> None:
    payload = {
        "asof": "t",
        "n_bases": 1,
        "horizon_sessions": 7,
        "label": "x",
        "note": "n",
        "train_end": "2025-12-31",
        "base_rate_train": {"n": 1, "p30": 0.0, "p50": 0.0},
        "base_rate_test": {"n": 1, "p30": 0.0, "p50": 0.0},
        "alphai_session_days": 0,
        "alphai_coverage_test_rows": 0,
        "alphai_pick_rows_test": 0,
        "alphai_pick_p50_test": None,
        "best_precision_n20": None,
        "rules_hitting_60pct_precision_n10": [],
        "ranked": [],
    }
    md = to_markdown(payload)
    assert "60–70%" in md
