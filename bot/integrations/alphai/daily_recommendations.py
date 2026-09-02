"""AlphaI bullish/bearish ticker recommendations (15-min / hourly / daily refresh)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.integrations.alphai.client import AlphaIClient
from bot.integrations.alphai.parse import AlphaIHeadline, parse_news_page
from bot.integrations.alphai.symbols import LIQUID_EUR_BASES

logger = logging.getLogger(__name__)

_OPERATOR_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass
class BasePick:
    base: str
    score: float
    rank: int = 0
    bullish_headlines: list[str] = field(default_factory=list)
    bearish_headlines: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "score": round(self.score, 2),
            "rank": self.rank,
            "bullish_headlines": self.bullish_headlines[:4],
            "bearish_headlines": self.bearish_headlines[:4],
            "note": self.note,
        }


def resolve_interval_minutes(
    *,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    default_minutes: int = 15,
) -> int:
    """Prefer explicit minutes; fall back to hours×60; floor at 5 minutes."""
    if interval_minutes is not None and int(interval_minutes) > 0:
        return max(5, int(interval_minutes))
    if interval_hours is not None and int(interval_hours) > 0:
        return max(5, int(interval_hours) * 60)
    return max(5, int(default_minutes or 15))


def recommendation_session_id(
    *,
    now: datetime | None = None,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    update_hour_local: int = 12,
) -> str:
    """Bucket id for the current recommendation window.

    * ``interval < 1440 min`` → ``YYYY-MM-DDTHH:MM`` (Europe/Amsterdam floor)
    * ``interval >= 1440 min`` → date of noon→noon session (legacy daily)
    """
    instant = now or datetime.now(UTC)
    local = instant.astimezone(_OPERATOR_TZ)
    minutes = resolve_interval_minutes(
        interval_minutes=interval_minutes,
        interval_hours=interval_hours,
    )
    if minutes >= 24 * 60:
        anchor = local.replace(hour=update_hour_local, minute=0, second=0, microsecond=0)
        if local < anchor:
            anchor -= timedelta(days=1)
        return anchor.date().isoformat()
    total = local.hour * 60 + local.minute
    bucket = (total // minutes) * minutes
    hour, minute = divmod(bucket, 60)
    return f"{local.date().isoformat()}T{hour:02d}:{minute:02d}"


def next_update_at_utc(
    *,
    now: datetime | None = None,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    update_hour_local: int = 12,
) -> datetime:
    instant = now or datetime.now(UTC)
    local = instant.astimezone(_OPERATOR_TZ)
    minutes = resolve_interval_minutes(
        interval_minutes=interval_minutes,
        interval_hours=interval_hours,
    )
    if minutes >= 24 * 60:
        target = local.replace(
            hour=update_hour_local, minute=0, second=0, microsecond=0
        )
        if local >= target:
            target += timedelta(days=1)
        return target.astimezone(UTC)
    total = local.hour * 60 + local.minute
    bucket = (total // minutes) * minutes
    target = local.replace(hour=0, minute=0, second=0, microsecond=0)
    target += timedelta(minutes=bucket + minutes)
    return target.astimezone(UTC)


def load_daily_recommendations(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_daily_recommendations(path: Path | str, report: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def needs_session_refresh(
    cached: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    update_hour_local: int = 12,
) -> bool:
    if not cached:
        return True
    instant = now or datetime.now(UTC)
    nxt_raw = cached.get("next_update_at")
    if nxt_raw:
        try:
            nxt = datetime.fromisoformat(str(nxt_raw).replace("Z", "+00:00"))
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=UTC)
            if instant >= nxt.astimezone(UTC):
                return True
        except (TypeError, ValueError):
            pass
    expected = recommendation_session_id(
        now=instant,
        interval_minutes=interval_minutes,
        interval_hours=interval_hours,
        update_hour_local=update_hour_local,
    )
    return str(cached.get("session_id") or "") != expected


def score_focus_bases(
    headlines: list[AlphaIHeadline],
    focus_bases: set[str],
    *,
    min_relevance: int = 6,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {
        b: {"score": 0.0, "bullish": [], "bearish": [], "mentions": 0}
        for b in sorted(focus_bases)
    }
    for h in headlines:
        if h.relevance < min_relevance:
            continue
        touched: set[str] = set()
        for base in h.bullish_bases():
            if base not in rows:
                continue
            touched.add(base)
            weight = h.relevance * (3.0 if h.category == "crypto" else 1.5)
            rows[base]["score"] += weight
            rows[base]["bullish"].append(h.title[:100])
        for base in h.bearish_bases():
            if base not in rows:
                continue
            touched.add(base)
            weight = h.relevance * (3.5 if h.category == "crypto" else 2.0)
            rows[base]["score"] -= weight
            rows[base]["bearish"].append(h.title[:100])
        for ticker in h.tickers:
            base = ticker.split("-")[0].upper() if "-" in ticker else ticker.upper()
            if base in rows:
                touched.add(base)
        for base in touched:
            rows[base]["mentions"] += 1
    return rows


def build_picks_from_scores(
    scores: dict[str, dict[str, Any]],
    *,
    top_n: int = 8,
    macro_caution: bool = False,
) -> tuple[list[BasePick], list[BasePick], list[BasePick]]:
    ranked: list[BasePick] = []
    for base, row in scores.items():
        note_parts: list[str] = []
        if row["bullish"]:
            note_parts.append(f"{len(row['bullish'])} bullish headline(s)")
        if row["bearish"]:
            note_parts.append(f"{len(row['bearish'])} bearish headline(s)")
        if macro_caution:
            note_parts.append("macro caution")
        ranked.append(
            BasePick(
                base=base,
                score=float(row["score"]),
                bullish_headlines=list(row["bullish"]),
                bearish_headlines=list(row["bearish"]),
                note=" · ".join(note_parts) if note_parts else "geen recente headlines",
            )
        )
    ranked.sort(key=lambda p: p.score, reverse=True)

    buy: list[BasePick] = []
    avoid: list[BasePick] = []
    watch: list[BasePick] = []
    for pick in ranked:
        # Softer hourly buy bar: net score ≥ 3 with bullish headlines dominating.
        if pick.score >= 3.0 and len(pick.bullish_headlines) >= len(pick.bearish_headlines):
            buy.append(pick)
        elif pick.score <= -4.0 or (
            pick.bearish_headlines
            and not pick.bullish_headlines
            and pick.score <= 0
        ):
            avoid.append(pick)
        elif pick.score > 0 or pick.bullish_headlines:
            watch.append(pick)

    if macro_caution and buy:
        buy = buy[: max(3, top_n // 2)]

    for idx, pick in enumerate(buy[:top_n], start=1):
        pick.rank = idx
    return buy[:top_n], avoid[:6], watch[:8]


def generate_daily_recommendations(
    client: AlphaIClient,
    *,
    focus_bases: set[str] | None = None,
    min_relevance: int = 6,
    top_n: int = 8,
    update_hour_local: int = 12,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    macro_caution: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch AlphaI news and rank focus bases for the current refresh window."""
    instant = now or datetime.now(UTC)
    universe = focus_bases or set(LIQUID_EUR_BASES)
    headlines: list[AlphaIHeadline] = []
    seen_uids: set[str] = set()

    def _merge(page: dict[str, Any]) -> None:
        for h in parse_news_page(page):
            if h.uid in seen_uids:
                continue
            seen_uids.add(h.uid)
            headlines.append(h)

    _merge(
        client.list_news(
            category="crypto",
            min_relevance=min_relevance,
            page_size=20,
            sort="ingested",
        )
    )
    try:
        _merge(
            client.list_news(
                min_relevance=max(min_relevance, 7),
                page_size=20,
                sort="ingested",
            )
        )
    except RuntimeError:
        logger.debug("ALPHAI_ACTIONABLE_NEWS_SKIPPED", exc_info=True)

    scores = score_focus_bases(headlines, universe, min_relevance=min_relevance)
    buy, avoid, watch = build_picks_from_scores(
        scores,
        top_n=top_n,
        macro_caution=macro_caution,
    )
    minutes = resolve_interval_minutes(
        interval_minutes=interval_minutes,
        interval_hours=interval_hours,
    )
    session = recommendation_session_id(
        now=instant,
        interval_minutes=minutes,
        update_hour_local=update_hour_local,
    )
    nxt = next_update_at_utc(
        now=instant,
        interval_minutes=minutes,
        update_hour_local=update_hour_local,
    )
    if minutes >= 24 * 60:
        refresh_mode = "daily"
    elif minutes >= 60 and minutes % 60 == 0:
        refresh_mode = "hourly"
    else:
        refresh_mode = "intrahour"

    return {
        "session_id": session,
        "generated_at": instant.astimezone(UTC).isoformat(),
        "next_update_at": nxt.isoformat(),
        "timezone": str(_OPERATOR_TZ),
        "update_hour_local": update_hour_local,
        "interval_minutes": minutes,
        "interval_hours": max(1, (minutes + 59) // 60),
        "refresh_mode": refresh_mode,
        "macro_caution": macro_caution,
        "headline_count": len(headlines),
        "rate_limit_remaining": client.last_rate_limit.remaining,
        "picks": [p.to_dict() for p in buy],
        "avoid": [p.to_dict() for p in avoid],
        "watch": [p.to_dict() for p in watch],
        "focus_universe": sorted(universe),
    }


def maybe_refresh_daily(
    client: AlphaIClient | None,
    path: Path | str,
    *,
    focus_bases: set[str],
    enabled: bool = True,
    min_relevance: int = 6,
    top_n: int = 8,
    update_hour_local: int = 12,
    interval_minutes: int | None = None,
    interval_hours: int | None = None,
    macro_caution: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Refresh when the session bucket rolls (or when ``force``)."""
    cached = load_daily_recommendations(path)
    instant = now or datetime.now(UTC)
    minutes = resolve_interval_minutes(
        interval_minutes=interval_minutes,
        interval_hours=interval_hours,
    )

    if not enabled:
        return cached
    if client is None:
        return cached
    if not force:
        if not needs_session_refresh(
            cached,
            now=instant,
            interval_minutes=minutes,
            update_hour_local=update_hour_local,
        ):
            return cached
        # Legacy daily mode: wait until past the local update hour on first run.
        if minutes >= 24 * 60 and cached is None:
            local = instant.astimezone(_OPERATOR_TZ)
            if local.hour < update_hour_local:
                return None

    try:
        report = generate_daily_recommendations(
            client,
            focus_bases=focus_bases,
            min_relevance=min_relevance,
            top_n=top_n,
            update_hour_local=update_hour_local,
            interval_minutes=minutes,
            macro_caution=macro_caution,
            now=instant,
        )
        save_daily_recommendations(path, report)
        logger.info(
            "ALPHAI_PICKS_REFRESHED session=%s mode=%s picks=%s avoid=%s",
            report.get("session_id"),
            report.get("refresh_mode"),
            [p.get("base") for p in (report.get("picks") or [])[:5]],
            [p.get("base") for p in (report.get("avoid") or [])[:5]],
        )
        return report
    except Exception:
        logger.exception("ALPHAI_PICKS_FAILED")
        return cached
