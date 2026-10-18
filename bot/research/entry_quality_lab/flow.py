"""Daily aggressor flow per base from Binance spot klines.

Bitvavo does not publish historical taker sides, so order-flow features come
from the deepest public spot tape for the same bases: Binance ``<BASE>USDT``
1d klines carry the trade count and the taker-buy base volume. A day where
taker buys were 55% of volume prints ``taker_buy_ratio = 0.55``.

Everything is cached under ``data/entry_quality_flow/<BASE>.json`` and read
strictly through the signal date (no look-ahead).
"""

from __future__ import annotations

import bisect
import json
import statistics
import time
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

KLINES_URL = (
    "https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&startTime={start}&limit=1000"
)
FLOW_FIELDS = ("ts", "volume", "quote_volume", "n_trades", "taker_buy_base")

# [ts, volume, quote_volume, n_trades, taker_buy_base]
FlowRow = list[float]
Flow = Mapping[str, Sequence[FlowRow]]


def parse_klines(raw: Sequence[Sequence[object]]) -> list[FlowRow]:
    out: list[FlowRow] = []
    for k in raw:
        try:
            out.append(
                [
                    float(k[0]),
                    float(k[5]),
                    float(k[7]),
                    float(k[8]),
                    float(k[9]),
                ]
            )
        except (IndexError, TypeError, ValueError):
            continue
    return out


def fetch_flow(
    base: str,
    *,
    start_ms: int,
    quote: str = "USDT",
    sleep_s: float = 0.25,
) -> list[FlowRow]:
    rows: list[FlowRow] = []
    cursor = int(start_ms)
    while True:
        url = KLINES_URL.format(symbol=f"{base}{quote}", start=cursor)
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            raw = json.load(resp)
        chunk = parse_klines(raw)
        if not chunk:
            break
        rows.extend(chunk)
        if len(raw) < 1000:
            break
        cursor = int(raw[-1][0]) + 1
        time.sleep(sleep_s)
    return rows


def load_flow(
    bases: Sequence[str],
    *,
    cache_dir: Path,
    start_ms: int,
    refresh: bool = False,
    log=None,
) -> dict[str, list[FlowRow]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[FlowRow]] = {}
    for base in bases:
        path = cache_dir / f"{base}.json"
        if path.exists() and not refresh:
            out[base] = json.loads(path.read_text())
            continue
        try:
            rows = fetch_flow(base, start_ms=start_ms)
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"flow {base}: {exc}")
            continue
        if rows:
            # Drop the still-open UTC day so cached files only hold closed bars.
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            rows = [r for r in rows if _day(r[0]) < today]
            path.write_text(json.dumps(rows))
            out[base] = rows
        if log:
            log(f"flow {base}: {len(rows)} days")
    return out


def _day(ts_ms: float) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, UTC).strftime("%Y-%m-%d")


def flow_through(rows: Sequence[FlowRow], date: str) -> list[FlowRow]:
    days = [_day(r[0]) for r in rows]
    return list(rows[: bisect.bisect_right(days, date)])


def taker_buy_ratio(rows: Sequence[FlowRow], date: str, n: int) -> float | None:
    win = flow_through(rows, date)[-n:]
    if len(win) < n:
        return None
    vol = sum(r[1] for r in win)
    if vol <= 0:
        return None
    return sum(r[4] for r in win) / vol


def taker_ratio_z(rows: Sequence[FlowRow], date: str, n: int = 3, base_n: int = 30) -> float | None:
    """Last-n taker-buy ratio vs the trailing ``base_n`` daily distribution."""
    win = flow_through(rows, date)
    if len(win) < base_n + n:
        return None
    daily = [r[4] / r[1] for r in win[-(base_n + n) : -n] if r[1] > 0]
    if len(daily) < 10:
        return None
    mu = statistics.fmean(daily)
    sd = statistics.pstdev(daily)
    cur = taker_buy_ratio(rows, date, n)
    if cur is None or sd <= 1e-9:
        return None
    return (cur - mu) / sd


def trade_count_ratio(
    rows: Sequence[FlowRow], date: str, n: int = 3, base_n: int = 30
) -> float | None:
    """Mean daily trade count over last n days / trailing base_n mean."""
    win = flow_through(rows, date)
    if len(win) < base_n + n:
        return None
    recent = statistics.fmean(r[3] for r in win[-n:])
    ref = statistics.fmean(r[3] for r in win[-(base_n + n) : -n])
    if ref <= 0:
        return None
    return recent / ref


def net_delta_share(rows: Sequence[FlowRow], date: str, n: int) -> float | None:
    """(taker buy - taker sell) / volume over the last n days, in [-1, 1]."""
    r = taker_buy_ratio(rows, date, n)
    return None if r is None else 2.0 * r - 1.0
