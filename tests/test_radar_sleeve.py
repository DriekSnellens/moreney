"""Locked radar-sleeve proxy. No network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.radar_sleeve.score import (
    HOT_Z,
    _poisson_tail,
    index_stories,
    pick_name,
    score_day,
)
from bot.research.radar_sleeve.sleeve import run_sleeve


def _day(offset: int, origin: str = "2026-09-01") -> str:
    start = datetime.strptime(origin, "%Y-%m-%d")
    return (start + timedelta(days=offset)).strftime("%Y-%m-%d")


def _story(day: str, sentiments: dict[str, str], uid: str) -> dict[str, object]:
    return {
        "uid": uid,
        "published": f"{day}T12:00:00Z",
        "relevance": 8,
        "category": "crypto",
        "sentiments": sentiments,
        "title": uid,
    }


def _quiet_baseline() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for offset in range(30):
        day = _day(offset)
        rows.append(_story(day, {"AAA-USD": "bullish"}, f"a{offset}"))
        rows.append(_story(day, {"BBB-USD": "neutral"}, f"b{offset}"))
    return rows


def test_poisson_tail_matches_the_partial_sum() -> None:
    # P(X >= 3 | mean 0.5) = 1 - e^-0.5 * (1 + 0.5 + 0.125)
    mean = 0.5
    base = 2.718281828459045 ** -mean
    cdf = base * (1.0 + mean + mean**2 / 2.0)
    assert abs(_poisson_tail(3, mean) - (1.0 - cdf)) < 1e-12


def test_signal_day_is_outside_its_baseline() -> None:
    rows = _quiet_baseline()
    signal = _day(30)
    rows.extend(_story(signal, {"AAA-USD": "bullish"}, f"s{i}") for i in range(8))
    grouped = index_stories(rows)
    score = next(row for row in score_day(grouped, signal, {"AAA"}) if row.base == "AAA")
    # 30 prior AAA stories, 60 prior stories in the market, 8 today: 0.5 * 8.
    assert score.stories == 8
    assert abs(score.expected - 4.0) < 1e-9
    assert score.z >= HOT_Z


def test_six_ticker_story_does_not_count() -> None:
    rows = _quiet_baseline()
    signal = _day(30)
    broad = {f"T{i}-USD": "bullish" for i in range(6)}
    broad["AAA-USD"] = "bullish"
    rows.append(_story(signal, broad, "broad"))
    rows.append(_story(signal, {"AAA-USD": "bullish"}, "one"))
    grouped = index_stories(rows)
    score = next(row for row in score_day(grouped, signal, {"AAA"}) if row.base == "AAA")
    assert score.stories == 1


def test_thin_baseline_and_bearish_tone_do_not_pass_the_locked_gates() -> None:
    rows = [_story(_day(offset), {"AAA-USD": "bullish"}, f"a{offset}") for offset in range(2)]
    signal = _day(30)
    rows.extend(_story(signal, {"AAA-USD": "bearish"}, f"s{i}") for i in range(8))
    grouped = index_stories(rows)
    assert score_day(grouped, signal, {"AAA"}) == []

    rows = _quiet_baseline()
    rows.extend(_story(signal, {"AAA-USD": "bearish"}, f"s{i}") for i in range(8))
    grouped = index_stories(rows)
    scores = score_day(grouped, signal, {"AAA", "BBB"})
    assert pick_name(scores, "hot") is None
    assert pick_name(scores, "spike") is None
    assert pick_name(scores, "hot_any") == "AAA"


def test_non_bearish_spike_can_be_selected() -> None:
    rows: list[dict[str, object]] = []
    for offset in range(30):
        day = _day(offset)
        rows.append(_story(day, {"AAA-USD": "bullish"}, f"a{offset}"))
        for extra in range(9):
            rows.append(_story(day, {"ZZ-USD": "neutral"}, f"z{offset}-{extra}"))
    signal = _day(30)
    rows.extend(_story(signal, {"AAA-USD": "bullish"}, f"s{i}") for i in range(12))
    grouped = index_stories(rows)
    scores = score_day(grouped, signal, {"AAA"})
    assert pick_name(scores, "spike") == "AAA"
    assert pick_name(scores, "hot") == "AAA"


def test_equal_scores_break_ties_alphabetically() -> None:
    rows: list[dict[str, object]] = []
    for offset in range(30):
        day = _day(offset)
        rows.append(_story(day, {"AAA-USD": "bullish"}, f"a{offset}"))
        rows.append(_story(day, {"BBB-USD": "bullish"}, f"b{offset}"))
        for extra in range(8):
            rows.append(_story(day, {"CCC-USD": "neutral"}, f"c{offset}-{extra}"))
    signal = _day(30)
    for i in range(8):
        rows.append(_story(signal, {"BBB-USD": "bullish"}, f"bb{i}"))
        rows.append(_story(signal, {"AAA-USD": "bullish"}, f"aa{i}"))
    grouped = index_stories(rows)
    assert pick_name(score_day(grouped, signal, {"AAA", "BBB"}), "hot_any") == "AAA"


def _bars(days: list[str], opens: list[float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for day, price in zip(days, opens, strict=True):
        stamp = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
        rows.append([stamp, price, price, price, price, 1_000_000.0])
    return rows


def test_same_pick_is_one_round_trip() -> None:
    days = [_day(offset, "2026-10-01") for offset in range(4)]
    ohlc = {
        "BTC": _bars(days, [100, 100, 100, 100]),
        "AAA": _bars(days, [100, 100, 105, 110]),
    }
    picks = {days[0]: "AAA", days[1]: "AAA", days[2]: None}
    result = run_sleeve(ohlc, picks, start=days[0], end=days[-1], book_eur=3_000.0)
    assert result["n_trades"] == 1
    assert result["trips"][0]["base"] == "AAA"
    assert result["trips"][0]["entry"] == days[1]
    assert result["trips"][0]["exit"] == days[3]
    assert float(result["pnl_eur"]) > 200.0
