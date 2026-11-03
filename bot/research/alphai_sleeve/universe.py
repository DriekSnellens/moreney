"""Bitvavo EUR alts. BTC and cash pegs stay outside. Nothing is armed."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
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
