"""Causal sentiment gate on the locked 5% bounce."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.integrations.alphai.daily_recommendations import score_focus_bases
from bot.integrations.alphai.parse import AlphaIHeadline
from bot.research.swing_grid.grid import (
    BOOK_EUR,
    BOUNCE_5,
    CUSHION,
    Choice,
    Session,
    choose_name,
    run_priced_bounce,
    run_session,
)

TONE_DAYS = 7
BUY_SCORE = 1.0
AVOID_SCORE = -4.0
MIN_RELEVANCE = 6
MODES = ("tone", "block", "plain", "rank")


@dataclass(frozen=True)
class Tone:
    buy: frozenset[str]
    avoid: frozenset[str]
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    mode: str
    start: str
    end: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    selected: int
    filled: int
    maker_exits: int
    flattens: int
    maker_pnl: float
    flatten_pnl: float
    largest_day: float
    picks: tuple[tuple[str, int], ...]


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


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def headline_from_row(row: Mapping[str, Any]) -> AlphaIHeadline | None:
    published = publication_date(row.get("published"))
    uid = str(row.get("uid") or "")
    if published is None or not uid:
        return None
    raw = row.get("sentiments") or {}
    if not isinstance(raw, dict):
        return None
    sentiments = {str(ticker): str(sentiment) for ticker, sentiment in raw.items()}
    return AlphaIHeadline(
        uid=uid,
        title=str(row.get("title") or uid),
        relevance=int(row.get("relevance") or 0),
        category=str(row.get("category") or "other"),
        tickers=tuple(sentiments),
        sentiments=sentiments,
        published_at=str(row.get("published") or published),
    )


def load_headlines(path: Path) -> dict[str, list[AlphaIHeadline]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected archive at {path}")
    grouped: dict[str, list[AlphaIHeadline]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        headline = headline_from_row(row)
        day = publication_date(row.get("published"))
        if headline is None or day is None:
            continue
        grouped[day].append(headline)
    return dict(grouped)


def classify(scores: Mapping[str, Mapping[str, Any]]) -> Tone:
    """Buy and avoid predicates from the live scorer. No watch-list fill."""
    buy: set[str] = set()
    avoid: set[str] = set()
    for base, row in scores.items():
        bull = list(row.get("bullish") or [])
        bear = list(row.get("bearish") or [])
        score = float(row.get("score") or 0.0)
        if score >= BUY_SCORE and len(bull) >= len(bear):
            buy.add(base)
        elif score <= AVOID_SCORE or (bear and not bull and score <= 0):
            avoid.add(base)
    recorded = {base: float(row.get("score") or 0.0) for base, row in scores.items()}
    return Tone(frozenset(buy), frozenset(avoid), recorded)


def tone_for(
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    day: str,
    universe: Sequence[str],
    *,
    window: int = TONE_DAYS,
) -> Tone:
    """Tone from publication days strictly before ``day``."""
    start = _shift(day, -window)
    end = _shift(day, -1)
    headlines = [
        headline
        for story_day, stories in grouped.items()
        if start <= story_day <= end
        for headline in stories
    ]
    scores = score_focus_bases(headlines, set(universe), min_relevance=MIN_RELEVANCE)
    return classify(scores)


def _choice(
    books: Mapping[str, Sequence[Session]],
    day: str,
    equity: float,
    universe: Sequence[str],
    tone: Tone,
    mode: str,
) -> Choice | None:
    if mode == "tone":
        allowed = tuple(base for base in universe if base in tone.buy)
        return choose_name(books, day, equity, allowed)
    if mode == "rank":
        return _rank_choice(books, day, equity, universe, tone)
    choice = choose_name(books, day, equity, universe)
    if mode == "block" and choice is not None and choice.base in tone.avoid:
        return None
    return choice


def _rank_choice(
    books: Mapping[str, Sequence[Session]],
    day: str,
    equity: float,
    universe: Sequence[str],
    tone: Tone,
) -> Choice | None:
    """Highest net score among buys that clear the swing gates."""
    best: tuple[float, str, Choice] | None = None
    for base in universe:
        if base not in tone.buy:
            continue
        choice = choose_name(books, day, equity, (base,))
        if choice is None:
            continue
        score = float(tone.scores.get(base, 0.0))
        if best is None or score > best[0] or (score == best[0] and base < best[1]):
            best = (score, base, choice)
    return None if best is None else best[2]


def tones_by_day(
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    days: Sequence[str],
    universe: Sequence[str],
) -> dict[str, Tone]:
    """One tone per session day. The score does not depend on the bid or offer."""
    return {day: tone_for(grouped, day, universe) for day in days}


def simulate(
    books: Mapping[str, Sequence[Session]],
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    *,
    universe: Sequence[str],
    mode: str,
    start: str,
    end: str,
    cushion: float = CUSHION,
    target: float = BOUNCE_5,
    tones: Mapping[str, Tone] | None = None,
) -> Run:
    days = sorted(
        {
            session.day
            for base in universe
            for session in books.get(base, ())
            if start <= session.day <= end
        }
    )
    index = {
        base: {session.day: session for session in sessions}
        for base, sessions in books.items()
    }
    equity = BOOK_EUR
    peak = BOOK_EUR
    max_dd = 0.0
    selected = 0
    filled = 0
    maker_exits = 0
    flattens = 0
    maker_pnl = 0.0
    flatten_pnl = 0.0
    largest = 0.0
    picks: Counter[str] = Counter()
    for day in days:
        if mode == "plain":
            tone = Tone(frozenset(), frozenset())
        elif tones is not None:
            tone = tones[day]
        else:
            tone = tone_for(grouped, day, universe)
        choice = _choice(books, day, equity, universe, tone, mode)
        if choice is None:
            continue
        selected += 1
        picks[choice.base] += 1
        session = index[choice.base][day]
        if cushion == CUSHION and target == BOUNCE_5:
            out = run_session(session, choice, equity, "bounce5")
        else:
            out = run_priced_bounce(session, choice, equity, cushion=cushion, target=target)
        day_pnl = out.end_equity - equity
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        equity = out.end_equity
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        filled += int(out.filled)
        maker_exits += out.maker_exits
        flattens += out.flattens
        maker_pnl += out.maker_pnl
        flatten_pnl += out.flatten_pnl
    return Run(
        mode=mode,
        start=days[0] if days else start,
        end=days[-1] if days else end,
        pnl=equity - BOOK_EUR,
        end_equity=equity,
        max_dd=max_dd,
        days=len(days),
        selected=selected,
        filled=filled,
        maker_exits=maker_exits,
        flattens=flattens,
        maker_pnl=maker_pnl,
        flatten_pnl=flatten_pnl,
        largest_day=largest,
        picks=tuple(picks.most_common()),
    )
