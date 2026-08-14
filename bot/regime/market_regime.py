"""HMM market-regime & toxic-flow detector (hmmlearn GaussianHMM).

Ultra-fast guardrail for capital-velocity market making:
  * REGIME_SIDEWAYS (0) — low risk, full quoting
  * REGIME_BULLISH  (1) — harvest EUR with tighter asks
  * REGIME_TOXIC_DUMP (2) — cancel bids, REDUCE_ONLY, tighten alt cap

``hmmlearn`` assigns arbitrary component IDs; after every ``fit()`` states are
sorted by Risk Score = Volatility − Log_Return (low → high risk).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Canonical regimes (risk-sorted; never trust raw hmmlearn IDs).
REGIME_SIDEWAYS = 0
REGIME_BULLISH = 1
REGIME_TOXIC_DUMP = 2

# Backward-compatible aliases used by older call sites / tests.
REGIME_LOW_VOL = REGIME_SIDEWAYS
REGIME_UP_TREND = REGIME_BULLISH
REGIME_TOXIC_FLOW = REGIME_TOXIC_DUMP

REGIME_LABELS = {
    REGIME_SIDEWAYS: "SIDEWAYS",
    REGIME_BULLISH: "BULLISH",
    REGIME_TOXIC_DUMP: "TOXIC_DUMP",
}


@dataclass(frozen=True, slots=True)
class RegimePrediction:
    """Smoothed HMM regime for the latest candle."""

    regime_id: int
    raw_state: int
    is_toxic_flow: bool
    label: str
    mean_return: float
    mean_volatility: float
    confidence: float = 0.0
    toxic_probability: float = 0.0
    inventory_target_pct: float = 0.30
    consecutive_toxic: int = 0

    @property
    def reduce_only(self) -> bool:
        return self.is_toxic_flow


@dataclass(slots=True)
class _CandleBuilder:
    """Aggregate mid ticks into fixed-timeframe OHLCV candles."""

    timeframe_sec: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    bucket_start: float | None = None

    def update(self, mid: float, *, now: float) -> dict[str, float] | None:
        """Feed a mid; return a completed candle dict when the bucket rolls."""
        if mid <= 0 or not np.isfinite(mid):
            return None
        bucket = now - (now % self.timeframe_sec)
        completed: dict[str, float] | None = None
        if self.bucket_start is None:
            self.bucket_start = bucket
            self.open = self.high = self.low = self.close = mid
            self.volume = 1.0
            return None
        if bucket > self.bucket_start:
            completed = {
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "ts": self.bucket_start,
            }
            self.bucket_start = bucket
            self.open = self.high = self.low = self.close = mid
            self.volume = 1.0
            return completed
        self.high = max(self.high, mid)
        self.low = min(self.low, mid)
        self.close = mid
        self.volume += 1.0
        return None


class MarketRegimeDetector:
    """3-state GaussianHMM on log-returns + ATR(14)/close (or rolling σ).

    Defaults target intraday crypto (5m candles, 500–1000 bar history,
    refit every ~5 hours). Toxic status uses hysteresis so one noisy bar
    cannot flip the bot into REDUCE_ONLY.
    """

    def __init__(
        self,
        *,
        n_states: int = 3,
        atr_window: int = 14,
        vol_window: int | None = None,
        n_iter: int = 100,
        min_samples: int = 80,
        history_len: int = 750,
        candle_timeframe_sec: float = 300.0,
        refit_every_sec: float = 5 * 3600.0,
        toxic_confirm_steps: int = 2,
        toxic_proba_threshold: float = 0.70,
        normal_inventory_pct: float = 0.30,
        toxic_inventory_pct: float = 0.10,
        random_state: int = 42,
    ) -> None:
        if n_states != 3:
            raise ValueError("MarketRegimeDetector expects exactly 3 HMM states")
        # vol_window kept as alias for atr_window (older config knobs).
        atr = int(vol_window) if vol_window is not None else int(atr_window)
        self.n_states = n_states
        self.atr_window = max(2, atr)
        self.vol_window = self.atr_window  # alias used by prepare_features fallback
        self.n_iter = max(10, int(n_iter))
        self.min_samples = max(self.atr_window + 20, int(min_samples))
        self.history_len = max(self.min_samples, int(history_len))
        # Production soft range is 500–1000; never exceed 1000 candles.
        self.history_len = min(1000, self.history_len)
        self.candle_timeframe_sec = max(60.0, float(candle_timeframe_sec))
        self.refit_every_sec = max(60.0, float(refit_every_sec))
        self.toxic_confirm_steps = max(1, int(toxic_confirm_steps))
        self.toxic_proba_threshold = min(0.99, max(0.5, float(toxic_proba_threshold)))
        self.normal_inventory_pct = float(normal_inventory_pct)
        self.toxic_inventory_pct = float(toxic_inventory_pct)
        self.random_state = int(random_state)

        self._model: Any | None = None
        self._raw_to_canonical: dict[int, int] = {}
        self._canonical_stats: dict[int, tuple[float, float]] = {}
        self._candles: dict[str, deque[dict[str, float]]] = {}
        self._builders: dict[str, _CandleBuilder] = {}
        self._last_global: RegimePrediction | None = None
        self._fit_count = 0
        self._last_fit_mono: float | None = None
        self._raw_toxic_streak = 0
        self._smoothed_toxic = False

    # ------------------------------------------------------------------ features

    def prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        """Exact 2-feature pipe: log_returns + normalized ATR(14)/close.

        Falls back to rolling std of returns when high/low are absent.
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
            atr = tr.rolling(
                self.atr_window, min_periods=max(2, self.atr_window // 2)
            ).mean()
            vol = atr / close.replace(0.0, np.nan)
        else:
            vol = log_ret.rolling(
                self.atr_window, min_periods=max(2, self.atr_window // 2)
            ).std()

        feats = pd.DataFrame(
            {"log_return": log_ret, "normalized_volatility": vol}
        ).dropna()
        if feats.empty:
            return np.empty((0, 2), dtype=float)
        # Clip flash outliers so EM is not dominated by one 5m spike.
        feats["log_return"] = feats["log_return"].clip(-0.08, 0.08)
        feats["normalized_volatility"] = feats["normalized_volatility"].clip(
            lower=0.0, upper=0.08
        )
        return feats.to_numpy(dtype=float)

    # ------------------------------------------------------------------ fit / predict

    def fit(self, df: pd.DataFrame) -> MarketRegimeDetector:
        """Train GaussianHMM and sort states by Risk Score = Vol − LogReturn."""
        from hmmlearn.hmm import GaussianHMM

        # Rolling window: keep only the newest history_len bars.
        if len(df) > self.history_len:
            df = df.iloc[-self.history_len :].copy()
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
        self._raw_to_canonical, self._canonical_stats = self._sort_states_by_risk(
            model.means_
        )
        self._fit_count += 1
        self._last_fit_mono = time.monotonic()
        logger.info(
            "HMM fitted samples=%s risk_mapping=%s stats=%s",
            len(X),
            self._raw_to_canonical,
            {REGIME_LABELS[k]: v for k, v in self._canonical_stats.items()},
        )
        return self

    def predict_regime(self, df: pd.DataFrame) -> RegimePrediction:
        """Raw (unsmoothed) canonical regime for the latest candle."""
        if self._model is None:
            raise RuntimeError("MarketRegimeDetector.fit() must be called first")
        X = self.prepare_features(df)
        if len(X) == 0:
            raise ValueError("No valid features to predict")
        raw_states = self._model.predict(X)
        raw = int(raw_states[-1])
        regime_id = int(self._raw_to_canonical.get(raw, raw))
        mean_ret, mean_vol = self._canonical_stats.get(regime_id, (0.0, 0.0))

        toxic_raw = next(
            (
                r
                for r, c in self._raw_to_canonical.items()
                if c == REGIME_TOXIC_DUMP
            ),
            None,
        )
        toxic_probability = 0.0
        confidence = 0.0
        try:
            posteriors = self._model.predict_proba(X)
            confidence = float(posteriors[-1, raw])
            if toxic_raw is not None:
                toxic_probability = float(posteriors[-1, toxic_raw])
        except Exception:  # noqa: BLE001 — hmmlearn API variance
            confidence = 0.0
            toxic_probability = 1.0 if regime_id == REGIME_TOXIC_DUMP else 0.0

        return RegimePrediction(
            regime_id=regime_id,
            raw_state=raw,
            is_toxic_flow=regime_id == REGIME_TOXIC_DUMP,
            label=REGIME_LABELS.get(regime_id, f"STATE_{regime_id}"),
            mean_return=float(mean_ret),
            mean_volatility=float(mean_vol),
            confidence=confidence,
            toxic_probability=toxic_probability,
            inventory_target_pct=self.toxic_inventory_pct
            if regime_id == REGIME_TOXIC_DUMP
            else self.normal_inventory_pct,
            consecutive_toxic=0,
        )

    def get_current_regime(self, df: pd.DataFrame) -> RegimePrediction:
        """Predict + hysteresis: toxic only after confirm streak or high proba."""
        raw_pred = self.predict_regime(df)
        return self._apply_hysteresis(raw_pred)

    def _apply_hysteresis(self, pred: RegimePrediction) -> RegimePrediction:
        """Block whipsaw: need 2 toxic steps OR toxic_proba > threshold."""
        instant_toxic = pred.regime_id == REGIME_TOXIC_DUMP
        high_proba = pred.toxic_probability >= self.toxic_proba_threshold

        if instant_toxic:
            self._raw_toxic_streak += 1
        else:
            self._raw_toxic_streak = 0

        confirmed = self._raw_toxic_streak >= self.toxic_confirm_steps or high_proba
        # Sticky exit: leave toxic only when the latest raw state is non-toxic
        # and proba is no longer elevated (avoids single-bar flip-flops).
        if confirmed:
            self._smoothed_toxic = True
        elif not instant_toxic and pred.toxic_probability < (
            self.toxic_proba_threshold - 0.15
        ):
            self._smoothed_toxic = False

        regime_id = (
            REGIME_TOXIC_DUMP if self._smoothed_toxic else pred.regime_id
        )
        # While smoothed toxic, keep dump label even if raw flicker to sideways.
        if self._smoothed_toxic:
            label = REGIME_LABELS[REGIME_TOXIC_DUMP]
            inventory = self.toxic_inventory_pct
        else:
            label = REGIME_LABELS.get(regime_id, pred.label)
            inventory = self.normal_inventory_pct

        smoothed = RegimePrediction(
            regime_id=regime_id if self._smoothed_toxic else pred.regime_id,
            raw_state=pred.raw_state,
            is_toxic_flow=self._smoothed_toxic,
            label=label,
            mean_return=pred.mean_return,
            mean_volatility=pred.mean_volatility,
            confidence=pred.confidence,
            toxic_probability=pred.toxic_probability,
            inventory_target_pct=inventory,
            consecutive_toxic=self._raw_toxic_streak,
        )
        self._last_global = smoothed
        return smoothed

    @staticmethod
    def _sort_states_by_risk(
        means: np.ndarray,
    ) -> tuple[dict[int, int], dict[int, tuple[float, float]]]:
        """Map raw HMM IDs → SIDEWAYS / BULLISH / TOXIC_DUMP by risk score.

        Risk Score = Volatility − Log_Return
        (high vol + negative return ⇒ highest risk ⇒ TOXIC_DUMP).
        """
        n = len(means)
        risk_scores = [float(means[i, 1] - means[i, 0]) for i in range(n)]
        ordered = sorted(range(n), key=lambda i: risk_scores[i])
        # lowest risk → sideways, mid → bullish, highest → toxic dump
        mapping = {
            ordered[0]: REGIME_SIDEWAYS,
            ordered[1]: REGIME_BULLISH,
            ordered[2]: REGIME_TOXIC_DUMP,
        }
        stats = {
            REGIME_SIDEWAYS: (float(means[ordered[0], 0]), float(means[ordered[0], 1])),
            REGIME_BULLISH: (float(means[ordered[1], 0]), float(means[ordered[1], 1])),
            REGIME_TOXIC_DUMP: (
                float(means[ordered[2], 0]),
                float(means[ordered[2], 1]),
            ),
        }
        return mapping, stats

    # ------------------------------------------------------------------ candles

    def observe_mid(
        self, symbol: str, mid: float, *, now: float | None = None
    ) -> None:
        """Ingest a mid tick; roll completed 5m (default) candles into history."""
        if mid <= 0 or not np.isfinite(mid):
            return
        key = symbol.upper()
        ts = time.time() if now is None else float(now)
        builder = self._builders.setdefault(
            key, _CandleBuilder(timeframe_sec=self.candle_timeframe_sec)
        )
        completed = builder.update(mid, now=ts)
        if completed is None:
            return
        buf = self._candles.setdefault(key, deque(maxlen=self.history_len))
        buf.append(completed)

    def frame_for(self, symbol: str) -> pd.DataFrame | None:
        """OHLCV frame from completed candles for one symbol."""
        buf = self._candles.get(symbol.upper())
        if not buf or len(buf) < self.min_samples:
            return None
        return pd.DataFrame(list(buf))

    def combined_frame(self, symbols: list[str] | None = None) -> pd.DataFrame | None:
        """Equal-weight basket of completed candles (portfolio-level regime)."""
        keys = symbols or list(self._candles.keys())
        frames: list[pd.DataFrame] = []
        for key in keys:
            buf = self._candles.get(key.upper())
            if not buf or len(buf) < max(20, self.min_samples // 2):
                continue
            df = pd.DataFrame(list(buf))
            # Normalize closes so mixed alts share a common scale.
            first = float(df["close"].iloc[0])
            if first <= 0:
                continue
            norm = df.copy()
            for col in ("open", "high", "low", "close"):
                norm[col] = norm[col] / first
            frames.append(norm.reset_index(drop=True))
        if not frames:
            return None
        # Align on index length (newest candles); truncate to shortest.
        min_len = min(len(f) for f in frames)
        if min_len < self.min_samples:
            return None
        trimmed = [f.iloc[-min_len:].reset_index(drop=True) for f in frames]
        basket = pd.DataFrame(
            {
                "open": np.mean([f["open"].to_numpy(dtype=float) for f in trimmed], axis=0),
                "high": np.mean([f["high"].to_numpy(dtype=float) for f in trimmed], axis=0),
                "low": np.mean([f["low"].to_numpy(dtype=float) for f in trimmed], axis=0),
                "close": np.mean(
                    [f["close"].to_numpy(dtype=float) for f in trimmed], axis=0
                ),
                "volume": np.mean(
                    [f["volume"].to_numpy(dtype=float) for f in trimmed], axis=0
                ),
            }
        )
        return basket

    def needs_refit(self, *, now: float | None = None) -> bool:
        """True when the model is missing or the refit interval elapsed."""
        if self._model is None or self._last_fit_mono is None:
            return True
        ts = time.monotonic() if now is None else float(now)
        return (ts - self._last_fit_mono) >= self.refit_every_sec

    def update_and_predict(
        self,
        *,
        symbols: list[str] | None = None,
        refit: bool | None = None,
    ) -> RegimePrediction | None:
        """Refit on a slow cadence, then return hysteresis-smoothed regime."""
        frame = self.combined_frame(symbols)
        if frame is None:
            return self._last_global
        do_fit = self.needs_refit() if refit is None else bool(refit)
        try:
            if do_fit:
                self.fit(frame)
            elif self._model is None:
                return self._last_global
            return self.get_current_regime(frame)
        except Exception as exc:  # noqa: BLE001 — keep trading loop alive
            logger.warning("HMM regime update failed: %s", exc)
            return self._last_global

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
            "candle_timeframe_sec": self.candle_timeframe_sec,
            "refit_every_sec": self.refit_every_sec,
            "symbols_tracked": sorted(self._candles.keys()),
            "regime_id": pred.regime_id if pred else None,
            "label": pred.label if pred else None,
            "is_toxic_flow": pred.is_toxic_flow if pred else False,
            "reduce_only": pred.reduce_only if pred else False,
            "inventory_target_pct": pred.inventory_target_pct if pred else None,
            "confidence": pred.confidence if pred else None,
            "toxic_probability": pred.toxic_probability if pred else None,
            "consecutive_toxic": pred.consecutive_toxic if pred else 0,
            "mean_return": pred.mean_return if pred else None,
            "mean_volatility": pred.mean_volatility if pred else None,
        }
