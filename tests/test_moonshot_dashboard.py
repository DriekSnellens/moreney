"""MoonShot dashboard title and fixed €2k book cap."""

from __future__ import annotations

import pytest

from bot.live.momentum_btc_rs_clip import ClipConfig
from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner
from bot.live.momentum_dashboard import render_momentum_dashboard


def _runner(tmp_path, *, book: float = 2_000.0) -> BtcRsClipPaperRunner:
    runner = BtcRsClipPaperRunner(
        ClipConfig(book_eur=book),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        dry_run=False,
    )
    runner.pin_cash_to_book = True
    runner.cash_eur = book
    return runner


def test_dashboard_names_live_sleeve_moonshot():
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 1},
            "positions": [],
            "equity_eur": 20_000.0,
            "unrealized_net_eur": 0.0,
            "realized_total_eur": 0.0,
        },
        [],
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 19_000.0,
            "book_eur": 19_000.0,
            "cash_eur": 19_000.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 0.0,
            "positions": [],
            "equity_curve": [],
        },
        show_moonshot=True,
        moonshot={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "book_eur": 2_000.0,
            "equity_eur": 2_000.0,
            "cash_eur": 2_000.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 0.0,
            "positions": [],
            "risk_on": True,
            "pack": "daily_brk20_day",
            "live_caption": "Daily sleeve €2,000: geen alt",
            "config": {"trail_pct": 0.12, "hard_stop_pct": 0.05},
        },
    ).body.decode()
    assert "<h2>MoonShot</h2>" in html
    assert 'id="moonshot"' in html
    assert "LIVE" in html
    assert "2,000.00 €" in html
    assert "PAPER" not in html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]


def test_dashboard_paper_moonshot_is_not_live():
    html = render_momentum_dashboard(
        {
            "running": False,
            "dry_run": True,
            "venues": ["bitvavo"],
            "config": {},
            "positions": [],
            "equity_eur": 0.0,
            "unrealized_net_eur": 0.0,
        },
        [],
        show_moonshot=True,
        moonshot={
            "running": True,
            "dry_run": True,
            "allow_live": False,
            "book_eur": 2_000.0,
            "equity_eur": 1_700.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 12.0,
            "positions": [{"base": "SKY", "quantity": 1, "notional_eur": 100, "unrealized_net_eur": 12}],
        },
    ).body.decode()
    panel = html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]
    assert "<h2>MoonShot</h2>" in panel
    assert "PAPER" in panel
    assert "LIVE" not in panel
    assert "SKY" in panel


def test_pinned_book_status_stays_at_configured_book(tmp_path):
    runner = _runner(tmp_path)
    runner.cash_eur = 19_196.0
    status = runner.status()
    assert status["book_eur"] == 2_000.0
    assert status["dry_run"] is False


@pytest.mark.asyncio
async def test_pinned_cash_does_not_adopt_venue_leftover(tmp_path):
    runner = _runner(tmp_path)
    runner._reserved_quote_eur = 0.0

    async def venue_eur() -> float:
        return 19_196.0

    runner._venue_quote_eur = venue_eur  # type: ignore[method-assign]
    cash = await runner._decision_cash()
    assert cash == 2_000.0
    assert runner.cash_eur == 2_000.0

    async def tight_venue() -> float:
        return 500.0

    runner._venue_quote_eur = tight_venue  # type: ignore[method-assign]
    cash = await runner._decision_cash()
    assert cash == 500.0
    assert runner.cash_eur == 500.0
