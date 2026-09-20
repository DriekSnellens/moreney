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
