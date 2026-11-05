"""A ladder buys the dip rung and sells both on the rally; a down year does not clear."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.vol_harvest.engine import (
    DAY_MS,
    START,
    Row,
    Spec,
    beta,
    clears,
    grid,
    run_follow,
    run_ladder,
    tape_of,
)
from bot.research.week_book.engine import make_coin


def _tape(base: str, close: np.ndarray, quote: float, start: int):
    n = len(close)
    ts = start + np.arange(n) * DAY_MS
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close)
    low = np.minimum(open_, close)
    return tape_of(make_coin(base, ts, open_, high, low, close, np.full(n, quote)))


def test_the_grid_is_locked_at_36_cells() -> None:
    specs = grid()
    assert len(specs) == 36
    assert len({spec.key() for spec in specs}) == 36
    names = {spec.name for spec in specs}
    assert "liquide en volatiel, ladder 10%, zonder poort" in names
    assert "obscure en volatiel, ladder 5%, marktwaarde omhoog" in names
    assert "liquide, volgdag na 3%, 1 sessies" in names
    assert "obscure, volgdag na 5%, 7 sessies" in names


def test_neighbours_sit_one_knob_away() -> None:
    from bot.research.vol_harvest.__main__ import _neighbours

    specs = grid()
    spec = next(s for s in specs if s.name == "liquide en volatiel, ladder 15%, zonder poort")
    around = _neighbours(spec, specs)
    assert len(around) == 5
    assert all(other.universe == spec.universe for other in around)
    follow = next(s for s in specs if s.name == "liquide, volgdag na 5%, 7 sessies")
    assert len(_neighbours(follow, specs)) == 3


def test_a_down_year_does_not_clear() -> None:
    row = Row(
        "x",
        "ladder",
        30_000.0,
        10_000.0,
        0.2,
        40,
        {2024: 1.0, 2025: -1.0, 2026: 1.0},
        {2024: 10, 2025: 10, 2026: 10},
    )
    assert not clears(row)


def test_a_ladder_adds_a_rung_on_the_dip_and_sells_both_on_the_rally() -> None:
    start = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    close = np.full(60, 100.0)
    close[30:40] = 85.0
    close[40:] = 120.0
    tape = _tape("ALT", close, 5_000_000.0, start)
    spec = Spec("proef", "ladder", "alles", step=0.10, gate="geen")
    row = run_ladder([tape], spec, tape.ts, start_ms=int(tape.ts[0]), bench=None, mcap_up=set())
    assert row.trades >= 2
    assert row.end > START


def test_beta_recovers_a_doubled_benchmark() -> None:
    start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    rng = np.random.default_rng(3)
    bench_ret = rng.normal(0.0, 0.02, 80)
    bench_close = 100.0 * np.cumprod(1.0 + bench_ret)
    alt_close = 10.0 * np.cumprod(1.0 + 2.0 * bench_ret)
    bench = _tape("BTC", bench_close, 1e9, start)
    alt = _tape("ALT", alt_close, 5_000_000.0, start)
    assert abs(beta(alt, bench, int(alt.ts[-1])) - 2.0) < 1e-6
    spec = Spec("proef", "volgdag", "alles", trigger=0.03, hold=1)
    row = run_follow([alt], spec, alt.ts, start_ms=int(alt.ts[0]), bench=bench)
    assert row.trades >= 1
