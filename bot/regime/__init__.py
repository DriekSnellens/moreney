"""Market regime detection."""

from bot.regime.detector import RegimeDetector
from bot.regime.market_regime import (
    REGIME_BULLISH,
    REGIME_LOW_VOL,
    REGIME_SIDEWAYS,
    REGIME_TOXIC_DUMP,
    REGIME_TOXIC_FLOW,
    REGIME_UP_TREND,
    MarketRegimeDetector,
    RegimePrediction,
)

__all__ = [
    "REGIME_BULLISH",
    "REGIME_LOW_VOL",
    "REGIME_SIDEWAYS",
    "REGIME_TOXIC_DUMP",
    "REGIME_TOXIC_FLOW",
    "REGIME_UP_TREND",
    "MarketRegimeDetector",
    "RegimeDetector",
    "RegimePrediction",
]
