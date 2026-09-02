"""AlphaI bullish/bearish ticker recommendations (hourly + daily)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bot.integrations.alphai.daily_recommendations import (
    build_picks_from_scores,
    needs_session_refresh,
    next_update_at_utc,
    recommendation_session_id,
    score_focus_bases,
)
from bot.integrations.alphai.parse import AlphaIHeadline


def _headline(
    *,
    uid: str,
    title: str,
    relevance: int,
    ticker: str,
    sentiment: str,
    category: str = "crypto",
) -> AlphaIHeadline:
    return AlphaIHeadline(
        uid=uid,
        title=title,
        relevance=relevance,
        category=category,
        tickers=(ticker,),
        sentiments={ticker: sentiment},
    )


def test_recommendation_session_id_daily_noon_amsterdam() -> None:
    # 2026-09-02 10:00 Amsterdam → still previous window (Sept 1 noon start)
    ts = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)  # 10:00 CEST
    assert recommendation_session_id(now=ts, interval_hours=24) == "2026-09-01"
    ts2 = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)  # 13:00 CEST
    assert recommendation_session_id(now=ts2, interval_hours=24) == "2026-09-02"


def test_recommendation_session_id_hourly() -> None:
    # 2026-09-02 21:30 Amsterdam = 19:30 UTC
    ts = datetime(2026, 9, 2, 19, 30, tzinfo=UTC)
    local = ts.astimezone(ZoneInfo("Europe/Amsterdam"))
    assert recommendation_session_id(now=ts, interval_hours=1) == (
        f"{local.date().isoformat()}T{local.hour:02d}"
    )
    nxt = next_update_at_utc(now=ts, interval_hours=1)
    assert nxt > ts
    assert nxt.astimezone(ZoneInfo("Europe/Amsterdam")).minute == 0


def test_needs_session_refresh_hourly_on_next_update() -> None:
    ts = datetime(2026, 9, 2, 19, 30, tzinfo=UTC)
    cached = {
        "session_id": "2026-09-02T20",
        "next_update_at": "2026-09-02T18:00:00+00:00",  # already past
    }
    assert needs_session_refresh(cached, now=ts, interval_hours=1) is True
    fresh = {
        "session_id": recommendation_session_id(now=ts, interval_hours=1),
        "next_update_at": next_update_at_utc(now=ts, interval_hours=1).isoformat(),
    }
    assert needs_session_refresh(fresh, now=ts, interval_hours=1) is False


def test_score_and_rank_bullish_over_bearish() -> None:
    headlines = [
        _headline(uid="1", title="ETH rally", relevance=8, ticker="ETH-USD", sentiment="bullish"),
        _headline(uid="2", title="BTC dump", relevance=9, ticker="BTC-USD", sentiment="bearish"),
    ]
    scores = score_focus_bases(headlines, {"ETH", "BTC", "SOL"}, min_relevance=6)
    buy, avoid, _watch = build_picks_from_scores(scores, top_n=3)
    assert any(p.base == "ETH" for p in buy)
    assert any(p.base == "BTC" for p in avoid)
    assert scores["ETH"]["score"] > scores["BTC"]["score"]


def test_mixed_headline_goes_to_watch_not_avoid() -> None:
    scores = {
        "SOL": {
            "score": -3.5,
            "bullish": ["Solana turns positive"],
            "bearish": ["Cryptos Hesitant"],
            "mentions": 2,
        },
        "ADA": {
            "score": 3.5,
            "bullish": ["ADA partnership"],
            "bearish": [],
            "mentions": 1,
        },
    }
    buy, avoid, watch = build_picks_from_scores(scores, top_n=8)
    assert any(p.base == "ADA" for p in buy)
    assert not any(p.base == "SOL" for p in avoid)
    assert any(p.base == "SOL" for p in watch)