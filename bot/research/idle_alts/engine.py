"""Idle-cash buys on alts outside the desk. Nothing is armed."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import WET, fill_px

NOT_ALT = frozenset(
    {
        "BTC",
        "EUR",
        "USD",
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
    }
)

DROP = 0.08
BTC_FLOOR = -0.03
TARGET = 0.06
HOLD_SESSIONS = 3
MAX_NAMES = 4
MIN_QUOTE = 100_000.0
LOOKBACK = 5
MAX_STALE_DAYS = 2
DESK = frozenset(DEFAULT_UNIVERSE)

Bar = tuple[float, float, float, float, float]


@dataclass(frozen=True)
class HoldDay:
    date: str
    hold: str
    equity: float


@dataclass
class _Lot:
    base: str
    qty: float
    entry_open: float
    entry_i: int
    adv: float
    cost: float


@dataclass
class Result:
    pnl: float
    end_equity: float
    max_dd: float
    year_pnl: dict[str, float]
    buys: int
    target_exits: int
    time_exits: int
    forced_exits: int
    names: tuple[tuple[str, float], ...] = field(default_factory=tuple)


def qualifies(ret: float, btc_ret: float, quote: float, base: str) -> bool:
    """Idiosyncratic drop outside the desk, liquid enough to trade."""
    if base in DESK or base in NOT_ALT:
        return False
    if ret > -DROP or btc_ret <= BTC_FLOOR:
        return False
    return quote >= MIN_QUOTE


def pick_names(rows: Sequence[tuple[str, float]], n: int = MAX_NAMES) -> list[str]:
    """Most negative first. ``rows`` are ``(base, return)``."""
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return [base for base, _ret in ordered[:n]]


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, UTC).strftime("%Y-%m-%d")


def sessions_from_rows(rows: Sequence[Sequence[object]]) -> dict[str, Bar]:
    out: dict[str, Bar] = {}
    for row in rows:
        if len(row) < 6:
            continue
        day = _day(int(row[0]))
        open_, high, low, close = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        quote = float(row[5]) * close
        out[day] = (open_, high, low, close, quote)
    return out


def load_alt_sessions(cache: Path, bases: Sequence[str]) -> dict[str, dict[str, Bar]]:
    books: dict[str, dict[str, Bar]] = {}
    for base in bases:
        path = cache / f"{base}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            books[base] = sessions_from_rows(rows)
    return books


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _gap(earlier: str, later: str) -> int:
    left = datetime.strptime(earlier, "%Y-%m-%d").date()
    right = datetime.strptime(later, "%Y-%m-%d").date()
    return (right - left).days


def _prior_quote(history: Sequence[tuple[str, float]], day: str) -> tuple[float, float] | None:
    prior = [(d, q) for d, q in history if d < day and q > 0]
    if len(prior) < LOOKBACK:
        return None
    window = prior[-LOOKBACK:]
    if _gap(window[-1][0], day) > MAX_STALE_DAYS:
        return None
    quote = _median([q for _d, q in window])
    if quote < MIN_QUOTE:
        return None
    adv = sum(q for _d, q in prior[-20:]) / min(20, len(prior))
    return quote, adv


def _buy_cost(budget: float, open_: float, adv: float) -> tuple[float, float, float]:
    """Return qty, fill, and cash spent. ``budget`` includes the fee."""
    fee = WET.fee_side
    spend = budget / (1.0 + fee)
    px = fill_px(open_, "buy", notional=spend, adv=adv, model=WET)
    if px <= 0 or spend <= 0:
        return 0.0, 0.0, 0.0
    return spend / px, px, spend * (1.0 + fee)


def _sell_proceeds(qty: float, ref: float, adv: float) -> float:
    gross = qty * ref
    px = fill_px(ref, "sell", notional=gross, adv=adv, model=WET)
    return qty * px * (1.0 - WET.fee_side)


def _indexed(alts: Mapping[str, Mapping[str, Bar]]) -> dict[str, list[tuple[str, Bar]]]:
    return {base: sorted(days.items()) for base, days in alts.items()}


def _btc_return_before(closes: Mapping[str, float], day: str) -> float | None:
    prior = [key for key in closes if key < day]
    if len(prior) < 2:
        return None
    prior.sort()
    last, prev = closes[prior[-1]], closes[prior[-2]]
    if prev <= 0:
        return None
    return last / prev - 1.0


def simulate(
    holds: Sequence[HoldDay],
    alts: Mapping[str, Mapping[str, Bar]],
    btc_close: Mapping[str, float],
    *,
    book_eur: float = 20_000.0,
) -> Result:
    """Stack the idle book on the live equity path. Live days are untouched."""
    equity = book_eur
    peak = equity
    max_dd = 0.0
    lots: dict[str, _Lot] = {}
    realized: dict[str, float] = {}
    buys = targets = times = forced = 0
    year_end: dict[str, float] = {}
    indexed = _indexed(alts)
    histories = {base: tuple((day, bar[4]) for day, bar in rows) for base, rows in indexed.items()}
    pos = {base: {day: i for i, (day, _bar) in enumerate(rows)} for base, rows in indexed.items()}
    prev_hold = "cash"
    prev_equity = book_eur

    def _mark(day: str) -> float:
        value = equity
        for lot in lots.values():
            row = dict(indexed.get(lot.base, []))
            bar = row.get(day)
            value += lot.qty * (bar[3] if bar else lot.entry_open)
        return value

    def _close(lot: _Lot, ref: float, reason: str) -> None:
        nonlocal equity, targets, times, forced
        proceeds = _sell_proceeds(lot.qty, ref, lot.adv)
        realized[lot.base] = realized.get(lot.base, 0.0) + proceeds - lot.cost
        equity += proceeds
        if reason == "target":
            targets += 1
        elif reason == "time":
            times += 1
        else:
            forced += 1

    for day in holds:
        live_today = day.hold != "cash"
        live_prev = prev_hold != "cash"
        if live_today or live_prev:
            if live_today:
                for base, lot in list(lots.items()):
                    row = dict(indexed.get(base, [])).get(day.date)
                    _close(lot, row[0] if row else lot.entry_open, "forced")
                    lots.pop(base, None)
            if prev_equity > 0:
                equity *= day.equity / prev_equity
            marked = equity
        else:
            for base, lot in list(lots.items()):
                now_i = pos.get(base, {}).get(day.date)
                row = dict(indexed.get(base, [])).get(day.date)
                if now_i is None or row is None:
                    continue
                if now_i - lot.entry_i >= HOLD_SESSIONS:
                    _close(lot, row[0], "time")
                    lots.pop(base, None)
            for base, lot in list(lots.items()):
                row = dict(indexed.get(base, [])).get(day.date)
                if row is None:
                    continue
                limit = lot.entry_open * (1.0 + TARGET)
                if row[1] >= limit:
                    _close(lot, limit, "target")
                    lots.pop(base, None)
            free = MAX_NAMES - len(lots)
            book_value = _mark_open(equity, lots, indexed, day.date)
            if free > 0 and book_value > 0:
                candidates: list[tuple[str, float, float]] = []
                btc_ret = _btc_return_before(btc_close, day.date)
                for base, rows in indexed.items():
                    if base in lots or btc_ret is None:
                        continue
                    prior = _prior_quote(histories[base], day.date)
                    now = pos[base].get(day.date)
                    if prior is None or now is None or now < 2:
                        continue
                    yesterday, before = rows[now - 1][1], rows[now - 2][1]
                    if before[3] <= 0 or rows[now][1][0] <= 0:
                        continue
                    ret = yesterday[3] / before[3] - 1.0
                    if not qualifies(ret, btc_ret, prior[0], base):
                        continue
                    candidates.append((base, ret, prior[1]))
                chosen = pick_names([(base, ret) for base, ret, _adv in candidates], free)
                advs = {base: adv for base, _ret, adv in candidates}
                slot = book_value / MAX_NAMES
                for base in chosen:
                    row = indexed[base][pos[base][day.date]][1]
                    if equity < slot:
                        continue
                    qty, _px, cost = _buy_cost(slot, row[0], advs[base])
                    if qty <= 0 or cost > equity + 1e-6:
                        continue
                    equity -= cost
                    lots[base] = _Lot(base, qty, row[0], pos[base][day.date], advs[base], cost)
                    buys += 1
                    limit = row[0] * (1.0 + TARGET)
                    if row[1] >= limit:
                        _close(lots.pop(base), limit, "target")
            marked = _mark(day.date)
        peak = max(peak, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        year_end[day.date[:4]] = marked
        prev_hold = day.hold
        prev_equity = day.equity
    names = tuple(sorted(realized.items(), key=lambda item: (-item[1], item[0])))
    prev = book_eur
    years: dict[str, float] = {}
    for year in sorted(year_end):
        years[year] = year_end[year] - prev
        prev = year_end[year]
    end = year_end[max(year_end)] if year_end else book_eur
    return Result(
        pnl=end - book_eur,
        end_equity=end,
        max_dd=max_dd,
        year_pnl=years,
        buys=buys,
        target_exits=targets,
        time_exits=times,
        forced_exits=forced,
        names=tuple((base, pnl) for base, pnl in names if abs(pnl) > 1e-6),
    )


def _mark_open(
    cash: float,
    lots: Mapping[str, _Lot],
    indexed: Mapping[str, Sequence[tuple[str, Bar]]],
    day: str,
) -> float:
    value = cash
    for lot in lots.values():
        row = dict(indexed.get(lot.base, [])).get(day)
        value += lot.qty * (row[0] if row else lot.entry_open)
    return value


def beats_live(
    combined: Result,
    live_pnl: float,
    live_dd: float,
    live_years: Mapping[str, float],
) -> bool:
    if combined.pnl <= live_pnl or combined.max_dd > live_dd:
        return False
    return all(combined.year_pnl.get(year, -math.inf) >= pnl for year, pnl in live_years.items())
