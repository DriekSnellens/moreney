"""Period net PnL for dual-sleeve desk (Amsterdam calendar)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_period_pnl import (
    compute_desk_earnings,
    load_exit_fills,
    operator_month_start,
    operator_week_start,
    sum_period,
)


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_period_sums_week_month_all_time(tmp_path: Path) -> None:
    now = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    week0 = operator_week_start(now)
    month0 = operator_month_start(now)
    assert week0.astimezone(UTC).weekday() == 0  # Monday
    assert month0.day == 1 or month0.astimezone(
        __import__("zoneinfo").ZoneInfo("Europe/Amsterdam")
    ).day == 1

    rows = [
        # previous month — counts only in all-time
        {
            "ts": "2026-08-20T10:00:00+00:00",
            "event": "exit",
            "base": "AAA",
            "net_eur": 10.0,
        },
        # this month, before this week
        {
            "ts": "2026-09-02T10:00:00+00:00",
            "event": "exit",
            "base": "BBB",
            "net_eur": 5.0,
        },
        # this week
        {
            "ts": "2026-09-10T10:00:00+00:00",
            "event": "exit",
            "base": "CCC",
            "net_eur": 7.5,
        },
        {"ts": "2026-09-11T10:00:00+00:00", "event": "entry", "base": "DDD"},
    ]
    path = tmp_path / "core.jsonl"
    _write_ledger(path, rows)
    exits = load_exit_fills(path)
    assert len(exits) == 3
    period = sum_period(exits, now=now)
    assert period.week_eur == 7.5
    assert period.month_eur == 12.5
    assert period.all_time_eur == 22.5
    assert period.trades_week == 1
    assert period.trades_month == 2
    assert period.trades_all_time == 3


def test_combined_desk_earnings(tmp_path: Path) -> None:
    core = tmp_path / "core.jsonl"
    vol = tmp_path / "vol.jsonl"
    _write_ledger(
        core,
        [{"ts": "2026-09-11T10:00:00+00:00", "event": "exit", "base": "NEAR", "net_eur": 40.0}],
    )
    _write_ledger(
        vol,
        [{"ts": "2026-09-11T16:00:00+00:00", "event": "exit", "base": "WLD", "net_eur": -2.5}],
    )
    earn = compute_desk_earnings(
        core_ledger_path=core,
        volatile_ledger_path=vol,
        core_status={"unrealized_net_eur": 1.0},
        volatile_status={"unrealized_net_eur": 0.5},
        now=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    assert earn.combined.week_eur == 37.5
    assert earn.combined.all_time_eur == 37.5
    assert earn.open_mtm_eur == 1.5


def test_dashboard_shows_period_earnings(tmp_path: Path) -> None:
    from bot.live.momentum_dashboard import render_momentum_dashboard
    from bot.live.momentum_period_pnl import compute_desk_earnings

    core = tmp_path / "c.jsonl"
    _write_ledger(
        core,
        [{"ts": "2026-09-11T10:00:00+00:00", "event": "exit", "base": "UNI", "net_eur": 12.34}],
    )
    earn = compute_desk_earnings(
        core_ledger_path=core,
        volatile_ledger_path=None,
        now=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    html = render_momentum_dashboard(
        {
            "running": True,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 1000,
            "exposure_eur": 0,
            "equity_eur": 1000,
            "realized_total_eur": 12.34,
            "trade_count": 1,
            "unrealized_net_eur": 0,
            "next_decision": "2026-09-12T16:00:00+00:00",
        },
        [],
        earnings=earn,
    ).body.decode()
    assert "Moreney" in html
    assert "Netto verdiend" in html
    assert "Deze week" in html and "Deze maand" in html and "Vanaf begin" in html
    assert "+12.34" in html
    assert "Syne" in html  # new brand font
