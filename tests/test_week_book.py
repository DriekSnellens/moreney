"""The weekly book buys the next open and does not look at that day's jump."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.capital_flow.engine import wet_net
from bot.research.week_book.engine import (
    DAY_MS,
    START,
    Rule,
    clears,
    make_coin,
    master_dates,
    run_rule,
)

ORIGIN = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
N = 37


def _coin(
    base: str,
    close: np.ndarray,
    *,
    low: np.ndarray | None = None,
    open_: np.ndarray | None = None,
    quote: float = 2_000_000.0,
):
    ts = ORIGIN + np.arange(len(close)) * DAY_MS
    if open_ is None:
        open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close)
    if low is None:
        low = np.minimum(open_, close)
    quotes = np.full(len(close), quote)
    return make_coin(base, ts, open_, high, low, close, quotes)


def _flat_then_level(level: float) -> np.ndarray:
    close = np.full(N, 100.0)
    close[22:28] = 105.0
    close[28:] = level
    return close


def test_a_flat_hold_pays_the_fee_and_the_higher_week_is_the_one_bought() -> None:
    leader = _coin("AAA", _flat_then_level(110.0))
    loser = _coin("BBB", _flat_then_level(105.0))
    crash = _flat_then_level(105.0)
    crash[29:] = 50.0
    loser = _coin("BBB", crash)
    dates = master_dates([leader, loser], min_names=1)
    result = run_rule(Rule("mom7"), [leader, loser], dates, {})
    assert result.trades == 1
    assert result.equity < START
    assert result.equity > 19_000.0


def test_the_entry_day_jump_is_not_a_signal_and_a_peg_stays_out() -> None:
    close = np.full(N, 100.0)
    close[22] = 110.0
    open_ = np.concatenate([[close[0]], close[:-1]])
    open_[29] = 200.0
    close[29] = 200.0
    coin = _coin("AAA", close, open_=open_)
    dates = master_dates([coin], min_names=1)
    result = run_rule(Rule("mom7"), [coin], dates, {})
    assert result.trades == 0
    assert result.equity == START
    peg = _coin("PEG", np.full(N, 100.0))
    peg_dates = master_dates([peg], min_names=1)
    held = run_rule(Rule("mom7"), [peg], peg_dates, {})
    assert held.trades == 0


def test_a_stop_fills_at_the_level_and_a_gap_fills_at_the_open() -> None:
    close = _flat_then_level(110.0)
    coin = _coin("AAA", close)
    coin.low[29] = 80.0
    dates = master_dates([coin], min_names=1)
    result = run_rule(Rule("mom7_stop", stop=0.15), [coin], dates, {})
    assert result.trades == 1
    assert result.equity == START * (1.0 + wet_net(110.0, 93.5, 2_000_000.0, notional=START))
    gap_close = _flat_then_level(110.0)
    gap_open = np.concatenate([[gap_close[0]], gap_close[:-1]])
    gap_open[30] = 50.0
    gap_low = np.minimum(gap_open, gap_close)
    gap_low[30] = 50.0
    gap = _coin("AAA", gap_close, low=gap_low, open_=gap_open)
    gap_dates = master_dates([gap], min_names=1)
    gapped = run_rule(Rule("mom7_stop", stop=0.15), [gap], gap_dates, {})
    assert gapped.equity == START * (1.0 + wet_net(110.0, 50.0, 2_000_000.0, notional=START))


def test_five_slots_leave_the_empty_ones_in_cash() -> None:
    one = _coin("AAA", _flat_then_level(110.0))
    two = _coin("BBB", _flat_then_level(108.0))
    dates = master_dates([one, two], min_names=1)
    result = run_rule(Rule("top5", n=5), [one, two], dates, {})
    drag = wet_net(110.0, 110.0, 2_000_000.0, notional=START / 5)
    other = wet_net(108.0, 108.0, 2_000_000.0, notional=START / 5)
    assert result.trades == 2
    assert result.equity == START * (1.0 + (drag + other) / 5)


def test_a_dip_in_an_uptrend_is_not_the_name_that_just_made_the_high() -> None:
    leader = np.full(N, 100.0)
    leader[22:28] = 110.0
    leader[28:] = 120.0
    dip = np.linspace(100.0, 130.0, 22)
    rest = np.linspace(130.0, 110.0, 8)
    hold = np.linspace(110.0, 160.0, N - 29)
    path = np.concatenate([dip, rest[1:], hold])
    assert len(path) == N
    dates = master_dates(
        [_coin("AAA", leader), _coin("BBB", path)],
        min_names=1,
    )
    coins = [_coin("AAA", leader), _coin("BBB", path)]
    bought = run_rule(Rule("dip", dip=True, side="low"), coins, dates, {})
    chased = run_rule(Rule("mom7"), coins, dates, {})
    assert bought.equity > START
    assert chased.equity < START


def test_hold_keeps_the_deepest_name_until_the_last_open() -> None:
    close = np.full(N, 100.0)
    close[22:29] = [110.0, 108.0, 106.0, 104.0, 102.0, 101.0, 100.0]
    close[29:] = 150.0
    other = np.full(N, 100.0)
    other[22] = 110.0
    deep = _coin("AAA", close, quote=5_000_000.0)
    thin = _coin("BBB", other, quote=1_500_000.0)
    dates = master_dates([deep, thin], min_names=1)
    result = run_rule(Rule("vasthouden", hold=True, deepest=True), [deep, thin], dates, {})
    assert result.trades == 1
    assert result.equity > START


def test_the_bar_needs_every_year() -> None:
    good = {
        "name": "mom7",
        "equity": 30_000.0,
        "dd": 0.2,
        "trades": 40,
        "wins": 20,
        "year_pnl": {2024: 100.0, 2025: 100.0, 2026: 100.0},
        "year_trades": {2024: 10, 2025: 10, 2026: 10},
    }
    from bot.research.week_book.engine import Result

    assert clears(Result(**good))
    thin = dict(good)
    thin["year_trades"] = {2024: 10, 2025: 10, 2026: 3}
    assert not clears(Result(**thin))
    down = dict(good)
    down["year_pnl"] = {2024: 100.0, 2025: -1.0, 2026: 100.0}
    assert not clears(Result(**down))
