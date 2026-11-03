"""Hold the radar's one buy from the next open. Cash when it says don't."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from bot.core.venue_fees import VENUE_TAKER_FEE
from bot.research.radar_gate.score import (
    BASELINE_DAYS,
    NameScore,
    Story,
    _shift,
    pick_name,
    score_day,
)

BOOK_EUR = 20_000.0
LOOKBACK = 5
MIN_CHOP = 0.02
CAPACITY = 0.10
IMPACT_K = 0.02
IMPACT_CAP = 0.03
MIN_BARS = 48
TAKER = float(VENUE_TAKER_FEE["bitvavo"])
MODES = ("hot", "spike", "hot_any", "chop")


@dataclass(frozen=True)
class Session:
    day: str
    open: float
    high: float
    low: float
    close: float
    quote: float
    n_bars: int


@dataclass(frozen=True)
class Run:
    mode: str
    start: str
    end: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    days_held: int
    trips: int
    buys: int
    largest_day: float
    picks: tuple[tuple[str, int], ...]


def sessions_from_candles(rows: Sequence[Sequence[float]]) -> dict[str, Session]:
    buckets: dict[str, list[Sequence[float]]] = {}
    for row in rows:
        if len(row) < 6:
            continue
        day = datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(row)
    out: dict[str, Session] = {}
    for day, bars in buckets.items():
        ordered = sorted(bars, key=lambda item: int(item[0]))
        high = max(float(bar[2]) for bar in ordered)
        low = min(float(bar[3]) for bar in ordered)
        quote = sum(float(bar[5]) * float(bar[4]) for bar in ordered)
        out[day] = Session(
            day=day,
            open=float(ordered[0][1]),
            high=high,
            low=low,
            close=float(ordered[-1][4]),
            quote=quote,
            n_bars=len(ordered),
        )
    return out


def load_books(cache: Path, universe: Sequence[str]) -> dict[str, dict[str, Session]]:
    books: dict[str, dict[str, Session]] = {}
    for base in universe:
        path = cache / f"{base}.json"
        if not path.exists():
            books[base] = {}
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        books[base] = sessions_from_candles(rows if isinstance(rows, list) else [])
    return books


def _impact(notional: float, adv: float) -> float:
    if adv <= 0:
        return IMPACT_CAP
    return min(IMPACT_CAP, IMPACT_K * math.sqrt(max(0.0, notional) / adv))


def _buy(cash: float, open_: float, adv: float) -> tuple[float, float]:
    if cash <= 0 or open_ <= 0 or adv <= 0 or cash > CAPACITY * adv:
        return 0.0, 0.0
    price = open_ * (1.0 + _impact(cash, adv))
    if price <= 0:
        return 0.0, 0.0
    return 0.0, cash * (1.0 - TAKER) / price


def _sell(qty: float, open_: float, adv: float) -> float:
    gross = qty * open_
    price = open_ * (1.0 - _impact(gross, adv))
    return qty * price * (1.0 - TAKER)


def _chop(session: Session) -> float:
    if session.open <= 0:
        return 0.0
    span = session.high - session.low - abs(session.close - session.open)
    return max(0.0, span / session.open)


def _adv(book: Mapping[str, Session], day: str) -> float:
    priors = [
        session
        for name, session in book.items()
        if name < day and session.n_bars >= MIN_BARS
    ]
    priors.sort(key=lambda session: session.day)
    trail = priors[-LOOKBACK:]
    if not trail:
        return 0.0
    return float(median(session.quote for session in trail))


def _chop_pick(
    books: Mapping[str, Mapping[str, Session]],
    day: str,
    universe: Sequence[str],
) -> str | None:
    best: tuple[float, str] | None = None
    for base in universe:
        session = books.get(base, {}).get(day)
        trail = [
            row
            for name, row in books.get(base, {}).items()
            if name <= day and row.n_bars >= MIN_BARS
        ]
        trail.sort(key=lambda row: row.day)
        window = trail[-LOOKBACK:]
        if session is None or len(window) < LOOKBACK:
            continue
        score = sum(_chop(row) for row in window) / LOOKBACK
        if score < MIN_CHOP:
            continue
        if best is None or score > best[0] or (score == best[0] and base < best[1]):
            best = (score, base)
    return None if best is None else best[1]


def _calendar(
    books: Mapping[str, Mapping[str, Session]],
    universe: Sequence[str],
    last: str,
) -> list[str]:
    days = {
        session.day
        for base in universe
        for session in books.get(base, {}).values()
        if session.n_bars >= MIN_BARS and session.day <= last
    }
    return sorted(days)


def simulate(
    books: Mapping[str, Mapping[str, Session]],
    grouped: Mapping[str, Sequence[Story]],
    *,
    universe: Sequence[str],
    mode: str,
    last: str,
) -> Run:
    """Decide at a session close. Trade the next session's open."""
    names = set(universe)
    days = _calendar(books, universe, last)
    if grouped:
        ready = _shift(min(grouped), BASELINE_DAYS)
        days = [day for day in days if day >= ready]
    scores: dict[str, list[NameScore]] = {}
    if mode != "chop":
        for day in days:
            scores[day] = score_day(grouped, day, names)
    cash = BOOK_EUR
    held: str | None = None
    qty = 0.0
    peak = BOOK_EUR
    max_dd = 0.0
    days_held = 0
    trips = 0
    buys = 0
    largest = 0.0
    mark = BOOK_EUR
    last_close = 0.0
    last_adv = 0.0
    picks: Counter[str] = Counter()
    traded: list[str] = []
    for index, day in enumerate(days):
        if index == 0:
            continue
        decision_day = days[index - 1]
        if mode == "chop":
            target = _chop_pick(books, decision_day, universe)
        else:
            target = pick_name(scores.get(decision_day, ()), mode)
        if held is not None and held != target:
            session = books.get(held, {}).get(day)
            if session is not None:
                cash += _sell(qty, session.open, _adv(books[held], day))
                trips += 1
                held = None
                qty = 0.0
        if held is None and target is not None:
            session = books.get(target, {}).get(day)
            adv = _adv(books.get(target, {}), day)
            if session is not None and session.open > 0:
                _cash, bought = _buy(cash, session.open, adv)
                if bought > 0:
                    cash = _cash
                    qty = bought
                    held = target
                    buys += 1
        if target is not None:
            picks[target] += 1
        if held is not None:
            days_held += 1
        close_session = books.get(held, {}).get(day) if held is not None else None
        if held is not None and close_session is not None:
            last_close = close_session.close
            last_adv = _adv(books[held], day)
            end_mark = _sell(qty, last_close, last_adv)
        elif held is not None:
            end_mark = mark
        else:
            end_mark = cash
        day_pnl = end_mark - mark
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        mark = end_mark
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)
        traded.append(day)
    if held is not None and last_close > 0:
        cash += _sell(qty, last_close, last_adv)
        trips += 1
        held = None
        qty = 0.0
    end_equity = cash if held is None else mark
    return Run(
        mode=mode,
        start=traded[0] if traded else last,
        end=traded[-1] if traded else last,
        pnl=end_equity - BOOK_EUR,
        end_equity=end_equity,
        max_dd=max_dd,
        days=len(traded),
        days_held=days_held,
        trips=trips,
        buys=buys,
        largest_day=largest,
        picks=tuple(picks.most_common()),
    )
