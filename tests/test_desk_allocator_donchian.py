"""Allocator + Donchian decision tests (loop mix)."""

from __future__ import annotations

from datetime import UTC, datetime

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
    assert 'id="mix"' in html
    assert "Nu actief" in html
    assert "MIX · RISK ON" in html
    assert "Donchian · paper longs" in html
    assert "15m WR-core staat idle" in html
    assert "location.reload" not in html
    assert "patchMixOpen" in html


def _ohlc_breakout(n: int = 16, last_high: float = 120.0) -> list[list[float]]:
    rows = []
    for i in range(n - 1):
        px = 100.0 + i * 0.1
        rows.append([i, px, px + 1.0, px - 1.0, px, 1.0])
    rows.append([n, 110.0, last_high, 109.0, 110.0, 1.0])
    return rows


def test_donchian_breakout_enters_when_btc_ok():
    cfg = DonchianConfig(name="t", title="t", channel=10, exit_n=5, friday_flatten=False, universe=("AAA",))
    ohlc = {"AAA": _ohlc_breakout(16, last_high=140.0)}
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(ohlc, btc, cfg, held=set(), cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC))
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
    out = evaluate_donchian(ohlc, btc, cfg, held=set(), cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC))
    assert out["entries"] == []
    assert any(r["reason"] == "no_breakout" for r in out["rejected"])


def test_donchian_friday_flattens():
    cfg = DonchianConfig(name="t", title="t", friday_flatten=True)
    friday = datetime(2026, 9, 18, tzinfo=UTC)  # Friday
    out = evaluate_donchian({}, _ramp(60, 100.0, 2.0), cfg, held={"AAA"}, cash_eur=10_000.0, now=friday)
    assert out["risk_block"] == "friday_flatten"
    assert out["exits"][0]["base"] == "AAA"


def test_donchian_channel_low_exit():
    cfg = DonchianConfig(name="t", title="t", channel=10, exit_n=3, friday_flatten=False)
    rows = []
    for i in range(12):
        px = 100.0
        rows.append([i, px, px + 2, px - 1, px, 1.0])
    rows.append([12, 98.0, 99.0, 90.0, 91.0, 1.0])  # today low 90 < prior lows ~99
    ohlc = {"AAA": rows}
    btc = _ramp(60, 100.0, 2.0)
    out = evaluate_donchian(ohlc, btc, cfg, held={"AAA"}, cash_eur=10_000.0, now=datetime(2026, 9, 21, tzinfo=UTC))
    assert any(e["base"] == "AAA" and e["reason"] == "channel_low" for e in out["exits"])


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
        DonchianPosition(base="AAA", entry_price=10.0, notional_eur=4000.0, opened_ms=1, venue="paper")
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
            self.placed.append({"symbol": symbol, "side": side, "qty": qty, "price": price, "post_only": post_only})
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
