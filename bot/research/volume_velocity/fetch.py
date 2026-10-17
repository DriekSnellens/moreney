"""Resumable Bitvavo 1m download for section member-days. Cache lives under data/."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DAY_MS = 86_400_000
CACHE = Path("data/research/volume_velocity/1m")


def day_path(base: str, day_ms: int) -> Path:
    return CACHE / base / f"{int(day_ms)}.json"


def _get(url: str, timeout: int = 20):
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


_PACE_LOCK = threading.Lock()
_NEXT_AT = 0.0


def _pace(min_interval: float) -> None:
    """Space request starts so a burst does not trip Bitvavo's public limit."""
    global _NEXT_AT
    with _PACE_LOCK:
        now = time.monotonic()
        wait = _NEXT_AT - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _NEXT_AT = now + min_interval


def fetch_day(base: str, day: int, *, min_interval: float = 0.2) -> str:
    """Download one UTC day of 1m bars. Returns cached | ok | empty | error."""
    path = day_path(base, day)
    if path.exists():
        return "cached"
    end = int(day) + DAY_MS - 1
    url = (
        f"https://api.bitvavo.com/v2/{base}-EUR/candles?interval=1m&limit=1440"
        f"&start={int(day)}&end={end}"
    )
    last = "error"
    for attempt in range(6):
        _pace(min_interval)
        try:
            raw = _get(url)
            rows = [
                [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                for r in raw
                if isinstance(r, (list, tuple)) and len(r) >= 6
            ]
            rows.sort(key=lambda r: r[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, separators=(",", ":")))
            return "ok" if rows else "empty"
        except urllib.error.HTTPError as exc:
            last = f"http{exc.code}"
            if exc.code == 429:
                body = exc.read().decode("utf-8", errors="replace")
                stamp = re.search(r"(\d{13})", body)
                if stamp:
                    until = int(stamp.group(1)) / 1000.0
                    time.sleep(max(5.0, min(until - time.time() + 2.0, 900.0)))
                else:
                    time.sleep(8.0 + 4.0 * attempt)
                continue
            if exc.code in {400, 404}:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[]")
                return "empty"
            time.sleep(0.5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            last = "error"
            time.sleep(0.5 * (attempt + 1))
    return last


def fetch_members(members: list[dict], *, workers: int = 4, log=None) -> dict[str, int]:
    pending = [m for m in members if not day_path(m["base"], int(m["day_ms"])).exists()]
    counts = {"cached": len(members) - len(pending), "ok": 0, "empty": 0, "error": 0}
    if log:
        log(f"fetch 1m: {len(pending)} to pull, {counts['cached']} cached, workers={workers}")
    done = 0
    # Steady pace. A burst of parallel calls gets HTTP 429 long before the
    # documented per-minute cap; 429s back off inside fetch_day.
    interval = 0.28
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [
            pool.submit(fetch_day, m["base"], int(m["day_ms"]), min_interval=interval)
            for m in pending
        ]
        for fut in as_completed(futs):
            status = fut.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if log and done % 500 == 0:
                log(f"  {done}/{len(pending)} {counts}")
    if log:
        log(f"fetch done {counts}")
    return counts
