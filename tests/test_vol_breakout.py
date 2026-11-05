"""A close through the prior channel is a breakout, and a down year does not clear."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.vol_breakout.engine import (
    DAY_MS,
    START,
    Row,
    Spec,
    broke_out,
    clears,
    grid,
    run,
    tape_of,
)
from bot.research.week_book.engine import make_coin


def test_the_grid_covers_obscure_and_liquid_breakouts() -> None:
    specs = grid()
    assert len(specs) == 35
    assert len({spec.key() for spec in specs}) == 35
    names = {spec.name for spec in specs}
    assert "obscure, uitbraak 20/10" in names
    assert "liquide en volatiel, uitbraak 20/10" in names
    assert "alles, uitbraak 20/10, marktwaarde omhoog" in names
    assert "obscure, uitbraak 20/10, positief nieuws" in names
    assert "liquide, uitbraak 20/10, positief nieuws" in names


def test_a_down_year_does_not_clear() -> None:
    row = Row(
        "x",
        "uitbraak",
        30_000.0,
        10_000.0,
        0.2,
        40,
        {2024: 1.0, 2025: -1.0, 2026: 1.0},
        {2024: 10, 2025: 10, 2026: 10},
    )
    assert not clears(row)
    assert row.end > START


def test_a_new_high_breaks_out_and_the_lower_channel_sells() -> None:
    start = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    n = 20
    ts = start + np.arange(n) * DAY_MS
    close = np.full(n, 100.0)
    close[10] = 120.0
    close[11:15] = 120.0
    close[15] = 50.0
    open_ = close.copy()
    open_[11] = 120.0
    open_[16] = 50.0
    high = np.maximum(open_, close)
    low = np.minimum(open_, close)
    quote = np.full(n, 2_000_000.0)
    tape = tape_of(make_coin("ALT", ts, open_, high, low, close, quote))
    assert broke_out(tape, 10, 5) > 0.0
    assert not np.isfinite(broke_out(tape, 9, 5))
    spec = Spec("proef", "uitbraak", "alles", 5, 3)
    result = run(
        [tape],
        spec,
        ts,
        start_ms=int(ts[0]),
        mcap_up=set(),
        mcap_down=set(),
        news_pos=set(),
        news_from=0,
    )
    assert result.trades == 1
    assert result.end < START
