"""The neighbor grid stays locked, and a down year does not beat the pack."""

from __future__ import annotations

from bot.research.factor_scoreboard.engine import LIVE_KNOBS
from bot.research.residual_neighbors.engine import Row, beats, grid, knobs, live_spec


def test_the_grid_is_unique_and_starts_at_the_armed_pack() -> None:
    specs = grid()
    assert specs[0] == live_spec()
    assert len(specs) == 66
    assert len({spec.key() for spec in specs}) == 66
    made = knobs(specs[0])
    assert made["lookback_days"] == LIVE_KNOBS["lookback_days"]
    assert made["skip_days"] == LIVE_KNOBS["skip_days"]
    assert made["excess_floor"] == LIVE_KNOBS["excess_floor"]
    assert made["near_high_weight"] == LIVE_KNOBS["near_high_weight"]
    assert made["rebalance_weekday"] is None
    assert made["policy"].alt_trail_pct == 0.10
    assert made["policy"].alt_lock_arm_pct == 0.12
    assert made["policy"].alt_lock_floor_pct == 0.06
    near = {spec.near_high_weight: spec.name for spec in specs if spec.family == "near"}
    assert near[0.0] == "near-high 0,00"
    assert near[0.50] == "near-high 0,50"


def test_a_higher_book_with_a_down_year_does_not_beat_the_pack() -> None:
    control = Row("live pakket", "live", 100.0, 80.0, 0.30, 40, {"2024": 1, "2025": 1, "2026": 1})
    higher = Row("trail 12%", "trail", 140.0, 120.0, 0.30, 40, {"2024": 1, "2025": -1, "2026": 1})
    ok = Row("klok 5 dagen", "klok", 130.0, 110.0, 0.40, 40, {"2024": 1, "2025": 1, "2026": 1})
    assert not beats(higher, control)
    assert beats(ok, control)
