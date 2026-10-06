"""Forward +8% label and the select gate for the 70% search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from bot.research.daily_green_lab.hit8_precision import (
    Cand,
    build_frame,
    pick_winner,
)
from bot.research.moonshot_preimage.day50 import Series


def _flat(n: int = 90, *, px: float = 100.0) -> Series:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]
    return Series(
        dates=dates,
        o=[px] * n,
        h=[px] * n,
        l=[px] * n,
        c=[px] * n,
        v=[1_000.0] * n,
    )


def test_hit_is_the_forward_high_from_the_next_open() -> None:
    btc = _flat()
    alt = _flat()
    # Signal on day 70: next open 100, high exactly +8% → hit.
    alt.h[71] = 108.0
    alt.l[71] = 99.0
    alt.c[71] = 104.0
    # Signal on day 72: next high is just short of +8%.
    alt.h[73] = 107.9
    frame = build_frame({"BTC": btc, "AAA": alt})
    i70 = int(np.flatnonzero(frame.date == "2024-03-11")[0])
    i72 = int(np.flatnonzero(frame.date == "2024-03-13")[0])
    assert frame.base[i70] == "AAA"
    assert bool(frame.y_up[1][i70]) is True
    assert bool(frame.y_up[1][i72]) is False
    r1 = frame.names.index("r1")
    # A later spike must not leak into the earlier signal's features.
    spiked = _flat()
    spiked.h[71] = 108.0
    spiked.l[71] = 99.0
    spiked.c[71] = 104.0
    spiked.h[73] = 107.9
    spiked.h[80] = 250.0
    again = build_frame({"BTC": btc, "AAA": spiked})
    j70 = int(np.flatnonzero(again.date == "2024-03-11")[0])
    assert again.x[j70, r1] == frame.x[i70, r1]
    assert again.col("day_up")[j70] == frame.col("day_up")[i70]


def test_pick_winner_requires_fit_and_select_both_at_70() -> None:
    mask = np.ones(4, dtype=bool)
    thin = Cand("select_only", 1, "high", mask, 80, 0.40, 0.30, 80, 0.90, 0.80)
    both = Cand("both", 1, "high", mask, 80, 0.72, 0.62, 80, 0.71, 0.61)
    winner, claimed = pick_winner([thin, both])
    assert claimed is True
    assert winner is not None and winner.name == "both"
    ceiling, claimed_ceiling = pick_winner([thin])
    assert claimed_ceiling is False
    assert ceiling is not None and ceiling.name == "select_only"
