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
SMA_WINDOW = 20
CLIP = 0.005
DOUBLE_BREAK = 0.005
SCALE_FIRST = 0.02
SHALLOW_RUNG = 0.35
SCRATCH_AFTER = 64
VWAP_WARMUP = 4
VWAP_BAND = 0.01
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
    "sma_bounce",
    "sma_reclaim",
    "sma_prior",
    "quiet5",
    "clip",
    "double5",
    "scale",
    "shallow_rung",
    "scratch",
    "vwap",
    "carry",
    "mid_exit",
    "second_day",
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
    volumes: tuple[float, ...] = ()

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


_INDEX: dict[tuple[int, int, int, int], tuple[list[Session], dict[str, Session]]] = {}


def _index(sessions: Sequence[Session]) -> tuple[list[Session], dict[str, Session]]:
    """Usable sessions, sorted, plus every session by day.

    The key includes the first and last session, not only the list id.
    A freed list can be recycled under the same id with different rows.
    """
    if not sessions:
        return [], {}
    stamp = (id(sessions), len(sessions), id(sessions[0]), id(sessions[-1]))
    hit = _INDEX.get(stamp)
    if hit is None:
        usable = [row for row in sessions if _usable(row)]
        usable.sort(key=lambda row: row.day)
        hit = (usable, {row.day: row for row in sessions})
        _INDEX[stamp] = hit
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
                volumes=tuple(float(bar[5]) for bar in bars),
            )
        )
    return out


def choose_name(
    by_base: Mapping[str, Sequence[Session]],
    day: str,
    equity: float,
    universe: Sequence[str],
    rank: str = "chop",
    above_sma: bool = False,
    lower_low: bool = False,
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
        if above_sma:
            trail = usable[:cut][-SMA_WINDOW:]
            if len(trail) < SMA_WINDOW:
                continue
            sma = sum(row.close for row in trail) / SMA_WINDOW
            if prior[-1].close <= sma:
                continue
        if lower_low and not prior[-1].low < prior[-2].low:
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


def _run_grid(
    session: Session,
    choice: Choice,
    equity: float,
    mode: str,
    fracs: tuple[float, ...] = RUNG_FRACS,
) -> DayOut:
    buys = tuple(choice.mid * (1.0 - frac * choice.range_frac) for frac in fracs)
    sells = tuple(choice.mid * (1.0 + frac * choice.range_frac) for frac in fracs)
    active = [
        i
        for i, buy in enumerate(buys)
        if 0 < buy < session.open and sells[i] > buy
    ]
    # Half the book per rung. A one-rung cell keeps that size.
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
    quiet: bool = False,
    scratch_after: int | None = None,
) -> DayOut:
    """Bid 1% above the low already printed. Sell ``target`` above the fill."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    buy_px_fill = 0.0
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
            wide = False
            if quiet and session.open > 0:
                printed_high = max(bar[1] for bar in session.bars[:bi])
                wide = (printed_high - printed_low) / session.open > choice.range_frac
            blocked = wide or (sell_at_open and buy_px * (1.0 + ROOM) >= session.open)
            if printed_low > 0 and not blocked and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = session.open if sell_at_open else buy_px * (1.0 + target)
                    buy_px_fill = buy_px
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        elif qty > 0 and bi > bought_on:
            working = sell_px
            if scratch_after is not None and bi >= scratch_after:
                working = buy_px_fill
            if high >= working:
                cash, proceeds = _sell_maker(cash, qty, working)
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


def _run_clip(session: Session, choice: Choice, equity: float) -> DayOut:
    """One buy 0.5% under the open, sold 0.5% above the fill."""
    buy_px = session.open * (1.0 - CLIP)
    return _run_limits(session, choice, equity, buy_px, buy_px * (1.0 + CLIP))


def _run_prior(session: Session, choice: Choice, equity: float) -> DayOut:
    """Buy halfway down yesterday's range. Sell a return to yesterday's close."""
    buy_px = choice.mid * (1.0 - PRIOR_FRAC * choice.range_frac)
    return _run_limits(session, choice, equity, buy_px, choice.mid)


def _run_mid_exit(session: Session, choice: Choice, equity: float) -> DayOut:
    """Buy the shallow rung. Sell the prior close, not the upper rung."""
    buy_px = choice.mid * (1.0 - SHALLOW_RUNG * choice.range_frac)
    return _run_limits(session, choice, equity, buy_px, choice.mid, equity / 2.0)


def _run_limits(
    session: Session,
    choice: Choice,
    equity: float,
    buy_px: float,
    sell_px: float,
    notional: float | None = None,
) -> DayOut:
    size = equity if notional is None else notional
    if size <= 0 or not (0 < buy_px < session.open and sell_px >= buy_px * (1.0 + ROOM)):
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
            cash, qty, cost = _buy_maker(cash, size, buy_px)
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


def _run_vwap(session: Session, choice: Choice, equity: float) -> DayOut:
    """Buy 1% under the VWAP of completed bars. Sell that frozen VWAP."""
    if len(session.volumes) != len(session.bars):
        return DayOut(equity, False, 0.0, 0.0, 0, 0)
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
    weighted = 0.0
    volume = 0.0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        elif qty == 0 and not entered and bi >= VWAP_WARMUP and volume > 0:
            vwap = weighted / volume
            buy_px = vwap * (1.0 - VWAP_BAND)
            if (
                buy_px > 0
                and buy_px < open_
                and low <= buy_px
                and vwap >= buy_px * (1.0 + ROOM)
            ):
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    sell_px = vwap
                    bought_on = bi
                    entered = True
                else:
                    qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
        size = session.volumes[bi]
        if size > 0:
            typical = (high + low + close) / 3.0
            weighted += typical * size
            volume += size
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_double(session: Session, choice: Choice, equity: float) -> DayOut:
    """Buy the second test of one low. A break of more than 0.5% starts over."""
    cash = equity
    qty = 0.0
    cost = 0.0
    sell_px = 0.0
    bought_on = -1
    entered = False
    ref: float | None = None
    tests = 0
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if qty > 0 and bi > bought_on and high >= sell_px:
            cash, proceeds = _sell_maker(cash, qty, sell_px)
            maker_pnl += proceeds - cost
            maker_exits += 1
            qty = 0.0
        elif qty == 0 and not entered and ref is not None and ref > 0:
            buy_px = ref * (1.0 + CUSHION)
            if low < ref * (1.0 - DOUBLE_BREAK):
                ref = low
                tests = 0
            elif low <= buy_px:
                if tests < 1:
                    tests = 1
                elif buy_px < open_:
                    cash, qty, cost = _buy_maker(cash, equity, buy_px)
                    if qty > 0:
                        sell_px = buy_px * (1.0 + BOUNCE_5)
                        bought_on = bi
                        entered = True
                    else:
                        qty = 0.0
        if bi == last and qty > 0:
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            qty = 0.0
        if ref is None and low > 0:
            ref = low
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def _run_scale(session: Session, choice: Choice, equity: float) -> DayOut:
    """Bounce entry. Sell half at 2% and the rest at 5%."""
    cash = equity
    lots: list[tuple[float, float, float]] = []
    bought_on = -1
    entered = False
    maker_pnl = 0.0
    flatten_pnl = 0.0
    maker_exits = 0
    flattens = 0
    last = len(session.bars) - 1
    for bi, (open_, high, low, close) in enumerate(session.bars):
        if lots and bi > bought_on:
            kept: list[tuple[float, float, float]] = []
            for lot_qty, lot_cost, lot_px in lots:
                if high >= lot_px:
                    cash, proceeds = _sell_maker(cash, lot_qty, lot_px)
                    maker_pnl += proceeds - lot_cost
                    maker_exits += 1
                else:
                    kept.append((lot_qty, lot_cost, lot_px))
            lots = kept
        if not lots and not entered and bi >= 1:
            printed_low = min(bar[2] for bar in session.bars[:bi])
            buy_px = printed_low * (1.0 + CUSHION)
            if printed_low > 0 and buy_px < open_ and low <= buy_px:
                cash, qty, cost = _buy_maker(cash, equity, buy_px)
                if qty > 0:
                    half_qty = qty / 2.0
                    half_cost = cost / 2.0
                    lots = [
                        (half_qty, half_cost, buy_px * (1.0 + SCALE_FIRST)),
                        (half_qty, half_cost, buy_px * (1.0 + BOUNCE_5)),
                    ]
                    bought_on = bi
                    entered = True
        if bi == last and lots:
            qty = sum(lot[0] for lot in lots)
            cost = sum(lot[1] for lot in lots)
            cash, proceeds, _px = _flatten(cash, qty, close, choice.adv)
            flatten_pnl += proceeds - cost
            flattens += 1
            lots = []
    return DayOut(cash, maker_exits + flattens > 0, maker_pnl, flatten_pnl, maker_exits, flattens)


def run_session(session: Session, choice: Choice, equity: float, mode: str) -> DayOut:
    if mode in {"strict", "path"}:
        return _run_grid(session, choice, equity, mode)
    if mode == "shallow_rung":
        return _run_grid(session, choice, equity, "strict", (SHALLOW_RUNG,))
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
    if mode == "quiet5":
        return _run_bounce(session, choice, equity, BOUNCE_5, quiet=True)
    if mode == "clip":
        return _run_clip(session, choice, equity)
    if mode == "double5":
        return _run_double(session, choice, equity)
    if mode == "scale":
        return _run_scale(session, choice, equity)
    if mode == "scratch":
        return _run_bounce(session, choice, equity, BOUNCE_5, scratch_after=SCRATCH_AFTER)
    if mode == "vwap":
        return _run_vwap(session, choice, equity)
    if mode == "mid_exit":
        return _run_mid_exit(session, choice, equity)
    if mode == "second_day":
        return _run_bounce(session, choice, equity, BOUNCE_5)
    if mode == "hold":
        return _run_hold(session, choice, equity)
    if mode == "oracle":
        return _run_oracle(session, equity)
    raise ValueError(mode)


@dataclass
class _Open:
    qty: float
    notional: float
    sell_px: float


@dataclass
class _Bag:
    base: str
    adv: float
    lots: list[_Open]
    last_close: float


def _mark_bag(cash: float, bag: _Bag | None) -> float:
    if bag is None or not bag.lots:
        return cash
    qty = sum(lot.qty for lot in bag.lots)
    proceeds, _px = _taker_exit(qty, bag.last_close, bag.adv)
    return cash + proceeds


def _enter_carry(
    session: Session, choice: Choice, equity: float
) -> tuple[float, _Bag | None, float, int]:
    """Strict grid with no close-out. Leftover lots stay open."""
    buys = tuple(choice.mid * (1.0 - frac * choice.range_frac) for frac in RUNG_FRACS)
    sells = tuple(choice.mid * (1.0 + frac * choice.range_frac) for frac in RUNG_FRACS)
    active = [
        i for i, buy in enumerate(buys) if 0 < buy < session.open and sells[i] > buy
    ]
    notional = equity / 2.0
    cash = equity
    held: dict[int, _Lot | None] = {i: None for i in active}
    maker_pnl = 0.0
    maker_exits = 0
    for bi, (_open, high, low, _close) in enumerate(session.bars):
        for i in active:
            lot = held[i]
            if lot is None or lot.bar == bi or high < sells[i]:
                continue
            cash, proceeds = _sell_maker(cash, lot.qty, sells[i])
            maker_pnl += proceeds - lot.notional
            maker_exits += 1
            held[i] = None
        for i in active:
            if held[i] is not None or low > buys[i]:
                continue
            cash, qty, spent = _buy_maker(cash, notional, buys[i])
            if qty > 0:
                held[i] = _Lot(bi, qty, spent)
    lots = [
        _Open(lot.qty, lot.notional, sells[i])
        for i, lot in held.items()
        if lot is not None
    ]
    if not lots:
        return cash, None, maker_pnl, maker_exits
    return cash, _Bag(choice.base, choice.adv, lots, session.close), maker_pnl, maker_exits


def _advance_carry(
    session: Session, bag: _Bag, cash: float
) -> tuple[float, _Bag | None, float, int]:
    """Work paired sells. A fill from an earlier session may sell on any bar."""
    if not session.bars:
        return cash, bag, 0.0, 0
    maker_pnl = 0.0
    maker_exits = 0
    lots = list(bag.lots)
    last_close = bag.last_close
    for _open, high, _low, close in session.bars:
        last_close = close
        kept: list[_Open] = []
        for lot in lots:
            if high >= lot.sell_px:
                cash, proceeds = _sell_maker(cash, lot.qty, lot.sell_px)
                maker_pnl += proceeds - lot.notional
                maker_exits += 1
            else:
                kept.append(lot)
        lots = kept
    if not lots:
        return cash, None, maker_pnl, maker_exits
    return cash, _Bag(bag.base, bag.adv, lots, last_close), maker_pnl, maker_exits


def _simulate_carry(
    by_base: Mapping[str, Sequence[Session]],
    *,
    universe: Sequence[str],
    start: str,
    end: str,
) -> Run:
    """Strict rungs. Paired sells stay working until they fill or the window ends."""
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
    bag: _Bag | None = None
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
        mark = _mark_bag(cash, bag)
        traded = False
        if bag is not None:
            session = index.get(bag.base, {}).get(day)
            if session is not None:
                cash, bag, made, exits = _advance_carry(session, bag, cash)
                maker_pnl += made
                maker_exits += exits
                traded = exits > 0
        else:
            choice = choose_name(by_base, day, cash, universe)
            if choice is not None:
                selected += 1
                picks[choice.base] += 1
                cash, bag, made, exits = _enter_carry(
                    index[choice.base][day], choice, cash
                )
                maker_pnl += made
                maker_exits += exits
                traded = exits > 0 or bag is not None
        if traded:
            filled += 1
        end_mark = _mark_bag(cash, bag)
        day_pnl = end_mark - mark
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        peak = max(peak, end_mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - end_mark) / peak)
    if bag is not None and bag.lots:
        qty = sum(lot.qty for lot in bag.lots)
        cost = sum(lot.notional for lot in bag.lots)
        proceeds, _px = _taker_exit(qty, bag.last_close, bag.adv)
        cash += proceeds
        flatten_pnl += proceeds - cost
        flattens += 1
    return Run(
        mode="carry",
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
    if mode == "carry":
        return _simulate_carry(by_base, universe=universe, start=start, end=end)
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
    rank = "liquid" if mode in {"liquid5", "clip"} else "chop"
    above = mode in {"sma_bounce", "sma_reclaim", "sma_prior"}
    play = {
        "liquid5": "bounce5",
        "sma_bounce": "bounce5",
        "sma_reclaim": "reclaim",
        "sma_prior": "prior",
        "second_day": "bounce5",
    }.get(mode, mode)
    for day in days:
        choice = choose_name(
            by_base,
            day,
            equity,
            universe,
            rank,
            above_sma=above,
            lower_low=mode == "second_day",
        )
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
