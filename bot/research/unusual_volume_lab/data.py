"""Bitvavo candle cache for every EUR market (1d) plus 4h bars on demand."""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Iterable
from pathlib import Path

MARKETS_URL = "https://api.bitvavo.com/v2/markets"
CANDLES_URL = "https://api.bitvavo.com/v2/{market}/candles?interval={interval}&limit=1440{extra}"
CACHE_1D = Path("data/research/bitvavo_1d_all")
CACHE_4H = Path("data/research/bitvavo_4h")
H4_MS = 4 * 3_600_000
DAY_MS = 86_400_000


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


def eur_bases() -> list[str]:
    rows = _get(MARKETS_URL)
    return sorted(
        str(m["base"]) for m in rows if m.get("quote") == "EUR" and m.get("status") == "trading"
    )


def _norm(rows) -> list[list[float]]:
    out = [
        [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        for r in rows
        if isinstance(r, (list, tuple)) and len(r) >= 6
    ]
    out.sort(key=lambda r: r[0])
    return out


def fetch_series(
    base: str, interval: str, *, start_ms: int, end_ms: int, pause: float = 0.12
) -> list[list[float]]:
    """Walk backwards from ``end_ms`` in 1440-bar pages until ``start_ms``."""
    have: dict[int, list[float]] = {}
    cursor_end = end_ms
    for _ in range(40):
        extra = f"&end={cursor_end}"
        try:
            page = _norm(
                _get(CANDLES_URL.format(market=f"{base}-EUR", interval=interval, extra=extra))
            )
        except Exception:  # noqa: BLE001
            break
        if not page:
            break
        for r in page:
            have[r[0]] = r
        first = page[0][0]
        if first <= start_ms or len(page) < 1400:
            break
        cursor_end = first - 1
        time.sleep(pause)
    return [have[k] for k in sorted(have) if k >= start_ms]


def load_cached(cache: Path, bases: Iterable[str]) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in bases:
        path = cache / f"{base}.json"
        if path.exists():
            try:
                out[base] = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
    return out


def refresh(
    bases: Iterable[str],
    *,
    interval: str,
    cache: Path,
    start_ms: int,
    end_ms: int,
    log=None,
) -> dict[str, list[list[float]]]:
    """Fetch only the missing tail (or whole history) per base and persist."""
    cache.mkdir(parents=True, exist_ok=True)
    bar_ms = DAY_MS if interval == "1d" else H4_MS
    out: dict[str, list[list[float]]] = {}
    for i, base in enumerate(bases):
        path = cache / f"{base}.json"
        rows: list[list[float]] = []
        if path.exists():
            try:
                rows = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                rows = []
        have_to = rows[-1][0] if rows else None
        if have_to is None or have_to < end_ms - bar_ms:
            fresh = fetch_series(
                base, interval, start_ms=(have_to - bar_ms) if have_to else start_ms, end_ms=end_ms
            )
            merged = {r[0]: r for r in rows}
            for r in fresh:
                merged[r[0]] = r
            rows = [merged[k] for k in sorted(merged)]
            path.write_text(json.dumps(rows))
        if rows:
            out[base] = rows
        if log and (i + 1) % 25 == 0:
            log(f"  {interval}: {i + 1} markets")
    return out
