"""Allocator + Donchian decision tests (loop mix)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.live.desk_allocator import classify_sma20_50, euros_for, sleeve_rows, snapshot
from bot.live.momentum_donchian import DonchianConfig, evaluate_donchian


def _ramp(n: int, start: float, step: float) -> list[float]:
    return [start + i * step for i in range(n)]


def test_sma20_50_risk_off_below_sma20():
    # 50 days rising then a crash below SMA20.
    up = _ramp(40, 100.0, 1.0)
    crash = [80.0] * 15
    closes = up + crash
    reg = classify_sma20_50(closes)
    assert reg["label"] == "risk_off"
    assert reg["sma20"] is not None
    assert closes[-1] < reg["sma20"]


def test_sma20_50_risk_on_above_sma50():
    closes = _ramp(60, 100.0, 2.0)
    reg = classify_sma20_50(closes)
    assert reg["label"] == "risk_on"
    assert closes[-1] > reg["sma50"]


def test_sma20_50_mid_between_gates():
    closes = [200.0] * 50 + [100.0] * 20
    reg = classify_sma20_50(closes)
    assert reg["label"] == "mid"
    assert reg["sma20"] is not None and reg["sma50"] is not None
    assert reg["sma20"] <= closes[-1] < reg["sma50"]


def test_risk_on_euros_are_two_donchians():
    euros = euros_for("risk_on")
    assert euros == {"donch_fri10": 10000, "donch10": 10000}


def test_risk_off_euros_short_and_donch_fri():
    euros = euros_for("risk_off")
    assert euros["short_weakest"] == 14000
    assert euros["donch_fri"] == 6000


def test_mid_is_all_cash():
    euros = euros_for("mid")
    assert euros == {"cash": 20000}
    rows = {r["id"]: r for r in sleeve_rows("mid")}
    assert rows["donch10"]["active"] is False
    assert rows["short_weakest"]["active"] is False
    assert rows["core_15m"]["active"] is False
    assert rows["cash"]["active"] is True


def test_snapshot_includes_why():
    closes = _ramp(60, 100.0, 2.0)
    snap = snapshot(closes)
    assert snap["ok"] is True
    assert "why" in snap
    assert snap["core_15m"] == "idle"
    assert "Donchian" in snap["now_trading"]["headline"]
    assert "short_weakest" not in snap["now_trading"]["ids"]
    assert snap["now_trading"]["stance"]


def test_now_trading_mid_is_cash():
    from bot.live.desk_allocator import now_trading

    nt = now_trading("mid")
    assert nt["headline"] == "Cash — geen trades"
    assert nt["ids"] == ["cash"]


def test_mix_panel_shows_active_strategy_and_why():
    from bot.live.momentum_dashboard import _mix_panel

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = _mix_panel(
        snap,
        {
            "running": True,
            "dry_run": False,
            "paper_only": False,
            "positions": [
                {
                    "holding_id": "dc-1",
                    "sleeve": "donch10",
                    "base": "NEAR",
                    "unrealized_net_eur": 12.5,
                    "quantity": 1,
                }
            ],
            "sleeves": [
                {
                    "id": "donch10",
                    "title": "Donchian 10/5",
                    "active": True,
                    "reasons": ["breakout"],
                }
            ],
        },
    )
    assert "Nu actief" in html
    assert "Donchian 10/5" in html
    assert "NEAR" in html
    assert "SMA50" in html
    assert "uptrend" in html.lower() or "Donchian-longs aan" in html
    assert "AAN" in html
    assert "Short weakest" in html
    assert "UIT" in html
    assert "Welke strategie nu" in html
    assert "DONCHIAN LIVE" in html
    assert "LIVE Bitvavo-orders" in html
    assert "UTC-dagclose" in html
    assert "vrijdagclose" in html


def test_dashboard_mix_board_renders():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 20000,
            "exposure_eur": 0,
            "equity_eur": 20000,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={"running": True, "positions": [], "sleeves": []},
        show_short_weakest=True,
        short_weakest={"enabled_setting": True, "positions": [], "running": True},
    ).body.decode()
    assert 'id="mix"' not in html
    assert 'id="paper-sw"' not in html
    assert "DONCHIAN LIVE" not in html
    assert "location.reload" not in html
    assert 'data-live="eq-chart"' in html
    assert "applyPulse" in html
    assert 'id="core-15m"' not in html


def test_dashboard_paper_sw_panel_beside_mix():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": True,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 20000,
            "exposure_eur": 0,
            "equity_eur": 20000,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={"running": True, "dry_run": False, "positions": [], "sleeves": []},
        show_short_weakest=True,
        short_weakest={
            "running": True,
            "paper_only": True,
            "mode": "short_weakest_paper",
            "book_eur": 14000,
            "equity_eur": 14000,
            "realized_total_eur": 0,
            "unrealized_net_eur": 0,
            "positions": [],
            "bear": {"bear_ok": False, "btc": 75000, "sma": 69000, "sma_days": 20, "gap_pct": 0.09},
            "live_caption": "Paper short standby: BTC boven SMA20.",
        },
        short_weakest_ledger_rows=[],
    ).body.decode()
    assert 'id="paper-sw"' not in html
    assert "Paper · Short weakest" not in html
    assert "Short-weakest ledger" not in html
    assert "apart paper-boek" not in html


def test_dashboard_live_15m_beside_mix_is_not_idle():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo", "okx"],
            "config": {"max_positions": 2, "clip_eur": 2000},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 3900,
            "cash_by_venue": {"bitvavo": 2000, "okx": 1900},
            "venue_cash_caps": {"bitvavo": 2000},
            "exposure_eur": 0,
            "equity_eur": 3900,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
            "next_decision": "2026-09-23T07:00:00+00:00",
        },
        [],
        allocator=snap,
        donchian={
            "running": True,
            "dry_run": False,
            "equity_eur": 19800.0,
            "positions": [
                {
                    "holding_id": "dc-1",
                    "sleeve": "donch10",
                    "base": "NEAR",
                    "quantity": 10,
                    "unrealized_net_eur": 12.0,
                }
            ],
            "sleeves": [],
        },
        show_short_weakest=True,
        short_weakest={"enabled_setting": True, "positions": [], "running": True},
    ).body.decode()
    assert "DONCHIAN LIVE" not in html
    assert "NEAR" not in html or 'data-live="clip"' not in html
    assert 'id="paper-sw"' not in html
    assert 'data-live="eq-chart"' in html
    assert "3,900.00" in html


def test_dashboard_paper_clip_is_separate_from_mix_equity():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 20000,
            "exposure_eur": 0,
            "equity_eur": 21122,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={
            "running": True,
            "dry_run": False,
            "equity_eur": 19683.0,
            "cash_eur": 9915.0,
            "unrealized_net_eur": -40.0,
            "positions": [
                {
                    "holding_id": "dc-1",
                    "sleeve": "donch10",
                    "base": "NEAR",
                    "quantity": 10,
                    "unrealized_net_eur": -12.0,
                }
            ],
            "sleeves": [],
        },
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 20100.0,
            "book_eur": 20000.0,
            "cash_eur": 50.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 100.0,
            "want_alt": "LINK",
            "risk_on": True,
            "equity_curve": [[1.0, 20000.0], [2.0, 20100.0]],
            "positions": [
                {
                    "holding_id": "clip-btc",
                    "base": "BTC",
                    "role": "btc",
                    "quantity": 0.2,
                    "mark": 70000.0,
                    "entry_price": 69000.0,
                    "notional_eur": 14000.0,
                    "unrealized_net_eur": 80.0,
                    "gross_return": 0.01,
                }
            ],
        },
        btc_rs_clip_ledger_rows=[],
    ).body.decode()
    assert 'id="paper-clip"' not in html
    assert "Paper · BTC + RS-clip" not in html
    assert 'id="clip"' in html
    assert 'data-live="eq-chart"' in html
    assert "20,100.00" in html
    assert "21,122" not in html
    assert "19,683" not in html
    assert "clip-btc" in html
    assert "BTC" in html


def test_dashboard_clip_live_pill():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": True,
            "dry_run": True,
            "equity_eur": 2000,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={
            "running": True,
            "dry_run": True,
            "equity_eur": 19900.0,
            "positions": [],
            "sleeves": [],
        },
        show_btc_rs_clip=True,
        btc_rs_clip={
            "running": True,
            "dry_run": False,
            "allow_live": True,
            "equity_eur": 20100.0,
            "book_eur": 20000.0,
            "cash_eur": 29.0,
            "realized_total_eur": 0.0,
            "unrealized_net_eur": 100.0,
            "want_alt": "NEAR",
            "risk_on": True,
            "live_caption": "Clip: 20% BTC boven SMA50, 80% NEAR",
            "positions": [
                {
                    "holding_id": "clip-btc",
                    "base": "BTC",
                    "role": "btc",
                    "quantity": 0.2,
                    "unrealized_net_eur": 80.0,
                    "venue": "bitvavo",
                    "mark": 73000,
                    "entry_price": 72000,
                    "notional_eur": 14600,
                    "gross_return": 0.01,
                }
            ],
        },
        btc_rs_clip_ledger_rows=[],
    ).body.decode()
    assert 'data-live="clip-title"' in html
    assert 'data-live="clip-pill"' in html
    assert ">LIVE</span>" in html
    assert 'data-live="clip-title">Paper' not in html
    assert "/live/momentum/btc-rs-clip/sell" in html
    assert "/live/momentum/btc-rs-clip/sell-all" in html
    assert "Verkoop clip" in html
    assert "clip-btc" in html
    assert 'id="paper-earn"' not in html
    assert 'id="core-15m"' not in html  # 15m dry/shadow hidden


def test_dashboard_shows_donchian_ledger_fills():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    rows = [
        {
            "ts": "2026-09-21T06:19:22+00:00",
            "event": "entry",
            "sleeve": "donch_fri10",
            "base": "NEAR",
            "venue": "bitvavo",
            "notional_eur": 3993.11,
            "entry_price": 3.7106,
            "fee_eur": 0.0,
            "reason": "breakout_10,mom=0.817",
            "dry_run": False,
        },
        {
            "ts": "2026-09-21T08:16:57+00:00",
            "event": "exit",
            "sleeve": "donch_fri10",
            "base": "NEAR",
            "venue": "bitvavo",
            "notional_eur": 3993.11,
            "entry_price": 3.7106,
            "exit_price": 3.7449,
            "net_eur": 36.87,
            "reason": "friday_flatten",
            "dry_run": False,
        },
        {
            "ts": "2026-09-21T08:17:00+00:00",
            "event": "decision",
            "sleeve": "donch_fri10",
            "ok": False,
            "reasons": ["friday_flatten"],
        },
    ]
    html = render_momentum_dashboard(
        {
            "running": False,
            "venues": ["bitvavo"],
            "config": {"max_positions": 3},
            "positions": [],
            "risk": {"day_realized_eur": 0, "entries_allowed": True},
            "cash_eur": 20000,
            "exposure_eur": 0,
            "equity_eur": 20000,
            "realized_total_eur": 0,
            "trade_count": 0,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={"running": True, "dry_run": False, "positions": [], "sleeves": []},
        donchian_ledger_rows=rows,
        show_short_weakest=True,
        short_weakest={"enabled_setting": True, "positions": [], "running": True},
        short_weakest_ledger_rows=[],
    ).body.decode()
    assert "Execution stream · Donchian ledger" not in html
    assert "Donchian · live longs" not in html
    assert "friday_flatten" not in html
    assert 'id="ledger"' in html
    assert "/live/momentum#ledger" in html
    assert 'data-live="eq-chart"' in html


def test_ledger_table_maps_donchian_entry_exit_price():
    from bot.live.momentum_dashboard import _ledger_table

    html = _ledger_table(
        [
            {
                "ts": "2026-09-21T08:17:04+00:00",
                "event": "exit",
                "sleeve": "donch10",
                "base": "AVAX",
                "venue": "bitvavo",
                "notional_eur": 3994.12,
                "exit_price": 9.5111,
                "net_eur": -121.27,
                "reason": "friday_flatten",
            }
        ]
    )
    assert "AVAX" in html
    assert "9.5111" in html
    assert "donch10 · friday_flatten" in html
    assert "−121.27" in html or "-121.27" in html


def test_ledger_table_maps_manual_external():
    from bot.live.momentum_dashboard import _ledger_table

    html = _ledger_table(
        [
            {
                "ts": "2026-09-23T08:20:00+00:00",
                "event": "exit",
                "sleeve": "donch_fri10",
                "base": "AAA",
                "venue": "bitvavo",
                "notional_eur": 3900.0,
                "exit_price": 3.65,
                "net_eur": -80.5,
                "reason": "manual_external",
                "detail": "reconcile_external_delta",
            }
        ]
    )
    assert "AAA" in html
    assert "3.65" in html
    assert "donch_fri10 · manual_external" in html


class _ReconGw:
    def __init__(
        self,
        *,
        held: dict[str, float] | None = None,
        sells: list[dict] | None = None,
    ) -> None:
        self.held = {str(k).upper(): float(v) for k, v in (held or {}).items()}
        self.sells = list(sells or [])
        self.placed: list[dict] = []

    async def base_held(self, base: str) -> float:
        return float(self.held.get(str(base).upper(), 0.0))

    async def recent_base_sells(self, base: str, *, since_ms: int, until_ms: int | None = None):
        want = str(base).upper()
        return [row for row in self.sells if str(row.get("base") or "").upper() == want]

    async def place_limit(self, symbol, side, qty, price, *, post_only):
        self.placed.append(
            {"symbol": symbol, "side": side, "qty": qty, "price": price, "post_only": post_only}
        )
        raise AssertionError("external reconcile must not place venue orders")


class _Feed:
    def __init__(self, px: float = 10.0) -> None:
        self.px = px

    async def last_price(self, base: str) -> float:
        return self.px


def _live_runner(tmp_path, gw: _ReconGw, **kwargs):
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    return DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        feed=_Feed(),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
        **kwargs,
    )


def _seed_live_lot(r, sleeve: str, *, base: str, qty: float, px: float, opened_ms: int, hid: str):
    from bot.live.momentum_donchian import DonchianPosition

    sl = r.sleeves[sleeve]
    sl.book_eur = 10_000
    sl.positions.append(
        DonchianPosition(
            base=base,
            entry_price=px,
            notional_eur=qty * px,
            opened_ms=opened_ms,
            holding_id=hid,
            sleeve=sleeve,
            venue="bitvavo",
            quantity=qty,
        )
    )
    r.marks[base] = px


def test_reconcile_external_closes_gone_lots_keeps_held(tmp_path):
    """UI-sold lots leave the book; matching venue qty stays. No sell orders."""
    import asyncio
    import json

    gw = _ReconGw(
        held={"CCC": 406.9801828},
        sells=[
            {"base": "AAA", "qty": 2138.44008843, "price": 3.65, "fee_eur": 7.8, "ts_ms": 2_000},
            {"base": "BBB", "qty": 20036.05665769, "price": 0.19, "fee_eur": 7.6, "ts_ms": 2_001},
        ],
    )
    r = _live_runner(tmp_path, gw)
    r.sleeves["donch10"].cash_eur = 2012.56
    r.sleeves["donch_fri10"].cash_eur = 1995.46
    _seed_live_lot(
        r, "donch10", base="AAA", qty=1074.23163211, px=3.7094, opened_ms=1_000, hid="dc-a"
    )
    _seed_live_lot(
        r, "donch10", base="CCC", qty=406.9801828, px=9.8056, opened_ms=1_000, hid="dc-c"
    )
    _seed_live_lot(
        r, "donch_fri10", base="AAA", qty=1064.20845632, px=3.7219, opened_ms=2_000, hid="dc-a2"
    )
    _seed_live_lot(
        r, "donch_fri10", base="BBB", qty=20036.05665769, px=0.1976, opened_ms=2_000, hid="dc-b"
    )

    closed = asyncio.run(r.reconcile_external_inventory())
    assert gw.placed == []
    assert {row["base"] for row in closed} == {"AAA", "BBB"}
    assert all(row["reason"] == "manual_external" for row in closed)
    assert all(row["detail"] == "reconcile_external_delta" for row in closed)
    assert len(closed) == 3
    open_pos = r.status()["positions"]
    assert len(open_pos) == 1
    assert open_pos[0]["base"] == "CCC"
    assert open_pos[0]["quantity"] == pytest.approx(406.9801828)
    assert r.sleeves["donch10"].positions[0].base == "CCC"
    assert r.sleeves["donch_fri10"].positions == []
    led = json.loads("[" + ",".join((tmp_path / "l.jsonl").read_text().splitlines()) + "]")
    exits = [row for row in led if row.get("event") == "exit"]
    assert len(exits) == 3
    assert {row["holding_id"] for row in exits} == {"dc-a", "dc-a2", "dc-b"}
    saved = json.loads((tmp_path / "s.json").read_text())
    assert [p["base"] for p in saved["sleeves"]["donch10"]["positions"]] == ["CCC"]
    assert saved["sleeves"]["donch_fri10"]["positions"] == []


def test_reconcile_external_fifo_partial(tmp_path):
    import asyncio

    gw = _ReconGw(
        held={"AAA": 10.0},
        sells=[{"base": "AAA", "qty": 10.0, "price": 11.0, "fee_eur": 1.0, "ts_ms": 3_000}],
    )
    r = _live_runner(tmp_path, gw)
    r.sleeves["donch10"].cash_eur = 0.0
    _seed_live_lot(r, "donch10", base="AAA", qty=10.0, px=10.0, opened_ms=1_000, hid="old")
    _seed_live_lot(r, "donch10", base="AAA", qty=10.0, px=10.0, opened_ms=2_000, hid="new")
    closed = asyncio.run(r.reconcile_external_inventory())
    assert len(closed) == 1
    assert closed[0]["holding_id"] == "old"
    assert closed[0]["quantity"] == pytest.approx(10.0)
    assert closed[0]["exit_price"] == pytest.approx(11.0)
    assert closed[0]["fee_eur"] == pytest.approx(1.0)
    assert closed[0]["net_eur"] == pytest.approx(10.0 * (11.0 - 10.0) - 1.0)
    assert [p.holding_id for p in r.sleeves["donch10"].positions] == ["new"]
    assert r.sleeves["donch10"].positions[0].quantity == pytest.approx(10.0)
    assert r.sleeves["donch10"].cash_eur == pytest.approx(10.0 * 11.0 - 1.0)


def test_reconcile_external_skips_dry_run(tmp_path):
    import asyncio

    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    gw = _ReconGw(held={})
    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        feed=_Feed(),
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    _seed_live_lot(r, "donch10", base="AAA", qty=10.0, px=10.0, opened_ms=1, hid="p")
    # Paper venue is skipped even if dry_run were false; force a live venue on a dry runner.
    r.sleeves["donch10"].positions[0].venue = "bitvavo"
    closed = asyncio.run(r.reconcile_external_inventory())
    assert closed == []
    assert len(r.sleeves["donch10"].positions) == 1
    assert gw.placed == []


def test_reconcile_external_reserves_other_desk_qty(tmp_path):
    """Venue coins that belong to 15m must not be treated as Donchian inventory."""
    import asyncio

    gw = _ReconGw(held={"AAA": 50.0})
    r = _live_runner(tmp_path, gw)
    r.sleeves["donch10"].cash_eur = 0.0
    _seed_live_lot(r, "donch10", base="AAA", qty=100.0, px=10.0, opened_ms=1, hid="mix")
    r._other_desk_qty = lambda base, venue: 50.0 if str(base).upper() == "AAA" else 0.0  # noqa: ARG005
    closed = asyncio.run(r.reconcile_external_inventory())
    assert len(closed) == 1
    assert closed[0]["quantity"] == pytest.approx(100.0)
    assert r.sleeves["donch10"].positions == []
    assert gw.placed == []


def test_donchian_status_fresh_books_external(tmp_path):
    import asyncio

    from bot.live.momentum_donchian_runner import DonchianDeskManager

    gw = _ReconGw(held={})
    r = _live_runner(tmp_path, gw)
    _seed_live_lot(r, "donch10", base="AAA", qty=10.0, px=10.0, opened_ms=1, hid="gone")
    mgr = DonchianDeskManager()
    mgr._runner = r
    st = asyncio.run(mgr.status_fresh())
    assert st["positions"] == []
    assert r.sleeves["donch10"].positions == []
    led = (tmp_path / "l.jsonl").read_text()
    assert "manual_external" in led
    assert gw.placed == []


def test_sleeve_live_caption_ignores_stale_sma_when_bags_open():
    from bot.live.momentum_donchian import sleeve_live_caption

    held = sleeve_live_caption(
        n_positions=2,
        friday_flatten=False,
        risk_block="btc_below_sma",
        mix_label="risk_on",
        exit_n=5,
        next_decision="2026-09-22T00:00:00+00:00",
    )
    assert "houdt bags" in held
    assert "btc_below_sma" not in held
    cash_stale = sleeve_live_caption(
        n_positions=0,
        friday_flatten=False,
        risk_block="btc_below_sma",
        mix_label="risk_on",
        next_decision="2026-09-22T00:00:00+00:00",
    )
    assert "vorige decide" in cash_stale
    fri = sleeve_live_caption(
        n_positions=0,
        friday_flatten=True,
        risk_block="friday_flatten",
        mix_label="risk_on",
    )
    assert "Friday-flat" in fri or "friday" in fri.lower()
    off = sleeve_live_caption(
        n_positions=0,
        friday_flatten=False,
        risk_block="allocator_zero_book",
        mix_label="risk_off",
        allocator_active=False,
    )
    assert "risk_off" in off


def test_dashboard_mix_equity_and_donchian_table():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    snap = snapshot(_ramp(60, 100.0, 2.0))
    html = render_momentum_dashboard(
        {
            "running": True,
            "venues": ["bitvavo"],
            "config": {"max_positions": 1, "trail_pct": 0.05, "hard_stop_pct": 0.03},
            "positions": [],
            "risk": {"day_realized_eur": 12.0, "entries_allowed": True},
            "cash_eur": 21122.60,
            "exposure_eur": 0,
            "equity_eur": 21122.60,
            "realized_total_eur": 1122.60,
            "trade_count": 40,
            "unrealized_net_eur": 0,
        },
        [],
        allocator=snap,
        donchian={
            "running": True,
            "dry_run": False,
            "venues": ["bitvavo"],
            "equity_eur": 19683.82,
            "cash_eur": 11928.16,
            "deployed_eur": 7975.44,
            "unrealized_net_eur": -219.77,
            "next_decision": "2026-09-22T00:00:00+00:00",
            "positions": [
                {
                    "holding_id": "dc-donch10-NEAR-bd4fc361",
                    "base": "NEAR",
                    "sleeve": "donch10",
                    "venue": "bitvavo",
                    "quantity": 1074.23163211,
                    "entry_price": 3.70939,
                    "notional_eur": 3984.75,
                    "mark": 3.60,
                    "gross_return": -0.0295,
                    "unrealized_net_eur": -114.68,
                    "age_h": 13.4,
                    "entry_reason": "breakout_10",
                },
                {
                    "holding_id": "dc-donch10-AVAX-19192112",
                    "base": "AVAX",
                    "sleeve": "donch10",
                    "venue": "bitvavo",
                    "quantity": 406.9801828,
                    "entry_price": 9.80561,
                    "notional_eur": 3990.69,
                    "mark": 9.55,
                    "gross_return": -0.0261,
                    "unrealized_net_eur": -105.09,
                    "age_h": 13.4,
                    "entry_reason": "breakout_10",
                },
            ],
            "sleeves": [
                {
                    "id": "donch_fri10",
                    "title": "Donchian 10/5 Friday-flat",
                    "active": True,
                    "friday_flatten": True,
                    "n_positions": 0,
                    "risk_block": "friday_flatten",
                    "exit_n": 5,
                    "channel": 10,
                },
                {
                    "id": "donch10",
                    "title": "Donchian 10/5",
                    "active": True,
                    "friday_flatten": False,
                    "n_positions": 2,
                    "risk_block": "btc_below_sma",
                    "exit_n": 5,
                    "channel": 10,
                    "positions": [{"base": "NEAR"}, {"base": "AVAX"}],
                },
            ],
        },
        show_short_weakest=True,
        short_weakest={"enabled_setting": True, "positions": [], "running": True},
    ).body.decode()
    assert "19,683.82" not in html
    assert "21,122.60" in html
    assert "NEAR" not in html
    assert "AVAX" not in html
    assert "Donchian · live longs" not in html
    assert "Leeg Donchian" not in html
    assert 'id="paper-sw"' not in html
    assert 'data-live="eq-chart"' in html
    assert "applyPulse" in html
    assert "/live/momentum/pulse" in html
    assert ">Bags<" in html
    assert 'id="core-15m"' not in html
    assert "/live/momentum/donchian/sell" not in html


def test_mix_tape_uptrend_splits_at_sma50_not_chop():
    from bot.live.momentum_dashboard import _mix_tape

    html = _mix_tape(75447.0, 68245.0, 63598.0, "risk_on")
    assert "chop" not in html
    assert "risk on" in html
    assert "SMA50" in html
    assert "SMA20" in html
    # Death-cross (SMA20 below SMA50) still has a mid band.
    chop = _mix_tape(100.0, 90.0, 120.0, "mid")
    assert "chop" in chop


def _ohlc_breakout(n: int = 16, last_high: float = 120.0) -> list[list[float]]:
    rows = []
    for i in range(n - 1):
        px = 100.0 + i * 0.1
        rows.append([i, px, px + 1.0, px - 1.0, px, 1.0])
    rows.append([n, 110.0, last_high, 109.0, 110.0, 1.0])
    return rows


def _day_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def test_donchian_breakout_enters_when_btc_ok():
    cfg = DonchianConfig(
        name="t", title="t", channel=10, exit_n=5, friday_flatten=False, universe=("AAA",)
    )
    ohlc = {"AAA": _ohlc_breakout(16, last_high=140.0)}
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(
        ohlc, btc, cfg, held=set(), cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC)
    )
    assert out["ok"] is True
    assert out["entries"]
    assert out["entries"][0]["base"] == "AAA"


def test_donchian_no_breakout_rejected():
    cfg = DonchianConfig(name="t", title="t", channel=10, exit_n=5, universe=("AAA",))
    rows = _ohlc_breakout(16, last_high=101.0)  # not above prior highs
    # flatten last high to sit inside channel
    rows[-1][2] = 100.5
    ohlc = {"AAA": rows}
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(
        ohlc, btc, cfg, held=set(), cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC)
    )
    assert out["entries"] == []
    assert any(r["reason"] == "no_breakout" for r in out["rejected"])


def test_donchian_friday_morning_does_not_flatten():
    from bot.live.momentum_donchian import friday_close_reached, weekend_flatten

    cfg = DonchianConfig(name="t", title="t", friday_flatten=True)
    friday_noon = datetime(2026, 9, 18, 12, tzinfo=UTC)
    thu_ms = _day_ms(2026, 9, 17)
    ohlc = {"AAA": [[thu_ms, 100.0, 101.0, 99.0, 100.0, 1.0]]}
    out = evaluate_donchian(
        ohlc, _ramp(60, 100.0, 2.0), cfg, held={"AAA"}, cash_eur=10_000.0, now=friday_noon
    )
    assert out["risk_block"] != "friday_flatten"
    assert friday_close_reached(friday_noon) is False
    assert weekend_flatten(now=friday_noon, signal_weekday=3) is False


def test_donchian_friday_flattens_after_friday_close():
    cfg = DonchianConfig(name="t", title="t", friday_flatten=True)
    saturday = datetime(2026, 9, 19, 0, 5, tzinfo=UTC)
    fri_ms = _day_ms(2026, 9, 18)
    ohlc = {"AAA": [[fri_ms, 100.0, 101.0, 99.0, 100.0, 1.0]]}
    out = evaluate_donchian(
        ohlc, _ramp(60, 100.0, 2.0), cfg, held={"AAA"}, cash_eur=10_000.0, now=saturday
    )
    assert out["risk_block"] == "friday_flatten"
    assert out["exits"][0]["base"] == "AAA"


def test_donchian_monday_without_bars_does_not_buy_or_dump():
    """Live-kick race: SMA from closes, no OHLC, Monday wall clock."""
    cfg = DonchianConfig(name="t", title="t", friday_flatten=True, universe=("AAA",))
    monday = datetime(2026, 9, 21, 6, 19, tzinfo=UTC)
    out = evaluate_donchian(
        {}, _ramp(60, 100.0, 2.0), cfg, held={"AAA"}, cash_eur=10_000.0, now=monday
    )
    assert out["risk_block"] == "data_not_ready"
    assert out["entries"] == []
    assert out["exits"] == []


def test_donchian_saturday_without_bars_still_flattens():
    cfg = DonchianConfig(name="t", title="t", friday_flatten=True)
    saturday = datetime(2026, 9, 19, 0, 5, tzinfo=UTC)
    out = evaluate_donchian(
        {}, _ramp(60, 100.0, 2.0), cfg, held={"AAA"}, cash_eur=10_000.0, now=saturday
    )
    assert out["risk_block"] == "friday_flatten"
    assert out["exits"][0]["base"] == "AAA"


def test_donchian_short_btc_ohlc_keeps_sma_series():
    cfg = DonchianConfig(
        name="t", title="t", channel=10, exit_n=5, friday_flatten=False, universe=("AAA",)
    )
    ohlc = {
        "AAA": _ohlc_breakout(16, last_high=140.0),
        "BTC": [[i, 100.0, 101.0, 99.0, 100.0, 1.0] for i in range(3)],
    }
    out = evaluate_donchian(
        ohlc,
        _ramp(60, 100.0, 2.0),
        cfg,
        held=set(),
        cash_eur=10_000.0,
        now=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert out["btc"]["sma"] is not None
    assert out["risk_block"] != "sma_unavailable"
    assert out["entries"]


def test_donchian_sma_unavailable_blocks_entries_keeps_bag():
    cfg = DonchianConfig(name="t", title="t", friday_flatten=False, universe=("AAA",))
    thu_ms = _day_ms(2026, 9, 17)
    ohlc = {"AAA": [[thu_ms, 100.0, 101.0, 99.0, 100.0, 1.0]]}
    out = evaluate_donchian(
        ohlc,
        [100.0, 101.0],
        cfg,
        held={"AAA"},
        cash_eur=10_000.0,
        now=datetime(2026, 9, 21, 12, tzinfo=UTC),
    )
    assert out["risk_block"] == "sma_unavailable"
    assert out["entries"] == []
    assert out["exits"] == []


def test_donchian_decision_window_hour_zero_only():
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    r = DonchianBundleRunner(state_path="s.json", ledger_path="l.jsonl", dry_run=True)
    assert r._in_decision_window(datetime(2026, 9, 22, 0, 3, tzinfo=UTC)) is True
    assert r._in_decision_window(datetime(2026, 9, 21, 6, 19, tzinfo=UTC)) is False
    assert r._in_decision_window(datetime(2026, 9, 22, 0, 5, tzinfo=UTC)) is False


def test_donchian_ignores_in_progress_daily_breakout():
    cfg = DonchianConfig(name="t", title="t", channel=10, exit_n=5, universe=("AAA",))
    now = datetime(2026, 9, 21, 12, tzinfo=UTC)  # Monday midday
    start = datetime(2026, 9, 5, tzinfo=UTC)
    rows = []
    for i in range(16):
        t = start + timedelta(days=i)
        px = 100.0
        rows.append([int(t.timestamp() * 1000), px, px + 1.0, px - 1.0, px, 1.0])
    # In-progress Monday bar breaks out — must not fire until UTC close.
    today_ms = _day_ms(2026, 9, 21)
    rows.append([today_ms, 110.0, 140.0, 109.0, 110.0, 1.0])
    out = evaluate_donchian(
        {"AAA": rows}, _ramp(60, 100.0, 2.0), cfg, held=set(), cash_eur=10_000.0, now=now
    )
    assert out["entries"] == []
    assert any(r["reason"] == "no_breakout" for r in out["rejected"])


def test_donchian_second_clip_uses_sleeve_equity():
    cfg = DonchianConfig(
        name="t",
        title="t",
        channel=10,
        exit_n=5,
        max_pos=2,
        weight=0.4,
        universe=("AAA", "BBB"),
    )
    ohlc = {
        "AAA": _ohlc_breakout(16, last_high=140.0),
        "BBB": _ohlc_breakout(16, last_high=141.0),
    }
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(
        ohlc,
        btc,
        cfg,
        held=set(),
        cash_eur=10_000.0,
        deployed_eur=0.0,
        now=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert len(out["entries"]) == 2
    assert out["entries"][0]["notional_eur"] == 4000.0
    assert out["entries"][1]["notional_eur"] == 4000.0


def test_donchian_eod_decision_hour_default():
    cfg = DonchianConfig(name="t", title="t")
    assert cfg.decision_hours_utc == (0,)


def test_donchian_channel_low_exit():
    cfg = DonchianConfig(name="t", title="t", channel=10, exit_n=3, friday_flatten=False)
    rows = []
    for i in range(12):
        px = 100.0
        rows.append([i, px, px + 2, px - 1, px, 1.0])
    rows.append([12, 98.0, 99.0, 90.0, 91.0, 1.0])  # today low 90 < prior lows ~99
    ohlc = {"AAA": rows}
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(
        ohlc, btc, cfg, held={"AAA"}, cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC)
    )
    assert any(e["base"] == "AAA" and e["reason"] == "channel_low" for e in out["exits"])


def test_donchian_equity_counts_open_notional(tmp_path):
    from bot.live.momentum_donchian import DonchianPosition
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        dry_run=True,
    )
    sl = r.sleeves["donch10"]
    sl.cash_eur = 2_000.0
    sl.positions = [
        DonchianPosition(
            base="AAA", entry_price=10.0, notional_eur=4_000.0, opened_ms=1, venue="bitvavo"
        )
    ]
    r.marks["AAA"] = 10.0
    eq = r.status()["equity_eur"]
    # cash + notional + unrealized (~ -half-fee on 4000 * 0.003/2)
    assert eq > 5_900
    assert eq < 6_100


def test_discard_paper_lots_restores_cash(tmp_path):
    from bot.live.momentum_donchian import DonchianPosition
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        dry_run=True,
    )
    sl = r.sleeves["donch10"]
    sl.cash_eur = 0.0
    sl.positions = [
        DonchianPosition(
            base="AAA", entry_price=10.0, notional_eur=4000.0, opened_ms=1, venue="paper"
        )
    ]
    n = r.discard_paper_positions()
    assert n == 1
    assert sl.positions == []
    assert sl.cash_eur == 4000.0


def test_donchian_live_open_places_venue_order(tmp_path):
    import asyncio

    from bot.live.momentum_donchian_runner import DonchianBundleRunner
    from bot.live.momentum_runner import OrderState

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def best_bid_ask(self, symbol):
            return 10.0, 10.1

        async def place_limit(self, symbol, side, qty, price, *, post_only):
            self.placed.append(
                {"symbol": symbol, "side": side, "qty": qty, "price": price, "post_only": post_only}
            )
            return OrderState("o1", "closed", qty, price, qty * price * 0.001)

        async def fetch_order(self, order_id, symbol):
            p = self.placed[-1]
            return OrderState("o1", "closed", p["qty"], p["price"], 0.0)

        async def cancel_order(self, order_id, symbol):
            return await self.fetch_order(order_id, symbol)

    async def go() -> None:
        gw = Gw()
        r = DonchianBundleRunner(
            state_path=str(tmp_path / "s.json"),
            ledger_path=str(tmp_path / "l.jsonl"),
            book_eur=20_000,
            dry_run=False,
            venues=("bitvavo",),
            gateways={"bitvavo": gw},
        )
        sl = r.sleeves["donch10"]
        sl.book_eur = 10_000
        sl.cash_eur = 10_000
        await r._open(sl, {"base": "AAA", "notional_eur": 4000, "reasons": ["breakout"]}, now_ms=1)
        assert gw.placed and gw.placed[0]["side"] == "buy"
        assert gw.placed[0]["post_only"] is False
        assert sl.positions
        assert sl.positions[0].venue == "bitvavo"
        assert sl.positions[0].is_paper() is False
        st = r.status()
        assert st["mode"] == "donchian_live"
        assert st["dry_run"] is False
        assert st["paper_only"] is False

    asyncio.run(go())


def test_donchian_sell_all_places_live_sell(tmp_path):
    import asyncio

    from bot.live.momentum_donchian import DonchianPosition
    from bot.live.momentum_donchian_runner import DonchianBundleRunner
    from bot.live.momentum_runner import OrderState

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def best_bid_ask(self, symbol):
            return 9.8, 9.9

        async def place_limit(self, symbol, side, qty, price, *, post_only):
            self.placed.append(
                {"symbol": symbol, "side": side, "qty": qty, "price": price, "post_only": post_only}
            )
            return OrderState("s1", "closed", qty, price, qty * price * 0.001)

        async def fetch_order(self, order_id, symbol):
            p = self.placed[-1]
            return OrderState("s1", "closed", p["qty"], p["price"], 0.0)

        async def cancel_order(self, order_id, symbol):
            return await self.fetch_order(order_id, symbol)

    gw = Gw()
    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    sl = r.sleeves["donch10"]
    sl.book_eur = 10_000
    sl.cash_eur = 6000.0
    sl.positions = [
        DonchianPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=4000.0,
            opened_ms=1,
            holding_id="dc-aaa",
            sleeve="donch10",
            venue="bitvavo",
            quantity=400.0,
        )
    ]
    r.marks["AAA"] = 9.8
    out = asyncio.run(r.sell_all())
    assert out == {"ok": True, "closed": 1}
    assert gw.placed and gw.placed[0]["side"] == "sell"
    assert sl.positions == []
    led = (tmp_path / "l.jsonl").read_text()
    assert "manual_sell_all" in led
    assert '"event": "exit"' in led


def test_donchian_paper_sell_all_skips_venue(tmp_path):
    import asyncio

    from bot.live.momentum_donchian import DonchianPosition
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def place_limit(self, *a, **k):
            self.placed.append((a, k))
            raise AssertionError("paper sell_all must not place venue orders")

    gw = Gw()
    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    sl = r.sleeves["donch10"]
    sl.positions = [
        DonchianPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=4000.0,
            opened_ms=1,
            holding_id="dc-p",
            sleeve="donch10",
            venue="paper",
            quantity=400.0,
        )
    ]
    r.marks["AAA"] = 10.0
    out = asyncio.run(r.sell_all())
    assert out["closed"] == 1
    assert sl.positions == []
    assert gw.placed == []


def test_warmup_without_sma_keeps_live_bags(tmp_path):
    import asyncio
    import json

    from bot.live import desk_allocator
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    desk_allocator._cache["snap"] = None
    desk_allocator._cache["ms"] = 0.0
    state = tmp_path / "s.json"
    state.write_text(
        json.dumps(
            {
                "book_eur": 20000,
                "sleeves": {
                    "donch10": {
                        "book_eur": 10000,
                        "cash_eur": 2012.56,
                        "realized_total_eur": 0,
                        "positions": [
                            {
                                "base": "AAA",
                                "entry_price": 3.7,
                                "notional_eur": 3984.75,
                                "opened_ms": 1,
                                "holding_id": "dc-1",
                                "venue": "bitvavo",
                                "quantity": 1074.23,
                                "sleeve": "donch10",
                            }
                        ],
                        "last_decision": {"risk_block": "btc_below_sma"},
                    },
                    "donch_fri10": {"book_eur": 10000, "cash_eur": 9915.6, "positions": []},
                    "donch_fri": {"book_eur": 0, "cash_eur": 0, "positions": []},
                },
            }
        )
    )
    r = DonchianBundleRunner(
        state_path=str(state),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
    )
    r._btc_closes = []
    r.marks = {}
    book_before = r.sleeves["donch10"].book_eur
    snap = r._apply_allocator()
    assert snap["regime"]["ready"] is False
    assert r.sleeves["donch10"].book_eur == book_before
    asyncio.run(r.manage_exits())
    assert len(r.sleeves["donch10"].positions) == 1
    assert r.sleeves["donch10"].positions[0].base == "AAA"
    led = (tmp_path / "l.jsonl").read_text() if (tmp_path / "l.jsonl").exists() else ""
    assert "allocator_flatten" not in led
    assert '"event": "exit"' not in led


def test_donchian_runner_decides_at_utc_close(tmp_path):
    from bot.live.momentum_donchian_runner import DonchianBundleRunner

    r = DonchianBundleRunner(
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        book_eur=20_000,
        dry_run=True,
    )
    assert r._decision_hours() == (0,)
    assert r.status()["decision_hours_utc"] == [0]
