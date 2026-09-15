"""Phase A: AlphaI macro noise must not idle the desk without bearish signal."""

from __future__ import annotations

from bot.integrations.alphai.parse import (
    AlphaIHeadline,
    _headline_macro_bearish,
    build_regime_from_headlines,
)


def _h(
    title: str,
    *,
    category: str = "regulation",
    relevance: int = 8,
    sentiments: dict[str, str] | None = None,
    tickers: tuple[str, ...] = ("BTC-USD",),
) -> AlphaIHeadline:
    return AlphaIHeadline(
        uid="t1",
        title=title,
        relevance=relevance,
        category=category,
        tickers=tickers,
        sentiments=sentiments or {},
    )


def test_cftc_lawsuit_dismissal_is_not_macro_ro() -> None:
    h = _h(
        "CFTC Seeks Dismissal of CME's Crypto Futures Lawsuit, Citing Lack of Standing",
        category="regulation",
        relevance=8,
    )
    assert _headline_macro_bearish(h) is False
    state = build_regime_from_headlines(
        [h],
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"BTC", "ETH", "SOL"},
        observation_mode=False,
    )
    assert state.macro_reduce_only is False


def test_crypto_crash_headline_is_macro_ro() -> None:
    h = _h(
        "Bitcoin sell-off deepens as crypto risk-off hits majors",
        category="macro_economy",
        relevance=9,
        sentiments={"BTC-USD": "bearish"},
    )
    assert _headline_macro_bearish(h) is True


def test_regulation_relevance_alone_not_enough() -> None:
    h = _h(
        "SEC updates crypto custody FAQ for brokers",
        category="regulation",
        relevance=9,
        sentiments={"BTC-USD": "neutral"},
    )
    assert _headline_macro_bearish(h) is False
