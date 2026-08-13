"""Rolling markout tracker: mid move after maker fills (1s / 5s / 30s)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

_BPS = Decimal("10000")
_ZERO = Decimal("0")
_HORIZONS_MS = (1000, 5000, 30000)


@dataclass
class _PendingMarkout:
    fill_id: str
    opportunity_id: UUID | None
    symbol: str
    side: str  # buy / sell
    fill_price: Decimal
    mid_at_fill: Decimal
    filled_at_ms: float
    captured: dict[int, Decimal] = field(default_factory=dict)


class MarkoutTracker:
    """Measures adverse selection after fills and exposes a rolling haircut."""

    def __init__(self, *, window: int = 200) -> None:
        self._pending: list[_PendingMarkout] = []
        self._samples: deque[Decimal] = deque(maxlen=max(20, window))
        self._by_horizon: dict[int, deque[Decimal]] = {
            h: deque(maxlen=max(20, window)) for h in _HORIZONS_MS
        }

    def record_fill(
        self,
        *,
        fill_id: str,
        opportunity_id: UUID | None,
        symbol: str,
        side: str,
        fill_price: Decimal,
        mid: Decimal | None,
    ) -> None:
        if mid is None or mid <= 0 or fill_price <= 0:
            return
        self._pending.append(
            _PendingMarkout(
                fill_id=str(fill_id),
                opportunity_id=opportunity_id,
                symbol=symbol.upper(),
                side=side.lower(),
                fill_price=fill_price,
                mid_at_fill=mid,
                filled_at_ms=time.time() * 1000.0,
            )
        )

    def update(self, mids: dict[str, Decimal]) -> None:
        """Advance pending markouts using latest mid per symbol."""
        now = time.time() * 1000.0
        still: list[_PendingMarkout] = []
        for item in self._pending:
            mid = mids.get(item.symbol)
            if mid is None or mid <= 0:
                still.append(item)
                continue
            age = now - item.filled_at_ms
            for horizon in _HORIZONS_MS:
                if horizon in item.captured or age < horizon:
                    continue
                # Signed adverse bps: positive = mid moved against the fill.
                if item.side == "buy":
                    adverse = (mid - item.fill_price) / item.fill_price * _BPS
                else:
                    adverse = (item.fill_price - mid) / item.fill_price * _BPS
                item.captured[horizon] = adverse
                self._by_horizon[horizon].append(adverse)
            if len(item.captured) >= len(_HORIZONS_MS):
                # Use 5s markout as primary EV signal.
                primary = item.captured.get(5000, _ZERO)
                self._samples.append(primary)
            else:
                still.append(item)
        self._pending = still

    def suggested_adverse_bps(self, *, floor: Decimal, ceiling: Decimal) -> Decimal:
        """Rolling median 5s adverse markout, clamped to [floor, ceiling]."""
        samples = list(self._by_horizon[5000]) or list(self._samples)
        if not samples:
            return floor
        samples = sorted(samples)
        median = samples[len(samples) // 2]
        # Only raise haircut when markouts are adverse; never go below floor.
        raw = max(floor, median)
        return min(ceiling, raw)

    def snapshot(self) -> dict[str, Any]:
        def _avg(values: deque[Decimal]) -> str:
            if not values:
                return "0"
            return str(sum(values, _ZERO) / Decimal(len(values)))

        return {
            "pending": len(self._pending),
            "samples": len(self._samples),
            "avg_adverse_bps_1s": _avg(self._by_horizon[1000]),
            "avg_adverse_bps_5s": _avg(self._by_horizon[5000]),
            "avg_adverse_bps_30s": _avg(self._by_horizon[30000]),
            "suggested_adverse_bps": str(
                self.suggested_adverse_bps(floor=Decimal("2"), ceiling=Decimal("20"))
            ),
        }
