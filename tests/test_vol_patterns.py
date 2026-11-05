"""The month-ago rise is one locked cell, and a down year does not clear."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from bot.research.vol_patterns.engine import (
    BANDS,
    DAY_MS,
    START,
    Context,
    Row,
    Table,
    _in_band,
    _ret,
    build_table,
    cell_mask,
    clears,
    grid,
)
from bot.research.week_book.engine import make_coin


def test_the_grid_locks_the_quiet_month_ago_rise() -> None:
    specs = grid()
    assert len(specs) == 80
    assert len({spec.key() for spec in specs}) == 80
    core = [
        spec
        for spec in specs
        if spec.universe == "dun"
        and spec.lag == 28
        and spec.band == "0 tot 10%"
        and spec.shape == ""
    ]
    assert len(core) == 1
    assert _in_band(0.0, "0 tot 10%")
    assert not _in_band(0.10, "0 tot 10%")
    assert _in_band(0.30, "vanaf 30%")
    assert set(BANDS) >= {"0 tot 10%", "10 tot 30%", "−10 tot 0%", "vanaf 30%"}


def test_a_down_year_does_not_clear() -> None:
    row = Row(
        "x",
        "patroon",
        200,
        0.5,
        0.1,
        0.4,
        0.01,
        -0.01,
        30_000.0,
        10_000.0,
        0.2,
        40,
        {2024: 1_000.0, 2025: -1.0, 2026: 1_000.0},
        {2024: 10, 2025: 10, 2026: 10},
        0.45,
        0.5,
    )
    assert not clears(row)


def _coin() -> object:
    start = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    n = 60
    ts = start + np.arange(n) * DAY_MS
    close = np.full(n, 100.0)
    close[20] = 105.0
    close[48] = 101.0
    quote = np.full(n, 50_000.0)
    return make_coin("ALT", ts, close.copy(), close.copy(), close.copy(), close, quote)


def test_the_week_ending_a_month_earlier_is_the_feature() -> None:
    coin = _coin()
    assert abs(_ret(coin.close, coin.ts, 20, 7) - 0.05) < 1e-12
    table = build_table([coin], coin.ts, int(coin.ts[48]), Context())
    assert table.ms.size >= 1
    hit = np.flatnonzero(table.ms == int(coin.ts[48]))
    assert hit.size == 1
    assert abs(float(table.lag[28][hit[0]]) - 0.05) < 1e-12
    assert bool(table.obscure[hit[0]])
    assert not bool(table.quiet[hit[0]])


def _row(**kw: object) -> Table:
    lag = {lag: np.asarray([np.nan], dtype=np.float64) for lag in (0, 14, 28, 42)}
    lag[28] = np.asarray([kw.get("lag28", 0.05)], dtype=np.float64)

    def bit(name: str, default: bool = False) -> np.ndarray:
        return np.asarray([kw.get(name, default)], dtype=bool)

    return Table(
        ms=np.asarray([0], dtype=np.int64),
        phase=np.asarray([0], dtype=np.int8),
        quote=np.asarray([50_000.0]),
        entry=np.asarray([1.0]),
        exit=np.asarray([1.1]),
        gross=np.asarray([0.1]),
        year=np.asarray([2024], dtype=np.int16),
        obscure=bit("obscure", True),
        quiet=bit("quiet"),
        liquid=bit("liquid"),
        lag=lag,
        last=np.asarray([kw.get("last", 0.0)], dtype=np.float64),
        wide=bit("wide"),
        compress=bit("compress"),
        month=np.asarray([np.nan]),
        month_now=np.asarray([np.nan]),
        cmc_rank=np.asarray([kw.get("rank", -1)], dtype=np.int32),
        cmc_quiet=bit("cmc_quiet"),
        mcap_down=bit("mcap_down"),
        mcap_up=bit("mcap_up"),
        world_quiet=bit("world_quiet"),
        news=np.asarray([kw.get("news", -1)], dtype=np.int8),
    )


def test_ten_percent_sits_in_the_next_band_and_a_wide_rank_needs_the_rise() -> None:
    core = next(spec for spec in grid() if spec.name == "dun, een maand geleden 0 tot 10%")
    assert bool(cell_mask(_row(lag28=0.05), core)[0])
    assert not bool(cell_mask(_row(lag28=0.10), core)[0])
    ranked = next(spec for spec in grid() if spec.shape == "cmc_rang")
    assert not bool(cell_mask(_row(lag28=0.05, rank=50), ranked)[0])
    assert bool(cell_mask(_row(lag28=0.05, rank=150), ranked)[0])
    assert not clears(
        Row(
            "x",
            "patroon",
            10,
            0.9,
            0.2,
            0.4,
            0.0,
            0.0,
            START + 1,
            1,
            0.0,
            10,
            {2024: 1.0, 2025: 1.0, 2026: 1.0},
            {2024: 8, 2025: 8, 2026: 8},
            None,
            0.2,
        )
    )
