"""Conditional rise given depth and signed quantity.

Thresholds are fixed before the scan. A later table does not move them.

Bitvavo has no historical taker side, so the buy and the sell are the
Binance USDT print for the same base and the same UTC day. The price, the
depth, and the forward path are the Bitvavo EUR bar. A bot print is a
small average trade versus that name's own history: many bots, one small
ticket at a time.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

PRE_END = "2026-01-01"
HIGH_N = 60
FLOW_LOOK = 20
FORWARD = 10
FORWARD_SLOW = 20
MIN_ADV = 20_000.0
NEAR = 0.10
DEEP = 0.30
TAKER_BUY = 0.52
TAKER_SELL = 0.48
HEAVY = 2.0
LIGHT = 1.0
TICKET_LOOK = 60
LARGE_RANK = 0.80
SMALL_RANK = 0.20
MIN_N = 30
HIGH_GAP = 0.02
CLOSE_GAP = 0.01

LOCATIONS = ("near", "mid", "deep")
FLOWS = ("heavy_buy", "light_buy", "flat", "light_sell", "heavy_sell")
SAMPLES = (
    ("depth, all days", "all"),
    ("depth, heavy buy only", "heavy_buy"),
    ("buy multiple, deep only", "deep"),
)


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def period_of(day: str) -> str:
    return "pre" if day < PRE_END else "2026"


def location_of(depth: float) -> str:
    if depth < NEAR:
        return "near"
    if depth < DEEP:
        return "mid"
    return "deep"


def quantity_multiple(today: float, prior: Sequence[float]) -> float | None:
    if not prior or today < 0:
        return None
    base = statistics.median(prior)
    if base <= 0:
        return None
    return today / base


def flow_of(taker: float, buy_multiple: float | None, sell_multiple: float | None) -> str | None:
    if taker >= TAKER_BUY:
        if buy_multiple is None:
            return None
        if buy_multiple >= HEAVY:
            return "heavy_buy"
        if buy_multiple < LIGHT:
            return "light_buy"
        return None
    if taker <= TAKER_SELL:
        if sell_multiple is None:
            return None
        if sell_multiple >= HEAVY:
            return "heavy_sell"
        if sell_multiple < LIGHT:
            return "light_sell"
        return None
    return "flat"


def percentile_rank(prior: Sequence[float], value: float) -> float:
    if not prior:
        return 0.0
    return sum(1 for item in prior if item < value) / len(prior)


def ticket_of(rank: float | None) -> str | None:
    if rank is None:
        return None
    if rank >= LARGE_RANK:
        return "large"
    if rank <= SMALL_RANK:
        return "small"
    return None


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return _pearson(_ranks(xs), _ranks(ys))


@dataclass(slots=True)
class Event:
    base: str
    day: str
    depth: float
    location: str
    flow: str
    buy_multiple: float
    ticket: str | None
    fwd_high: float | None
    fwd_close: float | None
    slow_high: float | None
    slow_close: float | None


def _flow_series(rows: Sequence[Sequence[float]]) -> list[tuple[str, float, float, float]]:
    dedup: dict[str, tuple[float, float, float]] = {}
    for row in rows:
        if len(row) < 5:
            continue
        volume = float(row[1])
        trades = float(row[3])
        quote = float(row[2])
        if volume <= 0 or quote <= 0 or trades <= 0:
            continue
        taker = float(row[4]) / volume
        if taker < 0 or taker > 1:
            continue
        dedup[day_of(int(row[0]))] = (taker, quote, quote / trades)
    return [(day, *dedup[day]) for day in sorted(dedup)]


def _forward(
    opens: Sequence[float],
    highs: Sequence[float],
    closes: Sequence[float],
    i: int,
    n: int,
) -> tuple[float | None, float | None]:
    last = i + n
    if last >= len(closes) or opens[i + 1] <= 0 or closes[last] <= 0:
        return None, None
    window = max(highs[i + 1 : last + 1])
    if window <= 0:
        return None, None
    entry = opens[i + 1]
    return window / entry - 1.0, closes[last] / entry - 1.0


def scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    flow: Mapping[str, Sequence[Sequence[float]]],
) -> list[Event]:
    """One event per liquid name-day that has a same-day taker print."""
    events: list[Event] = []
    for base, rows in ohlc.items():
        series = _flow_series(flow.get(base) or ())
        if len(rows) < HIGH_N or len(series) <= FLOW_LOOK:
            continue
        flow_at = {row[0]: i for i, row in enumerate(series)}
        closes = [float(row[4]) for row in rows]
        highs = [float(row[2]) for row in rows]
        opens = [float(row[1]) for row in rows]
        qvols = [float(row[5]) * float(row[4]) for row in rows]
        days = [day_of(int(row[0])) for row in rows]
        for i in range(HIGH_N - 1, len(rows)):
            fi = flow_at.get(days[i])
            if fi is None or fi < FLOW_LOOK or closes[i] <= 0:
                continue
            prior_q = qvols[i - FLOW_LOOK : i]
            if sum(prior_q) / FLOW_LOOK < MIN_ADV:
                continue
            peak = max(highs[i - HIGH_N + 1 : i + 1])
            if peak <= 0:
                continue
            window = series[fi - FLOW_LOOK : fi]
            _day, taker, quote, _avg = series[fi]
            buy = taker * quote
            sell = (1.0 - taker) * quote
            buy_multiple = quantity_multiple(buy, [row[1] * row[2] for row in window])
            sell_multiple = quantity_multiple(sell, [(1.0 - row[1]) * row[2] for row in window])
            kind = flow_of(taker, buy_multiple, sell_multiple)
            if kind is None or buy_multiple is None:
                continue
            rank = None
            if fi >= TICKET_LOOK:
                prior_avg = [series[j][3] for j in range(fi - TICKET_LOOK, fi)]
                rank = percentile_rank(prior_avg, series[fi][3])
            high, close = _forward(opens, highs, closes, i, FORWARD)
            slow_high, slow_close = _forward(opens, highs, closes, i, FORWARD_SLOW)
            events.append(
                Event(
                    base=base,
                    day=days[i],
                    depth=1.0 - closes[i] / peak,
                    location=location_of(1.0 - closes[i] / peak),
                    flow=kind,
                    buy_multiple=buy_multiple,
                    ticket=ticket_of(rank),
                    fwd_high=high,
                    fwd_close=close,
                    slow_high=slow_high,
                    slow_close=slow_close,
                )
            )
    return events


def _pctile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    weight = k - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _empty() -> dict[str, float | None]:
    return {"n": 0, "high": None, "close": None, "p90": None}


def _stats(rows: Sequence[Event], high: str, close: str) -> dict[str, float | None]:
    usable = [
        row for row in rows if getattr(row, close) is not None and getattr(row, high) is not None
    ]
    if not usable:
        return _empty()
    highs = [float(getattr(row, high)) for row in usable]
    closes = [float(getattr(row, close)) for row in usable]
    return {
        "n": float(len(usable)),
        "high": statistics.median(highs),
        "close": statistics.median(closes),
        "p90": _pctile(highs, 0.90),
    }


def summarize(
    events: Sequence[Event],
    *,
    slow: bool = False,
) -> dict[tuple[str, str], dict[str, dict[str, float | None]]]:
    high_name = "slow_high" if slow else "fwd_high"
    close_name = "slow_close" if slow else "fwd_close"
    groups: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    for row in events:
        if getattr(row, close_name) is None:
            continue
        groups[(row.location, row.flow, period_of(row.day))].append(row)
    out: dict[tuple[str, str], dict[str, dict[str, float | None]]] = {}
    for location in LOCATIONS:
        for flow in FLOWS:
            key = (location, flow)
            out[key] = {
                "pre": _stats(groups.get((location, flow, "pre"), ()), high_name, close_name),
                "2026": _stats(groups.get((location, flow, "2026"), ()), high_name, close_name),
            }
    return out


def base_rate(events: Sequence[Event], *, slow: bool = False) -> dict[str, dict[str, float | None]]:
    high_name = "slow_high" if slow else "fwd_high"
    close_name = "slow_close" if slow else "fwd_close"
    groups: dict[str, list[Event]] = defaultdict(list)
    for row in events:
        if getattr(row, close_name) is None:
            continue
        groups[period_of(row.day)].append(row)
    return {
        "pre": _stats(groups.get("pre", ()), high_name, close_name),
        "2026": _stats(groups.get("2026", ()), high_name, close_name),
    }


def _ready(stats: Mapping[str, float | None] | None) -> bool:
    if not stats or int(stats.get("n") or 0) < MIN_N:
        return False
    return stats.get("high") is not None and stats.get("close") is not None


Summary = Mapping[tuple[str, str], Mapping[str, Mapping[str, float | None]]]


def shape_holds(summary: Summary) -> bool:
    """Heavy buying: the rise grows as the price sits lower, and beats heavy selling."""
    for period in ("pre", "2026"):
        deep = summary[("deep", "heavy_buy")][period]
        mid = summary[("mid", "heavy_buy")][period]
        near = summary[("near", "heavy_buy")][period]
        sold = summary[("deep", "heavy_sell")][period]
        if not all(_ready(cell) for cell in (deep, mid, near, sold)):
            return False
        dh = float(deep["high"])
        mh = float(mid["high"])
        nh = float(near["high"])
        sh = float(sold["high"])
        if not (dh > mh > nh and dh - nh >= HIGH_GAP and dh - sh >= HIGH_GAP):
            return False
    return True


def close_holds(summary: Summary) -> bool:
    """The close, not the wick, stays up and beats the two neighbouring cells."""
    for period in ("pre", "2026"):
        deep = summary[("deep", "heavy_buy")][period]
        near = summary[("near", "heavy_buy")][period]
        sold = summary[("deep", "heavy_sell")][period]
        if not all(_ready(cell) for cell in (deep, near, sold)):
            return False
        dc = float(deep["close"])
        if dc <= 0:
            return False
        if dc - float(near["close"]) < CLOSE_GAP or dc - float(sold["close"]) < CLOSE_GAP:
            return False
    return True


def bot_cells(events: Sequence[Event]) -> dict[tuple[str, str], dict[str, dict[str, float | None]]]:
    """Small versus large tickets, only when the price is already deep."""
    groups: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    for row in events:
        if row.location != "deep" or row.ticket is None or row.fwd_close is None:
            continue
        if row.flow == "heavy_buy":
            groups[(row.ticket, "buy", period_of(row.day))].append(row)
        elif row.flow == "heavy_sell":
            groups[(row.ticket, "sell", period_of(row.day))].append(row)
    out: dict[tuple[str, str], dict[str, dict[str, float | None]]] = {}
    for ticket in ("small", "large"):
        for side in ("buy", "sell"):
            key = (ticket, side)
            out[key] = {
                "pre": _stats(groups.get((ticket, side, "pre"), ()), "fwd_high", "fwd_close"),
                "2026": _stats(groups.get((ticket, side, "2026"), ()), "fwd_high", "fwd_close"),
            }
    return out


def bot_holds(cells: Summary, side: str) -> bool:
    """Small tickets at a deep price have the larger rise and the better close."""
    for period in ("pre", "2026"):
        small = cells[("small", side)][period]
        large = cells[("large", side)][period]
        if not _ready(small) or not _ready(large):
            return False
        if float(small["high"]) - float(large["high"]) < HIGH_GAP:
            return False
        if float(small["close"]) <= float(large["close"]):
            return False
    return True


def correlations(events: Sequence[Event]) -> dict[str, dict[str, float | None]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for row in events:
        if row.fwd_high is None:
            continue
        grouped[period_of(row.day)].append(row)
    out: dict[str, dict[str, float | None]] = {}
    for _label, kind in SAMPLES:
        out[kind] = {}
        for period in ("pre", "2026"):
            rows = grouped.get(period, ())
            if kind == "heavy_buy":
                rows = [row for row in rows if row.flow == "heavy_buy"]
            elif kind == "deep":
                rows = [row for row in rows if row.location == "deep"]
            if kind == "deep":
                xs = [row.buy_multiple for row in rows]
            else:
                xs = [row.depth for row in rows]
            ys = [float(row.fwd_high) for row in rows]
            rho = spearman(xs, ys)
            out[kind][period] = rho
            out[kind][f"n_{period}"] = float(len(rows))
    return out


def choose(rows: Sequence[Event], kind: str) -> str | None:
    if kind == "deep_buy":
        pool = [row for row in rows if row.location == "deep" and row.flow == "heavy_buy"]
        pool.sort(key=lambda row: (-row.buy_multiple, -row.depth, row.base))
    elif kind == "deep":
        pool = [row for row in rows if row.location == "deep"]
        pool.sort(key=lambda row: (-row.depth, row.base))
    elif kind == "buy":
        pool = [row for row in rows if row.flow == "heavy_buy"]
        pool.sort(key=lambda row: (-row.buy_multiple, row.base))
    else:
        return None
    if not pool:
        return None
    return pool[0].base


def picks_for(events: Sequence[Event], kind: str) -> dict[str, str]:
    by_day: dict[str, list[Event]] = defaultdict(list)
    for row in events:
        by_day[row.day].append(row)
    out: dict[str, str] = {}
    for day, rows in by_day.items():
        choice = choose(rows, kind)
        if choice:
            out[day] = choice
    return out
