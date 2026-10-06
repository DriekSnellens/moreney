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
    # Flat MoonShot cash already sits in the clip balance, so the headline
    # stays the account (19,000) and the two sleeve lines are 17,000 + 2,000.
    assert 'data-live="equity-total"' in html
    assert "19,000.00 €" in html
    assert "17,000.00 €" in html
    assert "PAPER" not in html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]
    panel = html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]
    assert 'data-eq-desk="moonshot"' in panel


def test_dashboard_headline_adds_both_sleeves_when_moonshot_holds_a_coin():
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {},
            "positions": [],
            "equity_eur": 0.0,
            "unrealized_net_eur": 0.0,
        },
        [],
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 17_202.0,
            "book_eur": 17_202.0,
            "cash_eur": 17_202.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 4.0,
            "positions": [],
            "equity_curve": [[1.0, 19_196.0], [2.0, 17_202.0]],
            "risk_on": True,
        },
        show_moonshot=True,
        moonshot={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "book_eur": 2_000.0,
            "equity_eur": 2_006.0,
            "cash_eur": 5.56,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 6.0,
            "positions": [
                {"base": "GTC", "quantity": 100, "notional_eur": 2000.44, "unrealized_net_eur": 6}
            ],
            "risk_on": True,
            "equity_curve": [[1.0, 2_000.0], [2.0, 2_006.0]],
            "pack": "brk20_now",
            "config": {"trail_pct": 0.12, "hard_stop_pct": 0.05},
        },
    ).body.decode()
    # 17,202 - 5.56 cash already in the clip + MoonShot 2,006 = 19,202.44
    assert "19,202.44 €" in html
    assert "17,196.44 €" in html
    assert "2,006.00 €" in html
    assert "+10.00 €" in html
    assert html.count('data-live="equity-total"') >= 3
    panel = html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]
    assert 'data-eq-desk="moonshot"' in panel
    assert "<svg" in panel
    assert "GTC" in panel


def test_dashboard_shows_both_sleeve_profits_and_live_hooks(tmp_path):
    from bot.live.momentum_dashboard import combine_ledgers
    from bot.live.momentum_period_pnl import compute_desk_earnings

    clip = tmp_path / "clip.jsonl"
    moon = tmp_path / "moon.jsonl"
    clip.write_text(
        '{"ts":"2026-10-06T08:00:00+00:00","event":"exit","base":"BTC",'
        '"net_eur":40,"dry_run":false,"venue":"bitvavo","reason":"trail"}\n'
        '{"ts":"2026-10-06T08:05:00+00:00","event":"entry","base":"NEAR",'
        '"entry_price":2.5,"notional_eur":800,"reason":"rebalance"}\n',
        encoding="utf-8",
    )
    moon.write_text(
        '{"ts":"2026-10-06T09:00:00+00:00","event":"exit","base":"GTC",'
        '"net_eur":15.5,"dry_run":false,"venue":"bitvavo","reason":"manual_external"}\n',
        encoding="utf-8",
    )
    earn = compute_desk_earnings(
        core_ledger_path=None,
        clip_ledger_path=clip,
        moonshot_ledger_path=moon,
        clip_status={"dry_run": False, "allow_live": True, "unrealized_net_eur": 4},
        moonshot_status={"dry_run": False, "allow_live": True, "unrealized_net_eur": 6},
        now=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
    )
    clip_rows = [
        {
            "ts": "2026-10-06T08:00:00+00:00",
            "event": "exit",
            "base": "BTC",
            "net_eur": 40,
            "reason": "trail",
            "venue": "bitvavo",
        },
        {
            "ts": "2026-10-06T08:05:00+00:00",
            "event": "entry",
            "base": "NEAR",
            "entry_price": 2.5,
            "notional_eur": 800,
            "reason": "rebalance",
            "venue": "bitvavo",
        },
    ]
    moon_rows = [
        {
            "ts": "2026-10-06T09:00:00+00:00",
            "event": "exit",
            "base": "GTC",
            "net_eur": 15.5,
            "reason": "manual_external",
            "venue": "bitvavo",
        }
    ]
    merged = combine_ledgers(("BTC+RS", clip_rows), ("MoonShot", moon_rows))
    assert [r["base"] for r in merged] == ["BTC", "NEAR", "GTC"]
    assert merged[2]["sleeve"] == "MoonShot"
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {},
            "positions": [],
            "equity_eur": 0.0,
            "unrealized_net_eur": 0.0,
        },
        [],
        earnings=earn,
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 17000.0,
            "cash_eur": 17000.0,
            "unrealized_net_eur": 4.0,
            "positions": [],
        },
        btc_rs_clip_ledger_rows=clip_rows,
        show_moonshot=True,
        moonshot={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 2006.0,
            "cash_eur": 5.0,
            "unrealized_net_eur": 6.0,
            "realized_total_eur": 15.5,
            "positions": [],
        },
        moonshot_ledger_rows=moon_rows,
    ).body.decode()
    assert "Netto verdiend" in html
    assert "+55.50" in html
    assert 'data-live="earn-week"' in html
    assert 'data-live="earn-day"' in html
    assert 'data-sleeve-earn="clip"' in html
    assert 'data-sleeve-earn="moonshot"' in html
    assert "+40.00" in html
    assert "+15.50" in html
    assert "BTC+RS · trail" in html
    assert "MoonShot · manual_external" in html
    assert "NEAR" in html
    assert 'data-live="ledger-body"' in html
    assert "patchEarnings" in html
    assert "MOONSHOT_STATUS_URL" in html
    assert "ledger_sig" in html


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


def _alphai_file(path, payload) -> None:
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


def test_current_alphai_session_is_used_without_a_refresh(tmp_path):
    from bot.integrations.alphai.daily_recommendations import (
        next_update_at_utc,
        recommendation_session_id,
    )
    from bot.live.momentum_btc_rs_clip import fresh_alphai_breakout_sets

    now = datetime(2026, 10, 5, 14, 7, tzinfo=UTC)
    path = tmp_path / "alphai.json"
    _alphai_file(
        path,
        {
            "session_id": recommendation_session_id(now=now, interval_minutes=15),
            "next_update_at": next_update_at_utc(now=now, interval_minutes=15).isoformat(),
            "picks": [{"base": "XRP"}],
            "avoid": [{"base": "AAVE"}],
        },
    )

    class Boom:
        def __getattr__(self, _name):
            raise AssertionError("fresh session must not call AlphaI")

    picks, avoid = fresh_alphai_breakout_sets(
        path=str(path),
        focus_bases=("XRP",),
        now=now,
        client=Boom(),
        interval_minutes=15,
    )
    assert picks == frozenset({"XRP"})
    assert avoid == frozenset({"AAVE"})


def test_stale_alphai_is_ignored_when_refresh_is_off(tmp_path):
    from bot.live.momentum_btc_rs_clip import fresh_alphai_breakout_sets

    path = tmp_path / "alphai.json"
    _alphai_file(
        path,
        {
            "session_id": "2026-10-04T11:45",
            "generated_at": "2026-10-04T09:45:13+00:00",
            "next_update_at": "2026-10-04T10:00:00+00:00",
            "picks": [{"base": "XRP"}],
            "avoid": [{"base": "ETH"}],
        },
    )
    picks, avoid = fresh_alphai_breakout_sets(
        path=str(path),
        focus_bases=("XRP", "ETH"),
        now=datetime(2026, 10, 5, 16, 0, tzinfo=UTC),
        interval_minutes=15,
        allow_network=False,
    )
    assert picks == frozenset()
    assert avoid == frozenset()


def test_stale_alphai_is_refreshed_before_the_scan(tmp_path, monkeypatch):
    from bot.integrations.alphai.daily_recommendations import (
        next_update_at_utc,
        recommendation_session_id,
        save_daily_recommendations,
    )
    from bot.live.momentum_btc_rs_clip import fresh_alphai_breakout_sets

    now = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)
    path = tmp_path / "alphai.json"
    _alphai_file(
        path,
        {
            "session_id": "2026-10-04T11:45",
            "next_update_at": "2026-10-04T10:00:00+00:00",
            "picks": [{"base": "XRP"}],
            "avoid": [{"base": "ETH"}],
        },
    )

    def _refresh(client, out_path, **kwargs):
        del client, kwargs
        report = {
            "session_id": recommendation_session_id(now=now, interval_minutes=15),
            "next_update_at": next_update_at_utc(now=now, interval_minutes=15).isoformat(),
            "picks": [{"base": "SOL"}],
            "avoid": [{"base": "AAVE"}],
        }
        save_daily_recommendations(out_path, report)
        return report

    monkeypatch.setattr(
        "bot.integrations.alphai.daily_recommendations.maybe_refresh_daily",
        _refresh,
    )
    picks, avoid = fresh_alphai_breakout_sets(
        path=str(path),
        focus_bases=("SOL", "AAVE"),
        now=now,
        client=object(),
        interval_minutes=15,
    )
    assert picks == frozenset({"SOL"})
    assert avoid == frozenset({"AAVE"})


def test_alphai_board_measures_the_gap_and_the_dashboard_shows_it():
    from bot.live.momentum_btc_rs_clip import (
        ClipConfig,
        alphai_breakout_board,
        breakout_board_headline,
        live_breakout_view,
    )
    from bot.live.momentum_desk import AlphaIView

    rows = _closed_days(40, 10.0, high=10.0)
    rows[-8][2] = 10.2
    cfg = ClipConfig(
        min_qvol_eur=1.0,
        max_entry_day_ret=0.08,
        max_break_extension=0.03,
        excess_floor=0.0,
        rebalance_days=1,
    )
    under = live_breakout_view(rows, 10.1, cfg)
    assert under["reason"] == "no_breakout"
    assert under["gap"] < 0
    assert live_breakout_view(rows, 10.35, cfg)["qualifies"] is True
    assert live_breakout_view(rows, 10.35, cfg, avoid=True)["reason"] == "alphai_avoid"
    view = AlphaIView(
        picks=frozenset({"ADA"}),
        avoid=frozenset({"BTC"}),
        pick_scores={"ADA": 18.0},
        pick_ranks={"ADA": 4},
    )
    now = datetime(2026, 10, 6, 10, 32, tzinfo=UTC)
    board = alphai_breakout_board({"ADA": rows}, {"ADA": 10.1}, view, cfg, now=now)
    assert board["cadence"] == "elk kwartier"
    assert board["rows"][0]["base"] == "ADA"
    assert board["rows"][0]["status"] == "onder de high"
    assert board["avoid"] == ["BTC"]
    assert board["next_label"] == "12:45"
    head = breakout_board_headline(
        {"rebalance_due": False, "want_alt": None, "ranked": [], "entries": []},
        cfg,
        1_791_210_519_426,
        now,
    )
    assert head["buy_line"] == "Geen koop"
    assert head["clock_line"] == "Nieuwe koop vanaf 16:28"
    ready = breakout_board_headline(
        {
            "rebalance_due": False,
            "want_alt": None,
            "ranked": [{"base": "ADA"}],
            "entries": [],
        },
        cfg,
        1_791_210_519_426,
        now,
    )
    assert ready["buy_line"] == "ADA staat klaar"
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 1},
            "positions": [],
            "equity_eur": 20_000.0,
        },
        [],
        show_moonshot=True,
        moonshot={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "book_eur": 2_000.0,
            "equity_eur": 2_000.0,
            "cash_eur": 2_000.0,
            "positions": [],
            "risk_on": True,
            "alphai_board": {**board, **head},
        },
    ).body.decode()
    panel = html.split('id="moonshot"', 1)[1].split("</section>", 1)[0]
    assert 'data-live="ms-alphai"' in panel
    assert "AlphaI · elk kwartier" in panel
    assert "Geen koop" in panel
    assert "ADA" in panel
    assert "onder de high" in panel
    assert "Avoid: BTC" in panel


def test_rs_sleeve_pick_names_the_buy_and_the_dashboard_shows_it():
    from bot.live.momentum_btc_rs_clip import rs_sleeve_pick

    decision = {
        "risk_on": True,
        "rebalance_due": True,
        "want_alt": "SUI",
        "entries": [{"base": "SUI", "role": "alt", "notional_eur": 1000}],
        "ranked": [
            {"base": "SUI", "excess": 0.168},
            {"base": "FET", "excess": 0.082},
            {"base": "AVAX", "excess": 0.066},
        ],
    }
    pick = rs_sleeve_pick({"last_decision": decision, "positions": []})
    assert pick["line"] == "Zou kopen SUI"
    assert pick["base"] == "SUI"
    assert pick["meta"] == "excess +16.8%"
    assert pick["rank"].startswith("SUI +16.8% · FET +8.2%")
    held = rs_sleeve_pick(
        {
            "last_decision": {
                "risk_on": True,
                "rebalance_due": False,
                "want_alt": "ETH",
                "entries": [],
                "ranked": [{"base": "ETH", "excess": 0.04}],
            },
            "positions": [{"base": "ETH", "role": "alt", "quantity": 2}],
        }
    )
    assert held["line"] == "Houdt ETH"
    waiting = rs_sleeve_pick(
        {
            "last_decision": {
                "risk_on": True,
                "rebalance_due": False,
                "want_alt": None,
                "entries": [],
                "ranked": [{"base": "NEAR", "excess": 0.05}],
            },
            "positions": [],
        }
    )
    assert waiting["line"] == "Volgende koop: NEAR"
    assert waiting["meta"] == "wacht op de klok"
    flat = rs_sleeve_pick(
        {
            "last_decision": {"risk_on": False, "ranked": [{"base": "SOL", "excess": 0.1}]},
            "positions": [],
        }
    )
    assert flat["line"] == "Geen koop"
    assert flat["meta"] == "BTC onder de SMA"
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "config": {},
            "positions": [],
            "equity_eur": 20_000.0,
        },
        [],
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 19_000.0,
            "cash_eur": 19_000.0,
            "positions": [],
            "last_decision": decision,
        },
    ).body.decode()
    card = html.split('data-live="clip"', 1)[1].split('data-live="rs-pick"', 1)[1]
    assert "RS sleeve" in card
    assert "Zou kopen SUI" in card
    assert "excess +16.8%" in card
    assert "FET +8.2%" in card
