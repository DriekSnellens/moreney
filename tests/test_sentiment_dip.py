"""Sentiment gate on the 5% bounce. No network."""

from __future__ import annotations

from bot.integrations.alphai.parse import AlphaIHeadline
from bot.research.sentiment_dip.gate import (
    AVOID_SCORE,
    BUY_SCORE,
    TONE_DAYS,
    Tone,
    _choice,
    classify,
    tone_for,
)
from bot.research.sentiment_dip.percents import BIDS, OFFERS
from bot.research.swing_grid.grid import BOOK_EUR, Session, choose_name


def test_percent_grid_is_the_locked_set() -> None:
    assert BIDS == (0.00, 0.01, 0.02, 0.04)
    assert OFFERS == (0.03, 0.05, 0.08, 0.12)
    assert (0.01, 0.05) in {(bid, offer) for bid in BIDS for offer in OFFERS}


def test_thresholds_match_the_live_scorer() -> None:
    assert TONE_DAYS == 7
    assert BUY_SCORE == 1.0
    assert AVOID_SCORE == -4.0


def _headline(day: str, ticker: str, sentiment: str, relevance: int = 6) -> AlphaIHeadline:
    return AlphaIHeadline(
        uid=f"{day}-{ticker}-{sentiment}",
        title="story",
        relevance=relevance,
        category="crypto",
        tickers=(ticker,),
        sentiments={ticker: sentiment},
        published_at=f"{day}T12:00:00Z",
    )


def test_a_bullish_name_is_a_buy_and_a_bearish_name_is_not() -> None:
    grouped = {
        "2026-09-01": [_headline("2026-09-01", "ETH-USD", "bullish")],
        "2026-09-02": [_headline("2026-09-02", "SOL-USD", "bearish")],
        "2026-09-03": [
            _headline("2026-09-03", "XRP-USD", "bullish", relevance=6),
            _headline("2026-09-03", "XRP-USD", "bearish", relevance=6),
        ],
    }
    tone = tone_for(grouped, "2026-09-04", ("ETH", "SOL", "XRP"))
    assert "ETH" in tone.buy
    assert "SOL" in tone.avoid
    assert "XRP" not in tone.buy
    assert "XRP" not in tone.avoid


def test_a_story_on_the_session_day_is_not_an_input() -> None:
    grouped = {
        "2026-09-03": [_headline("2026-09-03", "ETH-USD", "bearish")],
        "2026-09-04": [_headline("2026-09-04", "ETH-USD", "bullish")],
    }
    tone = tone_for(grouped, "2026-09-04", ("ETH",), window=7)
    assert "ETH" in tone.avoid
    assert "ETH" not in tone.buy


def test_classify_does_not_promote_a_weak_positive_watch_name() -> None:
    scores = {
        "ADA": {"score": 0.5, "bullish": ["one"], "bearish": []},
    }
    tone = classify(scores)
    assert tone.buy == frozenset()
    assert tone.avoid == frozenset()


def _session(day: str) -> Session:
    bars = tuple((100.0, 110.0, 90.0, 100.0) for _ in range(48))
    return Session(day, 100.0, 110.0, 90.0, 100.0, 1_000_000.0, bars)


def test_rank_prefers_the_stronger_buy_over_the_choppier_name() -> None:
    days = [f"2026-09-{offset:02d}" for offset in range(1, 7)]
    wild = [_session(day) for day in days]
    calm_bars = tuple((100.0, 101.0, 99.0, 100.0) for _ in range(48))
    calm = [Session(day, 100.0, 101.0, 99.0, 100.0, 1_000_000.0, calm_bars) for day in days]
    books = {"SOL": wild, "ETH": calm}
    tone = Tone(
        frozenset({"SOL", "ETH"}),
        frozenset(),
        {"SOL": 1.0, "ETH": 40.0},
    )
    ranked = _choice(books, "2026-09-06", BOOK_EUR, ("SOL", "ETH"), tone, "rank")
    chop = _choice(books, "2026-09-06", BOOK_EUR, ("SOL", "ETH"), tone, "tone")
    assert ranked is not None and ranked.base == "ETH"
    assert chop is not None and chop.base == "SOL"
    books["ADA"] = [_session(day) for day in days]
    tied = Tone(frozenset({"ADA", "SOL"}), frozenset(), {"ADA": 10.0, "SOL": 10.0})
    tie = _choice(books, "2026-09-06", BOOK_EUR, ("SOL", "ADA"), tied, "rank")
    assert tie is not None and tie.base == "ADA"
    quiet_bars = tuple((100.0, 100.4, 99.6, 100.0) for _ in range(48))
    books["DOGE"] = [
        Session(day, 100.0, 100.4, 99.6, 100.0, 1_000_000.0, quiet_bars) for day in days
    ]
    loud_score = Tone(
        frozenset({"DOGE", "ETH"}),
        frozenset(),
        {"DOGE": 80.0, "ETH": 2.0},
    )
    gated = _choice(books, "2026-09-06", BOOK_EUR, ("DOGE", "ETH"), loud_score, "rank")
    assert gated is not None and gated.base == "ETH"


def test_tone_keeps_the_choppy_buy_and_drops_a_bearish_name() -> None:
    days = [f"2026-09-{offset:02d}" for offset in range(1, 7)]
    wild = [_session(day) for day in days]
    calm_bars = tuple((100.0, 101.0, 99.0, 100.0) for _ in range(48))
    calm = [
        Session(day, 100.0, 101.0, 99.0, 100.0, 1_000_000.0, calm_bars) for day in days
    ]
    books = {"SOL": wild, "ETH": calm}
    choice = choose_name(books, "2026-09-06", BOOK_EUR, ("ETH",))
    assert choice is not None and choice.base == "ETH"
    skipped = choose_name(books, "2026-09-06", BOOK_EUR, ())
    assert skipped is None
