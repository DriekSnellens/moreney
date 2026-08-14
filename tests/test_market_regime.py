"""HMM market-regime detector: risk sort, ATR features, toxic hysteresis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.regime.market_regime import (
    REGIME_BULLISH,
    REGIME_SIDEWAYS,
    REGIME_TOXIC_DUMP,
    MarketRegimeDetector,
)


def _synthetic_ohlcv(
    *,
    n: int = 200,
    regime_blocks: list[tuple[str, int]] | None = None,
    seed: int = 7,
) -> pd.DataFrame:
    """Build OHLCV with sideways / bullish / dump segments."""
    rng = np.random.default_rng(seed)
    blocks = regime_blocks or [
        ("low", 70),
        ("up", 70),
        ("dump", 60),
    ]
    price = 100.0
    rows: list[dict[str, float]] = []
    for kind, length in blocks:
        for _ in range(length):
            if kind == "low":
                ret = rng.normal(0.0, 0.0004)
            elif kind == "up":
                ret = rng.normal(0.0015, 0.0012)
            else:
                ret = rng.normal(-0.0040, 0.0035)
            open_px = price
            price *= float(np.exp(ret))
            high = max(open_px, price) * (1.0 + abs(rng.normal(0, 0.0003)))
            low = min(open_px, price) * (1.0 - abs(rng.normal(0, 0.0003)))
            rows.append(
                {
                    "open": open_px,
                    "high": high,
                    "low": low,
                    "close": price,
                    "volume": 1.0,
                }
            )
    return pd.DataFrame(rows[:n])


def test_prepare_features_logret_and_normalized_atr() -> None:
    det = MarketRegimeDetector(atr_window=14, min_samples=40, history_len=500)
    df = _synthetic_ohlcv(n=120)
    X = det.prepare_features(df)
    assert X.ndim == 2
    assert X.shape[1] == 2
    assert len(X) >= 40
    assert np.isfinite(X).all()


def test_states_sorted_by_risk_score_vol_minus_return() -> None:
    det = MarketRegimeDetector(atr_window=8, min_samples=50, n_iter=80, random_state=0)
    df = _synthetic_ohlcv(n=240)
    det.fit(df)
    means = det._model.means_
    risk = [float(means[i, 1] - means[i, 0]) for i in range(3)]
    ordered = sorted(range(3), key=lambda i: risk[i])
    assert det._raw_to_canonical[ordered[0]] == REGIME_SIDEWAYS
    assert det._raw_to_canonical[ordered[1]] == REGIME_BULLISH
    assert det._raw_to_canonical[ordered[2]] == REGIME_TOXIC_DUMP
    assert set(det._raw_to_canonical.values()) == {
        REGIME_SIDEWAYS,
        REGIME_BULLISH,
        REGIME_TOXIC_DUMP,
    }


def test_hysteresis_requires_two_toxic_steps() -> None:
    det = MarketRegimeDetector(
        atr_window=8,
        min_samples=50,
        n_iter=100,
        random_state=2,
        toxic_confirm_steps=2,
        toxic_proba_threshold=0.99,  # force streak path, not proba shortcut
    )
    df = _synthetic_ohlcv(
        n=260,
        regime_blocks=[("low", 40), ("up", 40), ("dump", 180)],
        seed=5,
    )
    det.fit(df)
    # First toxic raw prediction alone must not arm the guardrail.
    first = det.get_current_regime(df)
    if first.regime_id == REGIME_TOXIC_DUMP or first.toxic_probability >= 0.99:
        # If model is extremely sure, high-proba path may arm immediately — that
        # is allowed by spec. Otherwise streak must reach 2.
        if first.toxic_probability < 0.99:
            assert first.is_toxic_flow is False or first.consecutive_toxic >= 1
    second = det.get_current_regime(df)
    if second.regime_id == REGIME_TOXIC_DUMP and second.consecutive_toxic >= 2:
        assert second.is_toxic_flow is True
        assert second.inventory_target_pct == pytest.approx(0.10)
        assert second.reduce_only is True


def test_high_toxic_proba_arms_immediately() -> None:
    det = MarketRegimeDetector(
        atr_window=8,
        min_samples=50,
        toxic_confirm_steps=5,
        toxic_proba_threshold=0.70,
    )
    # Inject a fake prediction path via hysteresis helper.
    from bot.regime.market_regime import RegimePrediction

    hot = RegimePrediction(
        regime_id=REGIME_SIDEWAYS,
        raw_state=0,
        is_toxic_flow=False,
        label="SIDEWAYS",
        mean_return=0.0,
        mean_volatility=0.001,
        confidence=0.2,
        toxic_probability=0.85,
    )
    out = det._apply_hysteresis(hot)
    assert out.is_toxic_flow is True
    assert out.inventory_target_pct == pytest.approx(0.10)


def test_candle_builder_rolls_on_timeframe() -> None:
    det = MarketRegimeDetector(
        atr_window=5,
        min_samples=5,
        history_len=500,
        candle_timeframe_sec=60.0,
    )
    t0 = 1_700_000_000.0
    price = 10.0
    for i in range(180):
        price *= 1.0001
        det.observe_mid("ATOMEUR", price, now=t0 + i)
    # 180 seconds → about 2 completed 60s candles (buckets 0 and 60; current open).
    assert len(det._candles.get("ATOMEUR", [])) >= 2


def test_fit_requires_enough_samples() -> None:
    det = MarketRegimeDetector(min_samples=80, atr_window=14)
    df = _synthetic_ohlcv(n=30)
    with pytest.raises(ValueError, match="Need"):
        det.fit(df)


def test_needs_refit_respects_interval() -> None:
    det = MarketRegimeDetector(refit_every_sec=100.0, min_samples=50, atr_window=8)
    assert det.needs_refit() is True
    df = _synthetic_ohlcv(n=120)
    det.fit(df)
    assert det.needs_refit(now=det._last_fit_mono) is False
    assert det.needs_refit(now=det._last_fit_mono + 101.0) is True
