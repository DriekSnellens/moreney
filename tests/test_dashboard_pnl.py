"""Exchange-FIFO calendar PnL for dashboard KPIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.live.dashboard_history import (
    chart_history_points,
    metrics_from_payload,
)
from bot.live.dashboard_pnl import (
    calendar_pnl_for_metrics,
    clear_calendar_pnl_cache,
    refresh_calendar_pnl_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bot.live.dashboard_pnl._CACHE_PATH",
        tmp_path / "pnl_cache.json",
    )
    monkeypatch.setattr(
        "bot.live.dashboard_pnl._ANCHOR_PATH",
        tmp_path / "pnl_anchor.json",
    )
    clear_calendar_pnl_cache()
    from bot.live.dashboard_pnl import clear_operator_pnl_anchor

    clear_operator_pnl_anchor()
    yield
    clear_calendar_pnl_cache()
    clear_operator_pnl_anchor()


def test_operator_pnl_anchor_zeros_until_refresh(tmp_path, monkeypatch) -> None:
    from bot.live.dashboard_pnl import (
        get_operator_pnl_anchor,
        set_operator_pnl_anchor,
        get_calendar_pnl_cache,
        _effective_since,
    )
    from bot.live.dashboard_history import operator_day_start_utc

    when = datetime(2026, 8, 31, 21, 45, tzinfo=UTC)
    set_operator_pnl_anchor(when)
    assert get_operator_pnl_anchor() == when
    cache = get_calendar_pnl_cache()
    assert cache["daily_eur"] == "0.00"
    assert cache["source"] == "operator_reset"
    # Anchor after day start → Geïnd window starts at anchor
    day = operator_day_start_utc()
    assert _effective_since(day) == when


def test_chart_history_points_drops_reconcile_stairs() -> None:
    history = [
        {"t": "2026-08-30T12:00:00+00:00", "realized_pnl_eur": "0", "unrealized_eur": "0", "winnable_eur": "0"},
        {"t": "2026-08-30T13:00:00+00:00", "realized_pnl_eur": "50", "unrealized_eur": "0", "winnable_eur": "0"},
        {"t": "2026-08-31T11:00:00+00:00", "realized_pnl_eur": "160", "unrealized_eur": "-3", "winnable_eur": "0.02"},
    ]
    cleaned = chart_history_points(history)
    assert len(cleaned) == 1
    assert cleaned[0]["realized_pnl_eur"] == "160"


@pytest.mark.asyncio
async def test_refresh_calendar_pnl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = MagicMock()
    bridge._quote = "EUR"
    bridge._execute_venues = {"bitvavo"}
    bridge._exclude_bases = set()
    bridge._allowed_bases = None
    bridge._venue_raw_balances = {"bitvavo": []}

    async def fake_compute(_bridge, since, *, seed_days=21):
        # Distinct values so day/week paths are exercised separately.
        if since.hour == 22 and since.minute == 0:
            return Decimal("65.09"), []
        return Decimal("65.09"), []

    monkeypatch.setattr(
        "bot.live.dashboard_pnl.compute_realized_since",
        fake_compute,
    )
    out = await refresh_calendar_pnl_cache(bridge, force=True)
    assert out["source"] == "exchange_fifo"
    assert Decimal(str(out["daily_eur"])) == Decimal("65.09")
    daily, weekly, source = calendar_pnl_for_metrics()
    assert source == "exchange_fifo"
    assert daily == Decimal("65.09")


def test_metrics_from_payload_prefers_exchange_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.live.dashboard_pnl.calendar_pnl_for_metrics",
        lambda: (Decimal("65.09"), Decimal("65.09"), "exchange_fifo"),
    )
    metrics = metrics_from_payload(
        {
            "session": {
                "running": True,
                "realized_trade_pnl_eur": "164.82",
                "bridge": {
                    "realized_trade_pnl_eur": "164.82",
                    "unrealized_mtm_eur": "-5.09",
                },
            }
        }
    )
    assert metrics["daily_realized_eur"] == pytest.approx(65.09)
    assert metrics["harvested_today_eur"] == pytest.approx(65.09)
    assert metrics["open_unrealized_eur"] == pytest.approx(-5.09)
    assert metrics["portfolio_pnl_eur"] == pytest.approx(60.0)
    assert metrics["daily_realized_source"] == "exchange_fifo"
