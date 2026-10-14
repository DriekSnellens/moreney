"""Free external series with multi-year history, cached on disk.

* Fear & Greed (alternative.me): daily, since 2018, no key.
* Binance USDT-perp funding (fapi): 8h settlements, since 2019, no key.

Both are read as-of the signal date only. No per-coin branches.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
FUNDING_URL = (
    "https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&startTime={start}&limit=1000"
)
DEFAULT_CACHE = Path("data/research/external")


def day_of_ms(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


def fear_greed_by_day(
    cache_dir: Path | str = DEFAULT_CACHE, *, refresh: bool = False
) -> dict[str, int]:
    """``{YYYY-MM-DD: value}`` for the whole published history."""
    cache = Path(cache_dir) / "fear_greed.json"
    if cache.exists() and not refresh:
        raw = json.loads(cache.read_text())
    else:
        raw = _get(FNG_URL).get("data") or []
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw))
    return {day_of_ms(int(x["timestamp"]) * 1000): int(x["value"]) for x in raw}


def funding_history(
    bases: Iterable[str],
    *,
    start_ms: int = 1_704_067_200_000,
    quote: str = "USDT",
    cache_dir: Path | str = DEFAULT_CACHE,
    refresh: bool = False,
    pause_sec: float = 0.2,
) -> dict[str, list[tuple[int, float]]]:
    """``{base: [(settle_ms, rate), ...]}`` from Binance perps, paged by 1000."""
    cache = Path(cache_dir) / "binance_funding.json"
    have: dict[str, list[tuple[int, float]]] = {}
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())
        have = {k: [(int(a), float(b)) for a, b in v] for k, v in cached.items()}
    out: dict[str, list[tuple[int, float]]] = {}
    changed = False
    for base in bases:
        rows = list(have.get(base) or [])
        cursor = rows[-1][0] + 1 if rows else int(start_ms)
        for _ in range(12):
            try:
                page = _get(FUNDING_URL.format(symbol=f"{base}{quote}", start=cursor))
            except Exception:  # noqa: BLE001
                page = []
            if not page:
                break
            rows.extend((int(x["fundingTime"]), float(x["fundingRate"])) for x in page)
            changed = True
            if len(page) < 1000:
                break
            cursor = int(page[-1]["fundingTime"]) + 1
            time.sleep(pause_sec)
        out[base] = rows
    if changed:
        cache.parent.mkdir(parents=True, exist_ok=True)
        merged = {**have, **out}
        cache.write_text(json.dumps({k: [[a, b] for a, b in v] for k, v in merged.items()}))
    return out


def funding_daily_mean(history: dict[str, list[tuple[int, float]]]) -> dict[str, dict[str, float]]:
    """Mean settlement rate per UTC day per base."""
    out: dict[str, dict[str, float]] = {}
    for base, rows in history.items():
        acc: dict[str, list[float]] = {}
        for ts, rate in rows:
            acc.setdefault(day_of_ms(ts), []).append(float(rate))
        out[base] = {d: sum(v) / len(v) for d, v in acc.items()}
    return out
