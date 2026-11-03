"""Radar buy gate. No network."""

from __future__ import annotations

from bot.research.radar_gate.book import BOOK_EUR, Session, _adv, _buy, _sell, simulate
from bot.research.radar_gate.score import (
    HOT_Z,
    SENT_POS,
    SPIKE_TAIL,
    NameScore,
    index_stories,
    passes,
    pick_name,
    score_day,
)


def test_thresholds_stay_locked() -> None:
    assert HOT_Z == 1.0
    assert SENT_POS == 0.15
    assert SPIKE_TAIL == 0.001
    assert BOOK_EUR == 20_000.0


def test_a_negative_tone_is_not_a_buy_and_a_missing_tone_is_not_either() -> None:
    hot = NameScore("ETH", 4, 1.0, 2.0, 0.01, 0.40)
    sour = NameScore("SOL", 4, 1.0, 3.0, 0.001, -0.30)
    quiet = NameScore("XRP", 4, 1.0, 2.0, 0.01, None)
    flat = NameScore("ADA", 4, 1.0, 1.5, 0.05, 0.10)
    assert passes(hot, "hot")
    assert not passes(sour, "hot")
    assert not passes(quiet, "hot")
    assert not passes(flat, "hot")
    assert passes(sour, "hot_any")
    assert not passes(hot, "spike")
    assert pick_name((sour, hot), "hot") == "ETH"
    assert pick_name((hot, NameScore("ADA", 4, 1.0, 2.0, 0.01, 0.50)), "hot") == "ADA"


def test_later_stories_do_not_change_an_earlier_day() -> None:
    rows: list[dict[str, object]] = []
    for offset in range(1, 31):
        day = f"2026-08-{offset:02d}"
        rows.append(
            {
                "published": f"{day}T12:00:00Z",
                "relevance": 6,
                "sentiments": {"ETH-USD": "neutral", "SOL-USD": "neutral"},
            }
        )
    rows.append(
        {
            "published": "2026-08-31T12:00:00Z",
            "relevance": 8,
            "sentiments": {"ETH-USD": "bullish"},
        }
    )
    rows.append(
        {
            "published": "2026-09-01T12:00:00Z",
            "relevance": 9,
            "sentiments": {"SOL-USD": "bullish", "ETH-USD": "bullish"},
        }
    )
    grouped = index_stories(rows)
    early = score_day(grouped, "2026-08-31", {"ETH", "SOL"})
    later = score_day(grouped, "2026-09-01", {"ETH", "SOL"})
    early_bases = {score.base for score in early}
    assert "SOL" not in early_bases
    assert any(score.base == "SOL" for score in later)


def _session(
    day: str,
    open_: float,
    *,
    high: float | None = None,
    low: float | None = None,
) -> Session:
    top = open_ if high is None else high
    bottom = open_ if low is None else low
    return Session(day, open_, top, bottom, open_, 1.0e12, 96)


def test_the_fill_is_the_next_open_not_the_signal_day() -> None:
    book: dict[str, Session] = {}
    for day, price in (
        ("2026-09-01", 100.0),
        ("2026-09-02", 100.0),
        ("2026-09-03", 100.0),
        ("2026-09-04", 100.0),
        ("2026-09-05", 100.0),
    ):
        book[day] = _session(day, price, high=120.0, low=100.0)
    for day in ("2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"):
        book[day] = _session(day, 50.0)
    book["2026-09-11"] = _session(day="2026-09-11", open_=80.0)
    run = simulate({"ETH": book}, {}, universe=("ETH",), mode="chop", last="2026-09-11")
    adv = _adv(book, "2026-09-06")
    _cash, qty = _buy(BOOK_EUR, 50.0, adv)
    expected = _sell(qty, 80.0, adv)
    assert run.buys == 1
    assert run.trips == 1
    assert abs(run.end_equity - expected) < 1e-6
    # A fill on the signal day's open (100) would not land on this cash.
    _cash_early, early = _buy(BOOK_EUR, 100.0, adv)
    assert abs(run.end_equity - _sell(early, 80.0, adv)) > 1.0
