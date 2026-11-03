"""Bitvavo 15-minute candle cache. Files stay under data/ and are not committed."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BAR_MS = 15 * 60 * 1000
PAGE = 1440


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


def _norm(rows) -> list[list[float]]:
    out = [
        [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        for r in rows
        if isinstance(r, (list, tuple)) and len(r) >= 6
    ]
    out.sort(key=lambda r: r[0])
    return out


def _page(base: str, end_ms: int) -> list[list[float]]:
    url = (
        f"https://api.bitvavo.com/v2/{base}-EUR/candles?interval=15m&limit={PAGE}&end={int(end_ms)}"
    )
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            return _norm(_get(url))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {400, 404}:
                return []
            if exc.code == 429:
                time.sleep(8.0 + 4.0 * attempt)
                continue
            time.sleep(0.5 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"{base} 15m fetch failed") from last_error


def fetch_base(base: str, start_ms: int, end_ms: int, *, pause: float = 0.12) -> list[list[float]]:
    """Walk backwards in 1440-bar pages until ``start_ms``."""
    have: dict[int, list[float]] = {}
    cursor = int(end_ms)
    for _ in range(90):
        page = _page(base, cursor)
        if not page:
            break
        for row in page:
            if row[0] >= start_ms:
                have[row[0]] = row
        first = int(page[0][0])
        if first <= start_ms or len(page) < 1000:
            break
        cursor = first - 1
        time.sleep(pause)
    return [have[k] for k in sorted(have)]


def cache_path(cache: Path, base: str) -> Path:
    return cache / f"{base}.json"


def load_cache(cache: Path, bases: tuple[str, ...] | list[str]) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in bases:
        path = cache_path(cache, base)
        if not path.exists():
            continue
        try:
            rows = _norm(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if rows:
            out[base] = rows
    return out


def _fresh(rows: list[list[float]], start_ms: int, end_ms: int) -> bool:
    if not rows:
        return False
    return int(rows[0][0]) <= start_ms + BAR_MS and int(rows[-1][0]) >= end_ms - 2 * 86_400_000


def ensure_candles(
    bases: tuple[str, ...] | list[str],
    cache: Path,
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, list[list[float]]]:
    """Load the cache, and download any base that does not already cover the window."""
    cache.mkdir(parents=True, exist_ok=True)
    have = load_cache(cache, bases)
    for base in bases:
        rows = have.get(base)
        if rows and _fresh(rows, start_ms, end_ms):
            print(f"{base} cached {len(rows)} bars", flush=True)
            continue
        print(f"{base} fetching 15m", flush=True)
        rows = fetch_base(base, start_ms, end_ms)
        cache_path(cache, base).write_text(json.dumps(rows, separators=(",", ":")))
        have[base] = rows
        print(f"{base} stored {len(rows)} bars", flush=True)
    return {base: have.get(base, []) for base in bases}
