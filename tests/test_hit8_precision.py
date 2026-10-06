"""Forward +8% label and the select gate for the 70% search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from bot.research.daily_green_lab.hit8_precision import (
    BOOK_EUR,
    FEATURES,
    Cand,
    Frame,
    build_frame,
    pick_green,
    pick_winner,
    sleeve_on,
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


def _hand_frame() -> Frame:
    n = 4
    x = np.zeros((n, len(FEATURES)), dtype=np.float64)
    x[:, FEATURES.index("xs10")] = [0.1, 0.4, 0.2, 0.3]
    dates = np.array(["2024-06-01", "2024-06-01", "2024-06-02", "2024-06-04"])
    y = np.array([False, True, False, True])
    ret = np.array([0.20, 0.10, -0.50, 0.02])
    exit_on = np.array(["2024-06-03", "2024-06-04", "2024-06-03", "2024-06-05"])
    return Frame(
        date=dates,
        base=np.array(["AAA", "BBB", "AAA", "AAA"]),
        x=x,
        names=FEATURES,
        y_up={1: y},
        y_close={1: y},
        y_tp={1: y},
        ret={1: ret},
        exit_date={1: exit_on},
    )


def test_sleeve_takes_one_name_and_waits_for_the_exit() -> None:
    frame = _hand_frame()
    cand = Cand(
        "both",
        1,
        "high",
        np.ones(4, dtype=bool),
        4,
        0.5,
        0.2,
        4,
        0.5,
        0.2,
    )
    stats = sleeve_on(frame, cand, np.ones(4, dtype=bool), book=BOOK_EUR)
    # BBB wins the first day. The next signal is still inside that exit, so it is skipped.
    assert stats["n"] == 2
    assert stats["k"] == 2
    assert stats["pnl"] == BOOK_EUR * (0.10 + 0.02)


def test_pick_green_keeps_the_highest_hit_rate_among_profitable_sleeves() -> None:
    n = 5
    y = np.array([True, True, True, False, True])
    ret = np.array([0.05, 0.05, 0.04, -0.01, 0.08])
    frame = Frame(
        date=np.array(["2024-06-01", "2024-07-01", "2025-03-01", "2025-04-01", "2025-05-01"]),
        base=np.array(["AAA", "AAA", "AAA", "AAA", "BBB"]),
        x=np.zeros((n, len(FEATURES))),
        names=FEATURES,
        y_up={1: y},
        y_close={1: y},
        y_tp={1: y},
        ret={1: ret.copy()},
        exit_date={1: np.array(["2024-06-02", "2024-07-02", "2025-03-02", "2025-04-02", "2025-05-02"])},
    )
    fit = frame.date <= "2024-12-31"
    sel = frame.date > "2024-12-31"
    loose = Cand("loose", 1, "high", np.ones(n, dtype=bool), n, 0.8, 0.5, n, 0.6, 0.4)
    tight = Cand(
        "tight",
        1,
        "high",
        np.array([True, True, True, False, True]),
        4,
        0.9,
        0.6,
        4,
        0.9,
        0.7,
    )
    picked = pick_green(frame, [loose, tight], fit, sel, min_fit=1, min_sel=1)
    assert picked is not None
    assert picked[0].name == "tight"
    assert picked[1]["pnl"] > 0 and picked[2]["pnl"] > 0
    frame.ret[1] = np.array([0.05, 0.05, -0.10, -0.10, -0.10])
    loser = Cand("loser", 1, "high", np.ones(n, dtype=bool), n, 0.99, 0.8, n, 0.99, 0.8)
    assert pick_green(frame, [loser], fit, sel, min_fit=1, min_sel=1) is None


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
