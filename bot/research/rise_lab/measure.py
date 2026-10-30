"""Forward-rise horse race across causal daily states.

A state is tagged from the signal close. The rise is the max high, and the
close, of the next sessions divided by the next open. No per-coin branches.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bot.research.moonshot_preimage.engine import _features
from bot.research.wake_lab.detect import WakeSpec, day_of, detect_wakes

PRE_END = "2026-01-01"
PATTERNS = (
    "liquid",
    "quiet",
    "wake",
    "r3_15",
    "xs25",
    "continuation",
    "breakout",
    "live_xs",
    "r3_neg",
    "r3_mild",
    "r3_mid",
    "r3_hot",
    "day_leader",
)


@dataclass(frozen=True)
class RiseSpec:
    min_qvol: float = 20_000.0
    horizon: int = 7
    history: int = 55
    boom: float = 0.50


def _median(xs: Sequence[float]) -> float:
    ordered = sorted(xs)
    return ordered[len(ordered) // 2]


def summarize(highs: Sequence[float], closes: Sequence[float], *, boom: float) -> dict[str, float]:
    n = len(highs)
    if n == 0 or len(closes) != n:
        return {}
    return {
        "n": float(n),
        "high_median": _median(highs),
        "high_p90": sorted(highs)[min(n - 1, int(n * 0.90))],
        "high_mean": statistics.fmean(highs),
        "close_median": _median(closes),
        "p_close_pos": sum(1 for x in closes if x > 0) / n,
        "p_high_20": sum(1 for x in highs if x >= 0.20) / n,
        "p_high_boom": sum(1 for x in highs if x >= boom) / n,
    }


def _tags(feats: Mapping[str, Any], *, wake: bool) -> set[str]:
    r3 = float(feats["r3"])
    tags = {"liquid"}
    if bool(feats["quiet10"]):
        tags.add("quiet")
    if wake:
        tags.add("wake")
    if bool(feats["r3_15"]):
        tags.add("r3_15")
    if bool(feats["xs25"]):
        tags.add("xs25")
    if bool(feats["r3_15"] and feats["xs25"] and feats["trend"]):
        tags.add("continuation")
    if bool(feats["brk20"] and feats["day6"] and feats["vol2"]):
        tags.add("breakout")
    own = float(feats["r10"])
    # Live book is milder: 10-day excess of 3.5% with the coin itself up.
    if own > 0.0 and float(feats["xs10"]) >= 0.035:
        tags.add("live_xs")
    if r3 < 0.0:
        tags.add("r3_neg")
    elif r3 < 0.15:
        tags.add("r3_mild")
    elif r3 < 0.40:
        tags.add("r3_mid")
    else:
        tags.add("r3_hot")
    return tags


def collect(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: RiseSpec | None = None,
    *,
    wake_spec: WakeSpec | None = None,
) -> list[dict[str, Any]]:
    """One row per liquid name-day that has a full forward window."""
    spec = spec or RiseSpec()
    if "BTC" not in ohlc:
        return []
    return _collect(ohlc, spec, wake_spec or WakeSpec())


def _collect(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: RiseSpec,
    wake_spec: WakeSpec,
) -> list[dict[str, Any]]:
    btc_rows = ohlc.get("BTC") or []
    btc_close: dict[str, float] = {}
    btc_order: list[str] = []
    for row in btc_rows:
        day = day_of(int(row[0]))
        if day not in btc_close:
            btc_order.append(day)
        btc_close[day] = float(row[4])
    btc_at = {day: i for i, day in enumerate(btc_order)}
    btc_px = [btc_close[day] for day in btc_order]

    wakes: set[tuple[str, str]] = set()
    for base, rows in ohlc.items():
        if base == "BTC":
            continue
        for sig in detect_wakes(rows, wake_spec):
            wakes.add((base, str(sig["day"])))

    out: list[dict[str, Any]] = []
    by_day: dict[str, list[int]] = {}
    need = spec.history
    horizon = spec.horizon
    for base, rows in ohlc.items():
        if base == "BTC" or len(rows) < need + horizon:
            continue
        days = [day_of(int(r[0])) for r in rows]
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        vols = [float(r[5]) for r in rows]
        last = len(rows) - horizon
        for i in range(need - 1, last):
            day = days[i]
            bi = btc_at.get(day)
            if bi is None or bi + 1 < spec.history:
                continue
            entry = float(rows[i + 1][1])
            if entry <= 0 or closes[i] <= 0:
                continue
            feats = _features(
                closes[i - need + 1 : i + 1],
                highs[i - need + 1 : i + 1],
                lows[i - need + 1 : i + 1],
                vols[i - need + 1 : i + 1],
                btc_px[bi - need + 1 : bi + 1],
            )
            if feats is None or float(feats["qvol"]) < spec.min_qvol:
                continue
            seg = rows[i + 1 : i + 1 + horizon]
            high = max(float(r[2]) for r in seg) / entry - 1.0
            close = float(seg[-1][4]) / entry - 1.0
            tags = _tags(feats, wake=(base, day) in wakes)
            by_day.setdefault(day, []).append(len(out))
            out.append(
                {
                    "base": base,
                    "day": day,
                    "high": high,
                    "close": close,
                    "r3": float(feats["r3"]),
                    "tags": tags,
                }
            )
    for idxs in by_day.values():
        best = max(idxs, key=lambda k: float(out[k]["r3"]))
        out[best]["tags"].add("day_leader")
    return out


def split_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    boom: float = 0.50,
    cut: str = PRE_END,
) -> dict[str, dict[str, dict[str, float]]]:
    """Pattern → period → rise stats. Periods are before ``cut`` and from ``cut``."""
    buckets: dict[str, dict[str, dict[str, list[float]]]] = {
        name: {"before": {"high": [], "close": []}, "after": {"high": [], "close": []}}
        for name in PATTERNS
    }
    for event in events:
        period = "before" if str(event["day"]) < cut else "after"
        tags = event["tags"]
        for name in PATTERNS:
            if name not in tags:
                continue
            buckets[name][period]["high"].append(float(event["high"]))
            buckets[name][period]["close"].append(float(event["close"]))
    return {
        name: {
            period: summarize(parts["high"], parts["close"], boom=boom)
            for period, parts in periods.items()
        }
        for name, periods in buckets.items()
    }
