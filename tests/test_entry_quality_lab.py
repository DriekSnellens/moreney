from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.entry_quality_lab.engine import (
    ext_sma,
    gate_ext_max,
    gate_impulse_max,
    gate_ret1_min,
    impulse_share,
    off_high,
    range_pos,
    rsi,
    scale_ext,
)
from bot.research.entry_quality_lab.flow import (
    flow_through,
    parse_klines,
    taker_buy_ratio,
    taker_ratio_z,
    trade_count_ratio,
)

DAY_MS = 86_400_000
T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def _day(i: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)).strftime("%Y-%m-%d")


def _bars(closes: list[float], *, hi_pad: float = 1.0, vol: float = 1_000.0) -> list[list[float]]:
    return [[T0 + i * DAY_MS, c, c * hi_pad, c * 0.98, c, vol] for i, c in enumerate(closes)]


def test_location_features_are_causal_and_signed() -> None:
    closes = [100.0] * 59 + [130.0]
    ohlc = {"X": _bars(closes, hi_pad=1.1)}
    d = _day(59)
    assert ext_sma(ohlc, "X", d, 20) is not None and ext_sma(ohlc, "X", d, 20) > 0.1
    # 60d high is the padded high of the last bar (143); close 130 is 9% below it.
    oh = off_high(ohlc, "X", d, 60)
    assert oh is not None and abs(oh - (130 / 143 - 1)) < 1e-9
    rp = range_pos(ohlc, "X", d, 20)
    assert rp is not None and 0.0 < rp < 1.0
    # Earlier date never sees the jump.
    assert abs(ext_sma(ohlc, "X", _day(58), 20) or 0.0) < 1e-9
    assert rsi(ohlc, "X", d) == 100.0
    assert gate_ext_max(ohlc, 20, 0.10)(d, "X") is False
    assert gate_ext_max(ohlc, 20, 0.50)(d, "X") is True
    assert gate_ext_max(ohlc, 20, 0.10)(d, "MISSING") is True  # no data = no opinion


def test_impulse_share_flags_single_candle_windows() -> None:
    # One +30% candle, then a -8% retrace on the skipped day.
    closes = [100.0] * 10 + [130.0, 119.6]
    ohlc = {"X": _bars(closes)}
    d = _day(len(closes) - 1)
    share = impulse_share(ohlc, "X", d, lookback=10, skip=1)
    assert share is not None and abs(share - 1.0) < 1e-9
    assert gate_impulse_max(ohlc, 0.7)(d, "X") is False
    assert gate_ret1_min(ohlc, 0.05)(d, "X") is False  # -8% retrace blocks
    assert gate_ret1_min(ohlc, 0.10)(d, "X") is True
    # Steady grind: ten +2% days, share ~ 1/10.
    steady = [100.0 * 1.02**i for i in range(12)]
    ohlc2 = {"X": _bars(steady)}
    s2 = impulse_share(ohlc2, "X", _day(11))
    assert s2 is not None and s2 < 0.15


def test_scale_ext_interpolates() -> None:
    closes = [100.0] * 19 + [116.0]
    ohlc = {"X": _bars(closes)}
    d = _day(19)
    ext = ext_sma(ohlc, "X", d, 20) or 0.0
    s = scale_ext(ohlc, 20, 0.08, 0.25, floor=0.25)(d, "X")
    expect = 1.0 - 0.75 * (ext - 0.08) / 0.17
    assert abs(s - expect) < 1e-9
    assert scale_ext(ohlc, 20, 0.30, 0.50)(d, "X") == 1.0


def test_flow_features_from_klines() -> None:
    raw = []
    for i in range(40):
        ts = T0 + i * DAY_MS
        vol = 1000.0
        taker = 450.0 if i < 37 else 600.0  # last 3 days buyers take over
        n_trades = 100 if i < 37 else 200
        raw.append(
            [
                ts,
                "1",
                "1",
                "1",
                "1",
                str(vol),
                ts + DAY_MS - 1,
                "1000",
                n_trades,
                str(taker),
                "1",
                "0",
            ]
        )
    rows = parse_klines(raw)
    d = _day(39)
    assert len(flow_through(rows, d)) == 40
    assert len(flow_through(rows, _day(10))) == 11
    assert abs((taker_buy_ratio(rows, d, 3) or 0) - 0.6) < 1e-9
    assert abs((taker_buy_ratio(rows, _day(36), 3) or 0) - 0.45) < 1e-9
    z = taker_ratio_z(rows, d, n=3, base_n=30)
    assert z is None  # flat trailing distribution has zero std -> no opinion
    tr = trade_count_ratio(rows, d, n=3, base_n=30)
    assert tr is not None and abs(tr - 2.0) < 1e-9
