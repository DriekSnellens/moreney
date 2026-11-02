"""Point-in-time news-spike scores from archived crypto stories.

This is not AlphaI's ``news_z``. The live radar is not a historical series,
and this archive was pulled at relevance 6 while that radar uses relevance 4.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import NormalDist
from typing import Any

from bot.research.alphai_crypto_feeds.features import publication_date

BASELINE_DAYS = 30
MAX_STORY_TICKERS = 5
MIN_RELEVANCE = 6
MIN_BASELINE_STORIES = 5
MIN_BASELINE_DAYS = 3
MIN_SENTIMENT_STORIES = 2
MIN_SENTIMENT_COVERAGE = 0.5
SPIKE_TAIL = 0.001
HOT_Z = 1.0
GATES: tuple[str, ...] = ("spike", "hot", "hot_any")

_NORMAL = NormalDist()


@dataclass(frozen=True)
class Story:
    day: str
    tone: tuple[tuple[str, int | None], ...]


@dataclass(frozen=True)
class NameScore:
    base: str
    stories: int
    expected: float
    z: float
    tail: float
    sent_delta: float | None


def _base(ticker: str) -> str:
    text = ticker.strip().upper()
    if text.endswith("-USD"):
        return text[:-4]
    return text


def _tone(sentiment: str) -> int | None:
    label = sentiment.strip().lower()
    if label in {"bullish", "positive", "very_bullish"}:
        return 1
    if label in {"bearish", "negative", "very_bearish"}:
        return -1
    return None


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def index_stories(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Story]]:
    """Group archive rows by UTC publication day. Broad stories are dropped."""
    grouped: dict[str, list[Story]] = defaultdict(list)
    for row in rows:
        day = publication_date(row.get("published"))
        if day is None or int(row.get("relevance") or 0) < MIN_RELEVANCE:
            continue
        raw = row.get("sentiments") or {}
        if not isinstance(raw, dict) or not raw or len(raw) > MAX_STORY_TICKERS:
            continue
        pairs: dict[str, int | None] = {}
        for ticker, sentiment in raw.items():
            pairs[_base(str(ticker))] = _tone(str(sentiment))
        grouped[day].append(Story(day=day, tone=tuple(sorted(pairs.items()))))
    return dict(grouped)


def _poisson_tail(observed: int, expected: float) -> float:
    """P(X >= observed) for a Poisson mean. One story day stays in the exact sum."""
    if observed <= 0:
        return 1.0
    if expected <= 0:
        return 0.0
    if expected > 80:
        z = (observed - 0.5 - expected) / math.sqrt(expected)
        return max(0.0, min(1.0, 1.0 - _NORMAL.cdf(z)))
    term = math.exp(-expected)
    cdf = term
    for i in range(1, observed):
        term *= expected / i
        cdf += term
        if cdf >= 1.0:
            return 0.0
    return max(0.0, min(1.0, 1.0 - cdf))


def _z_of(tail: float) -> float:
    clamped = min(1.0 - 1e-12, max(1e-12, tail))
    return _NORMAL.inv_cdf(1.0 - clamped)


def _mentions(stories: Sequence[Story], base: str) -> list[int | None]:
    found: list[int | None] = []
    for story in stories:
        for name, tone in story.tone:
            if name == base:
                found.append(tone)
                break
    return found


def _sentiment_delta(
    current: Sequence[int | None],
    baseline: Sequence[int | None],
) -> float | None:
    labeled = [tone for tone in current if tone is not None]
    if len(current) == 0 or len(labeled) < MIN_SENTIMENT_STORIES:
        return None
    if len(labeled) / len(current) < MIN_SENTIMENT_COVERAGE:
        return None
    base_labeled = [tone for tone in baseline if tone is not None]
    if not base_labeled:
        return None
    current_mean = sum(labeled) / len(labeled)
    baseline_mean = sum(base_labeled) / len(base_labeled)
    return current_mean - baseline_mean


def score_day(
    grouped: Mapping[str, Sequence[Story]],
    day: str,
    universe: set[str],
) -> list[NameScore]:
    """Score ``universe`` from stories on ``day`` and the prior 30 calendar days.

    The signal day is not part of its own baseline. Days before the archive
    has a full baseline window produce no scores.
    """
    if not grouped:
        return []
    first = min(grouped)
    baseline_start = _shift(day, -BASELINE_DAYS)
    if baseline_start < first:
        return []
    current = list(grouped.get(day) or [])
    baseline: list[Story] = []
    active: dict[str, set[str]] = defaultdict(set)
    base_counts: dict[str, int] = defaultdict(int)
    last_baseline = _shift(day, -1)
    for story_day, stories in grouped.items():
        if story_day < baseline_start or story_day > last_baseline:
            continue
        baseline.extend(stories)
        for story in stories:
            for name, _tone_value in story.tone:
                if name in universe:
                    active[name].add(story_day)
                    base_counts[name] += 1
    market_baseline = len(baseline)
    market_current = len(current)
    if market_baseline <= 0 or market_current <= 0:
        return []
    current_counts: dict[str, list[int | None]] = {}
    for name in universe:
        mentions = _mentions(current, name)
        if mentions:
            current_counts[name] = mentions
    scores: list[NameScore] = []
    for name, mentions in current_counts.items():
        observed = len(mentions)
        stories_before = base_counts.get(name, 0)
        days_before = len(active.get(name, ()))
        if stories_before < MIN_BASELINE_STORIES or days_before < MIN_BASELINE_DAYS:
            continue
        expected = (stories_before / market_baseline) * market_current
        if expected <= 0 or observed <= 0:
            continue
        tail = _poisson_tail(observed, expected)
        baseline_mentions = _mentions(baseline, name)
        scores.append(
            NameScore(
                base=name,
                stories=observed,
                expected=expected,
                z=_z_of(tail),
                tail=tail,
                sent_delta=_sentiment_delta(mentions, baseline_mentions),
            )
        )
    return scores


def passes(score: NameScore, gate: str) -> bool:
    if gate == "spike":
        return score.tail < SPIKE_TAIL and score.sent_delta is not None and score.sent_delta >= 0
    if gate == "hot":
        return score.z >= HOT_Z and score.sent_delta is not None and score.sent_delta >= 0
    if gate == "hot_any":
        return score.z >= HOT_Z
    raise KeyError(gate)


def pick_name(scores: Sequence[NameScore], gate: str) -> str | None:
    ranked = [score for score in scores if passes(score, gate)]
    if not ranked:
        return None
    ranked.sort(key=lambda score: (-score.z, score.base))
    return ranked[0].base


def picks_for(
    grouped: Mapping[str, Sequence[Story]],
    signal_dates: Sequence[str],
    universe: set[str],
    gate: str,
) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for day in signal_dates:
        out[day] = pick_name(score_day(grouped, day, universe), gate)
    return out
