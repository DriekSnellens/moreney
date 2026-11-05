"""The dip sleeve sells a stop before a target, and a down year does not clear."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.alt_vol.engine import (
    BENCHMARK,
    DAY_MS,
    START,
    Coin,
    Result,
    Spec,
    clears,
    depth,
    exit_fill,
    grid,
    make_coin,
    run,
    slot_notional,
)


def test_a_stop_prints_before_a_target_and_a_gap_fills_at_the_open() -> None:
    both = exit_fill(100.0, 120.0, 80.0, 92.0, 108.0, time_stop=False)
    gap = exit_fill(80.0, 90.0, 70.0, 92.0, 108.0, time_stop=False)
    timed = exit_fill(100.0, 130.0, 70.0, 92.0, 108.0, time_stop=True)
    assert both == (92.0, "stop")
    assert gap == (80.0, "gat")
    assert timed == (100.0, "tijd")


def test_the_grid_is_unique_and_a_down_year_does_not_clear() -> None:
    specs = grid()
    assert specs[0] == Spec("primaire band")
    assert len(specs) == 16
    assert len({spec.key() for spec in specs}) == 16
    assert slot_notional(1_000_000.0, 1_000_000.0, 1) == 20_000.0
    down = Result(
        "x",
        30_000.0,
        10_000.0,
        0.2,
        40,
        {2024: 5_000.0, 2025: -1.0, 2026: 6_000.0},
        {2024: 10, 2025: 10, 2026: 10},
        0.5,
        0.0,
        0.0,
    )
    assert not clears(down)


def _coin(base: str, closes: list[float], *, high: float, low: float) -> Coin:
    start = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    n = len(closes)
    ts = np.asarray([start + i * DAY_MS for i in range(n)], dtype=np.int64)
    close = np.asarray(closes, dtype=np.float64)
    return make_coin(
        base,
        ts,
        close.copy(),
        np.full(n, high),
        np.full(n, low),
        close,
        np.full(n, 2_000_000.0),
    )


def test_a_peg_and_the_benchmark_stay_out_and_a_bounce_pays() -> None:
    flat = [100.0] * 12 + [120.0] + [100.0] * 7
    peg = _coin("STABLE", flat, high=130.0, low=70.0)
    assert depth(peg, 19, Spec("primaire band")) is None
    alt_closes = [100.0] * 19 + [90.0]
    alt = _coin("ALT", alt_closes, high=130.0, low=70.0)
    assert depth(alt, 19, Spec("primaire band")) is not None
    btc = _coin(BENCHMARK, alt_closes, high=130.0, low=70.0)
    assert depth(btc, 19, Spec("primaire band")) is None

    # The next open is bought, and a high through the target sells that day.
    extra_ts = alt.ts[-1] + DAY_MS
    alt = make_coin(
        "ALT",
        np.append(alt.ts, extra_ts),
        np.append(alt.open, 91.0),
        np.append(alt.high, 120.0),
        np.append(alt.low, 90.0),
        np.append(alt.close, 110.0),
        np.append(alt.quote, 2_000_000.0),
    )
    dates = np.append(alt.ts[:-1], extra_ts)
    # Signal on the dip bar. The entry bar is the one just appended.
    result = run([alt, btc], Spec("primaire band", n=1), dates, btc, start_ms=int(alt.ts[19]))
    assert result.trades == 1
    assert result.end > START
