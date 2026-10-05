"""Path rule for a fixed take-profit on a daily bar."""

from bot.research.daily_green_lab.tp8 import resolve_from_entry


def test_up_bar_takes_profit_before_the_stop():
    hit = resolve_from_entry(
        entry=100.0, o=100.0, h=109.0, l=94.0, c=101.0, hard=0.05, tp=0.08, slip=0.0
    )
    assert hit == ("take_profit", 108.0)


def test_down_bar_stops_before_the_target():
    hit = resolve_from_entry(
        entry=100.0, o=100.0, h=109.0, l=94.0, c=99.0, hard=0.05, tp=0.08, slip=0.0
    )
    assert hit == ("hard_stop", 95.0)


def test_gap_through_the_target_fills_at_the_open():
    hit = resolve_from_entry(
        entry=100.0, o=112.0, h=113.0, l=111.0, c=112.5, hard=0.05, tp=0.08, slip=0.0
    )
    assert hit == ("take_profit", 112.0)
