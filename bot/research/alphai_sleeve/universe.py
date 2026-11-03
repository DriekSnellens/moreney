"""Bitvavo EUR alts. BTC and cash pegs stay outside. Nothing is armed."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

STABLE_BASES = frozenset(
    {
        "EURC",
        "EURCV",
        "EUROP",
        "USDC",
        "USDCV",
        "USDT",
        "DAI",
        "FRAX",
        "TUSD",
        "USDE",
        "FDUSD",
        "PYUSD",
        "USD",
        "EUR",
    }
)
NOT_ALT = frozenset({"BTC"}) | STABLE_BASES
LISTED_MIN_QUOTE = 10_000.0
DAILY = Path("data/research/alphai_sleeve/1d")
INTRADAY = Path("data/research/alphai_sleeve/15m")
DESK_INTRADAY = Path("data/research/swing_grid/15m")


def alt_bases(markets: list[dict[str, Any]]) -> tuple[str, ...]:
    """Trading EUR bases that are alts. Order is alphabetical."""
    bases = {
        str(row.get("base") or "").upper()
        for row in markets
        if row.get("quote") == "EUR" and row.get("status") == "trading"
    }
    return tuple(sorted(base for base in bases if base and base not in NOT_ALT))


def _get(url: str) -> Any:
    last: Exception | None = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 400:
                return None
            if exc.code == 429:
                time.sleep(2.0 + attempt)
                continue
            time.sleep(0.4 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"GET failed for {url}") from last


def fetch_markets() -> list[dict[str, Any]]:
    data = _get("https://api.bitvavo.com/v2/markets")
    if not isinstance(data, list):
        raise RuntimeError("unexpected Bitvavo markets payload")
    return [row for row in data if isinstance(row, dict)]


def ensure_daily(bases: tuple[str, ...] | list[str], cache: Path = DAILY) -> int:
    """Cache one daily history per base. Returns how many files are present."""
    cache.mkdir(parents=True, exist_ok=True)
    present = 0
    for offset, base in enumerate(bases, start=1):
        path = cache / f"{base}.json"
        if path.exists() and path.stat().st_size > 2:
            present += 1
            continue
        rows = _get(f"https://api.bitvavo.com/v2/{base}-EUR/candles?interval=1d&limit=1440")
        if isinstance(rows, list) and rows:
            path.write_text(json.dumps(rows), encoding="utf-8")
            present += 1
        if offset % 40 == 0:
            print(f"daily {offset}/{len(bases)} cached {present}", flush=True)
        time.sleep(0.05)
    return present


def _candle_page(market: str, end_ms: int) -> list[list[float]]:
    url = f"https://api.bitvavo.com/v2/{market}/candles?interval=15m&limit=1440&end={int(end_ms)}"
    rows = _get(url)
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        out.append(
            [
                int(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            ]
        )
    out.sort()
    return out


def _fetch_intraday(base: str, start_ms: int, end_ms: int) -> list[list[float]]:
    have: dict[int, list[float]] = {}
    cursor = end_ms
    for _ in range(80):
        page = _candle_page(f"{base}-EUR", cursor)
        if not page:
            break
        for row in page:
            if row[0] >= start_ms:
                have[int(row[0])] = row
        oldest = int(page[0][0])
        if oldest <= start_ms or len(page) < 1000:
            break
        cursor = oldest - 1
        time.sleep(0.08)
    return [have[key] for key in sorted(have)]


def _write_json(path: Path, payload: str) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _store_intraday(base: str, cache: Path, start_ms: int, end_ms: int) -> bool:
    path = cache / f"{base}.json"
    if path.exists() and path.stat().st_size > 10:
        return True
    desk = DESK_INTRADAY / f"{base}.json"
    if desk.exists() and desk.stat().st_size > 10:
        _write_json(path, desk.read_text(encoding="utf-8"))
        return True
    print(f"15m download {base}", flush=True)
    try:
        rows = _fetch_intraday(base, start_ms, end_ms)
    except RuntimeError as exc:
        print(f"15m failed {base} {exc}", flush=True)
        return False
    if not rows:
        print(f"15m empty {base}", flush=True)
        return False
    _write_json(path, json.dumps(rows))
    print(f"15m stored {base} {len(rows)}", flush=True)
    return True


def ensure_intraday(
    bases: tuple[str, ...] | list[str],
    cache: Path = INTRADAY,
    *,
    start_ms: int = 1704067200000,
) -> int:
    """Cache 15-minute bars from 2024-01-01. Returns how many files exist."""
    cache.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() * 1000)
    present = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(_store_intraday, base, cache, start_ms, end_ms) for base in bases]
        for offset, job in enumerate(jobs, start=1):
            present += int(job.result())
            if offset % 20 == 0:
                print(f"15m {offset}/{len(bases)} cached {present}", flush=True)
    return present
