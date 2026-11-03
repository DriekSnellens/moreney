"""Sleeve rules. No network."""

from __future__ import annotations

from bot.integrations.alphai.parse import AlphaIHeadline
from bot.research.alphai_sleeve.book import (
    MAX_NAMES,
    MIN_BARS,
    SLEEVE_EUR,
    TONE_DAYS,
    Session,
    counts_for,
    live_basket,
    simulate,
    tone_names,
)


def test_the_sleeve_constants_stay_locked() -> None:
    assert SLEEVE_EUR == 10_000.0
    assert MAX_NAMES == 4
    assert TONE_DAYS == 7


def _headline(day: str, ticker: str, sentiment: str, category: str = "crypto") -> AlphaIHeadline:
    return AlphaIHeadline(
        uid=f"{day}-{ticker}-{sentiment}-{category}",
        title="story",
        relevance=6,
        category=category,
        tickers=(ticker,),
        sentiments={ticker: sentiment},
        published_at=f"{day}T12:00:00Z",
    )


def test_a_story_on_the_session_day_is_not_an_input() -> None:
    grouped = {
        "2026-09-07": [_headline("2026-09-07", "ETH-USD", "positive")],
        "2026-09-08": [_headline("2026-09-08", "SOL-USD", "positive")],
    }
    counts = counts_for(grouped, "2026-09-08", ("ETH", "SOL"))
    assert counts["ETH"] == (1, 0)
    assert counts["SOL"] == (0, 0)


def test_an_insider_row_does_not_count() -> None:
    grouped = {
        "2026-09-07": [_headline("2026-09-07", "ETH-USD", "positive", category="insider")],
    }
    counts = counts_for(grouped, "2026-09-08", ("ETH",))
    assert counts["ETH"] == (0, 0)


def test_tone_keeps_four_names_and_breaks_ties_alphabetically() -> None:
    counts = {
        "SOL": (3, 1),
        "ETH": (2, 0),
        "ADA": (2, 0),
        "XRP": (4, 1),
        "DOGE": (1, 0),
        "NEAR": (1, 1),
    }
    assert tone_names(counts) == ["XRP", "ADA", "ETH", "SOL", "DOGE"]
    assert "NEAR" not in tone_names(counts)


def test_live_basket_uses_the_published_counts() -> None:
    payloads = {
        "ETH": {"bullish": 2, "bearish": 2, "neutral": 5},
        "SOL": {"bullish": 4, "bearish": 1, "neutral": 0},
        "ADA": {"bullish": 1, "bearish": 0, "neutral": 3},
    }
    assert live_basket(payloads) == ["SOL", "ADA"]


def _flat(day: str, price: float = 100.0) -> Session:
    return Session(day, price, price, price, price, 1_000_000_000.0, MIN_BARS)


def _days() -> list[str]:
    return [f"2026-09-{offset:02d}" for offset in range(1, 10)]


def test_the_sleeve_holds_several_names_and_pays_the_taker() -> None:
    days = _days()
    books = {
        "AAA": [_flat(day) for day in days],
        "BBB": [_flat(day) for day in days],
        "CCC": [_flat(day) for day in days],
    }
    grouped = {
        "2026-09-07": [
            _headline("2026-09-07", "AAA-USD", "positive"),
            _headline("2026-09-07", "BBB-USD", "positive"),
        ],
        "2026-09-08": [
            _headline("2026-09-08", "BBB-USD", "negative"),
            _headline("2026-09-08", "CCC-USD", "positive"),
        ],
    }
    run = simulate(
        books,
        grouped,
        universe=("AAA", "BBB", "CCC"),
        mode="four",
        start="2026-09-08",
        end="2026-09-09",
    )
    held = {base: days_held for base, days_held, _pnl in run.names}
    assert held["AAA"] == 2
    assert held["BBB"] == 1
    assert held["CCC"] == 1
    assert run.end_equity < SLEEVE_EUR
    assert run.buys >= 3
