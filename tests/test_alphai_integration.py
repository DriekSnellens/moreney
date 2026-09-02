"""AlphaI news integration — parse, guardrails, webhook."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from pydantic import SecretStr

from bot.core.config import Settings
from bot.integrations.alphai.parse import (
    AlphaIHeadline,
    build_regime_from_headlines,
    parse_news_row,
)
from bot.integrations.alphai.pending import drain_webhook_articles, push_webhook_article
from bot.integrations.alphai.status import alphai_metrics, merge_alphai_status
from bot.integrations.alphai.symbols import alphai_candidates_for_base, eur_symbol_to_base
from bot.integrations.alphai.webhook import verify_webhook_signature
from bot.main import app, reset_risk_singletons
from bot.strategies.maker_inventory import MakerInventoryStrategy
from fastapi.testclient import TestClient


def _sample_article(*, uid: str = "abc123", sentiment: str = "bearish") -> dict:
    return {
        "original": {
            "uid": uid,
            "title": "SEC probes exchange flows",
            "time_published": "2026-09-02T10:00:00Z",
            "source_domain": "example.com",
        },
        "enrichment": {
            "relevance_score": 8,
            "category": "crypto",
            "tickers": ["BTC-USD", "ETH-USD"],
            "ai_trading_insights": {
                "ticker_analysis": [
                    {
                        "ticker": "BTC-USD",
                        "impact_analysis": {"sentiment": sentiment},
                    },
                    {
                        "ticker": "ETH-USD",
                        "impact_analysis": {"sentiment": "neutral"},
                    },
                ]
            },
        },
    }


def test_eur_symbol_to_base_and_candidates() -> None:
    assert eur_symbol_to_base("BTCEUR") == "BTC"
    assert eur_symbol_to_base("DOGEUR") == "DOGE"
    assert "BTC-USD" in alphai_candidates_for_base("BTC")
    assert "MATIC-USD" in alphai_candidates_for_base("POL")


def test_parse_news_row_and_bearish_bases() -> None:
    row = parse_news_row(_sample_article())
    assert row is not None
    assert row.relevance == 8
    assert "BTC-USD" in row.tickers
    assert row.bearish_bases() == {"BTC"}


def test_build_regime_blocks_focus_bases() -> None:
    headline = parse_news_row(_sample_article())  # type: ignore[arg-type]
    state = build_regime_from_headlines(
        [headline],  # type: ignore[list-item]
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"BTC", "ETH"},
        observation_mode=False,
    )
    assert "BTC" in state.blocked_bases
    assert state.blocked_detail.get("BTC")


def test_observation_mode_logs_without_blocking() -> None:
    headline = parse_news_row(_sample_article())  # type: ignore[arg-type]
    state = build_regime_from_headlines(
        [headline],  # type: ignore[list-item]
        min_relevance=7,
        block_bearish_bases=True,
        macro_reduce_only=True,
        focus_bases={"BTC"},
        observation_mode=True,
    )
    assert state.blocked_bases == frozenset()
    assert "BTC" in state.blocked_detail


def test_webhook_signature_roundtrip() -> None:
    secret = "test-secret"
    body = b'{"article":{"original":{"uid":"x","title":"y"}}}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, sig, body)
    assert verify_webhook_signature(secret, f"sha256={sig}", body)
    assert not verify_webhook_signature(secret, "bad", body)


def test_pending_webhook_queue() -> None:
    while drain_webhook_articles():
        pass
    push_webhook_article({"original": {"uid": "q1", "title": "t"}})
    queued = drain_webhook_articles()
    assert len(queued) == 1
    assert drain_webhook_articles() == []


def test_merge_alphai_status_prefers_bridge_blocks() -> None:
    merged = merge_alphai_status(
        {"alphai": {"polls": 3, "headlines": [{"title": "h"}]}},
        {
            "alphai": {"blocked_bases": ["SOL"], "skips": 2},
            "alphai_blocked_bases": ["SOL"],
        },
    )
    assert merged.get("polls") == 3
    assert merged.get("blocked_bases") == ["SOL"]
    assert merged.get("skips") == 2


def test_alphai_metrics_compact() -> None:
    metrics = alphai_metrics(
        {"alphai": {"enabled": True, "observation_mode": False, "polls": 1}},
        {"alphai": {"blocked_bases": ["BTC"], "skips": 4}},
    )
    assert metrics["alphai_enabled"] is True
    assert metrics["alphai_blocked_count"] == 1
    assert metrics["alphai_skips"] == 4


def test_maker_news_blocked_bases_sell_only() -> None:
    settings = Settings(
        execution_mode="paper",
        paper_maker_enabled=True,
        paper_maker_min_profit_eur=0.02,
        paper_maker_min_net_return=0.0001,
        paper_maker_min_notional_eur=0.5,
        paper_maker_venues="bitvavo",
    )
    maker = MakerInventoryStrategy(settings)
    maker.set_news_blocked_bases({"BTC"})
    assert maker._symbol_sell_only("BTCEUR") is True
    assert maker._symbol_sell_only("ETHEUR") is False


def test_alphai_webhook_endpoint_rejects_without_secret() -> None:
    reset_risk_singletons()
    client = TestClient(app)
    settings = Settings(alphai_enabled=True, alphai_webhook_secret=None)
    from bot.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("bot.main.get_settings", lambda: settings)
        resp = client.post(
            "/integrations/alphai/webhook",
            content=json.dumps({"article": _sample_article()}),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 503


def test_alphai_webhook_accepts_valid_signature() -> None:
    reset_risk_singletons()
    client = TestClient(app)
    secret = "whsec-test"
    body = json.dumps({"article": _sample_article(uid="wh1")}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    settings = Settings(
        alphai_enabled=True,
        alphai_webhook_secret=SecretStr(secret),
    )
    from bot.core.config import get_settings

    get_settings.cache_clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("bot.main.get_settings", lambda: settings)
        resp = client.post(
            "/integrations/alphai/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Alphai-Signature": sig,
            },
        )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
