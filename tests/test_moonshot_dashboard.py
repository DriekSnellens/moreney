"""MoonShot dashboard title, fixed €2k book, and the live first-breakout entry."""

from __future__ import annotations

from datetime import UTC, datetime

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


def _closed_days(n: int, close: float, *, high: float | None = None, start_ms: int = 1_704_067_200_000):
    """Daily bars in the past so they stay completed."""
    rows = []
    hi = close if high is None else high
    for i in range(n):
        px = close
        rows.append([start_ms + i * 86_400_000, px, hi, px * 0.99, px, 100_000.0])
    return rows


def test_brk20_now_buys_the_live_cross_and_skips_a_finished_breakout():
    from bot.live.momentum_btc_rs_clip import ClipConfig, evaluate_clip

    btc = _closed_days(40, 100.0, high=101.0)
    btc[-1][4] = 110.0
    # SOL: 20d high is 10.20, yesterday closed at 10. A print at 10.35
    # is the first pierce (+3.5% on the day, 1.5% through the high).
    sol = _closed_days(40, 10.0, high=10.0)
    sol[-8][2] = 10.2
    # ETH already closed on its own 20d high yesterday.
    eth = _closed_days(40, 8.0, high=8.0)
    eth[-1][2] = 9.0
    eth[-1][4] = 9.0
    cfg = ClipConfig(
        universe=("SOL", "ETH"),
        entry_mode="brk20_now",
        sma_n=20,
        min_qvol_eur=1.0,
        book_eur=2_000.0,
        size_to_book=True,
        btc_frac=0.0,
        alt_frac=1.0,
        cash_when_no_alt=True,
        excess_floor=0.0,
        max_entry_day_ret=0.08,
        max_break_extension=0.03,
    )
    out = evaluate_clip(
        {"BTC": btc, "SOL": sol, "ETH": eth},
        cfg,
        held={},
        cash_eur=2_000.0,
        deployed_eur=0.0,
        now_ms=1_717_200_000_000,
        last_rebalance_ms=0,
        now=datetime(2024, 6, 1, tzinfo=UTC),
        live_marks={"SOL": 10.35, "ETH": 9.4},
        alphai_picks=("ETH",),
        alphai_avoid=(),
    )
    assert out["want_alt"] == "SOL"
    assert out["entries"][0]["base"] == "SOL"
    assert out["entries"][0]["notional_eur"] == pytest.approx(2_000.0, abs=0.01)
    assert "eerste 20d-breakout" in out["caption"]
    assert "AlphaI" not in out["caption"]


def test_brk20_now_blocks_alphai_avoid_and_marks_a_pick():
    from bot.live.momentum_btc_rs_clip import ClipConfig, evaluate_clip

    btc = _closed_days(40, 100.0, high=101.0)
    btc[-1][4] = 110.0
    sol = _closed_days(40, 10.0, high=10.0)
    sol[-8][2] = 10.2
    ada = _closed_days(40, 5.0, high=5.0)
    ada[-8][2] = 5.15
    cfg = ClipConfig(
        universe=("SOL", "ADA"),
        entry_mode="brk20_now",
        sma_n=20,
        min_qvol_eur=1.0,
        book_eur=2_000.0,
        btc_frac=0.0,
        alt_frac=1.0,
        cash_when_no_alt=True,
        excess_floor=0.0,
        max_entry_day_ret=0.08,
        max_break_extension=0.03,
    )
    blocked = evaluate_clip(
        {"BTC": btc, "SOL": sol, "ADA": ada},
        cfg,
        held={},
        cash_eur=2_000.0,
        deployed_eur=0.0,
        now_ms=1_717_200_000_000,
        last_rebalance_ms=0,
        now=datetime(2024, 6, 1, tzinfo=UTC),
        live_marks={"SOL": 10.35, "ADA": 5.25},
        alphai_avoid=("SOL",),
        alphai_picks=("ADA",),
    )
    assert blocked["want_alt"] == "ADA"
    assert "AlphaI" in blocked["caption"]


def test_brk20_now_skips_a_day_already_up_22pct():
    from bot.live.momentum_btc_rs_clip import ClipConfig, evaluate_clip

    btc = _closed_days(40, 100.0, high=101.0)
    btc[-1][4] = 110.0
    gtc = _closed_days(40, 10.0, high=10.0)
    gtc[-8][2] = 10.2
    cfg = ClipConfig(
        universe=("GTC",),
        entry_mode="brk20_now",
        sma_n=20,
        min_qvol_eur=1.0,
        book_eur=2_000.0,
        btc_frac=0.0,
        alt_frac=1.0,
        cash_when_no_alt=True,
        excess_floor=0.0,
        max_entry_day_ret=0.08,
        max_break_extension=0.03,
    )
    out = evaluate_clip(
        {"BTC": btc, "GTC": gtc},
        cfg,
        held={},
        cash_eur=2_000.0,
        deployed_eur=0.0,
        now_ms=1_717_200_000_000,
        last_rebalance_ms=0,
        now=datetime(2024, 6, 1, tzinfo=UTC),
        live_marks={"GTC": 12.2},
    )
    assert out["want_alt"] is None
    assert out["entries"] == []
    assert any(row.get("reason") == "too_extended" for row in out["skipped"])
