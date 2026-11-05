"""One book, rebalanced every seven days, on liquid Bitvavo names.

The clock is a day on which at least 20 names printed a daily bar. The
signal is that day's close. The buy is the next day's open. The sell is
the open seven days after the buy, unless a stop is hit first. A bar that
opens through the stop fills at the open. The next signal is seven days
later, so the holds do not overlap.

A name needs €1,000,000 of quote on the signal day and a seven-day close
band wider than 0.5%, so a peg never enters. The book starts at €20,000
and compounds. Each fill pays the Bitvavo taker and impact
``0.02 * sqrt(notional / quote)`` capped at 3% on both sides. The quote
is the signal day's turnover.

Rules, locked before the tape:

* mom7 — the highest 7-day return, and only when it is positive
* mom28 — the same with a 28-day return
* mom7_vol — mom7 among names whose quote is at least twice their own
  prior week
* luid_mom7 — mom7 only when all-name quote is at least twice its prior
  week, otherwise cash
* luid_diep — on a loud week, the name with the highest quote
* rev7 — the lowest 7-day return, and only when it is negative
* mom7_stop — mom7 with a stop 15% under the entry
* top5 — the five highest positive 7-day returns, equal weight, empty
  slots stay cash
* dip — the lowest 7-day return among names that are still up over 28 days
  and down over 7
* mom7_markt — mom7 only when the highest-quote name itself is up over 7
  days
* vasthouden — buy the highest-quote name once and hold to the last exit

A rule clears the bar only when it has at least 30 round trips, ends above
€20,000, and each of 2024, 2025 and 2026 has at least eight round trips and
a positive euro result. Nothing is armed.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import is_peg, wet_net
from bot.research.clip_exit_lab.engine import WET, extra_slip
from bot.research.small_peak.engine import FEE

DAY_MS = 86_400_000
START = 20_000.0
QUOTE = 1_000_000.0
MIN_NAMES = 20
HOLD = 7
WARM = 28
STOP = 0.15
VOL_MIN = 2.0
LOUD = 2.0
TOP_N = 5
YEARS = (2024, 2025, 2026)
MIN_TRADES = 30
MIN_YEAR_TRADES = 8


@dataclass(frozen=True)
class Rule:
    name: str
    lookback: int = HOLD
    side: str = "high"
    n: int = 1
    stop: float = 0.0
    vol_min: float = 0.0
    loud: bool = False
    deepest: bool = False
    dip: bool = False
    market_up: bool = False
    hold: bool = False


def locked_rules() -> tuple[Rule, ...]:
    return (
        Rule("mom7"),
        Rule("mom28", lookback=WARM),
        Rule("mom7_vol", vol_min=VOL_MIN),
        Rule("luid_mom7", loud=True),
        Rule("luid_diep", loud=True, deepest=True),
        Rule("rev7", side="low"),
        Rule("mom7_stop", stop=STOP),
        Rule("top5", n=TOP_N),
        Rule("dip", dip=True, side="low"),
        Rule("mom7_markt", market_up=True),
        Rule("vasthouden", hold=True, deepest=True),
    )


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


@dataclass
class Name:
    base: str
    ret: float
    quote: float
    index: int


@dataclass
class Result:
    name: str
    equity: float
    dd: float
    trades: int
    wins: int
    year_pnl: dict[int, float] = field(default_factory=dict)
    year_trades: dict[int, int] = field(default_factory=dict)
    year_pct: dict[int, float] = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return self.equity - START


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


def master_dates(coins: Sequence[Coin], *, min_names: int = MIN_NAMES) -> np.ndarray:
    counts: Counter[int] = Counter()
    for coin in coins:
        counts.update(int(stamp) for stamp in coin.ts)
    days = sorted(stamp for stamp, n in counts.items() if n >= min_names)
    return np.asarray(days, dtype=np.int64)


def _year(stamp: int) -> int:
    return datetime.fromtimestamp(stamp / 1000, UTC).year


def _at(coin: Coin, stamp: int) -> int | None:
    return coin.idx.get(int(stamp))


def _usable(coin: Coin, i: int, lookback: int) -> bool:
    if i < lookback or i < HOLD - 1:
        return False
    if int(coin.ts[i]) - int(coin.ts[i - lookback]) != lookback * DAY_MS:
        return False
    if float(coin.quote[i]) < QUOTE or float(coin.close[i]) <= 0.0:
        return False
    if float(coin.close[i - lookback]) <= 0.0:
        return False
    return not is_peg(coin.close[i - (HOLD - 1) : i + 1])


def _return(coin: Coin, i: int, lookback: int) -> float:
    return float(coin.close[i]) / float(coin.close[i - lookback]) - 1.0


def _volume_ratio(coin: Coin, i: int) -> float:
    if i < HOLD:
        return math.nan
    if int(coin.ts[i]) - int(coin.ts[i - HOLD]) != HOLD * DAY_MS:
        return math.nan
    baseline = float(np.mean(coin.quote[i - HOLD : i]))
    if baseline <= 0.0:
        return math.nan
    return float(coin.quote[i]) / baseline


def _names(coins: Sequence[Coin], stamp: int, lookback: int) -> list[Name]:
    found: list[Name] = []
    for coin in coins:
        i = _at(coin, stamp)
        if i is None or not _usable(coin, i, lookback):
            continue
        found.append(Name(coin.base, _return(coin, i, lookback), float(coin.quote[i]), i))
    return found


def _path(coin: Coin, dates: np.ndarray, start: int, stop: int) -> list[int] | None:
    idxs: list[int] = []
    for stamp in dates[start : stop + 1]:
        i = _at(coin, int(stamp))
        if i is None:
            return None
        idxs.append(i)
    return idxs


def _exit_px(coin: Coin, idxs: Sequence[int], entry_px: float, stop: float) -> float:
    if stop <= 0.0:
        return float(coin.open[idxs[-1]])
    level = entry_px * (1.0 - stop)
    for i in idxs[:-1]:
        if float(coin.low[i]) > level:
            continue
        opened = float(coin.open[i])
        return opened if opened <= level else level
    return float(coin.open[idxs[-1]])


def _leg(
    coin: Coin,
    dates: np.ndarray,
    signal: int,
    adv: float,
    notional: float,
    stop: float,
) -> float | None:
    entry = signal + 1
    exit_ = signal + 1 + HOLD
    if int(dates[exit_]) - int(dates[signal]) != (HOLD + 1) * DAY_MS:
        return None
    idxs = _path(coin, dates, entry, exit_)
    if idxs is None:
        return None
    entry_px = float(coin.open[idxs[0]])
    if entry_px <= 0.0:
        return None
    fill = _exit_px(coin, idxs, entry_px, stop)
    if fill <= 0.0:
        return None
    return wet_net(entry_px, fill, adv, notional=notional)


def _select(
    rule: Rule,
    coins: Sequence[Coin],
    by_base: Mapping[str, Coin],
    stamp: int,
    market: Mapping[int, float],
) -> list[tuple[Coin, float]]:
    if rule.loud and not (market.get(int(stamp), math.nan) >= LOUD):
        return []
    if rule.market_up:
        deep = _names(coins, stamp, HOLD)
        if not deep:
            return []
        leader = max(deep, key=lambda name: (name.quote, name.base))
        if leader.ret <= 0.0:
            return []
    pool = _names(coins, stamp, rule.lookback)
    if rule.dip:
        pool = [name for name in pool if name.ret < 0.0 and _up_month(by_base[name.base], stamp)]
    if rule.vol_min > 0.0:
        pool = [
            name for name in pool if _volume_ratio(by_base[name.base], name.index) >= rule.vol_min
        ]
    if rule.deepest:
        if not pool:
            return []
        leader = max(pool, key=lambda name: (name.quote, name.base))
        return [(by_base[leader.base], leader.quote)]
    if rule.side == "high":
        pool = [name for name in pool if name.ret > 0.0]
        pool.sort(key=lambda name: (-name.ret, name.base))
    else:
        if not rule.dip:
            pool = [name for name in pool if name.ret < 0.0]
        pool.sort(key=lambda name: (name.ret, name.base))
    return [(by_base[name.base], name.quote) for name in pool[: rule.n]]


def _up_month(coin: Coin, stamp: int) -> bool:
    i = _at(coin, stamp)
    if i is None or not _usable(coin, i, WARM):
        return False
    return _return(coin, i, WARM) > 0.0


def _finish(
    name: str,
    equity: float,
    dd: float,
    wins: int,
    year_pnl: dict[int, float],
    year_trades: dict[int, int],
    year_start: dict[int, float],
    year_end: dict[int, float],
) -> Result:
    pct = {
        year: (year_end[year] / year_start[year] - 1.0)
        for year in YEARS
        if year in year_start and year_start[year] > 0.0 and year in year_end
    }
    return Result(
        name=name,
        equity=equity,
        dd=dd,
        trades=sum(year_trades.values()),
        wins=wins,
        year_pnl=year_pnl,
        year_trades=year_trades,
        year_pct=pct,
    )


def run_rule(
    rule: Rule,
    coins: Sequence[Coin],
    dates: np.ndarray,
    market: Mapping[int, float],
) -> Result:
    if rule.hold:
        return _hold_deepest(rule, coins, dates)
    by_base = {coin.base: coin for coin in coins}
    equity = START
    peak = START
    worst = 0.0
    wins = 0
    year_pnl = {year: 0.0 for year in YEARS}
    year_trades = {year: 0 for year in YEARS}
    year_start: dict[int, float] = {}
    year_end: dict[int, float] = {}
    signal = WARM
    while signal + 1 + HOLD < len(dates):
        picks = _select(rule, coins, by_base, int(dates[signal]), market)
        slot = equity / rule.n
        rets: list[float] = []
        for coin, adv in picks:
            ret = _leg(coin, dates, signal, adv, slot, rule.stop)
            if ret is None:
                rets.append(0.0)
                continue
            rets.append(ret)
            if ret > 0.0:
                wins += 1
            year = _year(int(dates[signal + 1 + HOLD]))
            year_trades[year] = year_trades.get(year, 0) + 1
        while len(rets) < rule.n:
            rets.append(0.0)
        before = equity
        equity *= 1.0 + sum(rets) / rule.n
        if equity > peak:
            peak = equity
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
        exit_year = _year(int(dates[signal + 1 + HOLD]))
        if exit_year not in year_start:
            year_start[exit_year] = before
        year_end[exit_year] = equity
        year_pnl[exit_year] = year_pnl.get(exit_year, 0.0) + (equity - before)
        signal += HOLD
    return _finish(rule.name, equity, worst, wins, year_pnl, year_trades, year_start, year_end)


def _last_signal(n_dates: int) -> int | None:
    """The last clock index ``run_rule`` actually trades."""
    last = None
    signal = WARM
    while signal + 1 + HOLD < n_dates:
        last = signal
        signal += HOLD
    return last


def _blank(name: str) -> Result:
    return _finish(name, START, 0.0, 0, {year: 0.0 for year in YEARS}, {}, {}, {})


def _hold_deepest(rule: Rule, coins: Sequence[Coin], dates: np.ndarray) -> Result:
    """One round trip. Entry cost sits in the first mark, exit cost in the last."""
    if len(dates) <= WARM + 1 + HOLD:
        return _blank(rule.name)
    pool = _names(coins, int(dates[WARM]), HOLD)
    if not pool:
        return _blank(rule.name)
    leader = max(pool, key=lambda name: (name.quote, name.base))
    coin = {row.base: row for row in coins}[leader.base]
    entry = _at(coin, int(dates[WARM + 1]))
    last = _last_signal(len(dates))
    if last is None:
        return _blank(rule.name)
    exit_ = _at(coin, int(dates[last + 1 + HOLD]))
    if entry is None or exit_ is None or exit_ <= entry:
        return _blank(rule.name)
    entry_px = float(coin.open[entry])
    exit_px = float(coin.open[exit_])
    if entry_px <= 0.0 or exit_px <= 0.0:
        return _blank(rule.name)
    slip = extra_slip(START, leader.quote, WET)
    entry_fill = entry_px * (1.0 + FEE) * (1.0 + slip)
    exit_fill = exit_px * (1.0 - FEE) * (1.0 - slip)
    shares = START / entry_fill
    year_pnl = {year: 0.0 for year in YEARS}
    year_start: dict[int, float] = {}
    year_end: dict[int, float] = {}
    current = _year(int(coin.ts[entry]))
    opened_at = START
    year_start[current] = START
    last_mtm = START
    peak = START
    worst = 0.0
    for i in range(entry, exit_ + 1):
        mtm = shares * (exit_fill if i == exit_ else float(coin.close[i]))
        year = _year(int(coin.ts[i]))
        if year != current:
            year_pnl[current] = last_mtm - opened_at
            year_end[current] = last_mtm
            current = year
            opened_at = last_mtm
            year_start[current] = last_mtm
        last_mtm = mtm
        peak = max(peak, mtm)
        if peak > 0.0:
            worst = max(worst, 1.0 - mtm / peak)
    year_pnl[current] = last_mtm - opened_at
    year_end[current] = last_mtm
    year_trades = {year: 0 for year in YEARS}
    year_trades[current] = 1
    return _finish(
        rule.name,
        last_mtm,
        worst,
        1 if last_mtm > START else 0,
        year_pnl,
        year_trades,
        year_start,
        year_end,
    )


def clears(result: Result) -> bool:
    """The bar locked in the module doc. A thin or one-year book does not pass."""
    if result.trades < MIN_TRADES or result.equity <= START:
        return False
    for year in YEARS:
        if result.year_trades.get(year, 0) < MIN_YEAR_TRADES:
            return False
        if result.year_pnl.get(year, 0.0) <= 0.0:
            return False
    return True
