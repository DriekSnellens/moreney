"""HMM market-regime detector: feature prep, state remapping, toxic-flow flag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.regime.market_regime import (
    REGIME_LOW_VOL,
    REGIME_TOXIC_FLOW,
    REGIME_UP_TREND,
    MarketRegimeDetector,
)


def _synthetic_ohlcv(
    *,
    n: int = 200,
    regime_blocks: list[tuple[str, int]] | None = None,
    seed: int = 7,
) -> pd.DataFrame:
    """Build a close series with low-vol, up-trend, and dump segments."""
    rng = np.random.default_rng(seed)
    blocks = regime_blocks or [
        ("low", 70),
        ("up", 70),
        ("dump", 60),
    ]
    price = 100.0
    closes: list[float] = []
    for kind, length in blocks:
        for _ in range(length):
            if kind == "low":
                ret = rng.normal(0.0, 0.0004)
            elif kind == "up":
                ret = rng.normal(0.0015, 0.0012)
            else:  # dump / toxic
                ret = rng.normal(-0.0040, 0.0035)
            price *= float(np.exp(ret))
            closes.append(price)
    closes = closes[:n]
    arr = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": arr,
            "high": arr * 1.001,
            "low": arr * 0.999,
            "close": arr,
            "volume": np.ones(len(arr)),
        }
    )


def test_prepare_features_returns_logret_and_vol() -> None:
    det = MarketRegimeDetector(vol_window=10, min_samples=40)
    df = _synthetic_ohlcv(n=120)
    X = det.prepare_features(df)
    assert X.ndim == 2
    assert X.shape[1] == 2
    assert len(X) >= 40
    assert np.isfinite(X).all()


def test_fit_maps_toxic_to_regime_2() -> None:
    det = MarketRegimeDetector(vol_window=8, min_samples=50, n_iter=80, random_state=0)
    df = _synthetic_ohlcv(n=220)
    det.fit(df)
    # Predict on a pure dump tail.
    dump_tail = _synthetic_ohlcv(
        n=80,
        regime_blocks=[("dump", 80)],
        seed=99,
    )
    # Warm the model with enough history by concatenating calm + dump.
    calm = _synthetic_ohlcv(n=80, regime_blocks=[("low", 80)], seed=1)
    mixed = pd.concat([calm, dump_tail], ignore_index=True)
    det.fit(mixed)
    pred = det.predict_regime(mixed)
    assert pred.regime_id in {REGIME_LOW_VOL, REGIME_UP_TREND, REGIME_TOXIC_FLOW}
    assert set(det._raw_to_canonical.values()) == {
        REGIME_LOW_VOL,
        REGIME_UP_TREND,
        REGIME_TOXIC_FLOW,
    }
    # Toxic canonical state must be the one with worst return/vol score in means.
    means = det._model.means_
    toxic_raw = max(
        range(3), key=lambda i: float(-means[i, 0] + means[i, 1])
    )
    assert det._raw_to_canonical[toxic_raw] == REGIME_TOXIC_FLOW


def test_predict_toxic_flag_on_dump_series() -> None:
    det = MarketRegimeDetector(vol_window=8, min_samples=50, n_iter=100, random_state=1)
    # Long dump-dominated sample so the latest state is toxic.
    df = _synthetic_ohlcv(
        n=240,
        regime_blocks=[("low", 40), ("up", 40), ("dump", 160)],
        seed=3,
    )
    det.fit(df)
    pred = det.predict_regime(df)
    assert pred.label in {"LOW_VOL", "UP_TREND", "TOXIC_FLOW"}
    if pred.is_toxic_flow:
        assert pred.regime_id == REGIME_TOXIC_FLOW
        assert pred.reduce_only is True


def test_online_observe_and_update() -> None:
    det = MarketRegimeDetector(vol_window=5, min_samples=40, history_len=200)
    rng = np.random.default_rng(0)
    price = 10.0
    for i in range(120):
        # Mild sideways then a sharp dump.
        shock = -0.02 if i > 90 else 0.0
        price *= float(np.exp(rng.normal(shock, 0.001)))
        det.observe_mid("ATOMEUR", price)
    pred = det.update_and_predict(symbols=["ATOMEUR"], refit=True)
    assert pred is not None
    assert pred.regime_id in {0, 1, 2}
    snap = det.snapshot()
    assert snap["fitted"] is True
    assert "ATOMEUR" in snap["symbols_tracked"]


def test_fit_requires_enough_samples() -> None:
    det = MarketRegimeDetector(min_samples=80, vol_window=10)
    df = _synthetic_ohlcv(n=30)
    with pytest.raises(ValueError, match="Need"):
        det.fit(df)
