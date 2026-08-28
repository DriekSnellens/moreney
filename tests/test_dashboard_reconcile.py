"""Tests for dashboard reconciliation from exchange trade history."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bot.live.dashboard_reconcile import _replay_fifo


def test_replay_fifo_realized_since_midday() -> None:
    since = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    since_ms = int(since.timestamp() * 1000)
    t0 = since_ms - 3600_000
    t1 = since_ms + 60_000
    trades = [
        {
            "ts_ms": t0,
            "ts": datetime.fromtimestamp(t0 / 1000, tz=UTC),
            "base": "SOL",
            "side": "buy",
            "qty": Decimal("2"),
            "price": Decimal("90"),
            "fee_amt": Decimal("0.18"),
            "fee_cur": "EUR",
        },
        {
            "ts_ms": t1,
            "ts": datetime.fromtimestamp(t1 / 1000, tz=UTC),
            "base": "SOL",
            "side": "sell",
            "qty": Decimal("1"),
            "price": Decimal("91"),
            "fee_amt": Decimal("0.23"),
            "fee_cur": "EUR",
        },
    ]
    before, since_pnl, sells, fills = _replay_fifo(trades, since_ms=since_ms)
    assert before == Decimal("0")
    assert fills == 1
    assert len(sells) == 1
    assert since_pnl > Decimal("0")
