"""Buy an alt dip inside a wide range, sell the bounce or the break.

Bitvavo spot cannot short, so volatility is harvested from the long side:
buy after a completed drop, sell when the bounce pays the round trip, and
cut the trade when the range breaks. The signal is the close. The buy is
the next day's open. A resting-fee cell uses the Bitvavo maker, 0.15% a
side, and no impact. Every other cell pays the taker, 0.25% a side, plus
impact ``0.02 * sqrt(notional / quote)`` capped at 3%.

The band, the dip, the target, the stop and the hold are fixed below.
A cell clears the bar only with at least 30 round trips, an end above
€20,000, and a plus with at least eight round trips in each of 2024, 2025
and 2026. Nothing is armed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from bot.core.venue_fees import VENUE_MAKER_FEE, VENUE_TAKER_FEE
from bot.research.capital_flow.engine import is_peg
from bot.research.clip_exit_lab.engine import WET, FillModel, extra_slip

DAY_MS = 86_400_000
START = 20_000.0
QUOTE = 1_000_000.0
RANGE = 20
DIP_WIN = 10
SMA_N = 50
BTC_WIN = 30
PEG_BARS = 7
ADV_CAP = 0.02
BENCHMARK = "BTC"
YEARS = (2024, 2025, 2026)
MIN_TRADES = 30
MIN_YEAR_TRADES = 8
TAKER = float(VENUE_TAKER_FEE["bitvavo"])
MAKER = float(VENUE_MAKER_FEE["bitvavo"])
NO_IMPACT = FillModel(
    name="maker",
    delay_next_open=True,
    fee_side=MAKER,
    base_slip=0.0,
    impact_k=0.0,
    impact_cap=0.0,
)


@dataclass(frozen=True)
class Spec:
    name: str
    dip: float = 0.08
    target: float = 0.08
    stop: float = 0.08
    hold: int = 10
    n: int = 3
    vol_min: float = 0.15
    below_sma: bool = False
    range_bound: bool = False
    btc_down: bool = False
    fee: float = TAKER
    impact: bool = True

    def key(self) -> tuple:
        return (
            self.dip,
            self.target,
            self.stop,
            self.hold,
            self.n,
            self.vol_min,
            self.below_sma,
            self.range_bound,
            self.btc_down,
            self.fee,
            self.impact,
        )


def grid() -> list[Spec]:
    specs = [Spec("primaire band")]
    seen = {specs[0].key()}

    def add(spec: Spec) -> None:
        if spec.key() in seen:
            return
        seen.add(spec.key())
        specs.append(spec)

    add(Spec("dip 6%", dip=0.06))
    add(Spec("dip 12%", dip=0.12))
    add(Spec("doel 6%", target=0.06))
    add(Spec("doel 12%", target=0.12))
    add(Spec("stop 12%", stop=0.12))
    add(Spec("stop 16%", stop=0.16))
    add(Spec("vasthouden 5 dagen", hold=5))
    add(Spec("vasthouden 20 dagen", hold=20))
    add(Spec("één naam", n=1))
    add(Spec("band vanaf 8%", vol_min=0.08))
    add(Spec("band vanaf 25%", vol_min=0.25))
    add(Spec("onder het gemiddelde van 50", below_sma=True))
    add(Spec("binnen de eigen band", range_bound=True))
    add(Spec("alleen als bitcoin 30 dagen daalt", btc_down=True))
    add(Spec("primaire band, maker 0,15%", fee=MAKER, impact=False))
    return specs


@dataclass
class Coin:
    base: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote: np.ndarray
    idx: dict[int, int]


def make_coin(
    base: str,
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    quote: np.ndarray,
) -> Coin:
    order = np.argsort(ts, kind="mergesort")
    cols = [ts, open_, high, low, close, quote]
    ts, open_, high, low, close, quote = (col[order] for col in cols)
    keep = np.concatenate([[True], np.diff(ts) > 0])
    ts = ts[keep]
    return Coin(
        base=base,
        ts=ts,
        open=open_[keep],
        high=high[keep],
        low=low[keep],
        close=close[keep],
        quote=quote[keep],
        idx={int(stamp): i for i, stamp in enumerate(ts)},
    )


@dataclass
class Position:
    base: str
    qty: float
    entry_open: float
    stop: float
    target: float
    adv: float
    entry_di: int


@dataclass
class Result:
    name: str
    end: float
    pnl: float
    dd: float
    trades: int
    year_pnl: dict[int, float]
    year_trades: dict[int, int]
    invested: float
    btc_up: float
    btc_down: float
    stops: int = 0
    targets: int = 0
    clears: bool = False


def master_dates(coins: list[Coin], *, min_names: int = 20) -> np.ndarray:
    counts: Counter[int] = Counter()
    for coin in coins:
        counts.update(int(stamp) for stamp in coin.ts)
    days = sorted(stamp for stamp, n in counts.items() if n >= min_names)
    return np.asarray(days, dtype=np.int64)


def exit_fill(
    open_: float,
    high: float,
    low: float,
    stop: float,
    target: float,
    *,
    time_stop: bool,
) -> tuple[float, str] | None:
    """Market level of the sell. A stop prints before a target on the same bar."""
    if time_stop:
        return open_, "tijd"
    tol = max(abs(stop), abs(target), 1.0) * 1e-9
    if open_ <= stop + tol:
        return open_, "gat"
    if open_ >= target - tol:
        return open_, "gat"
    if low <= stop + tol:
        return stop, "stop"
    if high >= target - tol:
        return target, "doel"
    return None


def slot_notional(equity: float, quote: float, n: int) -> float:
    """One slot of the book, and at most 2% of that day's quote."""
    return min(equity / max(1, n), ADV_CAP * quote)


def _span(coin: Coin, i: int, bars: int) -> bool:
    if i < bars - 1:
        return False
    return int(coin.ts[i]) - int(coin.ts[i - (bars - 1)]) == (bars - 1) * DAY_MS


def _model(spec: Spec) -> FillModel:
    return WET if spec.impact else NO_IMPACT


def depth(coin: Coin, i: int, spec: Spec) -> float | None:
    """How far the close sits under its own 10-bar high. None when it stays out."""
    if coin.base == BENCHMARK or not _span(coin, i, RANGE):
        return None
    if float(coin.quote[i]) < QUOTE or float(coin.close[i]) <= 0.0:
        return None
    if not _span(coin, i, PEG_BARS) or is_peg(coin.close[i - (PEG_BARS - 1) : i + 1]):
        return None
    if not _span(coin, i, DIP_WIN):
        return None
    window = slice(i - (RANGE - 1), i + 1)
    hi = float(np.max(coin.high[window]))
    lo = float(np.min(coin.low[window]))
    close = float(coin.close[i])
    if hi <= lo:
        return None
    band = (hi - lo) / close
    if band < spec.vol_min:
        return None
    peak = float(np.max(coin.close[i - (DIP_WIN - 1) : i + 1]))
    if peak <= 0.0:
        return None
    drop = close / peak - 1.0
    if drop > -spec.dip:
        return None
    if spec.below_sma:
        if not _span(coin, i, SMA_N):
            return None
        sma = float(np.mean(coin.close[i - (SMA_N - 1) : i + 1]))
        if close >= sma:
            return None
    if spec.range_bound:
        earlier = float(coin.close[i - (RANGE - 1)])
        if earlier <= 0.0 or abs(close / earlier - 1.0) >= 0.5 * band:
            return None
    return drop


def _btc_down(btc: Coin | None, stamp: int) -> bool | None:
    if btc is None:
        return None
    i = btc.idx.get(int(stamp))
    if i is None or not _span(btc, i, BTC_WIN + 1):
        return None
    earlier = float(btc.close[i - BTC_WIN])
    if earlier <= 0.0:
        return None
    return float(btc.close[i]) / earlier - 1.0 <= 0.0


def _px(coin: Coin, stamp: int, field: str) -> float | None:
    i = coin.idx.get(int(stamp))
    if i is None:
        return None
    value = float(getattr(coin, field)[i])
    return value if value > 0.0 else None


def _buy(cash: float, notional: float, open_: float, adv: float, spec: Spec) -> tuple[float, float]:
    slip = extra_slip(notional, adv, _model(spec))
    px = open_ * (1.0 + slip)
    qty = notional / (px * (1.0 + spec.fee))
    return cash - notional, qty


def _sell(cash: float, qty: float, level: float, adv: float, spec: Spec) -> float:
    gross = qty * level
    slip = extra_slip(gross, adv, _model(spec))
    px = level * (1.0 - slip)
    return cash + qty * px * (1.0 - spec.fee)


def _mark(cash: float, positions: dict[str, Position], coins: dict[str, Coin], stamp: int) -> float:
    equity = cash
    for pos in positions.values():
        px = _px(coins[pos.base], stamp, "close")
        equity += pos.qty * (px if px is not None else pos.entry_open)
    return equity


def run(
    coins: list[Coin],
    spec: Spec,
    dates: np.ndarray,
    btc: Coin | None,
    *,
    start_ms: int,
    book: float = START,
) -> Result:
    by_base = {coin.base: coin for coin in coins}
    cash = float(book)
    positions: dict[str, Position] = {}
    marks: list[tuple[int, float]] = []
    deployed: list[float] = []
    year_trades = {year: 0 for year in YEARS}
    btc_up = 0.0
    btc_down = 0.0
    trades = 0
    stops = 0
    targets = 0

    def close_pos(pos: Position, level: float, stamp: int, reason: str) -> None:
        nonlocal cash, trades, stops, targets
        cash = _sell(cash, pos.qty, level, pos.adv, spec)
        trades += 1
        if reason == "doel" or (reason == "gat" and level >= pos.target):
            targets += 1
        elif reason == "stop" or reason == "gat":
            stops += 1
        year = _year(stamp)
        if year in year_trades:
            year_trades[year] += 1
        positions.pop(pos.base, None)

    last_di = len(dates) - 1
    for di, stamp in enumerate(int(day) for day in dates):
        if stamp < start_ms:
            continue
        previous = marks[-1][1] if marks else book
        for pos in list(positions.values()):
            if pos.entry_di == di:
                continue
            coin = by_base[pos.base]
            i = coin.idx.get(stamp)
            if i is None:
                continue
            fill = exit_fill(
                float(coin.open[i]),
                float(coin.high[i]),
                float(coin.low[i]),
                pos.stop,
                pos.target,
                time_stop=di - pos.entry_di >= spec.hold,
            )
            if fill is not None:
                close_pos(pos, fill[0], stamp, fill[1])
        if di > 0 and stamp - int(dates[di - 1]) == DAY_MS and int(dates[di - 1]) >= start_ms:
            signal = int(dates[di - 1])
            falling = _btc_down(btc, signal)
            if not spec.btc_down or falling is True:
                ranked: list[tuple[float, Coin, int]] = []
                for coin in coins:
                    i = coin.idx.get(signal)
                    if i is None or coin.base in positions:
                        continue
                    drop = depth(coin, i, spec)
                    if drop is not None:
                        ranked.append((drop, coin, i))
                ranked.sort(key=lambda item: item[0])
                equity = cash
                for held in positions.values():
                    px = _px(by_base[held.base], stamp, "open") or held.entry_open
                    equity += held.qty * px
                for _drop, coin, i in ranked:
                    if len(positions) >= spec.n:
                        break
                    open_ = float(coin.open[coin.idx[stamp]]) if stamp in coin.idx else 0.0
                    if open_ <= 0.0:
                        continue
                    notional = slot_notional(equity, float(coin.quote[i]), spec.n)
                    if notional < 1.0:
                        continue
                    if notional > cash:
                        if notional - cash > 0.01:
                            continue
                        notional = cash
                    cash, qty = _buy(cash, notional, open_, float(coin.quote[i]), spec)
                    positions[coin.base] = Position(
                        base=coin.base,
                        qty=qty,
                        entry_open=open_,
                        stop=open_ * (1.0 - spec.stop),
                        target=open_ * (1.0 + spec.target),
                        adv=float(coin.quote[i]),
                        entry_di=di,
                    )
                for pos in list(positions.values()):
                    if pos.entry_di != di:
                        continue
                    coin = by_base[pos.base]
                    i = coin.idx[stamp]
                    fill = exit_fill(
                        float(coin.open[i]),
                        float(coin.high[i]),
                        float(coin.low[i]),
                        pos.stop,
                        pos.target,
                        time_stop=False,
                    )
                    if fill is not None:
                        close_pos(pos, fill[0], stamp, fill[1])
        if di == last_di:
            for pos in list(positions.values()):
                coin = by_base[pos.base]
                level = _px(coin, stamp, "close") or pos.entry_open
                close_pos(pos, level, stamp, "einde")
        mark = _mark(cash, positions, by_base, stamp)
        delta = mark - previous
        regime = _btc_down(btc, stamp)
        if regime is True:
            btc_down += delta
        elif regime is False:
            btc_up += delta
        held_value = mark - cash
        if mark > 0.0:
            deployed.append(max(0.0, held_value) / mark)
        marks.append((stamp, mark))

    end = marks[-1][1] if marks else book
    year_end: dict[int, float] = {}
    for stamp, equity in marks:
        year_end[_year(stamp)] = equity
    year_pnl: dict[int, float] = {}
    prev = book
    for year in sorted(year_end):
        year_pnl[year] = year_end[year] - prev
        prev = year_end[year]
    peak = book
    worst = 0.0
    for _stamp, equity in marks:
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
    invested = float(np.mean(deployed)) if deployed else 0.0
    return Result(
        spec.name,
        end,
        end - book,
        worst,
        trades,
        year_pnl,
        year_trades,
        invested,
        btc_up,
        btc_down,
        stops,
        targets,
    )


def clears(row: Result) -> bool:
    if row.trades < MIN_TRADES or row.end <= START:
        return False
    return all(
        row.year_pnl.get(year, 0.0) > 0.0 and row.year_trades.get(year, 0) >= MIN_YEAR_TRADES
        for year in YEARS
    )


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year
