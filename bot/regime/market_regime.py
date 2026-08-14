"""HMM market-regime & toxic-flow detector (hmmlearn GaussianHMM).

Three canonical regimes after volatility/return sorting:
  0 — LOW_VOL / sideways — ideal for market making
  1 — UP_TREND / bullish volatility — aggressive sells (EUR harvest)
  2 — TOXIC_FLOW / dump — cancel bids, REDUCE_ONLY
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REGIME_LOW_VOL = 0
REGIME_UP_TREND = 1
REGIME_TOXIC_FLOW = 2

REGIME_LABELS = {
    REGIME_LOW_VOL: "LOW_VOL",
    REGIME_UP_TREND: "UP_TREND",
    REGIME_TOXIC_FLOW: "TOXIC_FLOW",
}


@dataclass(frozen=True, slots=True)
class RegimePrediction:
    """Latest HMM state mapped onto the canonical 0/1/2 regime scale."""

    regime_id: int
    raw_state: int
    is_toxic_flow: bool
    label: str
    mean_return: float
    mean_volatility: float
    confidence: float = 0.0

    @property
    def reduce_only(self) -> bool:
        return self.is_toxic_flow


class MarketRegimeDetector:
    """Fit a 3-state Gaussian HMM on log-returns + rolling volatility.

    Component indices from ``hmmlearn`` are remapped after every fit so that:
    * lowest-vol state → regime 0
    * remaining non-toxic state → regime 1 (up-trend / bullish vol)
    * highest (-return + vol) score → regime 2 (toxic dump)
    """

    def __init__(
        self,
        *,
        n_states: int = 3,
        vol_window: int = 10,
        n_iter: int = 100,
        min_samples: int = 60,
        history_len: int = 500,
        random_state: int = 42,
    ) -> None:
        if n_states != 3:
            raise ValueError("MarketRegimeDetector expects exactly 3 HMM states")
        self.n_states = n_states
        self.vol_window = max(2, int(vol_window))
        self.n_iter = max(10, int(n_iter))
        self.min_samples = max(self.vol_window + 15, int(min_samples))
        self.history_len = max(self.min_samples, int(history_len))
        self.random_state = int(random_state)

        self._model: Any | None = None
        self._raw_to_canonical: dict[int, int] = {}
        self._canonical_stats: dict[int, tuple[float, float]] = {}
        self._closes: dict[str, deque[float]] = {}
        self._last_by_symbol: dict[str, RegimePrediction] = {}
        self._last_global: RegimePrediction | None = None
        self._fit_count = 0

    # ------------------------------------------------------------------ features

    def prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        """Build ``[log_return, rolling_vol]`` features from OHLCV-like data.

        Accepts columns ``close`` (required) and optional ``high``/``low`` for a
        true-range volatility proxy; otherwise uses rolling std of log-returns.
        """
        if df is None or df.empty:
            return np.empty((0, 2), dtype=float)
        frame = df.copy()
        if "close" not in frame.columns:
            raise ValueError("prepare_features requires a 'close' column")
        close = pd.to_numeric(frame["close"], errors="coerce").astype(float)
        log_ret = np.log(close / close.shift(1))

        if {"high", "low"}.issubset(frame.columns):
            high = pd.to_numeric(frame["high"], errors="coerce").astype(float)
            low = pd.to_numeric(frame["low"], errors="coerce").astype(float)
            prev_close = close.shift(1)
            tr = pd.concat(
                [
                    (high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs(),
                ],
                axis=1,
            ).max(axis=1)
            # ATR-style vol scaled by price level → dimensionless.
            atr = tr.rolling(self.vol_window, min_periods=max(2, self.vol_window // 2)).mean()
            vol = atr / close.replace(0.0, np.nan)
        else:
            vol = log_ret.rolling(
                self.vol_window, min_periods=max(2, self.vol_window // 2)
            ).std()

        feats = pd.DataFrame({"log_return": log_ret, "volatility": vol}).dropna()
        if feats.empty:
            return np.empty((0, 2), dtype=float)
        # Clip extreme outliers so one flash tick cannot dominate EM.
        feats["log_return"] = feats["log_return"].clip(-0.05, 0.05)
        feats["volatility"] = feats["volatility"].clip(lower=0.0, upper=0.05)
        return feats.to_numpy(dtype=float)

    # ------------------------------------------------------------------ fit / predict

    def fit(self, df: pd.DataFrame) -> MarketRegimeDetector:
        """Train ``GaussianHMM`` and remap states onto canonical regimes."""
        from hmmlearn.hmm import GaussianHMM

        X = self.prepare_features(df)
        if len(X) < self.min_samples:
            raise ValueError(
                f"Need ≥{self.min_samples} feature rows to fit HMM, got {len(X)}"
            )
        model = GaussianHMM(
            n_components=self.n_states,
            covariance_type="diag",
            n_iter=self.n_iter,
            random_state=self.random_state,
        )
        model.fit(X)
        self._model = model
        self._raw_to_canonical, self._canonical_stats = self._map_states(model.means_)
        self._fit_count += 1
        logger.info(
            "HMM fitted samples=%s mapping=%s stats=%s",
            len(X),
            self._raw_to_canonical,
            {REGIME_LABELS[k]: v for k, v in self._canonical_stats.items()},
        )
        return self

    def predict_regime(self, df: pd.DataFrame) -> RegimePrediction:
        """Return the canonical regime for the latest observation in ``df``."""
        if self._model is None:
            raise RuntimeError("MarketRegimeDetector.fit() must be called first")
        X = self.prepare_features(df)
        if len(X) == 0:
            raise ValueError("No valid features to predict")
        raw_states = self._model.predict(X)
        raw = int(raw_states[-1])
        regime_id = int(self._raw_to_canonical.get(raw, raw))
        mean_ret, mean_vol = self._canonical_stats.get(regime_id, (0.0, 0.0))
        # Posterior confidence when available.
        confidence = 0.0
        try:
            posteriors = self._model.predict_proba(X)
            confidence = float(posteriors[-1, raw])
        except Exception:  # noqa: BLE001 — hmmlearn API variance
            confidence = 0.0
        return RegimePrediction(
            regime_id=regime_id,
            raw_state=raw,
            is_toxic_flow=regime_id == REGIME_TOXIC_FLOW,
            label=REGIME_LABELS.get(regime_id, f"STATE_{regime_id}"),
            mean_return=float(mean_ret),
            mean_volatility=float(mean_vol),
            confidence=confidence,
        )

    @staticmethod
    def _map_states(
        means: np.ndarray,
    ) -> tuple[dict[int, int], dict[int, tuple[float, float]]]:
        """Sort HMM components: low-vol → 0, toxic dump → 2, remainder → 1."""
        n = len(means)
        # Toxic score: strongly negative mean return + elevated volatility.
        toxic_scores = [float(-means[i, 0] + means[i, 1]) for i in range(n)]
        toxic_raw = int(max(range(n), key=lambda i: toxic_scores[i]))
        remaining = [i for i in range(n) if i != toxic_raw]
        low_vol_raw = int(min(remaining, key=lambda i: float(means[i, 1])))
        up_raw = int(next(i for i in remaining if i != low_vol_raw))
        mapping = {
            low_vol_raw: REGIME_LOW_VOL,
            up_raw: REGIME_UP_TREND,
            toxic_raw: REGIME_TOXIC_FLOW,
        }
        stats = {
            REGIME_LOW_VOL: (float(means[low_vol_raw, 0]), float(means[low_vol_raw, 1])),
            REGIME_UP_TREND: (float(means[up_raw, 0]), float(means[up_raw, 1])),
            REGIME_TOXIC_FLOW: (float(means[toxic_raw, 0]), float(means[toxic_raw, 1])),
        }
        return mapping, stats

    # ------------------------------------------------------------------ online mids

    def observe_mid(self, symbol: str, mid: float) -> None:
        """Append a mid/close tick for online bar construction."""
        if mid <= 0 or not np.isfinite(mid):
            return
        key = symbol.upper()
        buf = self._closes.setdefault(key, deque(maxlen=self.history_len))
        buf.append(float(mid))

    def frame_for(self, symbol: str) -> pd.DataFrame | None:
        """Synthetic OHLCV frame from observed mids (close=high=low=open)."""
        buf = self._closes.get(symbol.upper())
        if not buf or len(buf) < self.min_samples:
            return None
        closes = np.asarray(buf, dtype=float)
        return pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": np.ones(len(closes), dtype=float),
            }
        )

    def combined_frame(self, symbols: list[str] | None = None) -> pd.DataFrame | None:
        """Equal-weight basket close series across symbols (portfolio regime)."""
        keys = symbols or list(self._closes.keys())
        series: list[pd.Series] = []
        for key in keys:
            buf = self._closes.get(key.upper())
            if not buf or len(buf) < self.min_samples:
                continue
            # Normalize to 1.0 start so different price levels combine.
            arr = np.asarray(buf, dtype=float)
            if arr[0] <= 0:
                continue
            series.append(pd.Series(arr / arr[0], name=key.upper()))
        if not series:
            return None
        aligned = pd.concat(series, axis=1).dropna(how="any")
        if aligned.empty or len(aligned) < self.min_samples:
            return None
        basket = aligned.mean(axis=1).to_numpy(dtype=float)
        return pd.DataFrame(
            {
                "open": basket,
                "high": basket,
                "low": basket,
                "close": basket,
                "volume": np.ones(len(basket), dtype=float),
            }
        )

    def update_and_predict(
        self,
        *,
        symbols: list[str] | None = None,
        refit: bool = False,
    ) -> RegimePrediction | None:
        """Optionally refit on the basket, then predict the global regime."""
        frame = self.combined_frame(symbols)
        if frame is None:
            return self._last_global
        try:
            if refit or self._model is None:
                self.fit(frame)
            pred = self.predict_regime(frame)
        except Exception as exc:  # noqa: BLE001 — keep trading loop alive
            logger.warning("HMM regime update failed: %s", exc)
            return self._last_global
        self._last_global = pred
        return pred

    @property
    def last_prediction(self) -> RegimePrediction | None:
        return self._last_global

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    def snapshot(self) -> dict[str, Any]:
        pred = self._last_global
        return {
            "fitted": self.is_fitted,
            "fit_count": self._fit_count,
            "symbols_tracked": sorted(self._closes.keys()),
            "regime_id": pred.regime_id if pred else None,
            "label": pred.label if pred else None,
            "is_toxic_flow": pred.is_toxic_flow if pred else False,
            "reduce_only": pred.reduce_only if pred else False,
            "confidence": pred.confidence if pred else None,
            "mean_return": pred.mean_return if pred else None,
            "mean_volatility": pred.mean_volatility if pred else None,
        }
