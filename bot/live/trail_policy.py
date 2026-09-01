"""ATR-scaled soft/hard trailing take-profit helpers for live micro."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class TrailThresholds:
    soft_arm: Decimal
    soft_dd: Decimal
    hard_arm: Decimal
    hard_dd: Decimal
    atr: Decimal


class MarkSeries:
    """Rolling mid marks for ATR + momentum."""

    def __init__(self, maxlen: int = 64) -> None:
        self._marks: Deque[Decimal] = deque(maxlen=max(8, int(maxlen)))

    def push(self, mark: Decimal) -> None:
        if mark > 0:
            self._marks.append(Decimal(str(mark)))

    def __len__(self) -> int:
        return len(self._marks)

    def atr_pct(self) -> Decimal:
        """Mean absolute return over consecutive samples (fraction, not bps)."""
        if len(self._marks) < 3:
            return _ZERO
        total = _ZERO
        n = 0
        prev = self._marks[0]
        for cur in list(self._marks)[1:]:
            if prev > 0:
                total += abs(cur - prev) / prev
                n += 1
            prev = cur
        if n <= 0:
            return _ZERO
        return total / Decimal(n)

    def momentum_return(self) -> Decimal | None:
        if len(self._marks) < 2:
            return None
        first = self._marks[0]
        last = self._marks[-1]
        if first <= 0:
            return None
        return (last - first) / first

    def momentum_return_last(self, n: int) -> Decimal | None:
        """Rolling return over the last ``n`` marks (short-window momentum)."""
        need = max(2, int(n))
        if len(self._marks) < need:
            return None
        window = list(self._marks)[-need:]
        first = window[0]
        last = window[-1]
        if first <= 0:
            return None
        return (last - first) / first

    def last_n_rising(self, n: int = 3) -> bool:
        """True when the last n marks are strictly non-decreasing and last > first of window."""
        need = max(2, int(n))
        if len(self._marks) < need:
            return False
        window = list(self._marks)[-need:]
        for i in range(1, len(window)):
            if window[i] < window[i - 1]:
                return False
        return window[-1] > window[0]

    def last_n_mostly_rising(self, n: int = 3, *, min_up: int = 2) -> bool:
        """True when at least min_up of the last (n-1) steps are up and last ≥ first."""
        need = max(2, int(n))
        if len(self._marks) < need:
            return False
        window = list(self._marks)[-need:]
        ups = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
        return ups >= int(min_up) and window[-1] >= window[0]

    def marks(self) -> list[Decimal]:
        """Copy of rolling mark history (oldest → newest)."""
        return list(self._marks)


def scale_thresholds(
    *,
    atr: Decimal,
    soft_arm_floor: Decimal,
    soft_dd_floor: Decimal,
    hard_arm_floor: Decimal,
    hard_dd_floor: Decimal,
    atr_arm_mult: Decimal,
    atr_dd_mult: Decimal,
    atr_enabled: bool,
) -> TrailThresholds:
    soft_arm = soft_arm_floor
    soft_dd = soft_dd_floor
    hard_arm = hard_arm_floor
    hard_dd = hard_dd_floor
    if atr_enabled and atr > 0:
        soft_arm = max(soft_arm_floor, atr * atr_arm_mult * Decimal("0.7"))
        hard_arm = max(hard_arm_floor, atr * atr_arm_mult)
        soft_dd = max(soft_dd_floor, atr * atr_dd_mult)
        hard_dd = max(hard_dd_floor, atr * atr_dd_mult * Decimal("1.2"))
    # Keep soft < hard arms; soft dd <= hard dd.
    if soft_arm >= hard_arm:
        soft_arm = hard_arm * Decimal("0.6")
    if soft_dd > hard_dd:
        soft_dd = hard_dd
    return TrailThresholds(
        soft_arm=soft_arm,
        soft_dd=soft_dd,
        hard_arm=hard_arm,
        hard_dd=hard_dd,
        atr=atr,
    )


def parse_corr_group(raw: str) -> set[str]:
    return {
        p.strip().upper()
        for p in str(raw or "").split(",")
        if p.strip()
    }
