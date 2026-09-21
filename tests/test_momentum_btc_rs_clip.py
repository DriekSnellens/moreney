"""Paper BTC-core + RS clip — decision tests. No venue I/O."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.live.momentum_btc_rs_clip import ClipConfig, ClipPosition, completed_ohlc, evaluate_clip
from bot.live.momentum_btc_rs_clip_runner import BtcRsClipDeskManager, config_from_settings


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


def test_position_is_always_paper():
    p = ClipPosition(base="BTC", entry_price=100.0, notional_eur=1000.0, qty=10.0, opened_ms=1)
    assert p.is_paper() is True
    assert p.venue == "paper"


def test_manager_is_hard_paper():
    mgr = BtcRsClipDeskManager()
    st = mgr.status()
    assert st["allow_live"] is False
    assert st["paper_only"] is True
    from bot.core.config import Settings

    s = Settings(
        momentum_btc_rs_clip_enabled=True,
        momentum_btc_rs_clip_book_eur=20_000.0,
    )
    cfg = config_from_settings(s)
    assert cfg.alt_frac == 0.25
    assert cfg.btc_frac == 0.75
    text = open("bot/live/momentum_btc_rs_clip_runner.py", encoding="utf-8").read()
    assert "from bot.live.executor" not in text
    assert "allow_live=False" in text
    assert "paper_only=True" in text


def test_no_coin_hardcodes_in_clip_module():
    text = open("bot/live/momentum_btc_rs_clip.py", encoding="utf-8").read()
    for needle in ('== "UNI"', "== 'SOL'", 'base == "ETH"'):
        assert needle not in text
