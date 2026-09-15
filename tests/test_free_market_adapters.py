"""Unit tests for free public OKX / Bybit market-data adapters."""

from __future__ import annotations

import json

from bot.market_data.adapters.bybit import BybitPublicAdapter
from bot.market_data.adapters.okx import OkxPublicAdapter


def test_okx_subscribe_and_books5_snapshot() -> None:
    adapter = OkxPublicAdapter(["BTCEUR", "BTCUSDT"])
    msgs = adapter.build_subscribe_messages()
    assert len(msgs) == 1
    payload = json.loads(msgs[0])
    assert payload["op"] == "subscribe"
    assert {"channel": "books5", "instId": "BTC-EUR"} in payload["args"]

    raw = json.dumps(
        {
            "arg": {"channel": "books5", "instId": "BTC-EUR"},
            "action": "snapshot",
            "data": [
                {
                    "asks": [["100100", "0.5", "0", "1"]],
                    "bids": [["100000", "0.4", "0", "1"]],
                    "ts": "1710000000000",
                    "seqId": 12,
                }
            ],
        }
    )
    events = adapter.parse_message(raw)
    assert len(events) == 1
    assert events[0].symbol == "BTCEUR"
    assert events[0].book_update is not None
    assert events[0].book_update.is_snapshot is True
    assert events[0].tick is not None
    assert events[0].tick.bid > 0


def test_bybit_skips_eur_and_parses_usdt_book() -> None:
    adapter = BybitPublicAdapter(["BTCEUR", "BTCUSDT"])
    msgs = adapter.build_subscribe_messages()
    assert len(msgs) == 1
    payload = json.loads(msgs[0])
    assert payload["args"] == ["orderbook.50.BTCUSDT"]

    raw = json.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "data": {
                "s": "BTCUSDT",
                "b": [["65000", "0.2"]],
                "a": [["65010", "0.3"]],
                "u": 1,
                "seq": 99,
                "ts": 1710000000000,
            },
        }
    )
    events = adapter.parse_message(raw)
    assert len(events) == 1
    assert events[0].symbol == "BTCUSDT"
    assert events[0].book_update is not None
    assert events[0].tick is not None
