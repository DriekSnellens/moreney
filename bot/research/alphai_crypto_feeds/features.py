"""Point-in-time allow sets from archived AlphaI headlines.

The buy and avoid rules are the live daily-recommendation scorer. They were
locked before the replay: trailing 1, 3 and 7 UTC publication days, then
``gate`` / ``intersect`` / ``prefer`` on the buy set and ``avoid`` as a block
list. A window with no archived rows is left unfiltered. A covered window
with no buy names is an empty allow-set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.integrations.alphai.daily_recommendations import (
    build_picks_from_scores,
    score_focus_bases,
)
from bot.integrations.alphai.parse import AlphaIHeadline

# Locked before any wet replay of this archive.
WINDOWS: tuple[int, ...] = (1, 3, 7)
MODES: tuple[str, ...] = ("gate", "intersect", "prefer", "avoid")
MIN_RELEVANCE = 6
TOP_N = 8


def publication_date(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return raw[:10] if len(raw) >= 10 else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


def headline_from_row(row: dict[str, Any]) -> AlphaIHeadline | None:
    uid = str(row.get("uid") or "")
    published = str(row.get("published") or "")
    if not uid or not published:
        return None
    raw_sent = row.get("sentiments") or {}
    if not isinstance(raw_sent, dict):
        return None
    sentiments = {str(k): str(v) for k, v in raw_sent.items() if k}
    return AlphaIHeadline(
        uid=uid,
        title=str(row.get("title") or uid),
        relevance=int(row.get("relevance") or 0),
        category=str(row.get("category") or "other"),
        tickers=tuple(sentiments),
        sentiments=sentiments,
        published_at=published,
    )


def window_start(decision: str, days: int) -> str:
    start = datetime.strptime(decision, "%Y-%m-%d") - timedelta(days=max(1, days) - 1)
    return start.strftime("%Y-%m-%d")


def rows_in_window(
    rows: list[dict[str, Any]],
    *,
    decision: str,
    days: int,
) -> list[dict[str, Any]]:
    start = window_start(decision, days)
    out: list[dict[str, Any]] = []
    for row in rows:
        day = publication_date(row.get("published"))
        if day is not None and start <= day <= decision:
            out.append(row)
    return out


def classify_window(
    rows: list[dict[str, Any]],
    *,
    decision: str,
    days: int,
    universe: set[str],
) -> dict[str, set[str]] | None:
    """Return buy and avoid bases, or None when the archive has no rows."""
    window = rows_in_window(rows, decision=decision, days=days)
    if not window:
        return None
    headlines: list[AlphaIHeadline] = []
    for row in window:
        headline = headline_from_row(row)
        if headline is not None:
            headlines.append(headline)
    scores = score_focus_bases(headlines, universe, min_relevance=MIN_RELEVANCE)
    buy, avoid, _watch = build_picks_from_scores(scores, top_n=TOP_N)
    return {
        "buy": {pick.base for pick in buy},
        "avoid": {pick.base for pick in avoid},
    }


def allow_map(
    rows: list[dict[str, Any]],
    dates: list[str],
    *,
    days: int,
    mode: str,
    universe: set[str],
) -> dict[str, set[str]]:
    """Dates missing from the map have no archived news and stay unfiltered."""
    out: dict[str, set[str]] = {}
    for date in dates:
        classified = classify_window(rows, decision=date, days=days, universe=universe)
        if classified is None:
            continue
        if mode == "avoid":
            out[date] = set(universe) - classified["avoid"]
        else:
            out[date] = set(classified["buy"])
    return out
