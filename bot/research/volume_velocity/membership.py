"""Causal membership of the daily quote-volume section.

A market is in the section on day D when the median quote volume of the prior
``window`` completed days falls inside ``[lo, hi]``. Day D itself is not used,
so a burst that inflates today's turnover cannot put the name into the section.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DAY_MS = 86_400_000
CACHE_1D = Path("data/research/bitvavo_1d_all")


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def quote_vol(bar: Sequence[float]) -> float:
    close = float(bar[4])
    vol = float(bar[5])
    if close <= 0 or vol <= 0:
        return 0.0
    return vol * close


def member_days(
    daily: Mapping[str, Sequence[Sequence[float]]],
    *,
    start_ms: int,
    end_ms: int,
    lo: float,
    hi: float,
    window: int = 30,
) -> list[dict[str, Any]]:
    """One row per (base, day) inside the section. ``baseline_eur`` is the prior median."""
    out: list[dict[str, Any]] = []
    for base, rows in daily.items():
        series = [(int(r[0]), quote_vol(r)) for r in rows if quote_vol(r) > 0]
        if len(series) <= window:
            continue
        for i in range(window, len(series)):
            ts = series[i][0]
            if ts < start_ms or ts > end_ms:
                continue
            prior = [series[j][1] for j in range(i - window, i)]
            med = float(statistics.median(prior))
            if lo <= med <= hi:
                out.append({"base": str(base), "day_ms": ts, "baseline_eur": med})
    out.sort(key=lambda r: (r["day_ms"], r["base"]))
    return out


def load_daily(path: Path | None = None) -> dict[str, list[list[float]]]:
    root = path or CACHE_1D
    out: dict[str, list[list[float]]] = {}
    for file in sorted(root.glob("*.json")):
        try:
            rows = json.loads(file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rows, list) and rows:
            out[file.stem] = rows
    return out
