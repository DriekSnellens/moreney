"""1% above the printed low, sold 7% higher, across the alt list.

Nothing is armed. Reproduce with ``python -m bot.research.alphai_sleeve.dip``.
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.core.venue_fees import VENUE_MAKER_FEE, VENUE_TAKER_FEE
from bot.integrations.alphai.parse import AlphaIHeadline
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_sleeve.book import (
    LOOKBACK,
    MAX_STALE_DAYS,
    MIN_BARS,
    MIN_QUOTE_EUR,
    SLEEVE_EUR,
    TONE_DAYS,
    _gap,
    _shift,
    counts_for,
    load_headlines,
    tone_names,
)
from bot.research.alphai_sleeve.universe import (
    INTRADAY,
    LISTED_MIN_QUOTE,
    alt_bases,
    ensure_intraday,
    fetch_markets,
)

CUSHION = 0.01
TARGET = 0.07
CAPACITY = 0.10
IMPACT_K = 0.02
IMPACT_CAP = 0.03
MIN_ORDER_EUR = 5.0
MAKER = float(VENUE_MAKER_FEE["bitvavo"])
TAKER = float(VENUE_TAKER_FEE["bitvavo"])
ARCHIVE = Path("data/research/alphai_crypto_news.json")
OUT = Path(__file__).with_name("DIP.md")
TAPE_START = "2024-01-01"
PRE_END = "2025-12-31"
YEAR = "2026-01-01"

Bar = tuple[float, float, float, float]


@dataclass(frozen=True)
class PathOut:
    """A session's dip, decided without the fill size."""

    buy_px: float
    close: float
    maker: bool


@dataclass(frozen=True)
class DayBook:
    quote: float
    n_bars: int
    path: PathOut | None


@dataclass(frozen=True)
class Run:
    label: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    fills: int
    maker_exits: int
    flattens: int
    maker_pnl: float
    flatten_pnl: float
    largest_day: float
    names: tuple[tuple[str, float], ...]


def bounce_path(
    bars: Sequence[Bar],
    cushion: float = CUSHION,
    target: float = TARGET,
) -> PathOut | None:
    """Bid above lows already printed. The fill bar cannot also be the sale."""
    if len(bars) < 2:
        return None
    printed = bars[0][2]
    bought = -1
    buy_px = 0.0
    for index, (open_, high, low, _close) in enumerate(bars):
        if index == 0:
            continue
        if bought < 0:
            if printed > 0:
                buy_px = printed * (1.0 + cushion)
                if buy_px < open_ and low <= buy_px:
                    bought = index
            printed = min(printed, low)
        elif high >= buy_px * (1.0 + target):
            return PathOut(buy_px, bars[-1][3], True)
    if bought < 0 or buy_px <= 0:
        return None
    return PathOut(buy_px, bars[-1][3], False)


def sessions_from_rows(rows: Sequence[Sequence[float]]) -> dict[str, DayBook]:
    buckets: dict[str, list[Sequence[float]]] = defaultdict(list)
    for row in rows:
        if len(row) < 6:
            continue
        day = datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")
        buckets[day].append(row)
    out: dict[str, DayBook] = {}
    for day, bars in buckets.items():
        ordered = sorted(bars, key=lambda item: int(item[0]))
        packed = tuple(
            (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])) for bar in ordered
        )
        quote = sum(float(bar[5]) * float(bar[4]) for bar in ordered)
        out[day] = DayBook(quote, len(packed), bounce_path(packed))
    return out


def load_paths(cache: Path, bases: Sequence[str]) -> dict[str, dict[str, DayBook]]:
    books: dict[str, dict[str, DayBook]] = {}
    for base in bases:
        path = cache / f"{base}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            books[base] = sessions_from_rows(rows)
    return books


def _slip(notional: float, adv: float) -> float:
    if adv <= 0:
        return IMPACT_CAP
    return min(IMPACT_CAP, IMPACT_K * math.sqrt(max(0.0, notional) / adv))


def _adv(history: Sequence[tuple[str, float]], day: str, min_quote: float) -> float | None:
    cut = bisect.bisect_left(history, (day,))
    if cut < LOOKBACK:
        return None
    window = history[cut - LOOKBACK : cut]
    if _gap(window[-1][0], day) > MAX_STALE_DAYS:
        return None
    adv = float(statistics.median(quote for _day, quote in window))
    if adv < min_quote:
        return None
    return adv


def _pnl(path: PathOut, notional: float, adv: float) -> tuple[float, bool]:
    if path.maker:
        proceeds = notional * (1.0 - MAKER) ** 2 * (1.0 + TARGET)
        return proceeds - notional, True
    qty = notional * (1.0 - MAKER) / path.buy_px
    gross = qty * path.close
    proceeds = gross * (1.0 - _slip(gross, adv)) * (1.0 - TAKER)
    return proceeds - notional, False


def simulate(
    books: Mapping[str, Mapping[str, DayBook]],
    *,
    universe: Sequence[str],
    start: str,
    end: str,
    min_quote: float,
    allowed: Mapping[str, set[str]] | None = None,
) -> Run:
    usable = {
        base: tuple(
            sorted(
                (day, book.quote)
                for day, book in days.items()
                if book.n_bars >= MIN_BARS and book.quote > 0
            )
        )
        for base, days in books.items()
    }
    days = sorted(
        {
            day
            for base in universe
            for day in books.get(base, {})
            if start <= day <= end
        }
    )
    equity = SLEEVE_EUR
    peak = SLEEVE_EUR
    max_dd = 0.0
    largest = 0.0
    fills = 0
    maker_exits = 0
    flattens = 0
    maker_pnl = 0.0
    flatten_pnl = 0.0
    by_name: dict[str, float] = defaultdict(float)
    for day in days:
        gate = None if allowed is None else allowed.get(day, set())
        chosen: list[tuple[str, float, DayBook]] = []
        for base in universe:
            if gate is not None and base not in gate:
                continue
            book = books.get(base, {}).get(day)
            history = usable.get(base)
            if book is None or history is None or book.n_bars < MIN_BARS:
                continue
            adv = _adv(history, day, min_quote)
            if adv is None:
                continue
            chosen.append((base, adv, book))
        if not chosen:
            continue
        slice_ = equity / len(chosen)
        day_pnl = 0.0
        for base, adv, book in chosen:
            notional = min(slice_, CAPACITY * adv)
            path = book.path
            if path is None or notional < MIN_ORDER_EUR:
                continue
            pnl, maker = _pnl(path, notional, adv)
            day_pnl += pnl
            by_name[base] += pnl
            fills += 1
            if maker:
                maker_exits += 1
                maker_pnl += pnl
            else:
                flattens += 1
                flatten_pnl += pnl
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        equity += day_pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    ranked = tuple(sorted(by_name.items(), key=lambda item: (-item[1], item[0])))
    return Run(
        label="",
        pnl=equity - SLEEVE_EUR,
        end_equity=equity,
        max_dd=max_dd,
        days=len(days),
        fills=fills,
        maker_exits=maker_exits,
        flattens=flattens,
        maker_pnl=maker_pnl,
        flatten_pnl=flatten_pnl,
        largest_day=largest,
        names=tuple((base, pnl) for base, pnl in ranked if abs(pnl) > 1e-9),
    )


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _last_complete_day() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _one_window(run: Run) -> bool:
    if run.pnl <= 0 or run.maker_exits < 40:
        return False
    if run.max_dd >= run.pnl / SLEEVE_EUR:
        return False
    if run.largest_day > 0 and run.largest_day >= 0.5 * run.pnl:
        return False
    top = run.names[0][1] if run.names else 0.0
    return top < 0.5 * run.pnl


def _line(label: str, run: Run) -> str:
    top = ", ".join(f"{base} {_eur(pnl)}" for base, pnl in run.names[:4]) or "—"
    flag = "pass" if _one_window(run) else "—"
    return (
        f"| {label} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.fills} | "
        f"{run.maker_exits} | {run.flattens} | {_eur(run.maker_pnl)} | "
        f"{_eur(run.flatten_pnl)} | {_eur(run.largest_day)} | {top} | {flag} |"
    )


def _tone_allow(
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    days: Sequence[str],
    universe: Sequence[str],
) -> dict[str, set[str]]:
    return {day: set(tone_names(counts_for(grouped, day, universe))) for day in days}


def _render(
    rows: list[tuple[str, Run]],
    *,
    n_listed: int,
    n_paths: int,
    last: str,
    overlap: str,
) -> str:
    groups: dict[str, dict[str, Run]] = {}
    for label, run in rows:
        name, _, window = label.partition(" / ")
        groups.setdefault(name, {})[window or label] = run
    passed = []
    for name, windows in groups.items():
        if name == "tone":
            continue
        needed = ("full", "before 2026", "2026")
        if all(window in windows and _one_window(windows[window]) for window in needed):
            passed.append(name)
    verdict = (
        "Cells that clear both periods: " + ", ".join(f"`{name}`" for name in passed) + "."
        if passed
        else "No cell clears both periods."
    )
    header = (
        "| cell | pnl | max DD | fills | maker exits | flattens | maker pnl | "
        "flatten pnl | largest day | largest names | one-window |"
    )
    rule = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"
    return "\n".join(
        [
            "# Seven percent dip",
            "",
            "Rules are the module docstring in `__init__.py`. The sell is 7%",
            "above the fill. The bid stays 1% above the low already printed.",
            f"Sleeve €{SLEEVE_EUR:,.0f}, split across every eligible name that day.",
            f"Listed alts {n_listed}. Fifteen-minute books {n_paths}.",
            f"Tape {TAPE_START} → {last}. News overlap {overlap} → {last}.",
            "`tone` has no path before 2026.",
            "",
            header,
            rule,
            "\n".join(_line(label, run) for label, run in rows),
            "",
            "Names with at least one fill: "
            + "; ".join(f"{label} {len(run.names)}" for label, run in rows)
            + ".",
            "",
            verdict,
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.alphai_sleeve.dip`",
            "",
        ]
    )


def main() -> None:
    last = _last_complete_day()
    markets = fetch_markets()
    listed = alt_bases(markets)
    print(f"listed alts {len(listed)}", flush=True)
    cached = ensure_intraday(listed)
    print(f"15m cache {cached}", flush=True)
    books = load_paths(INTRADAY, listed)
    print(f"loaded {len(books)}", flush=True)
    grouped = load_headlines(ARCHIVE)
    overlap = _shift(min(grouped), TONE_DAYS)
    desk = tuple(base for base in DEFAULT_UNIVERSE if base in books)
    tone_days = sorted(
        {
            day
            for base in listed
            for day in books.get(base, {})
            if overlap <= day <= last
        }
    )
    allowed = _tone_allow(grouped, tone_days, listed)
    job = tuple[str, tuple[str, ...], str, str, float, dict[str, set[str]] | None]
    jobs: list[job] = []
    windows = (
        ("full", TAPE_START, last),
        ("before 2026", TAPE_START, PRE_END),
        ("2026", YEAR, last),
    )
    for window, start, end in windows:
        jobs.append((f"listed / {window}", listed, start, end, LISTED_MIN_QUOTE, None))
        jobs.append((f"liquid / {window}", listed, start, end, MIN_QUOTE_EUR, None))
        jobs.append((f"desk / {window}", desk, start, end, MIN_QUOTE_EUR, None))
    jobs.append((f"tone / {overlap} → {last}", listed, overlap, last, LISTED_MIN_QUOTE, allowed))
    rows: list[tuple[str, Run]] = []
    for label, universe, start, end, floor, gate in jobs:
        run = simulate(
            books,
            universe=universe,
            start=start,
            end=end,
            min_quote=floor,
            allowed=gate,
        )
        rows.append((label, run))
        print(
            f"{label:28} {_eur(run.pnl):>12} dd {_pct(run.max_dd):>6} "
            f"maker {run.maker_exits} flat {run.flattens}",
            flush=True,
        )
    text = _render(rows, n_listed=len(listed), n_paths=len(books), last=last, overlap=overlap)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
