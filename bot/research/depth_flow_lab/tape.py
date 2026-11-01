"""Load Bitvavo bars and Binance taker prints.

The flow cache is the entry-quality directory. Missing liquid bases are
fetched once and stored there. A base with no USDT market is skipped.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from bot.research.depth_flow_lab.profile import MIN_ADV, day_of
from bot.research.entry_quality_lab.flow import fetch_flow
from bot.research.unusual_volume_lab.data import CACHE_1D, load_cached

FLOW_DIR = Path("data/entry_quality_flow")
START_MS = 1_704_067_200_000  # 2024-01-01 UTC
EXCHANGE_URL = "https://api.binance.com/api/v3/exchangeInfo"


def completed_ohlc(bases: list[str] | None = None) -> dict[str, list[list[float]]]:
    now = datetime.now(UTC)
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000)
    if bases is None:
        bases = [path.stem for path in CACHE_1D.glob("*.json")]
    out: dict[str, list[list[float]]] = {}
    for base, rows in load_cached(CACHE_1D, bases).items():
        done = [row for row in rows if int(row[0]) < today_ms]
        if done:
            out[base] = done
    return out


def liquid_bases(ohlc: dict[str, list[list[float]]]) -> list[str]:
    kept: list[str] = []
    for base, rows in ohlc.items():
        if len(rows) < 80:
            continue
        qvols = [float(row[5]) * float(row[4]) for row in rows]
        hits = 0
        for i in range(20, len(qvols)):
            if sum(qvols[i - 20 : i]) / 20 >= MIN_ADV:
                hits += 1
                if hits >= 30:
                    kept.append(base)
                    break
    return sorted(kept)


def usdt_bases() -> set[str]:
    with urllib.request.urlopen(EXCHANGE_URL, timeout=30) as resp:  # noqa: S310
        raw = json.load(resp)
    out: set[str] = set()
    for row in raw.get("symbols") or []:
        if row.get("quoteAsset") != "USDT" or row.get("status") != "TRADING":
            continue
        symbol = str(row.get("symbol") or "")
        if symbol.endswith("USDT"):
            out.add(symbol[: -len("USDT")])
    return out


def _load_file(path: Path) -> list[list[float]]:
    try:
        rows = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return rows if isinstance(rows, list) else []


def load_flow(bases: list[str], cache_dir: Path = FLOW_DIR) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    if not cache_dir.exists():
        return out
    for base in bases:
        rows = _load_file(cache_dir / f"{base}.json")
        if rows:
            out[base] = rows
    return out


def ensure_flow(
    bases: list[str],
    *,
    cache_dir: Path = FLOW_DIR,
    sleep_s: float = 0.12,
    log=None,
) -> dict[str, list[list[float]]]:
    """Fetch USDT taker history for liquid bases that are not cached yet."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        listed = usdt_bases()
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        if log:
            log(f"exchange info: {exc}")
        listed = None
    out = load_flow(bases, cache_dir)
    for base in bases:
        path = cache_dir / f"{base}.json"
        if base in out:
            continue
        if listed is not None and base not in listed:
            continue
        try:
            rows = fetch_flow(base, start_ms=START_MS, sleep_s=sleep_s)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"flow {base}: {exc}")
            continue
        rows = [row for row in rows if day_of(int(row[0])) < today]
        if rows:
            path.write_text(json.dumps(rows))
            out[base] = rows
            if log:
                log(f"flow {base}: {len(rows)} days")
        time.sleep(sleep_s)
    return out
