"""Dashboard AlphaI tone: market, holdings, and the bullish ranking."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bot.integrations.alphai.outlook import (
    build_outlook,
    classify_tone,
    refresh_outlook,
    render_alphai_outlook,
)
from bot.live.momentum_dashboard import render_momentum_dashboard


def test_tone_follows_the_heavier_side() -> None:
    assert classify_tone(94, 363) == "bearish"
    assert classify_tone(40, 10) == "bullish"
    assert classify_tone(3, 3) == "neutral"
    assert classify_tone(0, 0) == "neutral"


def test_picks_are_ranked_by_score_and_holdings_keep_their_tone() -> None:
    view = build_outlook(
        sentiment={
            "BTC": {"bullish": 94, "bearish": 363, "neutral": 10},
            "SOL": {"bullish": 43, "bearish": 102, "neutral": 4},
            "XRP": {"bullish": 80, "bearish": 20, "neutral": 1},
            "ADA": {"bullish": 1, "bearish": 4, "neutral": 0},
        },
        report={
            "picks": [
                {"base": "ADA", "score": 12, "note": "lager"},
                {"base": "XRP", "score": 42, "note": "hoger"},
            ],
            "avoid": [{"base": "OP"}],
            "session_id": "2026-11-02T14:00",
        },
        portfolio=["SOL"],
        fetched_at="2026-11-02T12:15:00+00:00",
    )
    assert view["market"]["tone"] == "bearish"
    assert view["portfolio"][0]["base"] == "SOL"
    assert view["portfolio"][0]["tone"] == "bearish"
    assert [row["base"] for row in view["picks"]] == ["XRP", "ADA"]
    assert view["picks"][0]["rank"] == 1
    assert view["fetched_label"] == "2 nov 2026, 13:15 Amsterdam"
    html = render_alphai_outlook(view)
    assert "Bearish" in html
    assert "SOL" in html
    assert "meest bullish" in html
    assert html.index("XRP") < html.index("ADA")
    assert "Vermijden: OP" in html


def test_cash_book_says_the_portfolio_is_empty() -> None:
    view = build_outlook(
        sentiment={"BTC": {"bullish": 1, "bearish": 5, "neutral": 0}},
        report={"picks": []},
        portfolio=[],
        fetched_at="2026-11-02T12:15:00+00:00",
    )
    html = render_alphai_outlook(view)
    assert "Geen munt in de portefeuille" in html
    assert "AlphaI noemt nu geen munt als bullish" in html


def test_fresh_cache_does_not_call_alphai(tmp_path) -> None:
    view = build_outlook(
        sentiment={
            "BTC": {"bullish": 1, "bearish": 2, "neutral": 0},
            "SOL": {"bullish": 1, "bearish": 3},
        },
        report={"picks": [{"base": "SOL", "score": 5}]},
        portfolio=["SOL"],
        fetched_at=datetime.now(UTC).isoformat(),
    )
    path = tmp_path / "outlook.json"
    path.write_text(
        json.dumps(
            {
                "version": 3,
                "fetched_at": view["fetched_at"],
                "sentiment": {
                    "SOL": {"bullish": 1, "bearish": 3},
                    "BTC": {"bullish": 1, "bearish": 2},
                },
                "view": view,
            }
        ),
        encoding="utf-8",
    )

    class _Client:
        def sentiment_summary(self, ticker: str) -> dict:
            raise AssertionError(ticker)

    got = refresh_outlook(
        object(),
        ["SOL"],
        path=path,
        client=_Client(),
        allow_network=True,
    )
    assert got["portfolio"][0]["base"] == "SOL"
    empty = refresh_outlook(
        object(),
        [],
        path=path,
        client=_Client(),
        allow_network=True,
    )
    assert empty["portfolio"] == []


def test_momentum_dashboard_shows_the_outlook_panel() -> None:
    view = build_outlook(
        sentiment={"BTC": {"bullish": 2, "bearish": 9, "neutral": 0}},
        report={"picks": [{"base": "LINK", "score": 8}]},
        portfolio=[],
        fetched_at="2026-11-02T12:15:00+00:00",
    )
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": True,
            "venues": ["bitvavo"],
            "config": {"max_positions": 1},
            "positions": [],
            "equity_eur": 20_000.0,
            "unrealized_net_eur": 0.0,
        },
        [],
        alphai_outlook=view,
    ).body.decode()
    assert "id='alphai-outlook'" in html
    assert "Bearish" in html
    assert "LINK" in html
    assert 'href="/live/momentum#alphai-outlook"' in html
