"""Daily AlphaI crypto buy recommendations."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from bot.integrations.alphai.daily_recommendations import (
    build_picks_from_scores,
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


def test_recommendation_session_id_uses_noon_amsterdam() -> None:
    # 2026-09-02 10:00 Amsterdam → still previous window (Sept 1 noon start)
    ts = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)  # 10:00 CEST
    assert recommendation_session_id(now=ts) == "2026-09-01"
    ts2 = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)  # 13:00 CEST
    assert recommendation_session_id(now=ts2) == "2026-09-02"


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
