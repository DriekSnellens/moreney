"""Parse AlphaI news payloads into trading guard signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
import re


@dataclass
class AlphaIHeadline:
    uid: str
    title: str
    relevance: int
    category: str
    tickers: tuple[str, ...]
    sentiments: dict[str, str]
    published_at: str | None = None
    source_domain: str | None = None

    def bearish_bases(self) -> set[str]:
        out: set[str] = set()
        for ticker, sentiment in self.sentiments.items():
            if _is_bearish(sentiment):
                out.add(_base_from_alphai_ticker(ticker))
        return out

    def bullish_bases(self) -> set[str]:
        out: set[str] = set()
        for ticker, sentiment in self.sentiments.items():
            if _is_bullish(sentiment):
                out.add(_base_from_alphai_ticker(ticker))
        return out


@dataclass
class AlphaIRegimeState:
    enabled: bool = False
    global_reduce_only: bool = False
    blocked_bases: frozenset[str] = frozenset()
    bullish_bases: frozenset[str] = frozenset()
    macro_reduce_only: bool = False
    headlines: list[dict[str, Any]] = field(default_factory=list)
    blocked_detail: dict[str, str] = field(default_factory=dict)
    last_poll_at: str | None = None
    last_error: str | None = None
    polls: int = 0
    rate_limit_remaining: int | None = None
    observation_mode: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "global_reduce_only": self.global_reduce_only,
            "macro_reduce_only": self.macro_reduce_only,
            "blocked_bases": sorted(self.blocked_bases),
            "bullish_bases": sorted(self.bullish_bases),
            "blocked_detail": dict(self.blocked_detail),
            "headline_count": len(self.headlines),
            "headlines": self.headlines[:8],
            "last_poll_at": self.last_poll_at,
            "last_error": self.last_error,
            "polls": self.polls,
            "rate_limit_remaining": self.rate_limit_remaining,
            "observation_mode": self.observation_mode,
        }


def parse_news_page(page: dict[str, Any]) -> list[AlphaIHeadline]:
    rows = page.get("results") or []
    out: list[AlphaIHeadline] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_news_row(row)
        if parsed is not None:
            out.append(parsed)
    return out


def parse_news_row(row: dict[str, Any]) -> AlphaIHeadline | None:
    original = row.get("original") or {}
    enrichment = row.get("enrichment") or {}
    if not isinstance(original, dict) or not isinstance(enrichment, dict):
        return None
    uid = str(original.get("uid") or "")
    title = str(original.get("title") or "").strip()
    if not uid or not title:
        return None
    relevance = int(enrichment.get("relevance_score") or 0)
    category = str(enrichment.get("category") or "other")
    tickers_raw = enrichment.get("tickers") or []
    tickers = tuple(str(t) for t in tickers_raw if t)
    sentiments = _extract_sentiments(enrichment)
    return AlphaIHeadline(
        uid=uid,
        title=title,
        relevance=relevance,
        category=category,
        tickers=tickers,
        sentiments=sentiments,
        published_at=_str_or_none(original.get("time_published")),
        source_domain=_str_or_none(original.get("source_domain")),
    )


def build_regime_from_headlines(
    headlines: list[AlphaIHeadline],
    *,
    min_relevance: int,
    block_bearish_bases: bool,
    macro_reduce_only: bool,
    focus_bases: set[str],
    observation_mode: bool,
) -> AlphaIRegimeState:
    blocked: set[str] = set()
    bullish: set[str] = set()
    blocked_detail: dict[str, str] = {}
    macro_ro = False
    public_headlines: list[dict[str, Any]] = []

    for h in sorted(headlines, key=lambda x: x.relevance, reverse=True):
        if h.relevance < min_relevance:
            continue
        public_headlines.append(
            {
                "uid": h.uid,
                "title": h.title[:160],
                "relevance": h.relevance,
                "category": h.category,
                "tickers": list(h.tickers),
                "sentiments": dict(h.sentiments),
            }
        )
        if macro_reduce_only and h.category in {
            "macro_economy",
            "regulation",
            "geopolitics",
        }:
            if _headline_macro_bearish(h):
                macro_ro = True
                blocked_detail["_MACRO_"] = h.title[:120]
        for base in h.bullish_bases():
            if focus_bases and base not in focus_bases:
                continue
            if base not in blocked:
                bullish.add(base)
        if not block_bearish_bases:
            continue
        for base in h.bearish_bases():
            if focus_bases and base not in focus_bases:
                continue
            blocked.add(base)
            bullish.discard(base)
            blocked_detail.setdefault(base, h.title[:120])

    global_ro = macro_ro and not observation_mode
    return AlphaIRegimeState(
        enabled=True,
        global_reduce_only=global_ro,
        macro_reduce_only=macro_ro,
        blocked_bases=frozenset(blocked if not observation_mode else set()),
        bullish_bases=frozenset(bullish),
        headlines=public_headlines[:12],
        blocked_detail=blocked_detail,
        last_poll_at=datetime.now(UTC).isoformat(),
        observation_mode=observation_mode,
    )


def _extract_sentiments(enrichment: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    insights = enrichment.get("ai_trading_insights") or {}
    if isinstance(insights, dict):
        for row in insights.get("ticker_analysis") or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "")
            impact = row.get("impact_analysis") or {}
            if ticker and isinstance(impact, dict):
                sent = str(impact.get("sentiment") or "").lower()
                if sent:
                    out[ticker] = sent
    # Fallback: flat ticker list without per-ticker sentiment
    for ticker in enrichment.get("tickers") or []:
        t = str(ticker)
        if t and t not in out:
            out[t] = "neutral"
    return out


_MACRO_MARKET_KEYWORDS = (
    "fed",
    "fomc",
    "ecb",
    "interest rate",
    "rate hike",
    "rate cut",
    "inflation",
    "cpi",
    "gdp",
    "recession",
    "tariff",
    "sanction",
    "embargo",
    "war",
    "invasion",
    "geopolit",
    "crypto",
    "bitcoin",
    "ethereum",
    "sec",
    "cftc",
    "etf",
    "stablecoin",
    "treasury",
    "yield",
    "bond market",
    "stock market",
    "risk-off",
    "risk off",
)

_MACRO_NOISE_KEYWORDS = (
    "dress code",
    "labor rights",
    "workers'",
    "workplace",
    "employment lawsuit",
    "vaccine",
    "covid",
    "sports",
    "nba",
    "nfl",
    # Legal process noise — not desk-wide risk-off unless crash cues also present.
    "lawsuit",
    "court case",
    "seeks dismissal",
    "dismissed",
    "app tracking",
    "antitrust trial",
)

_MACRO_CRASH_KEYWORDS = (
    "crash",
    "selloff",
    "sell-off",
    "sell off",
    "crackdown",
    "ban crypto",
    "crypto ban",
    "default",
    "collapse",
    "risk-off",
    "risk off",
    "emergency rate",
    "circuit breaker",
    "liquidity crisis",
)


def _title_has_keyword(title: str, keyword: str) -> bool:
    """Substring match for multi-word phrases; token match for short words."""
    key = keyword.lower().strip()
    if not key:
        return False
    if " " in key or "-" in key:
        return key in title
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", title) is not None


def _headline_macro_market_relevant(h: AlphaIHeadline) -> bool:
    """Ignore corporate/labor regulation noise that is not market-moving."""
    title = (h.title or "").lower()
    if any(_title_has_keyword(title, noise) for noise in _MACRO_NOISE_KEYWORDS):
        return False
    if any(_title_has_keyword(title, key) for key in _MACRO_MARKET_KEYWORDS):
        return True
    # Crypto / major risk assets in the ticker set.
    for ticker in h.tickers:
        base = _base_from_alphai_ticker(str(ticker))
        if base in {
            "BTC",
            "ETH",
            "SOL",
            "XRP",
            "BNB",
            "ADA",
            "AVAX",
            "LINK",
            "DOGE",
            "SPY",
            "QQQ",
            "ES",
            "NQ",
            "DXY",
            "USD",
            "EUR",
        }:
            return True
    if h.category == "macro_economy":
        return True
    # Regulation/geopolitics without market/crypto cues is not desk macro.
    return False


def _headline_macro_bearish(h: AlphaIHeadline) -> bool:
    """True only for market-relevant macro risk that is actually bearish.

    Regulation/geopolitics headlines must show bearish sentiment or crash cues.
    Relevance alone is not enough (e.g. CFTC lawsuit dismissal ≠ desk reduce-only).
    """
    if not _headline_macro_market_relevant(h):
        return False
    if any(_is_bearish(s) for s in h.sentiments.values()):
        return True
    title = (h.title or "").lower()
    if any(_title_has_keyword(title, key) for key in _MACRO_CRASH_KEYWORDS):
        return True
    # High-relevance macro_economy still needs stress language; avoid idle RO.
    return False


def _is_bearish(sentiment: str) -> bool:
    return sentiment.lower() in {"bearish", "negative", "very_bearish"}


def _is_bullish(sentiment: str) -> bool:
    return sentiment.lower() in {"bullish", "positive", "very_bullish"}


def _base_from_alphai_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith("-USD"):
        return t.removesuffix("-USD")
    return t


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
