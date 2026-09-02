"""AlphaI news regime monitor — polls feed and drives maker guardrails."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from bot.core.config import Settings
from bot.integrations.alphai.client import AlphaIClient
from bot.integrations.alphai.parse import (
    AlphaIHeadline,
    AlphaIRegimeState,
    build_regime_from_headlines,
    parse_news_page,
    parse_news_row,
)
from bot.integrations.alphai.symbols import LIQUID_EUR_BASES

logger = logging.getLogger(__name__)


class AlphaINewsMonitor:
    """Poll AlphaI on a wall-clock cadence; never blocks the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(getattr(settings, "alphai_enabled", False))
        key = _resolve_api_key(settings)
        self._client: AlphaIClient | None = AlphaIClient(key) if key else None
        if self._enabled and self._client is None:
            logger.warning("ALPHAI_ENABLED_BUT_NO_API_KEY")
            self._enabled = False
        self._state = AlphaIRegimeState(enabled=self._enabled)
        self._last_poll_mono = 0.0
        self._news_cursor: str | None = None
        self._crypto_symbols: set[str] | None = None
        self._focus_bases = _parse_csv_bases(
            getattr(settings, "live_micro_focus_bases", "") or "",
            fallback=set(LIQUID_EUR_BASES),
        )
        self._daily_picks_path = str(
            getattr(
                settings,
                "alphai_daily_recommendations_path",
                "data/alphai/daily_recommendations.json",
            )
        )
        self._last_daily_picks: dict[str, Any] | None = None

    @property
    def state(self) -> AlphaIRegimeState:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        out = self._state.to_public_dict()
        if self._last_daily_picks:
            out["daily_picks"] = {
                "session_id": self._last_daily_picks.get("session_id"),
                "generated_at": self._last_daily_picks.get("generated_at"),
                "next_update_at": self._last_daily_picks.get("next_update_at"),
                "refresh_mode": self._last_daily_picks.get("refresh_mode"),
                "interval_hours": self._last_daily_picks.get("interval_hours"),
                "picks": (self._last_daily_picks.get("picks") or [])[:8],
                "avoid": (self._last_daily_picks.get("avoid") or [])[:4],
            }
        return out

    def daily_picks_snapshot(self) -> dict[str, Any] | None:
        if self._last_daily_picks:
            return dict(self._last_daily_picks)
        from bot.integrations.alphai.daily_recommendations import load_daily_recommendations

        cached = load_daily_recommendations(self._daily_picks_path)
        if cached:
            self._last_daily_picks = cached
        return cached

    async def maybe_refresh_daily_picks(self, *, force: bool = False) -> dict[str, Any] | None:
        if not self._enabled or self._client is None:
            return self.daily_picks_snapshot()
        if not bool(getattr(self._settings, "alphai_daily_recommendations_enabled", True)):
            return self.daily_picks_snapshot()
        from bot.integrations.alphai.daily_recommendations import maybe_refresh_daily

        report = await asyncio.to_thread(
            maybe_refresh_daily,
            self._client,
            self._daily_picks_path,
            focus_bases=self._focus_bases,
            enabled=True,
            min_relevance=int(
                getattr(self._settings, "alphai_daily_recommendations_min_relevance", 6)
                or 6
            ),
            top_n=int(getattr(self._settings, "alphai_daily_recommendations_top_n", 8) or 8),
            update_hour_local=int(
                getattr(self._settings, "alphai_daily_recommendations_hour", 12) or 12
            ),
            interval_hours=int(
                getattr(self._settings, "alphai_recommendations_interval_hours", 1) or 1
            ),
            macro_caution=bool(self._state.macro_reduce_only),
            force=force,
        )
        if report:
            self._last_daily_picks = report
        return report

    async def maybe_refresh(self) -> AlphaIRegimeState:
        if not self._enabled or self._client is None:
            return self._state
        from bot.integrations.alphai.pending import drain_webhook_articles

        for article in drain_webhook_articles():
            self.ingest_webhook_article(article)
        interval = float(getattr(self._settings, "alphai_poll_interval_sec", 120.0) or 120.0)
        if (time.monotonic() - self._last_poll_mono) < interval:
            return self._state
        self._last_poll_mono = time.monotonic()
        try:
            state = await asyncio.to_thread(self._poll_sync)
            self._state = state
        except Exception as exc:
            logger.warning("ALPHAI_POLL_FAILED: %s", exc)
            self._state.last_error = str(exc)
        try:
            await self.maybe_refresh_daily_picks()
        except Exception:
            logger.exception("ALPHAI_DAILY_PICKS_REFRESH_FAILED")
        return self._state

    def ingest_webhook_article(self, article: dict[str, Any]) -> AlphaIRegimeState:
        """Apply a push article (Pro webhooks) immediately."""
        if not self._enabled:
            return self._state
        row = parse_news_row(article)
        if row is None:
            return self._state
        merged = _merge_headline_into_state(
            self._state,
            row,
            min_relevance=int(getattr(self._settings, "alphai_min_relevance", 7) or 7),
            block_bearish_bases=bool(
                getattr(self._settings, "alphai_block_bearish_bases", True)
            ),
            macro_reduce_only=bool(
                getattr(self._settings, "alphai_macro_reduce_only", True)
            ),
            focus_bases=self._focus_bases,
            observation_mode=bool(
                getattr(self._settings, "alphai_observation_mode", False)
            ),
        )
        self._state = merged
        return self._state

    def _poll_sync(self) -> AlphaIRegimeState:
        assert self._client is not None
        min_rel = int(getattr(self._settings, "alphai_min_relevance", 7) or 7)
        observation = bool(getattr(self._settings, "alphai_observation_mode", False))
        headlines: list[AlphaIHeadline] = []

        crypto_page = self._client.list_news(
            category="crypto",
            min_relevance=min_rel,
            page_size=20,
            cursor=self._news_cursor,
            sort="ingested",
        )
        self._news_cursor = crypto_page.get("next_cursor") or self._news_cursor
        headlines.extend(parse_news_page(crypto_page))

        if bool(getattr(self._settings, "alphai_poll_macro", True)):
            macro_page = self._client.list_news(
                category=["macro_economy", "regulation", "geopolitics"],
                min_relevance=max(min_rel, 7),
                page_size=10,
            )
            headlines.extend(parse_news_page(macro_page))

        if bool(getattr(self._settings, "alphai_poll_actionable", True)):
            try:
                actionable = self._client.list_news(
                    min_relevance=max(min_rel, 8),
                    page_size=10,
                )
                headlines.extend(parse_news_page(actionable))
            except RuntimeError:
                pass

        state = build_regime_from_headlines(
            headlines,
            min_relevance=min_rel,
            block_bearish_bases=bool(
                getattr(self._settings, "alphai_block_bearish_bases", True)
            ),
            macro_reduce_only=bool(
                getattr(self._settings, "alphai_macro_reduce_only", True)
            ),
            focus_bases=self._focus_bases,
            observation_mode=observation,
        )
        state.polls = self._state.polls + 1
        state.rate_limit_remaining = self._client.last_rate_limit.remaining
        rl = self._client.last_rate_limit.remaining
        if rl is not None and rl < 5:
            logger.warning("ALPHAI_RATE_LIMIT_LOW remaining=%s", rl)
        return state

    def ensure_symbol_coverage(self) -> dict[str, Any]:
        """One-shot coverage report using cached symbol list."""
        if self._client is None:
            return {"enabled": False, "error": "no_api_key"}
        cache = Path(
            str(
                getattr(
                    self._settings,
                    "alphai_symbol_cache_path",
                    "data/alphai/symbol_cache.json",
                )
            )
        )
        crypto = self._client.load_or_fetch_symbol_cache(cache)
        self._crypto_symbols = crypto
        from bot.integrations.alphai.symbols import alphai_candidates_for_base

        rows = []
        for base in LIQUID_EUR_BASES:
            hit = None
            for cand in alphai_candidates_for_base(base):
                if cand in crypto:
                    hit = cand
                    break
            rows.append({"base": base, "alphai_ticker": hit, "found": hit is not None})
        found = sum(1 for r in rows if r["found"])
        return {
            "enabled": True,
            "coverage_found": found,
            "coverage_total": len(rows),
            "rows": rows,
            "rate_limit_remaining": self._client.last_rate_limit.remaining,
        }


def _merge_headline_into_state(
    prior: AlphaIRegimeState,
    headline: AlphaIHeadline,
    *,
    min_relevance: int,
    block_bearish_bases: bool,
    macro_reduce_only: bool,
    focus_bases: set[str],
    observation_mode: bool,
) -> AlphaIRegimeState:
    existing = [
        AlphaIHeadline(
            uid=str(h.get("uid") or ""),
            title=str(h.get("title") or ""),
            relevance=int(h.get("relevance") or 0),
            category=str(h.get("category") or "other"),
            tickers=tuple(h.get("tickers") or []),
            sentiments={
                str(k): str(v) for k, v in (h.get("sentiments") or {}).items()
            },
        )
        for h in prior.headlines
        if isinstance(h, dict)
    ]
    existing.append(headline)
    state = build_regime_from_headlines(
        existing,
        min_relevance=min_relevance,
        block_bearish_bases=block_bearish_bases,
        macro_reduce_only=macro_reduce_only,
        focus_bases=focus_bases,
        observation_mode=observation_mode,
    )
    state.polls = prior.polls
    state.rate_limit_remaining = prior.rate_limit_remaining
    return state


def _resolve_api_key(settings: Settings) -> str | None:
    raw = getattr(settings, "alphai_api_key", None)
    if raw is not None and str(raw).strip():
        secret = getattr(raw, "get_secret_value", lambda: raw)()
        if secret:
            return str(secret).strip()
    import os

    env = os.environ.get("ALPHAI_API_KEY", "").strip()
    return env or None


def _parse_csv_bases(raw: str, *, fallback: set[str]) -> set[str]:
    out = {b.strip().upper() for b in raw.split(",") if b.strip()}
    return out or set(fallback)
