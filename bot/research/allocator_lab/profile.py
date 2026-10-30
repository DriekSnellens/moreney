"""Where large capital shows up, and what the next ten sessions do.

Thresholds are fixed before the scan. A later result does not move them.

The public tape has no account ids. Quote volume is the euro footprint.
Average print size (Binance quote volume / trade count) is the ticket
footprint. Both are read through the signal day only. The forward window
buys the next session's open and marks the close ten sessions later.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

PRE_END = "2026-01-01"
LOOK = 20
FORWARD = 10
MIN_ADV = 20_000.0
WIN_RET = 0.10
LOSE_RET = -0.10
VOL_MULT = 2.0
LIGHT_VOL = 1.0
MIN_NAMES = 10
MIN_N = 30
GAP = 0.01
CONCENTRATED = 1.5
DISPERSED = 1.0
TICKET_LOOK = 60
LARGE_RANK = 0.80
SMALL_RANK = 0.20
TAKER_BUY = 0.52
TAKER_SELL = 0.48
SMA_N = 50

PAIRS: tuple[tuple[str, str, str], ...] = (
    ("winner_up_heavy", "winner_up_light", "up"),
    ("loser_down_heavy", "loser_down_light", "down"),
    ("winner_down_heavy", "winner_down_light", "down"),
    ("loser_up_heavy", "loser_up_light", "up"),
)
TICKET_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("large_buy", "small_buy", "up"),
    ("large_buy_winner", "small_buy_winner", "up"),
    ("large_sell", "small_sell", "down"),
    ("large_sell_loser", "small_sell_loser", "down"),
)
KINDS = ("size", "price", "size_btc", "price_btc")
SHARE_KEYS = (
    "winner_up",
    "winner_down",
    "loser_up",
    "loser_down",
    "flat_up",
    "flat_down",
)


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


def period_of(day: str) -> str:
    return "pre" if day < PRE_END else "2026"


def state_of(ret20: float) -> str:
    if ret20 >= WIN_RET:
        return "winner"
    if ret20 <= LOSE_RET:
        return "loser"
    return "flat"


def side_of(day_ret: float) -> str | None:
    if day_ret > 0:
        return "up"
    if day_ret < 0:
        return "down"
    return None


def weight_of(volx: float) -> str:
    if volx >= VOL_MULT:
        return "heavy"
    if volx < LIGHT_VOL:
        return "light"
    return "mid"


def cell_of(ret20: float, day_ret: float, volx: float) -> str | None:
    side = side_of(day_ret)
    weight = weight_of(volx)
    if side is None or weight == "mid":
        return None
    return f"{state_of(ret20)}_{side}_{weight}"


def percentile_rank(prior: Sequence[float], value: float) -> float:
    if not prior:
        return 0.0
    below = sum(1 for item in prior if item < value)
    return below / len(prior)


def leader_index(values: Sequence[float]) -> list[int]:
    """Top fifth by the slow return. Ties break toward the earlier row."""
    count = len(values) // 5
    if count < 1:
        return []
    order = sorted(range(len(values)), key=lambda i: (-values[i], i))
    return order[:count]


def concentration_ratio(qvols: Sequence[float], leaders: Sequence[int]) -> float | None:
    total = sum(qvols)
    if total <= 0 or not leaders:
        return None
    count_share = len(leaders) / len(qvols)
    if count_share <= 0:
        return None
    return (sum(qvols[i] for i in leaders) / total) / count_share


def ticket_labels(rank: float, taker: float, day_ret: float, ret20: float) -> list[str]:
    """Labels where ticket size and aggressor side agree with the day's close."""
    if rank >= LARGE_RANK:
        size = "large"
    elif rank <= SMALL_RANK:
        size = "small"
    else:
        return []
    labels: list[str] = []
    if taker >= TAKER_BUY and day_ret > 0:
        labels.append(f"{size}_buy")
        if ret20 >= WIN_RET:
            labels.append(f"{size}_buy_winner")
    elif taker <= TAKER_SELL and day_ret < 0:
        labels.append(f"{size}_sell")
        if ret20 <= LOSE_RET:
            labels.append(f"{size}_sell_loser")
    return labels


def passes(
    heavy: Mapping[str, float | None],
    light: Mapping[str, float | None],
    direction: str,
) -> bool:
    """Heavy beats light on the median and on the euros, with a real sample."""
    if int(heavy.get("n") or 0) < MIN_N or int(light.get("n") or 0) < MIN_N:
        return False
    hm, lm = heavy.get("median"), light.get("median")
    he, le = heavy.get("euro"), light.get("euro")
    if hm is None or lm is None or he is None or le is None:
        return False
    if direction == "up":
        return hm > 0 and he > 0 and hm - lm >= GAP and he >= le
    return hm < 0 and he < 0 and lm - hm >= GAP and he <= le


@dataclass(slots=True)
class Print:
    base: str
    day: str
    ret20: float
    day_ret: float
    volx: float
    qvol: float
    btc_up: bool
    on_stride: bool
    fwd_close: float | None
    fwd_high: float | None


def _btc_above_sma(rows: Sequence[Sequence[float]], n: int = SMA_N) -> dict[str, bool]:
    if len(rows) < n:
        return {}
    closes = [float(row[4]) for row in rows]
    days = [day_of(int(row[0])) for row in rows]
    window = sum(closes[: n - 1])
    out: dict[str, bool] = {}
    for i in range(n - 1, len(closes)):
        window += closes[i]
        out[days[i]] = closes[i] > window / n
        window -= closes[i - n + 1]
    return out


def scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
) -> tuple[list[Print], dict[str, list[Print]]]:
    """Liquid alt days. Average quote volume is the prior 20 sessions, not today."""
    btc_up = _btc_above_sma(ohlc.get("BTC") or [])
    prints: list[Print] = []
    by_day: dict[str, list[Print]] = defaultdict(list)
    for base, rows in ohlc.items():
        if base == "BTC" or len(rows) <= LOOK:
            continue
        closes = [float(row[4]) for row in rows]
        opens = [float(row[1]) for row in rows]
        highs = [float(row[2]) for row in rows]
        qvols = [float(row[5]) * float(row[4]) for row in rows]
        days = [day_of(int(row[0])) for row in rows]
        for i in range(LOOK, len(rows)):
            if closes[i] <= 0 or closes[i - 1] <= 0 or closes[i - LOOK] <= 0:
                continue
            prior = qvols[i - LOOK : i]
            med = statistics.median(prior)
            if med <= 0:
                continue
            if sum(prior) / LOOK < MIN_ADV:
                continue
            fwd_close: float | None = None
            fwd_high: float | None = None
            last = i + FORWARD
            if last < len(rows) and opens[i + 1] > 0 and closes[last] > 0:
                window_high = max(highs[i + 1 : last + 1])
                if window_high > 0:
                    fwd_close = closes[last] / opens[i + 1] - 1.0
                    fwd_high = window_high / opens[i + 1] - 1.0
            item = Print(
                base=base,
                day=days[i],
                ret20=closes[i] / closes[i - LOOK] - 1.0,
                day_ret=closes[i] / closes[i - 1] - 1.0,
                volx=qvols[i] / med,
                qvol=qvols[i],
                btc_up=btc_up.get(days[i], False),
                on_stride=(i % FORWARD == 0),
                fwd_close=fwd_close,
                fwd_high=fwd_high,
            )
            prints.append(item)
            by_day[days[i]].append(item)
    return prints, by_day


def _empty_stats() -> dict[str, float | None]:
    return {"n": 0, "median": None, "euro": None, "pos": None, "high": None}


def stats_of(rows: Sequence[Print]) -> dict[str, float | None]:
    usable = [row for row in rows if row.fwd_close is not None]
    if not usable:
        return _empty_stats()
    fwds = [float(row.fwd_close) for row in usable]
    weight = sum(row.qvol for row in usable)
    euro = sum(row.qvol * float(row.fwd_close) for row in usable) / weight if weight else None
    highs = [float(row.fwd_high) for row in usable if row.fwd_high is not None]
    return {
        "n": float(len(fwds)),
        "median": statistics.median(fwds),
        "euro": euro,
        "pos": sum(value > 0 for value in fwds) / len(fwds),
        "high": statistics.median(highs) if highs else None,
    }


def summarize_cells(prints: Sequence[Print]) -> dict[str, dict[str, dict[str, float | None]]]:
    groups: dict[tuple[str, str], list[Print]] = defaultdict(list)
    for row in prints:
        name = cell_of(row.ret20, row.day_ret, row.volx)
        if name is None or row.fwd_close is None:
            continue
        groups[(name, period_of(row.day))].append(row)
    names = {key[0] for key in groups}
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for name in names:
        out[name] = {
            "pre": stats_of(groups.get((name, "pre"), ())),
            "2026": stats_of(groups.get((name, "2026"), ())),
        }
    return out


def euro_shares(prints: Sequence[Print]) -> dict[str, dict[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    buckets: dict[tuple[str, str], float] = defaultdict(float)
    for row in prints:
        side = side_of(row.day_ret)
        if side is None or row.qvol <= 0:
            continue
        key = f"{state_of(row.ret20)}_{side}"
        period = period_of(row.day)
        buckets[(period, key)] += row.qvol
        totals[period] += row.qvol
    out: dict[str, dict[str, float]] = {}
    for period in ("pre", "2026"):
        total = totals.get(period) or 0.0
        out[period] = {
            key: (buckets[(period, key)] / total if total else 0.0) for key in SHARE_KEYS
        }
    return out


@dataclass(slots=True)
class CrowdDay:
    day: str
    ratio: float
    leader_median: float
    leader_euro: float
    universe_median: float
    magnet_fwd: float
    magnet_qvol: float
    magnet_ret20: float


def crowd_days(by_day: Mapping[str, Sequence[Print]]) -> list[CrowdDay]:
    out: list[CrowdDay] = []
    for day, rows in by_day.items():
        usable = [row for row in rows if row.fwd_close is not None]
        if len(usable) < MIN_NAMES:
            continue
        leaders = leader_index([row.ret20 for row in usable])
        qvols = [row.qvol for row in usable]
        ratio = concentration_ratio(qvols, leaders)
        if ratio is None:
            continue
        picked = [usable[i] for i in leaders]
        weight = sum(row.qvol for row in picked)
        if weight <= 0:
            continue
        magnet = min(usable, key=lambda row: (-row.qvol, -row.ret20, row.base))
        if magnet.fwd_close is None:
            continue
        out.append(
            CrowdDay(
                day=day,
                ratio=ratio,
                leader_median=statistics.median([float(row.fwd_close) for row in picked]),
                leader_euro=sum(row.qvol * float(row.fwd_close) for row in picked) / weight,
                universe_median=statistics.median([float(row.fwd_close) for row in usable]),
                magnet_fwd=float(magnet.fwd_close),
                magnet_qvol=magnet.qvol,
                magnet_ret20=magnet.ret20,
            )
        )
    return out


def _day_stats(rows: Sequence[CrowdDay], field: str) -> dict[str, float | None]:
    if not rows:
        return _empty_stats()
    values = [float(getattr(row, field)) for row in rows]
    weight = sum(row.magnet_qvol for row in rows)
    euro_values = [row.leader_euro for row in rows]
    return {
        "n": float(len(rows)),
        "median": statistics.median(values),
        "euro": statistics.median(euro_values),
        "pos": sum(value > 0 for value in values) / len(values),
        "high": (
            sum(row.magnet_qvol * row.magnet_fwd for row in rows) / weight if weight else None
        ),
    }


def summarize_crowds(days: Sequence[CrowdDay]) -> dict[str, dict[str, dict[str, float | None]]]:
    grouped: dict[tuple[str, str], list[CrowdDay]] = defaultdict(list)
    for row in days:
        period = period_of(row.day)
        if row.ratio >= CONCENTRATED:
            grouped[(period, "concentrated")].append(row)
        elif row.ratio <= DISPERSED:
            grouped[(period, "dispersed")].append(row)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for name in ("concentrated", "dispersed"):
        out[name] = {
            "pre": _day_stats(grouped.get(("pre", name), ()), "leader_median"),
            "2026": _day_stats(grouped.get(("2026", name), ()), "leader_median"),
        }
    return out


def summarize_magnets(days: Sequence[CrowdDay]) -> dict[str, dict[str, dict[str, float | None]]]:
    grouped: dict[tuple[str, str], list[CrowdDay]] = defaultdict(list)
    for row in days:
        period = period_of(row.day)
        grouped[(period, "any")].append(row)
        state = state_of(row.magnet_ret20)
        grouped[(period, state)].append(row)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for name in ("any", "winner", "loser", "flat"):
        out[name] = {}
        for period in ("pre", "2026"):
            rows = grouped.get((period, name), ())
            if not rows:
                out[name][period] = _empty_stats()
                continue
            fwds = [row.magnet_fwd for row in rows]
            gaps = [row.magnet_fwd - row.universe_median for row in rows]
            weight = sum(row.magnet_qvol for row in rows)
            out[name][period] = {
                "n": float(len(rows)),
                "median": statistics.median(fwds),
                "euro": None
                if not weight
                else sum(row.magnet_qvol * row.magnet_fwd for row in rows) / weight,
                "pos": sum(value > 0 for value in fwds) / len(fwds),
                "high": statistics.median(gaps),
            }
    return out


def magnet_passes(stats: Mapping[str, float | None]) -> bool:
    """The name that took the most euros beats a typical name, and stays positive."""
    if int(stats.get("n") or 0) < MIN_N:
        return False
    median, euro, gap = stats.get("median"), stats.get("euro"), stats.get("high")
    if median is None or euro is None or gap is None:
        return False
    return median > 0 and euro > 0 and gap >= GAP


@dataclass(slots=True)
class Ticket:
    day: str
    qvol: float
    fwd_close: float
    labels: tuple[str, ...]


def _flow_series(rows: Sequence[Sequence[float]]) -> list[tuple[str, float, float]]:
    dedup: dict[str, tuple[float, float]] = {}
    for row in rows:
        if len(row) < 5:
            continue
        trades = float(row[3])
        volume = float(row[1])
        if trades <= 0 or volume <= 0:
            continue
        dedup[day_of(int(row[0]))] = (float(row[2]) / trades, float(row[4]) / volume)
    return [(day, dedup[day][0], dedup[day][1]) for day in sorted(dedup)]


def ticket_events(
    flow: Mapping[str, Sequence[Sequence[float]]],
    prints: Mapping[tuple[str, str], Print],
) -> list[Ticket]:
    events: list[Ticket] = []
    for base, rows in flow.items():
        series = _flow_series(rows)
        for i in range(TICKET_LOOK, len(series)):
            day, avg, taker = series[i]
            row = prints.get((base, day))
            if row is None or row.fwd_close is None:
                continue
            prior = [series[j][1] for j in range(i - TICKET_LOOK, i)]
            labels = ticket_labels(percentile_rank(prior, avg), taker, row.day_ret, row.ret20)
            if not labels:
                continue
            events.append(Ticket(day, row.qvol, float(row.fwd_close), tuple(labels)))
    return events


def summarize_tickets(events: Sequence[Ticket]) -> dict[str, dict[str, dict[str, float | None]]]:
    groups: dict[tuple[str, str], list[Print]] = defaultdict(list)
    for event in events:
        period = period_of(event.day)
        stub = Print(
            base="",
            day=event.day,
            ret20=0.0,
            day_ret=0.0,
            volx=0.0,
            qvol=event.qvol,
            btc_up=False,
            on_stride=False,
            fwd_close=event.fwd_close,
            fwd_high=None,
        )
        for label in event.labels:
            groups[(label, period)].append(stub)
    names = {key[0] for key in groups}
    return {
        name: {
            "pre": stats_of(groups.get((name, "pre"), ())),
            "2026": stats_of(groups.get((name, "2026"), ())),
        }
        for name in names
    }


def choose(rows: Sequence[Print], kind: str) -> str | None:
    """One name. ``size`` follows the euros; ``price`` follows the slow return."""
    pool = [row for row in rows if row.ret20 >= WIN_RET and row.day_ret > 0]
    if kind.startswith("size"):
        pool = [row for row in pool if row.volx >= VOL_MULT]
    if kind.endswith("btc"):
        pool = [row for row in pool if row.btc_up]
    if not pool:
        return None
    if kind.startswith("size"):
        pool.sort(key=lambda row: (-row.qvol, -row.ret20, row.base))
    else:
        pool.sort(key=lambda row: (-row.ret20, -row.qvol, row.base))
    return pool[0].base


def picks_for(by_day: Mapping[str, Sequence[Print]], kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for day, rows in by_day.items():
        choice = choose(rows, kind)
        if choice:
            out[day] = choice
    return out
