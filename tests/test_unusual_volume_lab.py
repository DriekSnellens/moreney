from __future__ import annotations

from bot.research.unusual_volume_lab.scanner import ScanSpec, forward_audit, scan
from bot.research.unusual_volume_lab.system import SystemSpec, ema, macd, rsi, run_system

DAY = 86_400_000
H4 = 4 * 3_600_000
T0 = 1_717_200_000_000  # 2024-06-01 00:00 UTC


def _daily(prices, vols, t0=T0):
    return [
        [t0 + i * DAY, p, p * 1.02, p * 0.98, p, v]
        for i, (p, v) in enumerate(zip(prices, vols, strict=True))
    ]


def test_ema_macd_rsi_are_bounded_and_track_a_trend():
    up = [100.0 + i for i in range(60)]
    e = ema(up, 20)
    assert len(e) == 60 and e[-1] < up[-1] and e[-1] > e[-10]
    line, sig = macd(up)
    assert line[-1] > 0 and line[-1] > sig[-1] * 0.9
    r = rsi(up, 14)
    assert r[-1] == 100.0
    flat = [100.0] * 30
    assert all(0.0 <= x <= 100.0 for x in rsi(flat, 14))
    down = [100.0 - i * 0.5 for i in range(40)]
    assert rsi(down, 14)[-1] == 0.0


def test_scan_flags_only_the_volume_anomaly_and_reports_the_multiple():
    vols = [1_000.0] * 40
    vols[35] = 10_000.0
    prices = [10.0] * 40
    ohlc = {"AAA": _daily(prices, vols)}
    sig = scan(
        ohlc,
        ScanSpec(mult=3.0, avg_days=30, min_day_qvol_eur=50_000),
        start="2024-06-01",
        end="2024-08-01",
    )
    assert len(sig) == 1
    s = sig[0]
    assert s["base"] == "AAA" and s["idx"] == 35 and abs(s["vol_mult"] - 10.0) < 1e-9
    # a tiny market never qualifies even with a 10x spike
    small = {"BBB": _daily(prices, [v / 100 for v in vols])}
    assert scan(small, ScanSpec(), start="2024-06-01", end="2024-08-01") == []


def test_forward_audit_measures_from_the_next_open():
    vols = [1_000.0] * 45
    vols[35] = 10_000.0
    prices = [10.0] * 36 + [11.0, 12.0, 13.0, 12.0, 11.0, 10.0, 10.0, 10.0, 10.0]
    ohlc = {"AAA": _daily(prices, vols)}
    sig = scan(ohlc, ScanSpec(), start="2024-06-01", end="2024-08-01")
    a = forward_audit(ohlc, sig, horizons=(1, 3))
    assert a["n_used"] == 1
    # entry at the open of day 36 (11.0); the 3-day close is 13.0
    assert abs(a["horizons"][3]["close"]["mean"] - (13.0 / 11.0 - 1.0)) < 1e-9
    assert a["horizons"][1]["close"]["p_pos"] == 0.0  # day-36 close equals its open


def _h4_series(n, start_px, path):
    """4h rows: flat ``start_px`` then a list of (open, close) pairs."""
    rows = []
    t = T0 - 60 * DAY
    for _ in range(n):
        rows.append([t, start_px, start_px * 1.001, start_px * 0.999, start_px, 1_000.0])
        t += H4
    for o, c in path:
        rows.append([t, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 50_000.0])
        t += H4
    return rows


def test_run_system_enters_after_confluence_and_exits_on_the_trail():
    # signal day: 2024-06-01; the 4h path afterwards trends up then rolls over.
    pre = [[T0 - (40 - i) * DAY, 10.0, 10.1, 9.9, 10.0, 1_000.0] for i in range(40)]
    spike = [T0, 10.0, 10.5, 9.9, 10.4, 20_000.0]
    tail = [[T0 + (i + 1) * DAY, 10.0, 10.1, 9.9, 10.0, 1_000.0] for i in range(10)]
    daily = {"AAA": pre + [spike] + tail}
    ups = [(10.0 + 0.1 * i, 10.1 + 0.1 * i) for i in range(0, 60)]  # 60 bars of grind higher
    down = [(16.0, 15.0), (15.0, 14.0), (14.0, 13.0)]
    h4_rows = _h4_series(200, 10.0, ups + down)
    # place the 4h tape so that the signal day ends inside the rising leg
    shift = T0 + DAY - h4_rows[200 + 10][0]
    for r in h4_rows:
        r[0] += shift
    h4 = {"AAA": h4_rows}
    sig = scan(
        daily,
        ScanSpec(mult=3.0, avg_days=30, min_day_qvol_eur=50_000),
        start="2024-06-01",
        end="2024-06-02",
    )
    assert len(sig) == 1
    spec = SystemSpec(
        rsi_exit=0, trail_mode="pct", trail_pct=0.10, hard_stop=0.08, time_bars=0, book_eur=1_000.0
    )
    r = run_system(daily, h4, sig, spec, start="2024-06-01", end="2024-06-02")
    assert r["n_candidates"] == 1 and r["n_trades"] == 1
    t = r["trades"][0]
    assert t["reason"] == "pct_trail" and t["pnl_eur"] > 0 and t["entry_px"] > 10.0
    # an RSI exit sells much earlier in a straight-up grind
    r2 = run_system(
        daily,
        h4,
        sig,
        SystemSpec(rsi_exit=75, trail_mode="pct", time_bars=0),
        start="2024-06-01",
        end="2024-06-02",
    )
    assert r2["trades"][0]["reason"] == "rsi" and r2["trades"][0]["bars"] < t["bars"]
