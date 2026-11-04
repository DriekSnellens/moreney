"""Score AlphaI sentiment labels against later Bitvavo opens.

The entry is the open of the first 15-minute bar at or after the label.
The exit is the open one horizon later on the same grid. A missing bar, a
gap, or a bar more than one slot after the label drops the call. Neutral
is not scored. The rules are fixed: 1h, 4h, 24h and 7d, plus a high-
relevance cut at 8 and one majority vote per coin per UTC day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BAR_MS = 15 * 60 * 1000
HORIZONS: tuple[tuple[str, int], ...] = (
    ("1h", 4),
    ("4h", 16),
    ("24h", 96),
    ("7d", 96 * 7),
)
DAILY_HORIZONS: tuple[str, ...] = ("24h", "7d")
HIGH_RELEVANCE = 8
BULLISH = frozenset({"positive", "bullish", "very_bullish"})
BEARISH = frozenset({"negative", "bearish", "very_bearish"})
NEUTRAL = frozenset({"neutral"})


def side_of(sentiment: str) -> int | None:
    """+1 bullish, -1 bearish, 0 neutral, None when the label is unknown."""
    label = str(sentiment or "").strip().lower()
    if label in BULLISH:
        return 1
    if label in BEARISH:
        return -1
    if label in NEUTRAL:
        return 0
    return None


def majority_side(sides: list[int]) -> int | None:
    """Directional majority. A tie, or an empty list, is not a call."""
    pos = sum(1 for side in sides if side > 0)
    neg = sum(1 for side in sides if side < 0)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return None


def forward_return(
    ts: np.ndarray,
    opens: np.ndarray,
    published_ms: int,
    horizon_bars: int,
) -> tuple[float, int, int] | None:
    """Return (return, entry_ms, exit_ms) from the next aligned open.

    ``published_ms`` may be the headline time or the next UTC midnight.
    The entry bar has to start within one 15-minute slot of that time.
    """
    if horizon_bars <= 0 or len(ts) == 0:
        return None
    i = int(np.searchsorted(ts, int(published_ms), side="left"))
    if i >= len(ts):
        return None
    entry_ms = int(ts[i])
    if entry_ms - int(published_ms) > BAR_MS:
        return None
    j = i + int(horizon_bars)
    if j >= len(ts):
        return None
    exit_ms = int(ts[j])
    if exit_ms - entry_ms != int(horizon_bars) * BAR_MS:
        return None
    entry = float(opens[i])
    exit_px = float(opens[j])
    if entry <= 0 or exit_px <= 0:
        return None
    return exit_px / entry - 1.0, entry_ms, exit_ms


def price_at(ts: np.ndarray, opens: np.ndarray, when_ms: int) -> float | None:
    i = int(np.searchsorted(ts, int(when_ms), side="left"))
    if i >= len(ts) or int(ts[i]) != int(when_ms):
        return None
    px = float(opens[i])
    return px if px > 0 else None


def is_hit(side: int, ret: float) -> bool:
    if side > 0:
        return ret > 0.0
    if side < 0:
        return ret < 0.0
    return False


@dataclass(frozen=True)
class Rate:
    n: int
    hit_pct: float
    bull_n: int
    bull_hit_pct: float
    bear_n: int
    bear_hit_pct: float
    up_pct: float
    null_pct: float
    mean_bull_pct: float
    mean_bear_pct: float

    @property
    def lift_pp(self) -> float:
        """Hit rate minus the hit rate of labels shuffled against this tape."""
        return self.hit_pct - self.null_pct


def rate_of(pairs: list[tuple[int, float]]) -> Rate | None:
    """Hit rate of (side, return) pairs. Zero return is a miss."""
    n = len(pairs)
    if n == 0:
        return None
    hits = sum(1 for side, ret in pairs if is_hit(side, ret))
    bulls = [ret for side, ret in pairs if side > 0]
    bears = [ret for side, ret in pairs if side < 0]
    ups = sum(1 for _, ret in pairs if ret > 0.0)
    downs = sum(1 for _, ret in pairs if ret < 0.0)
    null = (len(bulls) / n) * (ups / n) + (len(bears) / n) * (downs / n)
    return Rate(
        n=n,
        hit_pct=100.0 * hits / n,
        bull_n=len(bulls),
        bull_hit_pct=100.0 * sum(1 for ret in bulls if ret > 0.0) / len(bulls) if bulls else 0.0,
        bear_n=len(bears),
        bear_hit_pct=100.0 * sum(1 for ret in bears if ret < 0.0) / len(bears) if bears else 0.0,
        up_pct=100.0 * ups / n,
        null_pct=100.0 * null,
        mean_bull_pct=100.0 * float(np.mean(bulls)) if bulls else 0.0,
        mean_bear_pct=100.0 * float(np.mean(bears)) if bears else 0.0,
    )
