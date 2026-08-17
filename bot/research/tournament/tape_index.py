"""Shared immutable tape index for tournament strategies."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from bot.market_data.research.chrono_split import chronological_split
from bot.market_data.research.tape_scan import dataset_id_from_fingerprint, scan_tape
from bot.market_data.research import SCHEMA_VERSION
from bot.research.tournament.criteria import CORE_VENUES


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    ts_ns: int
    mid: float
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    exchange_ts_ns: int | None
    sequence: int | None


@dataclass
class TapeIndex:
    """Compact per-(venue,symbol) mid/L1 series + shared split metadata."""

    root: str
    dataset_id: str
    content_fingerprint: str
    duration_seconds: float | None
    inventory: dict[str, Any]
    series: dict[tuple[str, str], list[SeriesPoint]] = field(default_factory=dict)
    load_seconds: float = 0.0
    peak_points: int = 0
    symbols: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)

    def points(self, venue: str, symbol: str) -> list[SeriesPoint]:
        return self.series.get((venue, symbol), [])

    def symbols_for(self, venue: str) -> list[str]:
        return sorted({s for (v, s) in self.series if v == venue})


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_tape_index(
    root: Path | str,
    *,
    venues: tuple[str, ...] = CORE_VENUES,
    max_events: int | None = None,
    symbol_suffix: str = "EUR",
    stride: int = 1,
) -> TapeIndex:
    """Stream JSONL into compact series. Prefer EUR symbols for EUR research."""
    t0 = time.perf_counter()
    root = Path(root)
    inv = scan_tape(root)
    ds_id = (
        dataset_id_from_fingerprint(inv.content_fingerprint, schema_version=SCHEMA_VERSION)
        if inv.total_events
        else "NONE"
    )
    series: dict[tuple[str, str], list[SeriesPoint]] = {}
    venue_set = set(venues)
    n = 0
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                n += 1
                if max_events is not None and n > max_events:
                    break
                if stride > 1 and (n % stride) != 0:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                venue = str(raw.get("venue") or "")
                if venue not in venue_set:
                    continue
                symbol = str(raw.get("symbol") or "")
                if symbol_suffix and not symbol.endswith(symbol_suffix):
                    continue
                bid = _f(raw.get("bid_price"))
                ask = _f(raw.get("ask_price"))
                mid = _mid(bid, ask)
                if mid is None:
                    continue
                ts = raw.get("received_ts_ns")
                if ts is None:
                    continue
                key = (venue, symbol)
                series.setdefault(key, []).append(
                    SeriesPoint(
                        ts_ns=int(ts),
                        mid=mid,
                        bid=float(bid or 0),
                        ask=float(ask or 0),
                        bid_size=float(_f(raw.get("bid_size")) or 0),
                        ask_size=float(_f(raw.get("ask_size")) or 0),
                        exchange_ts_ns=(
                            int(raw["exchange_ts_ns"])
                            if raw.get("exchange_ts_ns") is not None
                            else None
                        ),
                        sequence=(
                            int(raw["sequence_number"])
                            if raw.get("sequence_number") is not None
                            else None
                        ),
                    )
                )
        if max_events is not None and n > max_events:
            break

    for key in series:
        series[key].sort(key=lambda p: p.ts_ns)

    idx = TapeIndex(
        root=str(root),
        dataset_id=ds_id,
        content_fingerprint=inv.content_fingerprint,
        duration_seconds=inv.duration_seconds,
        inventory=inv.as_dict(),
        series=series,
        load_seconds=time.perf_counter() - t0,
        peak_points=sum(len(v) for v in series.values()),
        symbols=sorted({s for _, s in series}),
        venues=sorted({v for v, _ in series}),
    )
    return idx


def make_split(index: TapeIndex) -> dict[str, Any]:
    """Chronological split from indexed series with outlier-robust bounds.

    A single stale/corrupt early timestamp must not empty DEVELOPMENT.
    """
    all_ts: list[int] = []
    for pts in index.series.values():
        for p in pts:
            all_ts.append(p.ts_ns)
    if len(all_ts) >= 100:
        all_ts.sort()
        lo_i = max(0, int(len(all_ts) * 0.001))
        hi_i = min(len(all_ts) - 1, int(len(all_ts) * 0.999))
        start = all_ts[lo_i]
        end = all_ts[hi_i]
    elif all_ts:
        start = min(all_ts)
        end = max(all_ts)
    else:
        start = index.inventory.get("first_received_ts_ns")
        end = index.inventory.get("last_received_ts_ns")
    return chronological_split(
        start_ts_ns=start,
        end_ts_ns=end,
        content_fingerprint=index.content_fingerprint,
        dataset_id=index.dataset_id,
    )


def iter_window(
    points: list[SeriesPoint],
    *,
    start_ns: int,
    end_ns_exclusive: int | None = None,
    end_ns_inclusive: int | None = None,
) -> Iterator[SeriesPoint]:
    for p in points:
        if p.ts_ns < start_ns:
            continue
        if end_ns_exclusive is not None and p.ts_ns >= end_ns_exclusive:
            break
        if end_ns_inclusive is not None and p.ts_ns > end_ns_inclusive:
            break
        yield p


def past_return(
    points: list[SeriesPoint],
    i: int,
    lookback_ns: int,
) -> float | None:
    if i < 0 or i >= len(points):
        return None
    t0 = points[i].ts_ns
    m0 = points[i].mid
    target = t0 - lookback_ns
    j = i - 1
    while j >= 0 and points[j].ts_ns > target:
        j -= 1
    if j < 0:
        return None
    # Allow sparse books: accept nearest prior within 5x lookback
    if t0 - points[j].ts_ns > lookback_ns * 5:
        return None
    m1 = points[j].mid
    if m1 <= 0:
        return None
    return (m0 - m1) / m1


def forward_return(
    points: list[SeriesPoint],
    i: int,
    horizon_ns: int,
) -> float | None:
    """Causal forward mid return from points[i] using first point at/after t+h."""
    if i < 0 or i >= len(points):
        return None
    t0 = points[i].ts_ns
    m0 = points[i].mid
    target = t0 + horizon_ns
    j = i + 1
    while j < len(points) and points[j].ts_ns < target:
        j += 1
    if j >= len(points):
        return None
    if points[j].ts_ns - t0 > horizon_ns * 5:
        return None
    m1 = points[j].mid
    if m0 <= 0:
        return None
    return (m1 - m0) / m0
