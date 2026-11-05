"""A CMC gate sees the session close, and a down year does not beat the live book."""

from __future__ import annotations

from bot.research.cmc_live.engine import (
    ONLINE,
    Market,
    Quote,
    Row,
    beats,
    coin_gate,
    global_gate,
    session_ms,
)
from bot.research.cmc_regime.engine import Point
from bot.research.week_book.engine import DAY_MS


def _point(day: int, mcap: float) -> Point:
    return Point(session_ms(f"2026-10-{day:02d}"), mcap, 10.0, 50.0)


def test_the_global_gate_uses_the_close_and_not_the_next_session() -> None:
    points = [_point(21 - age, 100.0) for age in range(1, 8)]
    points.append(_point(21, 90.0))
    points.append(_point(22, 200.0))
    market = Market(points)
    gate = global_gate(market, "mcap+")
    assert gate("2026-10-20", "ANY") is False
    assert market.view(session_ms("2026-10-20")).mcap < 0.0
    assert DAY_MS > 0


def test_coin_rules_stay_off_until_the_tape_is_online() -> None:
    asof = session_ms("2026-10-21")
    thin = {"AAA": {asof: Quote(asof, 10.0, -5.0)}}
    assert coin_gate(thin, "cmc7")("2026-10-20", "AAA") is True
    series = {
        f"N{i:02d}": {asof: Quote(asof, float(100 - i), 1.0 if i < 3 else -2.0)}
        for i in range(ONLINE)
    }
    series["N00"] = {asof: Quote(asof, 1_000.0, 4.0)}
    gate = coin_gate(series, "cmc7")
    assert gate("2026-10-20", "N00") is True
    assert gate("2026-10-20", "N05") is False
    sleeve = coin_gate(series, "top10")
    assert sleeve("2026-10-20", "N00") is True
    assert sleeve("2026-10-20", "N15") is False


def test_a_higher_ending_with_a_down_year_does_not_beat_live() -> None:
    control = Row("live", 100.0, 80.0, 0.30, 40, {"2024": 10.0, "2025": 10.0, "2026": 10.0})
    higher = Row("mcap+", 120.0, 100.0, 0.30, 40, {"2024": 10.0, "2025": -1.0, "2026": 10.0})
    wild = Row("luid", 200.0, 180.0, 0.50, 40, {"2024": 10.0, "2025": 10.0, "2026": 10.0})
    ok = Row("seizoen", 130.0, 110.0, 0.40, 40, {"2024": 10.0, "2025": 10.0, "2026": 10.0})
    assert not beats(higher, control)
    assert not beats(wild, control)
    assert beats(ok, control)
