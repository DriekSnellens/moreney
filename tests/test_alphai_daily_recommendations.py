"""AlphaI bullish/bearish ticker recommendations (15-min / hourly / daily)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bot.integrations.alphai.daily_recommendations import (
    build_picks_from_scores,
    needs_session_refresh,
    next_update_at_utc,
    recommendation_session_id,
    resolve_interval_minutes,
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


def test_resolve_interval_minutes_prefers_minutes() -> None:
    assert resolve_interval_minutes(interval_minutes=15, interval_hours=1) == 15
    assert resolve_interval_minutes(interval_hours=1) == 60
    assert resolve_interval_minutes() == 15


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
    assert recommendation_session_id(now=ts, interval_minutes=60) == (
        f"{local.date().isoformat()}T{local.hour:02d}:00"
    )
    nxt = next_update_at_utc(now=ts, interval_minutes=60)
    assert nxt > ts
    assert nxt.astimezone(ZoneInfo("Europe/Amsterdam")).minute == 0


def test_recommendation_session_id_every_15_minutes() -> None:
    # 2026-09-02 22:07 Amsterdam = 20:07 UTC → bucket 22:00
    ts = datetime(2026, 9, 2, 20, 7, tzinfo=UTC)
    local = ts.astimezone(ZoneInfo("Europe/Amsterdam"))
    assert recommendation_session_id(now=ts, interval_minutes=15) == (
        f"{local.date().isoformat()}T{local.hour:02d}:00"
    )
    ts2 = datetime(2026, 9, 2, 20, 22, tzinfo=UTC)  # 22:22 Amsterdam → 22:15
    local2 = ts2.astimezone(ZoneInfo("Europe/Amsterdam"))
    assert recommendation_session_id(now=ts2, interval_minutes=15) == (
        f"{local2.date().isoformat()}T{local2.hour:02d}:15"
    )
    nxt = next_update_at_utc(now=ts, interval_minutes=15)
    assert nxt == datetime(2026, 9, 2, 20, 15, tzinfo=UTC)


def test_needs_session_refresh_15m_on_next_update() -> None:
    ts = datetime(2026, 9, 2, 20, 16, tzinfo=UTC)
    cached = {
        "session_id": "2026-09-02T22:00",
        "next_update_at": "2026-09-02T20:15:00+00:00",  # already past
    }
    assert needs_session_refresh(cached, now=ts, interval_minutes=15) is True
    fresh = {
        "session_id": recommendation_session_id(now=ts, interval_minutes=15),
        "next_update_at": next_update_at_utc(now=ts, interval_minutes=15).isoformat(),
    }
    assert needs_session_refresh(fresh, now=ts, interval_minutes=15) is False


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
