"""BTC-core + RS clip — decision and live-fill tests."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.live.momentum_btc_rs_clip import ClipConfig, ClipPosition, completed_ohlc, evaluate_clip
from bot.live.momentum_btc_rs_clip_runner import config_from_settings


def _bars(n: int, start: float, step: float, vol: float = 5_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    day_ms = 86_400_000
    t0 = 1_700_000_000_000
    for i in range(n):
        rows.append([t0 + i * day_ms, px, px + 1, px - 1, px, vol])
        px += step
    return rows


def test_completed_ohlc_drops_today():
    rows = _bars(5, 100.0, 1.0)
    rows[-1][0] = int(datetime.now(UTC).timestamp() * 1000)
    out = completed_ohlc(rows)
    assert len(out) == len(rows) - 1


def test_risk_off_exits_everything():
    btc = _bars(60, 200.0, -2.0)
    ohlc = {"BTC": btc, "ETH": _bars(60, 10.0, 0.1)}
    cfg = ClipConfig(universe=("ETH",))
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc", "ETH": "alt"},
        cash_eur=1000.0,
        deployed_eur=19000.0,
        now_ms=1,
        last_rebalance_ms=1,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["ok"] is True
    assert out["risk_on"] is False
    bases = {e["base"] for e in out["exits"]}
    assert bases == {"BTC", "ETH"}
    assert out["entries"] == []


def test_risk_on_opens_btc_core():
    btc = _bars(60, 100.0, 2.0)
    ohlc = {"BTC": btc, "ETH": _bars(60, 10.0, 0.0, vol=1.0)}
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=80_000.0)
    out = evaluate_clip(
        ohlc,
        cfg,
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_on"] is True
    assert any(e["base"] == "BTC" and e["role"] == "btc" for e in out["entries"])
    btc_n = next(e["notional_eur"] for e in out["entries"] if e["base"] == "BTC")
    assert 14_000 <= btc_n <= 15_000


def test_liquid_excess_alt_gets_clip():
    btc = _bars(60, 100.0, 0.05)
    eth = _bars(40, 10.0, 0.0)
    eth += _bars(21, 10.0, 0.8, vol=20_000.0)
    t0 = 1_700_000_000_000
    for i, r in enumerate(eth):
        r[0] = t0 + i * 86_400_000
    ohlc = {"BTC": btc, "ETH": eth}
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=50_000.0, excess_floor=0.08)
    out = evaluate_clip(
        ohlc,
        cfg,
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_on"] is True
    alts = [e for e in out["entries"] if e["role"] == "alt"]
    assert alts and alts[0]["base"] == "ETH"
    assert alts[0]["notional_eur"] == 5000.0


def test_thin_volume_skips_alt():
    btc = _bars(60, 100.0, 0.05)
    eth = _bars(61, 10.0, 0.8, vol=0.01)
    ohlc = {"BTC": btc, "ETH": eth}
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=80_000.0)
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc"},
        cash_eur=5_000.0,
        deployed_eur=15_000.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert not any(e.get("role") == "alt" for e in out["entries"])
    assert out["want_alt"] is None


def test_weekly_hold_keeps_alt_when_not_due():
    btc = _bars(60, 100.0, 0.05)
    ohlc = {"BTC": btc, "ETH": _bars(61, 10.0, 0.0, vol=20_000.0)}
    cfg = ClipConfig(universe=("ETH",), rebalance_days=7, min_qvol_eur=1.0)
    now_ms = 20 * 86_400_000
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc", "ETH": "alt"},
        cash_eur=4_000.0,
        deployed_eur=16_000.0,
        now_ms=now_ms,
        last_rebalance_ms=now_ms - 2 * 86_400_000,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["rebalance_due"] is False
    assert out["want_alt"] == "ETH"
    assert not any(e["base"] == "ETH" for e in out["exits"])


def test_sma_unavailable_keeps_bags():
    ohlc = {"BTC": _bars(10, 100.0, 1.0)}
    out = evaluate_clip(
        ohlc,
        ClipConfig(),
        held={"BTC": "btc"},
        cash_eur=1000.0,
        deployed_eur=15000.0,
        now_ms=1,
        last_rebalance_ms=1,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_block"] == "sma_unavailable"
    assert out["exits"] == []
    assert out["entries"] == []


def test_position_defaults_paper():
    p = ClipPosition(base="BTC", entry_price=100.0, notional_eur=1000.0, qty=10.0, opened_ms=1)
    assert p.is_paper() is True
    assert p.venue == "paper"


def test_live_position_persists_venue():
    p = ClipPosition(
        base="BTC",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=1,
        venue="bitvavo",
    )
    assert p.is_paper() is False
    restored = ClipPosition.from_dict(p.to_dict())
    assert restored.venue == "bitvavo"
    assert restored.is_paper() is False


def test_allow_live_defaults_off():
    from bot.core.config import Settings

    assert Settings.model_fields["momentum_btc_rs_clip_allow_live"].default is False
    s = Settings(
        momentum_btc_rs_clip_enabled=True,
        momentum_btc_rs_clip_book_eur=20_000.0,
        momentum_btc_rs_clip_allow_live=False,
    )
    cfg = config_from_settings(s)
    assert cfg.alt_frac == 0.25
    assert cfg.btc_frac == 0.75
    text = open("bot/live/momentum_btc_rs_clip_runner.py", encoding="utf-8").read()
    assert "from bot.live.executor" not in text


def test_no_coin_hardcodes_in_clip_module():
    text = open("bot/live/momentum_btc_rs_clip.py", encoding="utf-8").read()
    for needle in ('== "UNI"', "== 'SOL'", 'base == "ETH"'):
        assert needle not in text


def test_discard_paper_lots_resets_cash_and_rebalance(tmp_path):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    cfg = ClipConfig(book_eur=20_000.0)
    r = BtcRsClipPaperRunner(
        cfg,
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": object()},
    )
    r.cash_eur = 100.0
    r.last_rebalance_ms = 99
    r.positions = [
        ClipPosition(
            base="BTC",
            entry_price=70_000.0,
            notional_eur=15_000.0,
            qty=0.2,
            opened_ms=1,
            venue="paper",
        ),
        ClipPosition(
            base="ARB",
            entry_price=0.2,
            notional_eur=5_000.0,
            qty=25_000.0,
            opened_ms=1,
            venue="paper",
        ),
    ]
    n = r.discard_paper_positions()
    assert n == 2
    assert r.positions == []
    assert r.cash_eur == 20_000.0
    assert r.last_rebalance_ms == 0
    led = (tmp_path / "l.jsonl").read_text()
    assert "paper_reset" in led


def test_paper_open_skips_venue(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def place_limit(self, *a, **k):
            self.placed.append((a, k))
            raise AssertionError("paper open must not place venue orders")

    gw = Gw()
    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    pos = asyncio.run(r._open_lot("BTC", 15_000.0, 70_000.0, "btc", ["sma50_up"]))
    assert pos is not None
    assert pos.is_paper() is True
    assert pos.venue == "paper"
    assert gw.placed == []
    led = (tmp_path / "l.jsonl").read_text()
    assert '"dry_run": true' in led
    assert '"venue": "paper"' in led


def test_clip_live_open_places_venue_order(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner
    from bot.live.momentum_runner import OrderState

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def best_bid_ask(self, symbol):
            return 70_000.0, 70_010.0

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

        async def quote_balance_eur(self):
            return 22_000.0

    async def go() -> None:
        gw = Gw()
        r = BtcRsClipPaperRunner(
            ClipConfig(book_eur=20_000.0),
            state_path=str(tmp_path / "s.json"),
            ledger_path=str(tmp_path / "l.jsonl"),
            dry_run=False,
            venues=("bitvavo",),
            gateways={"bitvavo": gw},
            reserved_quote_eur=2_000.0,
        )
        r.cash_eur = 20_000.0
        pos = await r._open_lot("BTC", 15_000.0, 70_000.0, "btc", ["sma50_up"])
        assert gw.placed and gw.placed[0]["side"] == "buy"
        assert gw.placed[0]["symbol"] == "BTCEUR"
        assert gw.placed[0]["post_only"] is False
        assert pos is not None
        assert pos.venue == "bitvavo"
        assert pos.is_paper() is False
        st = r.status()
        assert st["mode"] == "btc_rs_clip_live"
        assert st["dry_run"] is False
        assert st["paper_only"] is False
        led = (tmp_path / "l.jsonl").read_text()
        assert '"venue": "bitvavo"' in led
        assert '"dry_run": false' in led

    asyncio.run(go())


def test_clip_sell_reserves_15m_qty(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner
    from bot.live.momentum_runner import OrderState

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def best_bid_ask(self, symbol):
            return 9.8, 9.9

        async def place_limit(self, symbol, side, qty, price, *, post_only):
            self.placed.append({"symbol": symbol, "side": side, "qty": qty, "price": price})
            return OrderState("s1", "closed", qty, price, qty * price * 0.001)

        async def fetch_order(self, order_id, symbol):
            p = self.placed[-1]
            return OrderState("s1", "closed", p["qty"], p["price"], 0.0)

        async def cancel_order(self, order_id, symbol):
            return await self.fetch_order(order_id, symbol)

        async def base_free(self, base):
            return 150.0

        async def base_held(self, base):
            return 150.0

    gw = Gw()
    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
        reserved_qty={"AAA": 50.0},
    )
    pos = ClipPosition(
        base="AAA",
        entry_price=10.0,
        notional_eur=1_000.0,
        qty=100.0,
        opened_ms=1,
        venue="bitvavo",
        role="alt",
    )
    r.positions = [pos]
    r.cash_eur = 19_000.0
    sellable = asyncio.run(r._sellable_qty(pos))
    assert sellable == 100.0
    # 15m holds 50 of the 150 venue units; clip may only sell its own 100.
    assert sellable <= 150.0 - 50.0
    net = asyncio.run(r._close_lot(pos, 9.8, "rs_rotate"))
    assert net is not None
    assert gw.placed and gw.placed[0]["side"] == "sell"
    assert gw.placed[0]["qty"] == 100.0


def test_decision_cash_leaves_15m_bitvavo_cap(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    class Gw:
        async def quote_balance_eur(self):
            return 18_000.0

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": Gw()},
        reserved_quote_eur=2_000.0,
    )
    r.cash_eur = 20_000.0
    cash = asyncio.run(r._decision_cash())
    assert cash == 16_000.0


def test_reserved_quote_uses_15m_cap():
    from bot.core.config import Settings
    from bot.live.momentum_btc_rs_clip_runner import reserved_quote_eur_from_settings

    s = Settings(
        momentum_desk_enabled=True,
        momentum_multi_strat_enabled=True,
        momentum_desk_venue_cash_caps="bitvavo:2000",
        momentum_desk_mix_bitvavo_cap_eur=2_000.0,
    )
    assert reserved_quote_eur_from_settings(s) == 2000.0
    off = Settings(momentum_desk_enabled=False, momentum_multi_strat_enabled=False)
    assert reserved_quote_eur_from_settings(off) == 0.0
