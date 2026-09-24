"""Period net PnL for dual-sleeve desk (Amsterdam calendar)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_period_pnl import (
    compute_desk_earnings,
    earnings_as_dict,
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
        core_status={"unrealized_net_eur": 1.0, "dry_run": False},
        volatile_status={"unrealized_net_eur": 0.5, "dry_run": False},
        now=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    assert earn.combined.week_eur == 37.5
    assert earn.combined.all_time_eur == 37.5
    assert earn.paper.all_time_eur == 0.0
    assert earn.open_mtm_eur == 1.5
    assert earn.paper_open_mtm_eur == 0.0


def test_paper_donchian_stays_out_of_live_net(tmp_path: Path) -> None:
    core = tmp_path / "core.jsonl"
    dc = tmp_path / "donch.jsonl"
    _write_ledger(core, [])
    _write_ledger(
        dc,
        [
            {
                "ts": "2026-09-21T08:16:57+00:00",
                "event": "exit",
                "base": "NEAR",
                "sleeve": "donch_fri10",
                "net_eur": 36.87,
                "dry_run": True,
                "venue": "paper",
            },
            {
                "ts": "2026-09-21T08:17:04+00:00",
                "event": "exit",
                "base": "AVAX",
                "sleeve": "donch_fri10",
                "net_eur": -121.27,
                "dry_run": True,
                "venue": "paper",
            },
        ],
    )
    earn = compute_desk_earnings(
        core_ledger_path=core,
        donchian_ledger_path=dc,
        donchian_status={
            "unrealized_net_eur": 12.5,
            "realized_total_eur": -84.4,
            "dry_run": True,
            "paper_only": True,
        },
        now=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )
    assert earn.donchian is not None
    assert earn.donchian.week_eur == -84.4
    assert earn.donchian.trades_week == 2
    assert earn.combined.week_eur == 0.0
    assert earn.combined.all_time_eur == 0.0
    assert earn.paper.week_eur == -84.4
    assert earn.paper.trades_week == 2
    assert earn.open_mtm_eur == 0.0
    assert earn.paper_open_mtm_eur == 12.5


def test_live_clip_fills_fold_into_combined(tmp_path: Path) -> None:
    core = tmp_path / "core.jsonl"
    clip = tmp_path / "clip.jsonl"
    _write_ledger(
        core,
        [
            {
                "ts": "2026-09-22T10:00:00+00:00",
                "event": "exit",
                "base": "UNI",
                "net_eur": 12.0,
                "dry_run": False,
                "venue": "bitvavo",
            }
        ],
    )
    _write_ledger(
        clip,
        [
            {
                "ts": "2026-09-22T11:00:00+00:00",
                "event": "exit",
                "base": "NEAR",
                "net_eur": 40.0,
                "dry_run": False,
                "venue": "bitvavo",
            }
        ],
    )
    earn = compute_desk_earnings(
        core_ledger_path=core,
        clip_ledger_path=clip,
        core_status={"dry_run": False, "unrealized_net_eur": 1.0},
        clip_status={
            "dry_run": False,
            "allow_live": True,
            "unrealized_net_eur": 8.0,
        },
        now=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
    )
    assert earn.clip is not None
    assert earn.clip.week_eur == 40.0
    assert earn.combined.week_eur == 52.0
    assert earn.paper.week_eur == 0.0
    assert earn.open_mtm_eur == 9.0
    payload = earnings_as_dict(earn)
    assert payload["combined"]["week_eur"] == 52.0
    assert payload["paper"]["week_eur"] == 0.0
    assert payload["clip"]["week_eur"] == 40.0
    assert payload["paper_open_mtm_eur"] == 0.0


def test_explicit_live_fill_stays_live_after_sleeve_goes_paper(tmp_path: Path) -> None:
    clip = tmp_path / "clip.jsonl"
    _write_ledger(
        clip,
        [
            {
                "ts": "2026-09-20T10:00:00+00:00",
                "event": "exit",
                "base": "BTC",
                "net_eur": 25.0,
                "dry_run": False,
                "venue": "bitvavo",
            },
            {
                "ts": "2026-09-21T10:00:00+00:00",
                "event": "exit",
                "base": "LINK",
                "net_eur": 3.0,
                "dry_run": True,
                "venue": "paper",
            },
        ],
    )
    earn = compute_desk_earnings(
        core_ledger_path=None,
        clip_ledger_path=clip,
        clip_status={"dry_run": True, "paper_only": True, "allow_live": False},
        now=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
    )
    assert earn.combined.all_time_eur == 25.0
    assert earn.paper.all_time_eur == 3.0


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
    assert "Plus Jakarta Sans" in html  # stitch brand font
    assert "live venue-fills" in html
    assert "id=\"paper-earn\"" not in html


def test_dashboard_paper_overview_is_separate_from_live_net(tmp_path: Path) -> None:
    from bot.live.momentum_dashboard import render_momentum_dashboard
    from bot.live.momentum_period_pnl import compute_desk_earnings

    core = tmp_path / "c.jsonl"
    dc = tmp_path / "d.jsonl"
    _write_ledger(
        core,
        [
            {
                "ts": "2026-09-11T10:00:00+00:00",
                "event": "exit",
                "base": "UNI",
                "net_eur": 12.34,
                "dry_run": False,
                "venue": "bitvavo",
            }
        ],
    )
    _write_ledger(
        dc,
        [
            {
                "ts": "2026-09-11T11:00:00+00:00",
                "event": "exit",
                "base": "NEAR",
                "net_eur": 88.88,
                "dry_run": True,
                "venue": "paper",
            }
        ],
    )
    earn = compute_desk_earnings(
        core_ledger_path=core,
        donchian_ledger_path=dc,
        core_status={"dry_run": False},
        donchian_status={"dry_run": True, "paper_only": True, "unrealized_net_eur": 5.0},
        now=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
    )
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 2000,
            "exposure_eur": 0,
            "equity_eur": 2000,
            "realized_total_eur": 12.34,
            "trade_count": 1,
            "unrealized_net_eur": 0,
        },
        [],
        earnings=earn,
        donchian={"running": True, "dry_run": True, "positions": [], "sleeves": []},
        donchian_ledger_rows=[],
        allocator={"ok": True, "label": "mid", "why": "test"},
    ).body.decode()
    assert "Netto verdiend" in html
    assert 'id="paper-earn"' in html
    assert "Paper overzicht" in html
    assert "+12.34" in html
    assert "+88.88" in html
    assert "+101.22" not in html
    assert "Telt niet mee in netto verdiend" in html
    live_block = html.split('id="paper-earn"')[0]
    paper_block = html.split('id="paper-earn"')[1]
    assert "+12.34" in live_block
    assert "+88.88" not in live_block
    assert "+88.88" in paper_block
    assert "+12.34" not in paper_block
    assert "21,122" not in html
