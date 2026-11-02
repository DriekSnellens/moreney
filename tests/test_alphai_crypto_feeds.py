"""AlphaI crypto-feed archive rules. No network."""

from __future__ import annotations

from bot.integrations.alphai.client import AlphaIClient
from bot.research.alphai_crypto_feeds.archive import _inside_horizon
from bot.research.alphai_crypto_feeds.features import allow_map, classify_window


def _row(
    uid: str,
    published: str,
    ticker: str,
    sentiment: str,
    *,
    relevance: int = 8,
) -> dict[str, object]:
    return {
        "uid": uid,
        "published": published,
        "relevance": relevance,
        "category": "crypto",
        "sentiments": {ticker: sentiment},
        "title": uid,
    }


def test_horizon_refusal_steps_one_day_inside() -> None:
    error = (
        'AlphaI HTTP 403: {"message":"older than your plan",'
        '"extra":{"reason":"archive_horizon","archive_days":90}}'
    )
    assert _inside_horizon("2026-11-02", error) == "2026-08-05"


def test_list_news_sends_the_date_window() -> None:
    client = AlphaIClient("test-key")
    seen: dict[str, object] = {}

    def _fake(method: str, path: str, params: dict[str, object] | None = None) -> dict:
        seen["method"] = method
        seen["path"] = path
        seen["params"] = params
        return {"results": []}

    client._request = _fake  # type: ignore[method-assign]
    client.list_news(from_date="2026-05-01", to_date="2026-05-08", category="crypto")
    params = seen["params"]
    assert isinstance(params, dict)
    assert params["from_date"] == "2026-05-01"
    assert params["to_date"] == "2026-05-08"
    assert params["category"] == "crypto"
    assert params["collapse"] == "story"


def test_signals_snapshot_asks_for_crypto() -> None:
    client = AlphaIClient("test-key")
    seen: dict[str, object] = {}

    def _fake(method: str, path: str, params: dict[str, object] | None = None) -> dict:
        seen["path"] = path
        seen["params"] = params
        return {"results": []}

    client._request = _fake  # type: ignore[method-assign]
    client.signals_snapshot(market="crypto", window="24h", limit=20)
    assert seen["path"] == "/api/signals/snapshot/"
    params = seen["params"]
    assert isinstance(params, dict)
    assert params["market"] == "crypto"
    assert params["window"] == "24h"


def test_missing_archive_day_is_unfiltered() -> None:
    rows = [
        _row("a", "2026-06-02T12:00:00+00:00", "SOL-USD", "positive"),
    ]
    classified = classify_window(
        rows,
        decision="2026-06-01",
        days=1,
        universe={"SOL", "ETH"},
    )
    assert classified is None
    allow = allow_map(
        rows,
        ["2026-06-01", "2026-06-02"],
        days=1,
        mode="gate",
        universe={"SOL", "ETH"},
    )
    assert "2026-06-01" not in allow
    assert allow["2026-06-02"] == {"SOL"}


def test_bearish_only_name_is_avoided_and_not_a_buy() -> None:
    rows = [
        _row("bull", "2026-06-02T08:00:00+00:00", "SOL-USD", "positive"),
        _row("bear", "2026-06-02T09:00:00+00:00", "ETH-USD", "negative", relevance=9),
    ]
    universe = {"SOL", "ETH"}
    buy = allow_map(rows, ["2026-06-02"], days=1, mode="gate", universe=universe)
    avoid = allow_map(rows, ["2026-06-02"], days=1, mode="avoid", universe=universe)
    assert buy["2026-06-02"] == {"SOL"}
    assert "ETH" not in avoid["2026-06-02"]
    assert "SOL" in avoid["2026-06-02"]


def test_seven_day_window_keeps_an_older_headline() -> None:
    rows = [
        _row("old", "2026-06-01T08:00:00+00:00", "NEAR-USD", "positive"),
    ]
    allow = allow_map(
        rows,
        ["2026-06-07"],
        days=7,
        mode="intersect",
        universe={"NEAR"},
    )
    assert allow["2026-06-07"] == {"NEAR"}
    short = allow_map(
        rows,
        ["2026-06-07"],
        days=1,
        mode="intersect",
        universe={"NEAR"},
    )
    assert "2026-06-07" not in short
