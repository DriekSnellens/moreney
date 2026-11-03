"""Causal swing grid. Constants below were locked before the wet replay."""

from __future__ import annotations

import bisect
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from bot.core.venue_fees import VENUE_MAKER_FEE, VENUE_TAKER_FEE

BOOK_EUR = 20_000.0
LOOKBACK = 5
MIN_CHOP = 0.02
MIN_QUOTE_EUR = 100_000.0
MAX_STALE_DAYS = 2
MIN_BARS = 48
RUNG_FRACS = (0.35, 0.70)
CAPACITY = 0.10
RETEST_WARMUP = 16
RETEST_INNER = 0.20
RETEST_MIN_RANGE = 0.02
CUSHION = 0.01
BOUNCE_5 = 0.05
BOUNCE_10 = 0.10
STOP_THROUGH = 0.02
SHALLOW_MAX = 0.04
HOLD_SESSIONS = 3
EARLY_BARS = 16
PRIOR_FRAC = 0.50
ROOM = 0.003
IMPACT_K = 0.02
IMPACT_CAP = 0.03
MAKER = float(VENUE_MAKER_FEE["bitvavo"])
TAKER = float(VENUE_TAKER_FEE["bitvavo"])
MODES = (
    "strict",
    "path",
    "retest",
    "bounce5",
    "bounce10",
    "confirm5",
    "stop5",
    "shallow5",
    "hold3",
    "liquid5",
    "early5",
    "reclaim",
    "prior",
    "hold",
    "oracle",
)

Bar = tuple[float, float, float, float]


@dataclass(frozen=True)
class Session:
    day: str
    open: float
    high: float
    low: float
    close: float
    quote: float
    bars: tuple[Bar, ...]

    @property
    def n_bars(self) -> int:
        return len(self.bars)


@dataclass(frozen=True)
class Choice:
    base: str
    chop: float
    range_frac: float
    mid: float
    adv: float


@dataclass(frozen=True)
class DayOut:
    end_equity: float
    filled: bool
    maker_pnl: float
    flatten_pnl: float
    maker_exits: int
    flattens: int


@dataclass(frozen=True)
class Run:
    mode: str
    start: str
    end: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    selected: int
    filled: int
    maker_exits: int
    flattens: int
    maker_pnl: float
    flatten_pnl: float
    largest_day: float
    picks: tuple[tuple[str, int], ...]


def day_chop(open_: float, high: float, low: float, close: float) -> float:
    if open_ <= 0 or high < low:
        return 0.0
    return (high - low - abs(close - open_)) / open_


def day_range(open_: float, high: float, low: float) -> float:
    if open_ <= 0 or high < low:
        return 0.0
    return (high - low) / open_


def _impact(notional: float, adv: float) -> float:
    if adv <= 0:
        return IMPACT_CAP
    return min(IMPACT_CAP, IMPACT_K * math.sqrt(max(0.0, notional) / adv))


def _usable(session: Session) -> bool:
    return (
        session.n_bars >= MIN_BARS
        and session.open > 0
        and session.high >= session.low
        and session.quote > 0
    )


_INDEX: dict[int, tuple[list[Session], dict[str, Session]]] = {}


def _index(sessions: Sequence[Session]) -> tuple[list[Session], dict[str, Session]]:
    """Usable sessions, sorted, plus every session by day. Keyed by list identity."""
    hit = _INDEX.get(id(sessions))
    if hit is None:
        usable = [row for row in sessions if _usable(row)]
        usable.sort(key=lambda row: row.day)
        hit = (usable, {row.day: row for row in sessions})
        _INDEX[id(sessions)] = hit
    return hit


def _gap_days(earlier: str, later: str) -> int:
    a = datetime.strptime(earlier, "%Y-%m-%d").date()
    b = datetime.strptime(later, "%Y-%m-%d").date()
    return (b - a).days


def sessions_from_rows(rows: Sequence[Sequence[float]]) -> list[Session]:
    buckets: dict[str, list[Sequence[float]]] = {}
    for row in rows:
        if len(row) < 6:
            continue
        day = datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(row)
    out: list[Session] = []
    for day in sorted(buckets):
        bars = sorted(buckets[day], key=lambda r: int(r[0]))
        packed = tuple(
            (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])) for bar in bars
        )
        quote = sum(float(bar[5]) * float(bar[4]) for bar in bars)
        out.append(
            Session(
                day=day,
                open=packed[0][0],
                high=max(bar[1] for bar in packed),
                low=min(bar[2] for bar in packed),
                close=packed[-1][3],
                quote=quote,
                bars=packed,
            )
        )
    return out


def choose_name(
    by_base: Mapping[str, Sequence[Session]],
    day: str,
    equity: float,
    universe: Sequence[str],
    rank: str = "chop",
) -> Choice | None:
    """Pick from sessions strictly before ``day``. Today's range is not an input."""
    rung = equity / 2.0
    if equity <= 0 or rung <= 0:
        return None
    best: tuple[float, str, Choice] | None = None
    for base in universe:
        sessions = by_base.get(base)
        if not sessions:
            continue
        usable, by_day = _index(sessions)
        today = by_day.get(day)
        if today is None or not _usable(today):
            continue
        cut = bisect.bisect_left(usable, day, key=lambda row: row.day)
        prior = usable[:cut][-LOOKBACK:]
        if len(prior) < LOOKBACK:
            continue
        if _gap_days(prior[-1].day, day) > MAX_STALE_DAYS:
            continue
        chop = sum(day_chop(row.open, row.high, row.low, row.close) for row in prior) / LOOKBACK
        span = sum(day_range(row.open, row.high, row.low) for row in prior) / LOOKBACK
        adv = float(statistics.median(row.quote for row in prior))
        if chop < MIN_CHOP or span <= 0 or adv < MIN_QUOTE_EUR or rung > CAPACITY * adv:
            continue
        choice = Choice(
            base=base,
            chop=chop,
            range_frac=span,
            mid=prior[-1].close,
            adv=adv,
        )
        score = adv if rank == "liquid" else chop
        if best is None or score > best[0] or (score == best[0] and base < best[1]):
            best = (score, base, choice)
    return None if best is None else best[2]


def _buy_maker(cash: float, notional: float, price: float) -> tuple[float, float, float]:
    if price <= 0 or cash + 1e-6 < notional:
        return cash, 0.0, 0.0
    qty = notional * (1.0 - MAKER) / price
    return cash - notional, qty, notional


def _sell_maker(cash: float, qty: float, price: float) -> tuple[float, float]:
    proceeds = qty * price * (1.0 - MAKER)
    return cash + proceeds, proceeds


def _flatten(
    cash: float, qty: float, close: float, adv: float
) -> tuple[float, float, float]:
    slip = _impact(qty * close, adv)
    price = close * (1.0 - slip)
    proceeds = qty * price * (1.0 - TAKER)
    return cash + proceeds, proceeds, price


@dataclass
class _Lot:
    bar: int
    qty: float
    notional: float


def _run_grid(session: Session, choice: Choice, equity: float, mode: str) -> DayOut:
    buys = tuple(choice.mid * (1.0 - frac * choice.range_frac) for frac in RUNG_FRACS)
    sells = tuple(choice.mid * (1.0 + frac * choice.range_frac) for frac in RUNG_FRACS)
    active = [
        i
        for i, buy in enumerate(buys)
        if 0 < buy < session.open and sells[i] > buy
    ]
    notional = equity / 2.0
    cash = equity
    held: dict[int, _Lot | None] = {i: None for i in active}
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1

    def sell_held(bi: int, high: float, *, same_bar: bool) -> None:
        nonlocal cash, maker_pnl, maker_exits
        for i in active:
            lot = held[i]
            if lot is None:
                continue
            if lot.bar == bi and not same_bar:
                continue
            if high < sells[i]:
                continue
            cash, proceeds = _sell_maker(cash, lot.qty, sells[i])
            maker_pnl += proceeds - lot.notional
            maker_exits += 1
            held[i] = None

    def buy_open(bi: int, low: float) -> None:
        nonlocal cash
        for i in active:
            if held[i] is not None or low > buys[i]:
                continue
            cash, qty, spent = _buy_maker(cash, notional, buys[i])
            if qty > 0:
                held[i] = _Lot(bi, qty, spent)

    for bi, (open_, high, low, close) in enumerate(session.bars):
        if mode == "path" and close >= open_:
            buy_open(bi, low)
            sell_held(bi, high, same_bar=True)
        else:
            sell_held(bi, high, same_bar=False)
            buy_open(bi, low)
        if bi != last:
            continue
        leftover = [(i, lot) for i, lot in held.items() if lot is not None]
        qty = sum(lot.qty for _, lot in leftover)
        if qty <= 0:
            continue
        cost = sum(lot.notional for _, lot in leftover)
        cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
        flatten_pnl += proceeds - cost
        flattens += len(leftover)
        for i, _lot in leftover:
            held[i] = None

    filled = maker_exits + flattens > 0
    return DayOut(cash, filled, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_retest(session: Session, choice: Choice, equity: float) -> DayOut:
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and bi >= RETEST_WARMUP:
            prev = session.bars[:bi]
            printed_high = max(bar[1] for bar in prev)
            printed_low = min(bar[2] for bar in prev)
            span = printed_high - printed_low
            if session.open > 0 and span / session.open >= RETEST_MIN_RANGE and span > 0:
                buy_px = printed_low + RETEST_INNER * span
                target = printed_high - RETEST_INNER * span
                if target > buy_px and buy_px < open_ and low <= buy_px:
                    cash, qty, cost = _buy_maker(cash, equity, buy_px)
                    if qty > 0:
                        sell_px = target
                        bought_on = bi
                    else:
                        qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_bounce(
    session: Session,
    choice: Choice,
    equity: float,
    target: float,
    *,
    entry_through: int = 10**9,
    sell_at_open: bool = False,
) -> DayOut:
    """Bid 1% above the low already printed. Sell ``target`` above the fill."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    entered = False
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and not entered and 1 <= bi <= entry_through:
            printed_low = min(bar[2] for bar in session.bars[:bi])
            buy_px = printed_low * (1.0 + CUSHION)
            blocked = sell_at_open and buy_px * (1.0 + ROOM) >= session.open
            if printed_low > 0 and not blocked and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = session.open if sell_at_open else buy_px * (1.0 + target)
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _taker_exit(qty: float, ref: float, adv: float) -> tuple[float, float]:
    slip = _impact(qty * ref, adv)
    price = ref * (1.0 - slip)
    proceeds = qty * price * (1.0 - TAKER)
    return proceeds, price


def _run_confirm(session: Session, choice: Choice, equity: float) -> DayOut:
    """Bid only after a later bar holds the low and closes back above it."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    entered = False
    printed: float | None = None
    confirmed: float | None = None
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and not entered and confirmed is not None and confirmed > 0:
            buy_px = confirmed * (1.0 + CUSHION)
            if low >= confirmed and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = buy_px * (1.0 + BOUNCE_5)
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
        if printed is None or low < printed:
            printed = low
            confirmed = None
        elif confirmed is None and close > printed and low >= printed:
            confirmed = printed
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_stop(session: Session, choice: Choice, equity: float) -> DayOut:
    """Same 5% target. A break 2% through the armed low is a taker stop."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    stop_px = 0.0
    bought_on = -1
    entered = False
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and not entered and bi >= 1:
            printed_low = min(bar[2] for bar in session.bars[:bi])
            buy_px = printed_low * (1.0 + CUSHION)
            broken = printed_low * (1.0 - STOP_THROUGH)
            if printed_low > 0 and low > broken and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = buy_px * (1.0 + BOUNCE_5)
                    stop_px = broken
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on:
            stopped = low <= stop_px or open_ <= stop_px
            targeted = high >= sell_px and not stopped
            if stopped:
                ref = open_ if open_ <= stop_px else stop_px
                proceeds, _px = _taker_exit(qty, ref, choice.adv)
                cash += proceeds
                flatten_pnl += proceeds - cost
                flattens += 1
                qty = 0.0
            elif targeted:
                cash, proceeds = _sell_maker(cash, qty, sell_px)
                maker_pnl += proceeds - cost
                maker_exits += 1
                qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_shallow(session: Session, choice: Choice, equity: float) -> DayOut:
    """No bid once today's low is more than 4% under the open."""
    floor = session.open * (1.0 - SHALLOW_MAX)
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    entered = False
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and not entered and bi >= 1:
            printed_low = min(bar[2] for bar in session.bars[:bi])
            buy_px = printed_low * (1.0 + CUSHION)
            if (
                printed_low >= floor
                and printed_low > 0
                and buy_px < open_
                and low <= buy_px
            ):
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = buy_px * (1.0 + BOUNCE_5)
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


@dataclass
class _Hold:
    base: str
    qty: float
    cost: float
    sell_px: float
    adv: float
    seen: int
    last_close: float


def _enter_hold_day(
    session: Session, equity: float, adv: float, base: str
) -> tuple[float, _Hold | None, float, int]:
    """Bounce entry. Returns cash, open position, maker pnl, maker exits."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    entered = False
    for bi, (open_, high, low, _close) in enumerate(session.bars):
        if qty == 0 and not entered and bi >= 1:
            printed_low = min(bar[2] for bar in session.bars[:bi])
            buy_px = printed_low * (1.0 + CUSHION)
            if printed_low > 0 and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = buy_px * (1.0 + BOUNCE_5)
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            return cash, None, proceeds - cost, 1
    if qty <= 0:
        return cash, None, 0.0, 0
    return cash, _Hold(base, qty, cost, sell_px, adv, 1, session.close), 0.0, 0


def _advance_hold(
    session: Session, pos: _Hold, cash: float
) -> tuple[float, _Hold | None, float, int, float]:
    """Walk one later session. Returns cash, position, maker pnl, exits, flatten pnl."""
    if not session.bars:
        return cash, pos, 0.0, 0, 0.0
    last_close = pos.last_close
    for _bi, (_open, high, _low, close) in enumerate(session.bars):
        last_close = close
        if high >= pos.sell_px:
            cash, proceeds = _sell_maker(cash, pos.qty, pos.sell_px)
            return cash, None, proceeds - pos.cost, 1, 0.0
    seen = pos.seen + 1
    if seen >= HOLD_SESSIONS:
        proceeds, _px = _taker_exit(pos.qty, last_close, pos.adv)
        return cash + proceeds, None, 0.0, 0, proceeds - pos.cost
    nxt = _Hold(pos.base, pos.qty, pos.cost, pos.sell_px, pos.adv, seen, last_close)
    return cash, nxt, 0.0, 0, 0.0


def _marked(cash: float, pos: _Hold | None) -> float:
    if pos is None or pos.qty <= 0:
        return cash
    proceeds, _px = _taker_exit(pos.qty, pos.last_close, pos.adv)
    return cash + proceeds


def _run_prior(session: Session, choice: Choice, equity: float) -> DayOut:
    """Buy halfway down yesterday's range. Sell a return to yesterday's close."""
    buy_px = choice.mid * (1.0 - PRIOR_FRAC * choice.range_frac)
    sell_px = choice.mid
    if not (0 < buy_px < session.open and sell_px >= buy_px * (1.0 + ROOM)):
        return DayOut(equity, False, 0.0, 0.0, 0, 0)
    cash = equity
    qty = 0.0
    cost = 0.0
    bought_on = -1
    entered = False
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty == 0 and not entered and buy_px < open_ and low <= buy_px:
            cash, qty, cost = _buy_maker(cash, equity, buy_px)
            if qty > 0:
                bought_on = bi
                entered = True
            else:
                qty = 0.0
        elif qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_hold(session: Session, choice: Choice, equity: float) -> DayOut:
    slip_in = _impact(equity, choice.adv)
    buy_px = session.open * (1.0 + slip_in)
    if buy_px <= 0:
        return DayOut(equity, False, 0.0, 0.0, 0, 0)
    qty = equity * (1.0 - TAKER) / buy_px
    _cash, proceeds, _px = _flatten(0.0, qty, session.close, choice.adv)
    pnl = proceeds - equity
    return DayOut(proceeds, True, 0.0, pnl, 0, 1)


def _run_oracle(session: Session, equity: float) -> DayOut:
    if session.low <= 0 or session.high <= session.low:
        return DayOut(equity, False, 0.0, 0.0, 0, 0)
    qty = equity * (1.0 - MAKER) / session.low
    proceeds = qty * session.high * (1.0 - MAKER)
    return DayOut(proceeds, True, proceeds - equity, 0.0, 1, 0)


def run_session(session: Session, choice: Choice, equity: float, mode: str) -> DayOut:
    if mode in {"strict", "path"}:
        return _run_grid(session, choice, equity, mode)
    if mode == "retest":
        return _run_retest(session, choice, equity)
    if mode == "bounce5":
        return _run_bounce(session, choice, equity, BOUNCE_5)
    if mode == "bounce10":
        return _run_bounce(session, choice, equity, BOUNCE_10)
    if mode == "confirm5":
        return _run_confirm(session, choice, equity)
    if mode == "stop5":
        return _run_stop(session, choice, equity)
    if mode == "shallow5":
        return _run_shallow(session, choice, equity)
    if mode == "early5":
        return _run_bounce(session, choice, equity, BOUNCE_5, entry_through=EARLY_BARS)
    if mode == "reclaim":
        return _run_bounce(session, choice, equity, BOUNCE_5, sell_at_open=True)
    if mode == "prior":
        return _run_prior(session, choice, equity)
    if mode == "hold":
        return _run_hold(session, choice, equity)
    if mode == "oracle":
        return _run_oracle(session, equity)
    raise ValueError(mode)


def _simulate_hold3(
    by_base: Mapping[str, Sequence[Session]],
    *,
    universe: Sequence[str],
    start: str,
    end: str,
) -> Run:
    """Keep a bounce5 fill for up to three sessions, then sell the close."""
    days = sorted(
        {
            session.day
            for base in universe
            for session in by_base.get(base, ())
            if start <= session.day <= end
        }
    )
    index = {
        base: {session.day: session for session in sessions}
        for base, sessions in by_base.items()
    }
    cash = BOOK_EUR
    pos: _Hold | None = None
    peak = BOOK_EUR
    max_dd = 0.0
    selected = 0
    filled = 0
    maker_exits = 0
    flattens = 0
    maker_pnl = 0.0
    flatten_pnl = 0.0
    largest = 0.0
    picks: Counter[str] = Counter()
    for day in days:
        mark = _marked(cash, pos)
        if pos is not None:
            session = index.get(pos.base, {}).get(day)
            if session is None:
                seen = pos.seen + 1
                if seen >= HOLD_SESSIONS:
                    proceeds, _px = _taker_exit(pos.qty, pos.last_close, pos.adv)
                    cash += proceeds
                    flatten_pnl += proceeds - pos.cost
                    flattens += 1
                    pos = None
                else:
                    pos = _Hold(
                        pos.base, pos.qty, pos.cost, pos.sell_px, pos.adv, seen, pos.last_close
                    )
            else:
                cash, pos, made, exits, forced = _advance_hold(session, pos, cash)
                maker_pnl += made
                maker_exits += exits
                flatten_pnl += forced
                if forced != 0.0:
                    flattens += 1
        else:
            choice = choose_name(by_base, day, cash, universe)
            if choice is not None:
                selected += 1
                picks[choice.base] += 1
                cash, pos, made, exits = _enter_hold_day(
                    index[choice.base][day], cash, choice.adv, choice.base
                )
                maker_pnl += made
                maker_exits += exits
                if exits or pos is not None:
                    filled += 1
        end_mark = _marked(cash, pos)
        day_pnl = end_mark - mark
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        peak = max(peak, end_mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - end_mark) / peak)
    if pos is not None:
        proceeds, _px = _taker_exit(pos.qty, pos.last_close, pos.adv)
        cash += proceeds
        flatten_pnl += proceeds - pos.cost
        flattens += 1
    return Run(
        mode="hold3",
        start=days[0] if days else start,
        end=days[-1] if days else end,
        pnl=cash - BOOK_EUR,
        end_equity=cash,
        max_dd=max_dd,
        days=len(days),
        selected=selected,
        filled=filled,
        maker_exits=maker_exits,
        flattens=flattens,
        maker_pnl=maker_pnl,
        flatten_pnl=flatten_pnl,
        largest_day=largest,
        picks=tuple(picks.most_common()),
    )


def simulate(
    by_base: Mapping[str, Sequence[Session]],
    *,
    universe: Sequence[str],
    start: str,
    end: str,
    mode: str,
) -> Run:
    if mode == "hold3":
        return _simulate_hold3(by_base, universe=universe, start=start, end=end)
    days = sorted(
        {
            session.day
            for base in universe
            for session in by_base.get(base, ())
            if start <= session.day <= end
        }
    )
    equity = BOOK_EUR
    peak = BOOK_EUR
    max_dd = 0.0
    selected = 0
    filled = 0
    maker_exits = 0
    flattens = 0
    maker_pnl = 0.0
    flatten_pnl = 0.0
    largest = 0.0
    picks: Counter[str] = Counter()
    index = {
        base: {session.day: session for session in sessions}
        for base, sessions in by_base.items()
    }
    rank = "liquid" if mode == "liquid5" else "chop"
    play = "bounce5" if mode == "liquid5" else mode
    for day in days:
        choice = choose_name(by_base, day, equity, universe, rank)
        if choice is None:
            continue
        selected += 1
        picks[choice.base] += 1
        out = run_session(index[choice.base][day], choice, equity, play)
        day_pnl = out.end_equity - equity
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        equity = out.end_equity
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        filled += int(out.filled)
        maker_exits += out.maker_exits
        flattens += out.flattens
        maker_pnl += out.maker_pnl
        flatten_pnl += out.flatten_pnl
    return Run(
        mode=mode,
        start=days[0] if days else start,
        end=days[-1] if days else end,
        pnl=equity - BOOK_EUR,
        end_equity=equity,
        max_dd=max_dd,
        days=len(days),
        selected=selected,
        filled=filled,
        maker_exits=maker_exits,
        flattens=flattens,
        maker_pnl=maker_pnl,
        flatten_pnl=flatten_pnl,
        largest_day=largest,
        picks=tuple(picks.most_common()),
    )
