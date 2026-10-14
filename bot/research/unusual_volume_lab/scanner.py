"""Unusual-volume scanner on completed daily bars, with forward-return audit.

Signal day D: quote volume on D >= ``mult`` x the mean quote volume of the
prior ``avg_days`` days, with a tradable floor on the day's volume. Forward
returns are measured from the next day's open (the first fill a scanner can
get) so the audit is causal. No per-coin branches.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ScanSpec:
    mult: float = 3.0
    avg_days: int = 30
    min_day_qvol_eur: float = 50_000.0
    max_day_qvol_eur: float = 0.0  # 0 = off
    max_abs_day_ret: float | None = None  # keep only "quiet" volume spikes
    min_day_ret: float | None = None


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def qvol(row: Sequence[float]) -> float:
    return float(row[5]) * float(row[4])


def scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: ScanSpec,
    *,
    start: str,
    end: str,
    bar_ms: int = 86_400_000,
) -> list[dict[str, Any]]:
    """All (base, bar) unusual-volume signals in [start, end].

    ``avg_days`` counts bars of the series handed in, so with 4h rows and
    ``bar_ms=4h`` the average covers ``avg_days`` 4h bars.
    """
    out: list[dict[str, Any]] = []
    for base, rows in ohlc.items():
        n = len(rows)
        if n < spec.avg_days + 2:
            continue
        for i in range(spec.avg_days, n):
            row = rows[i]
            day = day_of(row[0])
            if day < start or day > end:
                continue
            qv = qvol(row)
            if qv < spec.min_day_qvol_eur:
                continue
            if spec.max_day_qvol_eur > 0 and qv > spec.max_day_qvol_eur:
                continue
            prior = [qvol(r) for r in rows[i - spec.avg_days : i]]
            avg = statistics.fmean(prior) if prior else 0.0
            if avg <= 0 or qv < spec.mult * avg:
                continue
            o = float(row[1])
            c = float(row[4])
            day_ret = c / o - 1.0 if o > 0 else 0.0
            if spec.max_abs_day_ret is not None and abs(day_ret) > spec.max_abs_day_ret:
                continue
            if spec.min_day_ret is not None and day_ret < spec.min_day_ret:
                continue
            out.append(
                {
                    "base": base,
                    "day": day,
                    "ts": int(row[0]),
                    "bar_ms": bar_ms,
                    "idx": i,
                    "qvol": qv,
                    "vol_mult": qv / avg,
                    "day_ret": day_ret,
                    "close": c,
                }
            )
    out.sort(key=lambda s: (s["day"], -s["vol_mult"]))
    return out


def forward_audit(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    signals: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (1, 3, 5, 10),
) -> dict[str, Any]:
    """Entry at next open. Close-to-entry and max-high-to-entry per horizon."""
    per_h: dict[int, dict[str, list[float]]] = {
        h: {"close": [], "high": [], "low": []} for h in horizons
    }
    n_used = 0
    for s in signals:
        rows = ohlc.get(s["base"]) or []
        i = int(s["idx"])
        if i + 1 >= len(rows):
            continue
        entry = float(rows[i + 1][1])
        if entry <= 0:
            continue
        n_used += 1
        for h in horizons:
            seg = rows[i + 1 : i + 1 + h]
            if len(seg) < h:
                continue
            close = float(seg[-1][4]) / entry - 1.0
            high = max(float(r[2]) for r in seg) / entry - 1.0
            low = min(float(r[3]) for r in seg) / entry - 1.0
            per_h[h]["close"].append(close)
            per_h[h]["high"].append(high)
            per_h[h]["low"].append(low)

    def stats(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {}
        xs_sorted = sorted(xs)
        return {
            "n": len(xs),
            "mean": statistics.fmean(xs),
            "median": xs_sorted[len(xs) // 2],
            "p_pos": sum(1 for x in xs if x > 0) / len(xs),
            "p_ge_10": sum(1 for x in xs if x >= 0.10) / len(xs),
            "p_ge_20": sum(1 for x in xs if x >= 0.20) / len(xs),
            "p_ge_50": sum(1 for x in xs if x >= 0.50) / len(xs),
        }

    return {
        "n_signals": len(signals),
        "n_used": n_used,
        "horizons": {h: {k: stats(v) for k, v in per_h[h].items()} for h in horizons},
    }
