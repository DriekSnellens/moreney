"""A 50% rise is counted from the next open, not from the signal day's high."""

from __future__ import annotations

import numpy as np

from bot.research.rise_50.engine import (
    DAY_MS,
    coin_rows,
    intraday_flags,
    limit_or_open,
)
from bot.research.small_peak.engine import BAR_MS, net_long

START = 1_704_067_200_000


def _panel(n: int = 40) -> tuple[np.ndarray, ...]:
    ts = START + np.arange(n) * DAY_MS
    open_ = np.full(n, 100.0)
    high = np.full(n, 103.0)
    close = np.where(np.arange(n) % 2 == 0, 100.0, 103.0)
    quote = np.full(n, 50_000.0)
    return ts, open_, high, close, quote


def test_limit_sells_at_the_level_and_a_gap_fills_at_the_open() -> None:
    open_ = np.asarray([100.0, 100.0, 120.0, 90.0])
    high = np.asarray([101.0, 160.0, 400.0, 90.0])
    fill, hit = limit_or_open(open_, high, 0, 3, 0.50)
    assert hit == 1.0
    assert fill == 150.0
    gap_open = np.asarray([100.0, 200.0, 90.0])
    gap_high = np.asarray([101.0, 210.0, 90.0])
    fill, hit = limit_or_open(gap_open, gap_high, 0, 2, 0.50)
    assert fill == 200.0
    assert hit == 1.0


def test_signal_day_high_is_not_the_rise_and_a_peg_is_left_out() -> None:
    ts, open_, high, close, quote = _panel()
    high[14] = 1_000.0
    high[15] = 160.0
    close[14] = 110.0
    book = coin_rows(
        "AAA",
        ts,
        open_,
        high,
        close,
        quote,
        buy={},
        peaks=set(),
        taker={},
        market={},
        has_15m=False,
    )
    row = np.flatnonzero(book["entry_ms"] == ts[15])
    assert len(row) == 1
    assert book["hit50"][row[0]] == 1.0
    assert book["up10"][row[0]] == 1.0
    assert book["net50"][row[0]] == net_long(100.0, 150.0)
    assert book["peak"][row[0]] != book["peak"][row[0]]
    flat_ts, flat_open, flat_high, _close, flat_quote = _panel()
    flat_close = np.full(len(flat_ts), 100.0)
    peg = coin_rows(
        "PEG",
        flat_ts,
        flat_open,
        flat_high,
        flat_close,
        flat_quote,
        buy={},
        peaks=set(),
        taker={},
        market={},
        has_15m=False,
    )
    assert len(peg["entry_ms"]) == 0


def test_a_confirmed_peak_is_marked_on_the_confirmation_day() -> None:
    n = 400
    ts = START + np.arange(n) * BAR_MS
    open_ = np.full(n, 100.0)
    close = np.full(n, 100.0)
    high = np.full(n, 100.2)
    low = np.full(n, 99.8)
    for i, px in enumerate(np.linspace(100.0, 103.0, 12)):
        bar = 129 + i
        open_[bar] = float(px)
        close[bar] = float(px)
        high[bar] = float(px)
        low[bar] = float(px) - 0.05
    high[140] = 103.0
    close[140] = 103.0
    open_[141] = 102.6
    high[141] = 102.7
    low[141] = 101.8
    close[141] = 101.97
    volume = np.full(n, 500.0)
    _shares, peaks = intraday_flags(ts, open_, high, low, close, volume)
    day = int(ts[141] - ts[141] % DAY_MS)
    assert day in peaks
    assert int(ts[140] - ts[140] % DAY_MS) == day or day in peaks
