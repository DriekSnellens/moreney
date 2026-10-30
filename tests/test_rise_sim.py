"""Wet replay: the trail fills inside the bar, the hold sells the next open."""

from __future__ import annotations

from bot.research.rise_lab.sim import SimSpec, _Name, _pick, replay

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01 UTC
VOL = 1_000_000.0


def _bar(i: int, open_: float, high: float, low: float, close: float) -> list[float]:
    return [T0 + i * DAY, open_, high, low, close, VOL]


def _tape(path: list[tuple[float, float, float, float]]) -> dict[str, list[list[float]]]:
    rows = [_bar(i, o, h, low, c) for i, (o, h, low, c) in enumerate(path)]
    return {"BTC": rows, "AAA": rows}


def test_pick_takes_the_strongest_name_that_qualifies() -> None:
    names = [
        _Name(0.10, "BBB", False, False),
        _Name(0.50, "AAA", True, True),
        _Name(0.20, "CCC", True, False),
    ]
    assert _pick(names, "leader") == "AAA"
    assert _pick(names, "continuation") == "AAA"
    assert _pick(names, "hot") == "AAA"
    assert _pick([_Name(-0.05, "BBB", False, False)], "leader") is None


def test_trail_sells_inside_the_bar_and_a_gap_sells_the_open() -> None:
    # Signal on day 0. Buy day 1 at 100, high 130. Day 2 trades through the 10% trail.
    path = [(100, 100, 100, 100), (100, 130, 100, 120), (125, 128, 110, 112)]
    row = replay(
        _tape(path),
        SimSpec("trail", "leader", trail_pct=0.10),
        {"2024-01-01": "AAA"},
        start="2024-01-01",
        end="2024-01-03",
    )
    assert row["n_trades"] == 1
    assert row["reasons"]["trail"] == 1
    assert row["end_hold"] == "cash"
    assert row["pnl_eur"] > 0

    gap = [(100, 100, 100, 100), (100, 130, 100, 120), (100, 101, 90, 95)]
    gapped = replay(
        _tape(gap),
        SimSpec("gap", "leader", trail_pct=0.10),
        {"2024-01-01": "AAA"},
        start="2024-01-01",
        end="2024-01-03",
    )
    assert gapped["reasons"]["trail"] == 1
    assert gapped["pnl_eur"] < 0


def test_hold_sells_the_open_after_seven_bars() -> None:
    path = [(100, 101, 99, 100)] * 12
    row = replay(
        _tape(path),
        SimSpec("hold", "leader", hold_bars=7),
        {"2024-01-01": "AAA"},
        start="2024-01-01",
        end="2024-01-12",
    )
    assert row["n_trades"] == 1
    assert row["reasons"]["time"] == 1
    assert row["pnl_eur"] < 0
