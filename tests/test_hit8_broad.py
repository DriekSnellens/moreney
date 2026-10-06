"""Select picks the higher hit rate. The test window is not an input."""

import numpy as np

from bot.research.daily_green_lab.hit8_broad import (
    Cut,
    _exit_path,
    _pair_cuts,
    _tail_masks,
    choose_cut,
    episode_first,
    one_slot_dates,
    one_slot_indices,
)


def _cut(name: str, sel_p: float, fit_p: float = 0.60) -> Cut:
    return Cut(name, 100, fit_p, 0.5, 100, sel_p, sel_p - 0.05, np.ones(1, dtype=bool))


def test_choose_cut_keeps_the_higher_select_hit_rate() -> None:
    low = _cut("low", 0.55)
    high = _cut("high", 0.72)
    thin_fit = _cut("spike", 0.90, fit_p=0.30)
    picked = choose_cut([low, high, thin_fit])
    assert picked is high


def test_one_slot_takes_the_higher_score_and_waits_for_the_exit() -> None:
    stamp = np.array([1, 1, 2, 5])
    score = np.array([0.2, 0.9, 0.8, 0.7])
    exit_stamp = np.array([3, 4, 9, 9])
    taken = one_slot_indices(stamp, score, np.ones(4, dtype=bool), exit_stamp)
    assert taken.tolist() == [1, 3]


def test_one_slot_dates_frees_the_slot_on_the_exit_day() -> None:
    date = np.array(["2024-06-01", "2024-06-01", "2024-06-02", "2024-06-04"])
    score = np.array([0.2, 0.9, 0.5, 0.4])
    exit_date = np.array(["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06"])
    base = np.array(["BBB", "AAA", "AAA", "AAA"])
    taken = one_slot_dates(date, score, np.ones(4, dtype=bool), exit_date, base)
    assert taken.tolist() == [1, 3]


def test_episode_first_collapses_a_nearby_repeat() -> None:
    base = np.array(["A", "A", "A", "B"])
    stamp = np.array([0, 500, 5000, 0])
    epi = episode_first(base, stamp, np.ones(4, dtype=bool), gap_ms=1000)
    assert epi.tolist() == [0, 2, 3]


def test_tail_threshold_ignores_the_later_window() -> None:
    col = np.array([0.0, 1.0, 2.0, 3.0, 1000.0])
    fit = np.array([True, True, True, True, False])
    highs = [float(name.split(">=")[1]) for name, _mask in _tail_masks(col, fit, "r30") if ">=" in name]
    assert max(highs) < 10


def test_pair_cuts_do_not_combine_a_feature_with_itself() -> None:
    rng = np.random.default_rng(0)
    n = 1000
    col = rng.normal(size=n)
    x = np.column_stack([col, col])
    y = np.ones(n, dtype=bool)
    fit = np.zeros(n, dtype=bool)
    sel = np.zeros(n, dtype=bool)
    fit[:500] = True
    sel[500:] = True
    cuts = _pair_cuts(x, ["r30", "xs6"], ("r30", "xs6"), y, fit, sel)
    assert cuts
    for cut in cuts:
        assert cut.name.count("r30") == 1
        assert "xs6" in cut.name


def test_exit_path_books_the_target_on_an_up_bar() -> None:
    rows = [
        [0, 100.0, 100.0, 100.0, 100.0, 1.0],
        [10, 100.0, 120.0, 99.0, 110.0, 1.0],
    ]
    ret, ts, took = _exit_path(rows, 0, 1)
    assert took is True
    assert ts == 10
    assert ret > 0.05
