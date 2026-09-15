"""Rolling markout tracker: mid move after maker fills (1s / 5s / 30s / 60s)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

_BPS = Decimal("10000")
_ZERO = Decimal("0")
_HORIZONS_MS = (1000, 5000, 30000, 60000)


@dataclass
class _PendingMarkout:
    fill_id: str
    opportunity_id: UUID | None
    symbol: str
    side: str  # buy / sell
    venue: str
    strategy: str
    fill_price: Decimal
    mid_at_fill: Decimal
    filled_at_ms: float
    fill_type: str = ""
    captured: dict[int, Decimal] = field(default_factory=dict)


def _bucket_key(venue: str, symbol: str, side: str, fill_type: str = "") -> str:
    base = f"{(venue or '').lower()}|{(symbol or '').upper()}|{(side or '').lower()}"
    ft = (fill_type or "").lower()
    if ft and ft != "unknown":
        return f"{base}|{ft}"
    return base


class MarkoutTracker:
    """Measures adverse selection after fills and exposes a rolling haircut."""

    def __init__(self, *, window: int = 200) -> None:
        self._window = max(20, window)
        self._pending: list[_PendingMarkout] = []
        self._samples: deque[Decimal] = deque(maxlen=self._window)
        self._wins: deque[bool] = deque(maxlen=self._window)
        self._by_horizon: dict[int, deque[Decimal]] = {
            h: deque(maxlen=self._window) for h in _HORIZONS_MS
        }
        self._by_bucket: dict[str, deque[Decimal]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )

    def record_fill(
        self,
        *,
        fill_id: str,
        opportunity_id: UUID | None,
        symbol: str,
        side: str,
        fill_price: Decimal,
        mid: Decimal | None,
        venue: str = "",
        strategy: str = "",
        fill_type: str = "",
    ) -> None:
        if mid is None or mid <= 0 or fill_price <= 0:
            return
        self._pending.append(
            _PendingMarkout(
                fill_id=str(fill_id),
                opportunity_id=opportunity_id,
                symbol=symbol.upper(),
                side=side.lower(),
                venue=str(venue or "").lower(),
                strategy=str(strategy or ""),
                fill_price=fill_price,
                mid_at_fill=mid,
                filled_at_ms=time.time() * 1000.0,
                fill_type=str(fill_type or "").lower(),
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
                primary = item.captured.get(5000, _ZERO)
                self._samples.append(primary)
                self._wins.append(primary <= 0)
                self._by_bucket[
                    _bucket_key(item.venue, item.symbol, item.side, item.fill_type)
                ].append(primary)
            else:
                still.append(item)
        self._pending = still

    def suggested_adverse_bps(
        self,
        *,
        floor: Decimal,
        ceiling: Decimal,
        venue: str = "",
        symbol: str = "",
        side: str = "",
        fill_type: str = "",
    ) -> Decimal:
        """Rolling median 5s adverse markout, clamped to [floor, ceiling].

        When venue/symbol/side/fill_type are given, shrink the bucket median toward
        the global median so thin Bitvavo samples cannot dominate.
        """
        global_samples = list(self._by_horizon[5000]) or list(self._samples)
        global_med = _median(global_samples) if global_samples else floor
        if venue or symbol or side or fill_type:
            # Hierarchical fallback: specific fill_type → without fill_type → global.
            candidates = [
                _bucket_key(venue, symbol, side, fill_type),
                _bucket_key(venue, symbol, side, ""),
            ]
            bucket: list[Decimal] = []
            for key in candidates:
                bucket = list(self._by_bucket.get(key, []))
                if bucket:
                    break
            if bucket:
                local = _median(bucket)
                n = len(bucket)
                alpha = Decimal(n) / Decimal(n + 30)
                blended = alpha * local + (Decimal("1") - alpha) * global_med
                raw = max(floor, blended)
                return min(ceiling, raw)
        if not global_samples:
            return floor
        # Only raise haircut when markouts are adverse; never go below floor.
        raw = max(floor, global_med)
        return min(ceiling, raw)

    def empirical_win_rate(self, *, min_samples: int = 20) -> float | None:
        if len(self._wins) < min_samples:
            return None
        return sum(1 for w in self._wins if w) / len(self._wins)

    def snapshot(self) -> dict[str, Any]:
        def _avg(values: deque[Decimal]) -> str:
            if not values:
                return "0"
            return str(sum(values, _ZERO) / Decimal(len(values)))

        by_venue: dict[str, dict[str, str]] = {}
        for key, samples in self._by_bucket.items():
            if not samples:
                continue
            venue = key.split("|", 1)[0] or "unknown"
            agg = by_venue.setdefault(venue, {"n": "0", "avg_adverse_bps_5s": "0"})
            n = int(agg["n"]) + len(samples)
            prev = Decimal(agg["avg_adverse_bps_5s"])
            total = prev * Decimal(int(agg["n"])) + sum(samples, _ZERO)
            agg["n"] = str(n)
            agg["avg_adverse_bps_5s"] = str(total / Decimal(n) if n else _ZERO)

        return {
            "pending": len(self._pending),
            "samples": len(self._samples),
            "avg_adverse_bps_1s": _avg(self._by_horizon[1000]),
            "avg_adverse_bps_5s": _avg(self._by_horizon[5000]),
            "avg_adverse_bps_30s": _avg(self._by_horizon[30000]),
            "avg_adverse_bps_60s": _avg(self._by_horizon[60000]),
            "empirical_win_rate": self.empirical_win_rate(),
            "by_venue": by_venue,
            "suggested_adverse_bps": str(
                self.suggested_adverse_bps(floor=Decimal("2"), ceiling=Decimal("20"))
            ),
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "samples": [str(s) for s in self._samples],
            "wins": [bool(w) for w in self._wins],
            "by_horizon": {
                str(h): [str(v) for v in vals] for h, vals in self._by_horizon.items()
            },
            "by_bucket": {
                k: [str(v) for v in vals] for k, vals in self._by_bucket.items() if vals
            },
        }

    def import_state(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        self._samples = deque(
            (Decimal(str(x)) for x in (data.get("samples") or [])),
            maxlen=self._window,
        )
        self._wins = deque((bool(x) for x in (data.get("wins") or [])), maxlen=self._window)
        raw_h = data.get("by_horizon") or {}
        if isinstance(raw_h, dict):
            for h in _HORIZONS_MS:
                vals = raw_h.get(str(h)) or []
                self._by_horizon[h] = deque(
                    (Decimal(str(x)) for x in vals), maxlen=self._window
                )
        raw_b = data.get("by_bucket") or {}
        if isinstance(raw_b, dict):
            self._by_bucket = defaultdict(lambda: deque(maxlen=self._window))
            for key, vals in raw_b.items():
                self._by_bucket[str(key)] = deque(
                    (Decimal(str(x)) for x in (vals or [])), maxlen=self._window
                )


def _median(samples: list[Decimal]) -> Decimal:
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]
