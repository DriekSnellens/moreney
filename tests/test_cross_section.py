"""A later jump does not change the rank, and the top fifth is the high score."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from bot.research.cross_section.engine import (
    _clears,
    quintile_index,
    spearman,
    study,
)
from bot.research.week_book.engine import DAY_MS, START, make_coin

ORIGIN = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
N = 37


def test_spearman_tracks_the_order_and_the_fifth_is_two_of_ten() -> None:
    assert spearman(np.arange(5.0), np.arange(5.0)) == 1.0
    assert spearman(np.arange(5.0), np.arange(5.0)[::-1]) == -1.0
    names = [f"C{i:02d}" for i in range(10)]
    top, bottom = quintile_index(names, np.arange(10.0))
    assert [names[i] for i in top] == ["C09", "C08"]
    assert [names[i] for i in bottom] == ["C01", "C00"]


def _coin(base: str, close: np.ndarray, quote: float = 50_000.0):
    ts = ORIGIN + np.arange(len(close)) * DAY_MS
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close)
    low = np.minimum(open_, close)
    return make_coin(base, ts, open_, high, low, close, np.full(len(close), quote))


def _series(level: float, *, jump: float | None = None) -> np.ndarray:
    close = np.full(N, 100.0)
    close[22] = 103.0
    close[28:] = level
    if jump is not None:
        close[35:] = jump
    return close


def test_the_rank_uses_the_close_before_the_next_open() -> None:
    coins = []
    for i in range(10):
        level = 100.0 * (1.0 + i * 0.02)
        jump = level * 1.10 if i >= 8 else None
        coins.append(_coin(f"C{i:02d}", _series(level, jump=jump)))
    coins.append(_coin("JUMP", _series(99.0, jump=300.0)))
    coins.append(_coin("PEG", np.full(N, 100.0), quote=50_000_000.0))
    dates = ORIGIN + np.arange(N) * DAY_MS
    result = study(coins, dates, 10_000.0)
    assert result.coins == 11
    ret7 = next(row for row in result.rows if row.feature == "ret7" and row.side == "top")
    bottom = next(row for row in result.rows if row.feature == "ret7" and row.side == "bottom")
    assert ret7.names == 2
    assert ret7.gross == pytest.approx(0.10)
    assert ret7.corr > 0.0
    assert ret7.equity > START
    assert bottom.gross > 0.5
    flat = next(row for row in result.rows if row.feature == "size" and row.side == "top")
    assert flat.net < 0.0
    assert not _clears(19_000.0, {2024: 1.0, 2025: 1.0, 2026: 1.0}, 40, 18_000.0)
    assert _clears(30_000.0, {2024: 1.0, 2025: 1.0, 2026: 1.0}, 40, 25_000.0)
    assert not _clears(30_000.0, {2024: 1.0, 2025: -1.0, 2026: 1.0}, 40, 25_000.0)
