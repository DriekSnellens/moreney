"""BTC-core + RS clip — decision and live-fill tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bot.live.momentum_btc_rs_clip import (
    ClipConfig,
    ClipPosition,
    completed_ohlc,
    evaluate_clip,
    residual_full_config,
)
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
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=80_000.0, btc_frac=0.75, alt_frac=0.25)
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
    cfg = ClipConfig(
        universe=("ETH",), min_qvol_eur=50_000.0, excess_floor=0.08, alt_frac=0.25, btc_frac=0.75
    )
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
    assert cfg.alt_frac == 0.80
    assert cfg.btc_frac == 0.20
    assert cfg.excess_floor == 0.04
    assert cfg.lookback_days == 10
    assert cfg.alt_trail_pct == 0.10
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
    assert r.cash_eur == 16_000.0


def test_decision_cash_live_uses_full_venue_not_book(tmp_path):
    """Live clip deploys Bitvavo free EUR, even when the paper book is smaller."""
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    class Gw:
        async def quote_balance_eur(self):
            return 17_976.95

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=10_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": Gw()},
        reserved_quote_eur=0.0,
    )
    r.cash_eur = 10_000.0
    cash = asyncio.run(r._decision_cash())
    assert cash == 17_976.95
    assert r.cash_eur == 17_976.95


def test_decision_cash_paper_still_caps_to_ledger(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    class Gw:
        async def quote_balance_eur(self):
            return 18_000.0

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": Gw()},
        reserved_quote_eur=0.0,
    )
    r.cash_eur = 12_000.0
    # dry_run short-circuits venue fetch → synthetic ledger wins
    cash = asyncio.run(r._decision_cash())
    assert cash == 12_000.0


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


def test_clip_status_btc_prefers_live_mark(tmp_path):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
    )
    r.last_decision = {"btc": 60_000.0, "sma50": 65_000.0, "caption": "stale decide"}
    r.marks["BTC"] = 71_234.5
    st = r.status()
    assert st["btc"] == 71_234.5


def test_clip_sell_all_places_live_sells(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner
    from bot.live.momentum_runner import OrderState

    class Gw:
        def __init__(self) -> None:
            self.placed: list[dict] = []

        async def best_bid_ask(self, symbol):
            if str(symbol).startswith("BTC"):
                return 71_000.0, 71_010.0
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
    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    r.cash_eur = 0.0
    r.last_rebalance_ms = 99
    r.positions = [
        ClipPosition(
            base="BTC",
            entry_price=70_000.0,
            notional_eur=15_000.0,
            qty=0.2,
            opened_ms=1,
            venue="bitvavo",
            role="btc",
            holding_id="clip-btc",
        ),
        ClipPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=5_000.0,
            qty=500.0,
            opened_ms=1,
            venue="bitvavo",
            role="alt",
            holding_id="clip-alt",
        ),
    ]
    r.marks = {"BTC": 71_000.0, "AAA": 9.8}
    out = asyncio.run(r.sell_all())
    assert out["ok"] is True
    assert out["closed"] == 2
    assert r.positions == []
    assert r.last_rebalance_ms == 0
    assert [p["side"] for p in gw.placed] == ["sell", "sell"]
    led = (tmp_path / "l.jsonl").read_text()
    assert "manual_sell_all" in led


def test_clip_paper_sell_skips_venue(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    class Gw:
        async def place_limit(self, *args, **kwargs):
            raise AssertionError("paper sell must not place venue orders")

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": Gw()},
    )
    r.positions = [
        ClipPosition(
            base="BTC",
            entry_price=70_000.0,
            notional_eur=15_000.0,
            qty=0.2,
            opened_ms=1,
            venue="paper",
            role="btc",
            holding_id="clip-btc",
        )
    ]
    out = asyncio.run(r.sell("clip-btc"))
    assert out["ok"] is True
    assert r.positions == []


def test_clip_manager_sell_requires_running():
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipDeskManager

    m = BtcRsClipDeskManager()
    import asyncio

    assert asyncio.run(m.sell("x")) == {"ok": False, "reason": "not_running"}
    assert asyncio.run(m.sell_all()) == {"ok": False, "reason": "not_running"}
    assert asyncio.run(m.reconcile()) == {"ok": False, "reason": "not_running"}


def test_midweek_does_not_trim_existing_bags():
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
        sleeve_eur={"btc": 15_000.0, "alt": 5_000.0},
    )
    assert out["rebalance_due"] is False
    assert out["trims"] == []
    assert not any(e["base"] == "BTC" for e in out["exits"])
    assert not any(e.get("reasons") and "size_to_frac" in e["reasons"] for e in out["entries"])


def test_weekly_due_trims_btc_toward_winner_frac():
    btc = _bars(60, 100.0, 0.05)
    eth = _bars(40, 10.0, 0.0)
    eth += _bars(21, 10.0, 0.8, vol=20_000.0)
    t0 = 1_700_000_000_000
    for i, r in enumerate(eth):
        r[0] = t0 + i * 86_400_000
    ohlc = {"BTC": btc, "ETH": eth}
    cfg = ClipConfig(universe=("ETH",), min_qvol_eur=1.0, rebalance_days=7)
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc", "ETH": "alt"},
        cash_eur=30.0,
        deployed_eur=20_000.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
        sleeve_eur={"btc": 15_000.0, "alt": 5_000.0},
    )
    assert out["rebalance_due"] is True
    assert out["want_alt"] == "ETH"
    assert any(t["base"] == "BTC" and t["reason"] == "size_to_frac" for t in out["trims"])
    assert any("size_to_frac" in (e.get("reasons") or []) for e in out["entries"])


def _rising_alt() -> list[list[float]]:
    eth = _bars(40, 10.0, 0.0)
    eth += _bars(21, 10.0, 0.8, vol=20_000.0)
    t0 = 1_700_000_000_000
    for i, row in enumerate(eth):
        row[0] = t0 + i * 86_400_000
    return eth


def test_require_alt_sma_skips_a_bounce_under_its_average():
    btc = _bars(60, 100.0, 0.2)
    alt = _bars(30, 100.0, 0.0, vol=20_000.0)
    alt += _bars(15, 20.0, 0.0, vol=20_000.0)
    alt += _bars(10, 20.0, 2.0, vol=20_000.0)
    t0 = 1_700_000_000_000
    for i, row in enumerate(alt):
        row[0] = t0 + i * 86_400_000
    ohlc = {"BTC": btc, "ETH": alt}
    kwargs = dict(
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    plain = evaluate_clip(ohlc, ClipConfig(universe=("ETH",), min_qvol_eur=1.0), **kwargs)
    gated = evaluate_clip(
        ohlc,
        ClipConfig(universe=("ETH",), min_qvol_eur=1.0, require_alt_sma=True),
        **kwargs,
    )
    assert plain["want_alt"] == "ETH"
    assert gated["want_alt"] is None
    assert any(row["reason"] == "below_sma" for row in gated["skipped"])


def test_residual_full_weekly_drops_btc_and_tops_up_the_alt():
    ohlc = {"BTC": _bars(60, 100.0, 0.05), "ETH": _rising_alt()}
    cfg = residual_full_config(ClipConfig(universe=("ETH",), min_qvol_eur=1.0))
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc", "ETH": "alt"},
        cash_eur=30.0,
        deployed_eur=20_000.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
        sleeve_eur={"btc": 4_000.0, "alt": 16_000.0},
    )
    assert out["rebalance_due"] is True
    assert out["want_alt"] == "ETH"
    assert any(e["base"] == "BTC" and e["reason"] == "btc_sleeve_off" for e in out["exits"])
    topup = next(e for e in out["entries"] if e["base"] == "ETH")
    assert topup["notional_eur"] == 4_030.0
    assert not any(e["base"] == "BTC" for e in out["entries"])


def test_flat_book_waits_for_the_weekly_clock_after_a_sale():
    ohlc = {"BTC": _bars(60, 100.0, 0.05), "ETH": _rising_alt()}
    cfg = residual_full_config(ClipConfig(universe=("ETH",), min_qvol_eur=1.0))
    now_ms = 20 * 86_400_000
    out = evaluate_clip(
        ohlc,
        cfg,
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=now_ms,
        last_rebalance_ms=now_ms - 2 * 86_400_000,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["rebalance_due"] is False
    assert out["entries"] == []
    assert out["exits"] == []


def test_residual_full_midweek_keeps_both_bags():
    ohlc = {"BTC": _bars(60, 100.0, 0.05), "ETH": _rising_alt()}
    cfg = residual_full_config(ClipConfig(universe=("ETH",), min_qvol_eur=1.0))
    now_ms = 20 * 86_400_000
    out = evaluate_clip(
        ohlc,
        cfg,
        held={"BTC": "btc", "ETH": "alt"},
        cash_eur=30.0,
        deployed_eur=20_000.0,
        now_ms=now_ms,
        last_rebalance_ms=now_ms - 2 * 86_400_000,
        now=datetime(2026, 6, 1, tzinfo=UTC),
        sleeve_eur={"btc": 4_000.0, "alt": 16_000.0},
    )
    assert out["rebalance_due"] is False
    assert out["exits"] == []
    assert out["trims"] == []
    assert out["entries"] == []


def test_pending_pack_arms_when_the_alt_is_sold(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0, alt_trail_pct=0.10),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        pending_pack="residual_full",
    )
    r.last_rebalance_ms = 10**13
    r.positions = [
        ClipPosition(
            base="BTC",
            entry_price=100.0,
            notional_eur=4_000.0,
            qty=40.0,
            opened_ms=1,
            venue="paper",
            role="btc",
            peak_px=100.0,
        ),
        ClipPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=16_000.0,
            qty=1_600.0,
            opened_ms=1,
            venue="paper",
            role="alt",
            peak_px=10.0,
        ),
    ]
    assert r._rebalance_due(r.last_rebalance_ms + 2 * 86_400_000) is False
    r.marks = {"AAA": 8.8, "BTC": 100.0}
    asyncio.run(r.manage_alt_trail())
    assert [p.base for p in r.positions] == ["BTC"]
    assert r.pack_mode == "residual_full"
    assert r.pending_pack == ""
    assert r.cfg.btc_frac == 0.0
    assert r.cfg.alt_frac == 1.0
    assert r.cfg.excess_floor == 0.035
    assert r.cfg.require_alt_sma is True
    assert r.cfg.cash_when_no_alt is True
    raw = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert raw["pack_mode"] == "residual_full"


def test_empty_live_restart_keeps_the_weekly_clock(tmp_path):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
    )
    r.last_rebalance_ms = 1_790_588_675_335
    r.positions = []
    r.discard_paper_positions()
    assert r.last_rebalance_ms == 1_790_588_675_335


def test_pending_pack_stays_idle_until_the_clock_or_a_sale(tmp_path):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        pending_pack="residual_full",
    )
    r.last_rebalance_ms = 10**13
    r.positions = [
        ClipPosition(
            base="BTC",
            entry_price=100.0,
            notional_eur=4_000.0,
            qty=40.0,
            opened_ms=1,
            venue="paper",
            role="btc",
        )
    ]
    assert r._rebalance_due(r.last_rebalance_ms + 2 * 86_400_000) is False
    assert r.pack_mode == "clip_20_80"
    assert r.cfg.btc_frac == 0.20


def test_alt_trail_sells_dumped_sleeve(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0, alt_trail_pct=0.10),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
    )
    r.positions = [
        ClipPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=5_000.0,
            qty=500.0,
            opened_ms=1,
            venue="paper",
            role="alt",
            peak_px=10.0,
        )
    ]
    r.marks = {"AAA": 8.8}
    out = asyncio.run(r.manage_alt_trail())
    assert out and out[0]["reason"] == "alt_trail"
    assert r.positions == []


def test_alt_trail_does_not_fire_on_first_live_mark(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0, alt_trail_pct=0.10),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
    )
    r.positions = [
        ClipPosition(
            base="AAA",
            entry_price=10.0,
            notional_eur=5_000.0,
            qty=500.0,
            opened_ms=1,
            venue="paper",
            role="alt",
            peak_px=0.0,
        )
    ]
    r.marks = {"AAA": 8.8}
    out = asyncio.run(r.manage_alt_trail())
    assert out == []
    assert r.positions and r.positions[0].peak_px == 8.8


def test_clip_runner_samples_equity_curve(tmp_path):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=False,
    )
    r._sample_equity()
    assert len(r.equity_curve) == 1
    assert r.equity_curve[0][1] == 20_000.0
    r.cash_eur = 19_500.0
    r._sample_equity()
    assert len(r.equity_curve) == 1
    assert r.equity_curve[0][1] == 19_500.0
    r.equity_curve[-1][0] = 1.0
    r.cash_eur = 19_800.0
    r._sample_equity()
    assert len(r.equity_curve) == 2
    st = r.status()
    assert st["equity_curve"][-1][1] == 19_800.0
    raw = (tmp_path / "s.json").read_text(encoding="utf-8")
    assert "equity_curve" in raw


class _Feed:
    def __init__(self, px: float = 10.0) -> None:
        self.px = px

    async def last_price(self, base: str) -> float:
        return self.px


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


def _live_clip(tmp_path, gw: _ReconGw, *, reserved_qty: dict[str, float] | None = None):
    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    return BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        feed=_Feed(),
        dry_run=False,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
        reserved_qty=reserved_qty or {},
    )


def _seed_clip(
    r,
    *,
    base: str,
    qty: float,
    px: float,
    role: str,
    hid: str,
    opened_ms: int = 1_000,
) -> ClipPosition:
    pos = ClipPosition(
        base=base,
        entry_price=px,
        notional_eur=qty * px,
        qty=qty,
        opened_ms=opened_ms,
        venue="bitvavo",
        role=role,
        holding_id=hid,
    )
    r.positions.append(pos)
    r.marks[base] = px
    return pos


def test_reconcile_external_closes_gone_clip_keeps_nothing(tmp_path):
    """UI-sold clip lots leave the book. No sell orders."""
    import asyncio
    import json

    gw = _ReconGw(
        held={},
        sells=[{"base": "AAA", "qty": 1333.98, "price": 4.47, "fee_eur": 6.0, "ts_ms": 2_000}],
    )
    r = _live_clip(tmp_path, gw)
    r.cash_eur = 15_000.0
    _seed_clip(r, base="AAA", qty=1333.98, px=3.7456, role="alt", hid="clip-near")
    closed = asyncio.run(r.reconcile_external_inventory())
    assert gw.placed == []
    assert len(closed) == 1
    assert closed[0]["base"] == "AAA"
    assert closed[0]["reason"] == "manual_external"
    assert closed[0]["detail"] == "reconcile_external_delta"
    assert closed[0]["quantity"] == pytest.approx(1333.98)
    assert closed[0]["exit_price"] == pytest.approx(4.47)
    assert r.positions == []
    led = json.loads("[" + ",".join((tmp_path / "l.jsonl").read_text().splitlines()) + "]")
    exits = [row for row in led if row.get("event") == "exit"]
    assert len(exits) == 1
    assert exits[0]["reason"] == "manual_external"
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["positions"] == []
    assert r.cash_eur == pytest.approx(15_000.0 + 1333.98 * 4.47 - 6.0)


def test_reconcile_external_skips_dry_run_clip(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipPaperRunner

    gw = _ReconGw(held={})
    r = BtcRsClipPaperRunner(
        ClipConfig(book_eur=20_000.0),
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        dry_run=True,
        venues=("bitvavo",),
        gateways={"bitvavo": gw},
    )
    _seed_clip(r, base="AAA", qty=100.0, px=10.0, role="alt", hid="p")
    r.positions[0].venue = "bitvavo"
    closed = asyncio.run(r.reconcile_external_inventory())
    assert closed == []
    assert len(r.positions) == 1
    assert gw.placed == []


def test_reconcile_external_reserves_15m_qty(tmp_path):
    """Venue coins that belong to 15m must not be sold or imported as clip."""
    import asyncio

    gw = _ReconGw(held={"AAA": 50.0, "LINK": 20.0})
    r = _live_clip(tmp_path, gw, reserved_qty={"LINK": 20.0})
    r.cash_eur = 0.0
    _seed_clip(r, base="AAA", qty=100.0, px=10.0, role="alt", hid="clip-a")
    r._other_desk_qty = (  # type: ignore[method-assign]
        lambda base: 50.0
        if str(base).upper() == "AAA"
        else 20.0
        if str(base).upper() == "LINK"
        else 0.0
    )
    closed = asyncio.run(r.reconcile_external_inventory())
    assert len(closed) == 1
    assert closed[0]["base"] == "AAA"
    assert closed[0]["quantity"] == pytest.approx(100.0)
    assert r.positions == []
    assert gw.placed == []
    assert not any(p.base == "LINK" for p in r.positions)


def test_reconcile_external_keeps_still_held_clip(tmp_path):
    import asyncio

    gw = _ReconGw(held={"AAA": 100.0})
    r = _live_clip(tmp_path, gw)
    _seed_clip(r, base="AAA", qty=100.0, px=10.0, role="alt", hid="clip-a")
    closed = asyncio.run(r.reconcile_external_inventory())
    assert closed == []
    assert len(r.positions) == 1
    assert r.positions[0].qty == pytest.approx(100.0)
    assert gw.placed == []


def test_clip_sell_all_books_gone_lots_without_orders(tmp_path):
    """Dashboard flatten after a Bitvavo UI sell must not place another sell."""
    import asyncio

    gw = _ReconGw(
        held={},
        sells=[{"base": "AAA", "qty": 500.0, "price": 11.0, "fee_eur": 2.0, "ts_ms": 2_000}],
    )
    r = _live_clip(tmp_path, gw)
    r.cash_eur = 0.0
    _seed_clip(r, base="AAA", qty=500.0, px=10.0, role="alt", hid="clip-a")
    r.marks["AAA"] = 11.0
    out = asyncio.run(r.sell_all())
    assert out["ok"] is True
    assert out["closed"] == 1
    assert r.positions == []
    assert gw.placed == []
    led = (tmp_path / "l.jsonl").read_text()
    assert "manual_external" in led


def test_clip_refresh_live_reads_venue_eur_on_every_poll(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipDeskManager

    class _BalGw(_ReconGw):
        def __init__(self) -> None:
            super().__init__(held={})
            self.eur = 19_000.0
            self.calls = 0

        async def quote_balance_eur(self) -> float:
            self.calls += 1
            return self.eur

    gw = _BalGw()
    r = _live_clip(tmp_path, gw)
    r.cash_eur = 1.0
    mgr = BtcRsClipDeskManager()
    mgr._runner = r
    first = asyncio.run(mgr.refresh_live())
    gw.eur = 18_250.0
    second = asyncio.run(mgr.refresh_live())
    assert gw.calls == 2
    assert first["equity_eur"] == 19_000.0
    assert second["equity_eur"] == 18_250.0
    assert gw.placed == []


def test_clip_refresh_live_books_external(tmp_path):
    import asyncio

    from bot.live.momentum_btc_rs_clip_runner import BtcRsClipDeskManager

    gw = _ReconGw(held={})
    r = _live_clip(tmp_path, gw)
    _seed_clip(r, base="AAA", qty=10.0, px=10.0, role="alt", hid="gone")
    mgr = BtcRsClipDeskManager()
    mgr._runner = r
    st = asyncio.run(mgr.refresh_live())
    assert st["positions"] == []
    assert r.positions == []
    led = (tmp_path / "l.jsonl").read_text()
    assert "manual_external" in led
    assert gw.placed == []


def test_ledger_table_maps_clip_manual_external():
    from bot.live.momentum_dashboard import _ledger_table

    html = _ledger_table(
        [
            {
                "ts": "2026-09-25T13:08:00+00:00",
                "event": "exit",
                "desk": "btc_rs_clip",
                "base": "AAA",
                "venue": "bitvavo",
                "notional_eur": 5000.0,
                "exit_price": 4.47,
                "net_eur": 950.0,
                "reason": "manual_external",
                "detail": "reconcile_external_delta",
            }
        ]
    )
    assert "AAA" in html
    assert "4.47" in html
    assert "manual_external" in html


def _brk20_thrust(
    n: int, base_px: float, thrust_px: float, vol: float = 20_000.0
) -> list[list[float]]:
    """Flat range then close above prior 20d high — brk20_day candidate."""
    rows: list[list[float]] = []
    t0 = 1_700_000_000_000
    day = 86_400_000
    for i in range(n - 1):
        px = base_px
        rows.append([t0 + i * day, px, px * 1.01, px * 0.99, px, vol])
    rows.append(
        [
            t0 + (n - 1) * day,
            base_px,
            thrust_px * 1.01,
            base_px * 0.99,
            thrust_px,
            vol,
        ]
    )
    return rows


def test_daily_green_brk20_day_picks_breakout_and_caps_book():
    from bot.live.momentum_btc_rs_clip import EXPAND_LIQUID_UNIVERSE, daily_green_config

    btc = _bars(80, 100.0, 0.05)
    # ETH flat below range; SOL breaks 20d high
    eth = _bars(80, 10.0, 0.0, vol=20_000.0)
    sol = _brk20_thrust(80, base_px=8.0, thrust_px=8.5, vol=20_000.0)
    t0 = 1_700_000_000_000
    for series in (btc, eth, sol):
        for i, r in enumerate(series):
            r[0] = t0 + i * 86_400_000
    cfg = daily_green_config(
        ClipConfig(universe=("ETH", "SOL"), min_qvol_eur=1.0, book_eur=1_700.0)
    )
    out = evaluate_clip(
        {"BTC": btc, "ETH": eth, "SOL": sol},
        cfg,
        held={},
        cash_eur=5_000.0,
        deployed_eur=0.0,
        now_ms=10**12,
        last_rebalance_ms=0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_on"] is True
    assert out["want_alt"] == "SOL"
    alts = [e for e in out["entries"] if e["role"] == "alt"]
    assert alts and alts[0]["base"] == "SOL"
    assert alts[0]["notional_eur"] == pytest.approx(1_700.0, abs=0.01)
    assert "Daily sleeve" in out["caption"]
    assert "brk20_day" in out["caption"]
    assert cfg.entry_mode == "brk20_day"
    assert cfg.time_max_days == 5
    assert cfg.hard_stop_pct == pytest.approx(0.05)
    assert cfg.alt_trail_pct == pytest.approx(0.12)
    assert cfg.universe == ("ETH", "SOL")
    assert len(daily_green_config().universe) == len(EXPAND_LIQUID_UNIVERSE)


def test_rebalance_weekday_gate_only_fires_on_target_day():
    """Age due + weekday pin: Tuesday-only clock."""
    btc = _bars(60, 100.0, 0.1)
    eth = _bars(60, 10.0, 0.0, vol=20_000.0)
    t0 = 1_700_000_000_000
    for series in (btc, eth):
        for i, r in enumerate(series):
            r[0] = t0 + i * 86_400_000
    now_ms = t0 + 60 * 86_400_000
    last = now_ms - 8 * 86_400_000  # age > 7d
    cfg = ClipConfig(universe=("ETH",), rebalance_days=7, rebalance_weekday=1, min_qvol_eur=1.0)
    # Monday 2026-06-01 is weekday 0
    mon = evaluate_clip(
        {"BTC": btc, "ETH": eth},
        cfg,
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=now_ms,
        last_rebalance_ms=last,
        now=datetime(2026, 6, 1, tzinfo=UTC),  # Monday
    )
    assert mon["rebalance_due"] is False
    tue = evaluate_clip(
        {"BTC": btc, "ETH": eth},
        cfg,
        held={},
        cash_eur=20_000.0,
        deployed_eur=0.0,
        now_ms=now_ms,
        last_rebalance_ms=last,
        now=datetime(2026, 6, 2, tzinfo=UTC),  # Tuesday
    )
    assert tue["rebalance_due"] is True
