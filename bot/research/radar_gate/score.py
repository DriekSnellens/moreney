"""Causal news-activity scores from the archived wire.

This is the radar's arithmetic (market-adjusted Poisson tail, tone change).
It is not a replay of ``/api/signals/snapshot/``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import NormalDist
from typing import Any

BASELINE_DAYS = 30
MAX_STORY_TICKERS = 5
MIN_RELEVANCE = 6
MIN_BASELINE_STORIES = 5
MIN_BASELINE_DAYS = 3
MIN_SENTIMENT_STORIES = 2
MIN_SENTIMENT_COVERAGE = 0.5
HOT_Z = 1.0
SENT_POS = 0.15
SPIKE_TAIL = 0.001
GATES = ("hot", "spike", "hot_any")

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
        pairs = {_base(str(ticker)): _tone(str(sentiment)) for ticker, sentiment in raw.items()}
        grouped[day].append(Story(day=day, tone=tuple(sorted(pairs.items()))))
    return dict(grouped)


def _poisson_tail(observed: int, expected: float) -> float:
    """P(X >= observed) for a Poisson mean."""
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
    return (sum(labeled) / len(labeled)) - (sum(base_labeled) / len(base_labeled))


def score_day(
    grouped: Mapping[str, Sequence[Story]],
    day: str,
    universe: set[str],
) -> list[NameScore]:
    """Score ``universe`` from stories on ``day`` and the prior 30 calendar days."""
    if not grouped:
        return []
    first = min(grouped)
    if _shift(day, -BASELINE_DAYS) < first:
        return []
    current = list(grouped.get(day) or [])
    baseline: list[Story] = []
    active: dict[str, set[str]] = defaultdict(set)
    base_counts: dict[str, int] = defaultdict(int)
    last_baseline = _shift(day, -1)
    start = _shift(day, -BASELINE_DAYS)
    for story_day, stories in grouped.items():
        if story_day < start or story_day > last_baseline:
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
    scores: list[NameScore] = []
    for name in universe:
        mentions = _mentions(current, name)
        if not mentions:
            continue
        observed = len(mentions)
        if base_counts.get(name, 0) < MIN_BASELINE_STORIES:
            continue
        if len(active.get(name, ())) < MIN_BASELINE_DAYS:
            continue
        expected = (base_counts[name] / market_baseline) * market_current
        if expected <= 0:
            continue
        tail = _poisson_tail(observed, expected)
        scores.append(
            NameScore(
                base=name,
                stories=observed,
                expected=expected,
                z=_z_of(tail),
                tail=tail,
                sent_delta=_sentiment_delta(mentions, _mentions(baseline, name)),
            )
        )
    return scores


def passes(score: NameScore, gate: str) -> bool:
    tone_ok = score.sent_delta is not None and score.sent_delta >= SENT_POS
    if gate == "hot":
        return score.z >= HOT_Z and tone_ok
    if gate == "spike":
        return score.tail < SPIKE_TAIL and tone_ok
    if gate == "hot_any":
        return score.z >= HOT_Z
    raise KeyError(gate)


def pick_name(scores: Sequence[NameScore], gate: str) -> str | None:
    ranked = [score for score in scores if passes(score, gate)]
    if not ranked:
        return None
    ranked.sort(key=lambda score: (-score.z, score.base))
    return ranked[0].base
