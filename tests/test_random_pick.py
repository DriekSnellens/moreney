"""The moon has a phase, every selector returns a candidate, and the grid is locked."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.random_pick.engine import (
    SEEDS,
    Row,
    Spec,
    candidates_by_day,
    clears,
    grid,
    moon_phase,
    pick,
    run,
    seed_grid,
)
from bot.research.vol_harvest.engine import tape_of
from bot.research.week_book.engine import DAY_MS, make_coin


def test_the_grid_is_locked() -> None:
    specs = grid()
    assert len(specs) == 48
    assert len({spec.key() for spec in specs}) == 48
    seeds = seed_grid()
    assert len(seeds) == 8 * SEEDS
    assert len({spec.key() for spec in seeds}) == len(seeds)


def test_full_moon_sits_half_a_cycle_after_new_moon() -> None:
    new_moon = int(datetime(2000, 1, 6, 18, 14, tzinfo=UTC).timestamp() * 1000)
    assert abs(moon_phase(new_moon)) < 1e-9
    full = int(datetime(2000, 1, 21, 4, 40, tzinfo=UTC).timestamp() * 1000)
    # The mean synodic cycle sits within half a day of the true full moon.
    assert abs(moon_phase(full) - 0.5) < 0.02


def test_every_selector_returns_a_candidate_or_cash() -> None:
    names = ["ADA", "DOT", "MINA", "WLD", "ZRX"]
    stamp = int(datetime(2025, 3, 3, tzinfo=UTC).timestamp() * 1000)
    rng = np.random.default_rng(1)
    for spec in grid():
        chosen = pick(spec, stamp, names, rng)
        assert chosen is None or chosen in names
    letter = Spec("", "letter", "alles", "klok", "geen")
    assert pick(letter, stamp, names, None) == "MINA"
    assert pick(Spec("", "toeval", "alles", "klok", "geen", seed=0), stamp, names, rng) in names
    assert pick(letter, stamp, [], None) is None


def test_a_down_year_does_not_clear() -> None:
    row = Row(
        "x",
        "toeval",
        30_000.0,
        10_000.0,
        0.2,
        40,
        {2024: 1.0, 2025: -1.0, 2026: 1.0},
        {2024: 10, 2025: 10, 2026: 10},
    )
    assert not clears(row)


def test_the_clock_rotates_and_the_trail_sells() -> None:
    start = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    n = 40
    ts = start + np.arange(n) * DAY_MS
    close = np.full(n, 100.0)
    close[12:] = 80.0
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close)
    low = np.minimum(open_, close)
    quote = np.full(n, 5_000_000.0)
    tape = tape_of(make_coin("ALT", ts, open_, high, low, close, quote))
    pools = candidates_by_day([tape], ts, "alles")
    spec = Spec("proef", "tijd", "alles", "spoor", "geen")
    row = run([tape], spec, ts, start_ms=int(ts[0]), bench=None, candidates=pools)
    assert row.trades >= 1
    assert row.end < 20_000.0
