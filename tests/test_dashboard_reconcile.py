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
            "venue": "bitvavo",
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
            "venue": "bitvavo",
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


def test_replay_fifo_does_not_cross_match_bases() -> None:
    """SOL buy must not fund an ATOM sell (regression: mixed-coin Geïnd crash)."""
    since = datetime(2026, 8, 31, 0, 0, 0, tzinfo=UTC)
    since_ms = int(since.timestamp() * 1000)
    t_buy = since_ms + 60_000
    t_sell = since_ms + 120_000
    trades = [
        {
            "ts_ms": t_buy,
            "ts": datetime.fromtimestamp(t_buy / 1000, tz=UTC),
            "base": "SOL",
            "venue": "bitvavo",
            "side": "buy",
            "qty": Decimal("1"),
            "price": Decimal("100"),
            "fee_amt": Decimal("0"),
            "fee_cur": "EUR",
        },
        {
            "ts_ms": t_sell,
            "ts": datetime.fromtimestamp(t_sell / 1000, tz=UTC),
            "base": "ATOM",
            "venue": "bitvavo",
            "side": "sell",
            "qty": Decimal("1"),
            "price": Decimal("1.2"),
            "fee_amt": Decimal("0"),
            "fee_cur": "EUR",
        },
    ]
    before, since_pnl, sells, fills = _replay_fifo(trades, since_ms=since_ms)
    assert before == Decimal("0")
    assert since_pnl == Decimal("0")
    assert sells == []
    # buy still counts as a fill-since; unmatched sell is skipped
    assert fills == 1


def test_replay_fifo_separates_venues_same_base() -> None:
    since = datetime(2026, 8, 31, 0, 0, 0, tzinfo=UTC)
    since_ms = int(since.timestamp() * 1000)
    trades = [
        {
            "ts_ms": since_ms + 1,
            "ts": datetime.fromtimestamp((since_ms + 1) / 1000, tz=UTC),
            "base": "DOT",
            "venue": "bitvavo",
            "side": "buy",
            "qty": Decimal("10"),
            "price": Decimal("5"),
            "fee_amt": Decimal("0"),
            "fee_cur": "EUR",
        },
        {
            "ts_ms": since_ms + 2,
            "ts": datetime.fromtimestamp((since_ms + 2) / 1000, tz=UTC),
            "base": "DOT",
            "venue": "okx",
            "side": "sell",
            "qty": Decimal("10"),
            "price": Decimal("6"),
            "fee_amt": Decimal("0"),
            "fee_cur": "EUR",
        },
    ]
    _, since_pnl, sells, _ = _replay_fifo(trades, since_ms=since_ms)
    # OKX sell must not consume Bitvavo buy → no matched PnL
    assert since_pnl == Decimal("0")
    assert sells == []
