"""Bitvavo 1d OHLC cache helpers for the AlphaI pack compare."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE

BITVAVO = "https://api.bitvavo.com/v2"


def fetch_daily_ohlc(base: str, *, days: int = 1100, pause_sec: float = 0.15) -> list[list[float]]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days) * 86_400_000
    rows: dict[int, list[float]] = {}
    cursor = start_ms
    while cursor < end_ms:
        page_end = min(end_ms, cursor + 1000 * 86_400_000)
        url = (
            f"{BITVAVO}/{base}-EUR/candles"
            f"?interval=1d&start={cursor}&end={page_end}&limit=1000"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "moreney-alphai-pack-compare"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            batch = json.loads(resp.read().decode())
        for r in batch or []:
            ts = int(r[0])
            rows[ts] = [
                ts,
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
            ]
        cursor = page_end
        time.sleep(pause_sec)
    return [rows[k] for k in sorted(rows)]


def refresh_cache(cache_dir: Path, *, days: int = 1100) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {"ok": [], "failed": [], "last_bar": {}}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        try:
            fresh = fetch_daily_ohlc(base, days=days)
            if not fresh:
                raise RuntimeError("empty fetch")
            merged: dict[int, list[float]] = {}
            if path.exists():
                for r in json.loads(path.read_text(encoding="utf-8")):
                    merged[int(r[0])] = [
                        int(r[0]),
                        float(r[1]),
                        float(r[2]),
                        float(r[3]),
                        float(r[4]),
                        float(r[5]),
                    ]
            for r in fresh:
                merged[int(r[0])] = r
            ordered = [merged[k] for k in sorted(merged)]
            path.write_text(json.dumps(ordered), encoding="utf-8")
            last = datetime.fromtimestamp(ordered[-1][0] / 1000, UTC).strftime("%Y-%m-%d")
            meta["ok"].append(base)
            meta["last_bar"][base] = last
        except Exception as exc:  # noqa: BLE001
            meta["failed"].append({"base": base, "error": str(exc)})
    return meta


def load_cached(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text(encoding="utf-8"))
    return out


def ohlc_dates(ohlc: dict[str, list[list[float]]]) -> list[str]:
    return [
        datetime.fromtimestamp(int(r[0]) / 1000, UTC).strftime("%Y-%m-%d")
        for r in (ohlc.get("BTC") or [])
    ]
