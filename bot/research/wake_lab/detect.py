"""Causal detector for a decline, a dormant shelf, and the first interest day.

Bars are ``[ts, open, high, low, close, volume]`` with volume in base units.
Quote volume is volume times close. Every check uses bars at or before the
signal close, so a scan can be replayed without seeing the later spike.
No per-coin branches.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class WakeSpec:
    """Thresholds for the three phases. Tuned on the shape, then frozen."""

    decline_lookback: int = 90
    min_decline_days: int = 30
    min_drawdown: float = 0.40
    base_len: int = 21
    flush_room: int = 10
    max_base_range: float = 0.35
    max_median_abs_ret: float = 0.035
    wide_ret: float = 0.08
    max_wide_days: int = 2
    lift_min: float = 0.15
    lift_max: float = 0.55
    min_day_ret: float = 0.08
    max_day_ret: float = 0.70
    vol_mult: float = 3.0
    min_spark_qvol: float = 20_000.0
    quiet_ratio: float = 0.50
    still_below: float = 0.70
    cooldown: int = 8


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def qvol(row: Sequence[float]) -> float:
    return float(row[5]) * float(row[4])


def _median(xs: Sequence[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def likeness(drawdown: float, base_range: float, vol_multiple: float, lift: float) -> float:
    """How close the four measured phases sit to a deep, quiet, early wake."""
    depth = min(max(drawdown, 0.0) / 0.75, 1.0)
    tight = max(0.0, 1.0 - base_range / 0.35)
    loud = min(max(vol_multiple, 0.0) / 6.0, 1.0)
    early = 0.0 if lift < 0.15 or lift > 0.55 else max(0.0, 1.0 - abs(lift - 0.30) / 0.25)
    return (depth + tight + loud + early) / 4.0


def _shelf(
    rows: Sequence[Sequence[float]], end: int, length: int, spec: WakeSpec
) -> dict[str, float] | None:
    """Quiet window ending at ``end`` inclusive, or None."""
    start = end - length + 1
    if start < 1:
        return None
    closes = [float(rows[j][4]) for j in range(start, end + 1)]
    if any(c <= 0 for c in closes):
        return None
    med = _median(closes)
    if med <= 0:
        return None
    width = (max(closes) - min(closes)) / med
    if width > spec.max_base_range:
        return None
    abs_rets: list[float] = []
    wide = 0
    for j in range(start, end + 1):
        prev = float(rows[j - 1][4])
        if prev <= 0:
            return None
        ret = abs(float(rows[j][4]) / prev - 1.0)
        abs_rets.append(ret)
        if ret > spec.wide_ret:
            wide += 1
    med_abs = _median(abs_rets)
    if med_abs > spec.max_median_abs_ret or wide > spec.max_wide_days:
        return None
    vols = [qvol(rows[j]) for j in range(start, end + 1)]
    return {
        "start": float(start),
        "end": float(end),
        "median_close": med,
        "range": width,
        "median_abs_ret": med_abs,
        "wide_days": float(wide),
        "median_qvol": _median(vols),
    }


def _declined(
    rows: Sequence[Sequence[float]],
    shelf_start: int,
    shelf: Mapping[str, float],
    spec: WakeSpec,
) -> dict[str, float] | None:
    if shelf_start < spec.min_decline_days:
        return None
    look_start = max(0, shelf_start - spec.decline_lookback)
    highs = [float(rows[j][2]) for j in range(look_start, shelf_start)]
    prior_high = max(highs) if highs else 0.0
    med = float(shelf["median_close"])
    if prior_high <= 0 or med <= 0:
        return None
    drawdown = 1.0 - med / prior_high
    if drawdown < spec.min_drawdown:
        return None
    active = sorted(qvol(rows[j]) for j in range(look_start, shelf_start))
    if not active:
        return None
    top = active[int(len(active) * 0.70) :] or active[-1:]
    active_qvol = _median(top)
    shelf_qvol = float(shelf["median_qvol"])
    if active_qvol > 0 and shelf_qvol > spec.quiet_ratio * active_qvol:
        return None
    return {"prior_high": prior_high, "drawdown": drawdown, "active_qvol": active_qvol}


def detect_wakes(
    rows: Sequence[Sequence[float]],
    spec: WakeSpec | None = None,
) -> list[dict[str, Any]]:
    """Signal closes where a dormant shelf wakes, still under the old high."""
    spec = spec or WakeSpec()
    n = len(rows)
    need = spec.min_decline_days + spec.base_len
    out: list[dict[str, Any]] = []
    last = -(10**9)
    for i in range(need, n):
        if i - last <= spec.cooldown:
            continue
        prev_c = float(rows[i - 1][4])
        close = float(rows[i][4])
        if prev_c <= 0 or close <= 0:
            continue
        day_ret = close / prev_c - 1.0
        if day_ret < spec.min_day_ret or day_ret > spec.max_day_ret:
            continue
        spark_qv = qvol(rows[i])
        if spark_qv < spec.min_spark_qvol:
            continue
        shelf = None
        earliest = max(spec.base_len, i - spec.flush_room)
        for end in range(i - 1, earliest - 1, -1):
            shelf = _shelf(rows, end, spec.base_len, spec)
            if shelf is not None:
                break
        if shelf is None:
            continue
        start = int(shelf["start"])
        declined = _declined(rows, start, shelf, spec)
        if declined is None:
            continue
        low_from = max(start, i - spec.flush_room)
        recent_low = min(float(rows[j][4]) for j in range(low_from, i))
        if recent_low <= 0:
            continue
        lift = close / recent_low - 1.0
        if lift < spec.lift_min or lift > spec.lift_max:
            continue
        prior_high = float(declined["prior_high"])
        if close > prior_high * spec.still_below:
            continue
        shelf_qvol = float(shelf["median_qvol"])
        if shelf_qvol > 0 and spark_qv < spec.vol_mult * shelf_qvol:
            continue
        multiple = spark_qv / shelf_qvol if shelf_qvol > 0 else spec.vol_mult
        signal = {
            "idx": i,
            "ts": int(rows[i][0]),
            "day": day_of(int(rows[i][0])),
            "close": close,
            "day_ret": day_ret,
            "lift": lift,
            "recent_low": recent_low,
            "spark_qvol": spark_qv,
            "vol_mult": multiple,
            "drawdown": float(declined["drawdown"]),
            "prior_high": prior_high,
            "shelf_start": start,
            "shelf_end": int(shelf["end"]),
            "shelf_range": float(shelf["range"]),
            "shelf_median_abs": float(shelf["median_abs_ret"]),
            "shelf_qvol": shelf_qvol,
            "likeness": likeness(
                float(declined["drawdown"]), float(shelf["range"]), multiple, lift
            ),
        }
        out.append(signal)
        last = i
    return out


def detect_volume_pops(
    rows: Sequence[Sequence[float]],
    spec: WakeSpec | None = None,
) -> list[dict[str, Any]]:
    """The interest day alone: up day, expanded volume, no shelf and no decline.

    Same cooldown, day-return band, and quote-volume floor as the wake. The
    volume multiple is against the prior ``base_len`` days, whatever they did.
    """
    spec = spec or WakeSpec()
    n = len(rows)
    out: list[dict[str, Any]] = []
    last = -(10**9)
    for i in range(spec.base_len, n):
        if i - last <= spec.cooldown:
            continue
        prev_c = float(rows[i - 1][4])
        close = float(rows[i][4])
        if prev_c <= 0 or close <= 0:
            continue
        day_ret = close / prev_c - 1.0
        if day_ret < spec.min_day_ret or day_ret > spec.max_day_ret:
            continue
        spark_qv = qvol(rows[i])
        if spark_qv < spec.min_spark_qvol:
            continue
        prior = [qvol(rows[j]) for j in range(i - spec.base_len, i)]
        med = _median(prior)
        if med <= 0 or spark_qv < spec.vol_mult * med:
            continue
        out.append(
            {
                "idx": i,
                "ts": int(rows[i][0]),
                "day": day_of(int(rows[i][0])),
                "close": close,
                "day_ret": day_ret,
                "spark_qvol": spark_qv,
                "vol_mult": spark_qv / med,
            }
        )
        last = i
    return out


def scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: WakeSpec | None = None,
) -> list[dict[str, Any]]:
    """Every wake on the map, most alike first and then by day."""
    spec = spec or WakeSpec()
    found: list[dict[str, Any]] = []
    for base, rows in ohlc.items():
        for sig in detect_wakes(rows, spec):
            found.append({"base": base, **sig})
    found.sort(key=lambda s: (s["day"], -float(s["likeness"])))
    return found


def scan_pops(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: WakeSpec | None = None,
) -> list[dict[str, Any]]:
    """Volume pops with no shelf requirement, for the ablation."""
    spec = spec or WakeSpec()
    found: list[dict[str, Any]] = []
    for base, rows in ohlc.items():
        for sig in detect_volume_pops(rows, spec):
            found.append({"base": base, **sig})
    found.sort(key=lambda s: (s["day"], -float(s["vol_mult"])))
    return found


def forward_audit(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    signals: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int] = (3, 5, 10),
    boom: float = 0.50,
) -> dict[str, Any]:
    """Entry at the next open. A boom is a high that clears ``boom`` above entry."""
    per_h: dict[int, dict[str, list[float]]] = {
        h: {"close": [], "high": [], "low": []} for h in horizons
    }
    n_used = 0
    for sig in signals:
        rows = ohlc.get(str(sig["base"])) or []
        i = int(sig["idx"])
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
            per_h[h]["close"].append(float(seg[-1][4]) / entry - 1.0)
            per_h[h]["high"].append(max(float(r[2]) for r in seg) / entry - 1.0)
            per_h[h]["low"].append(min(float(r[3]) for r in seg) / entry - 1.0)

    def stats(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {}
        ordered = sorted(xs)
        return {
            "n": float(len(xs)),
            "mean": statistics.fmean(xs),
            "median": ordered[len(xs) // 2],
            "p_pos": sum(1 for x in xs if x > 0) / len(xs),
            "p_ge_20": sum(1 for x in xs if x >= 0.20) / len(xs),
            "p_ge_boom": sum(1 for x in xs if x >= boom) / len(xs),
        }

    return {
        "n_signals": len(signals),
        "n_used": n_used,
        "boom": boom,
        "horizons": {h: {k: stats(v) for k, v in bucket.items()} for h, bucket in per_h.items()},
    }


def open_setups(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    signals: Sequence[Mapping[str, Any]],
    *,
    last_day: str,
    fresh_days: int = 7,
    boom: float = 0.50,
) -> list[dict[str, Any]]:
    """Signals in the last ``fresh_days`` whose high has not already cleared ``boom``."""
    last = datetime.strptime(last_day, "%Y-%m-%d").replace(tzinfo=UTC)
    first = (last - timedelta(days=fresh_days)).strftime("%Y-%m-%d")
    out: list[dict[str, Any]] = []
    for sig in signals:
        if str(sig["day"]) < first or str(sig["day"]) > last_day:
            continue
        rows = ohlc.get(str(sig["base"])) or []
        i = int(sig["idx"])
        close = float(sig["close"])
        if close <= 0:
            continue
        later = rows[i:]
        peak = max(float(r[2]) for r in later) / close - 1.0 if later else 0.0
        if peak >= boom:
            continue
        out.append({**sig, "high_since": peak})
    out.sort(key=lambda s: -float(s["likeness"]))
    return out
