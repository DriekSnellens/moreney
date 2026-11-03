"""Idle-cash rules. No network."""

from __future__ import annotations

from bot.research.idle_alts.engine import (
    MAX_NAMES,
    HoldDay,
    pick_names,
    qualifies,
    simulate,
    stop_fill,
)


def test_an_idiosyncratic_drop_qualifies_and_a_crash_does_not() -> None:
    assert qualifies(-0.10, 0.0, 200_000.0, "PEPE") is True
    assert qualifies(-0.10, -0.04, 200_000.0, "PEPE") is False
    assert qualifies(-0.05, 0.0, 200_000.0, "PEPE") is False
    assert qualifies(-0.10, 0.0, 1_000.0, "PEPE") is False
    assert qualifies(-0.10, 0.0, 200_000.0, "ETH") is False


def test_the_most_negative_names_are_taken() -> None:
    assert pick_names([("BBB", -0.2), ("AAA", -0.2), ("CCC", -0.1)], n=2) == ["AAA", "BBB"]


def test_a_live_day_does_not_buy_the_tail() -> None:
    run = simulate(_holds("alt:NEAR"), _tape(), _btc(), book_eur=20_000.0)
    assert run.buys == 0
    assert run.pnl == 0.0


def test_the_stop_fills_at_the_bound_and_a_gap_fills_at_the_open() -> None:
    assert stop_fill(100.0, (100.0, 101.0, 80.0, 90.0, 1.0)) == (94.0, "stop")
    assert stop_fill(100.0, (90.0, 95.0, 88.0, 92.0, 1.0)) == (90.0, "gap")
    assert stop_fill(100.0, (100.0, 180.0, 97.0, 170.0, 1.0)) is None


def test_a_stop_sells_at_the_bound_not_at_the_low() -> None:
    tape = _tape()
    tape["PEPE"]["2026-09-02"] = (90.0, 91.0, 70.0, 85.0, 1_000_000_000.0)
    run = simulate(_holds("cash"), tape, _btc(), book_eur=20_000.0, exit_mode="stop")
    assert run.buys == 1
    assert run.stop_exits == 1
    assert -500.0 < run.pnl < -200.0


def test_a_winner_is_not_sold_just_because_it_is_up() -> None:
    tape = _tape()
    tape["PEPE"]["2026-09-02"] = (90.0, 160.0, 89.0, 150.0, 1_000_000_000.0)
    for offset in range(3, 6):
        tape["PEPE"][f"2026-09-{offset:02d}"] = (150.0, 155.0, 140.0, 150.0, 1_000_000_000.0)
    run = simulate(_holds("cash"), tape, _btc(), book_eur=20_000.0, exit_mode="stop")
    assert run.buys == 1
    assert run.stop_exits == 0
    assert run.target_exits == 0
    assert run.pnl > 1_000.0


def test_one_drop_uses_one_slot_and_sells_at_the_target_not_the_high() -> None:
    run = simulate(_holds("cash"), _tape(), _btc(), book_eur=20_000.0)
    assert run.buys == 1
    assert run.target_exits == 1
    assert MAX_NAMES == 4
    assert 100.0 < run.pnl < 800.0


def _holds(hold: str) -> list[HoldDay]:
    days = [f"2026-09-{offset:02d}" for offset in range(1, 6)]
    return [HoldDay(day, hold, 20_000.0) for day in days]


def _btc() -> dict[str, float]:
    return {f"2026-08-{offset:02d}": 100.0 for offset in range(25, 32)} | {
        f"2026-09-{offset:02d}": 100.0 for offset in range(1, 6)
    }


def _tape() -> dict[str, dict[str, tuple[float, float, float, float, float]]]:
    bars: dict[str, tuple[float, float, float, float, float]] = {}
    for offset in range(25, 32):
        bars[f"2026-08-{offset:02d}"] = (100.0, 101.0, 99.0, 100.0, 1_000_000_000.0)
    bars["2026-09-01"] = (100.0, 100.0, 88.0, 90.0, 1_000_000_000.0)
    bars["2026-09-02"] = (90.0, 120.0, 89.0, 96.0, 1_000_000_000.0)
    for offset in range(3, 6):
        bars[f"2026-09-{offset:02d}"] = (96.0, 97.0, 95.0, 96.0, 1_000_000_000.0)
    return {"PEPE": bars}
