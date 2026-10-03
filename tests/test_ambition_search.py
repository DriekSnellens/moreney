"""Ambition-search helpers."""

from __future__ import annotations

from bot.research.ambition_search.engine import ambition_specs, week_stats


def test_week_stats_green_rate() -> None:
    weeks = [
        {"pnl_eur": 100.0},
        {"pnl_eur": -50.0},
        {"pnl_eur": 10.0},
        {"pnl_eur": 0.0},
    ]
    st = week_stats(weeks)
    assert st["n_weeks"] == 4
    assert st["n_weeks_pos"] == 2
    assert st["n_weeks_neg"] == 1
    assert st["pct_weeks_pos"] == 0.5
    assert st["worst_week"] == -50.0
    assert st["all_weeks_green"] is False


def test_ambition_specs_are_bounded_and_named() -> None:
    specs = ambition_specs()
    assert 500 <= len(specs) <= 5000
    names = [s["name"] for s in specs]
    assert len(names) == len(set(names))
    assert any(s["btc_frac"] == 0.0 for s in specs)
    assert any(s["btc_frac"] == 0.5 for s in specs)
