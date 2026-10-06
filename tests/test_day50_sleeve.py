"""Synthetic tape for the 1-day +50% sleeve fill and label."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.moonshot_preimage.day50 import (
    Exit,
    Gate,
    _build_pick,
    _index_bases,
    build_signals,
    resolve_bar,
    simulate,
)


def _bars(n: int, *, close: float = 100.0, vol: float = 1_000.0) -> list[list[float]]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    px = close
    for i in range(n):
        ts = (start + timedelta(days=i)).timestamp() * 1000.0
        rows.append([ts, px, px * 1.01, px * 0.99, px, vol])
    return rows


def test_resolve_up_day_takes_profit_before_stop() -> None:
    hit = resolve_bar(open_raw=100.0, o=100.0, h=160.0, l=80.0, c=150.0, hard=0.12, tp=0.50)
    assert hit is not None
    assert hit[0] == "take_profit"
    assert abs(hit[1] - 150.0 * 0.999) < 1e-6


def test_resolve_down_day_stops_before_wick() -> None:
    hit = resolve_bar(open_raw=100.0, o=100.0, h=160.0, l=80.0, c=90.0, hard=0.12, tp=0.50)
    assert hit is not None
    assert hit[0] == "hard_stop"


def test_label_uses_entry_day_not_signal_day() -> None:
    btc = _bars(80, close=50_000.0, vol=10.0)
    alt = _bars(80, close=100.0, vol=1_000.0)
    # Signal day index 60: +20% close and a volume spike. Entry day index 61: +60% from the open.
    alt[60][1] = 100.0
    alt[60][2] = 122.0
    alt[60][3] = 100.0
    alt[60][4] = 120.0
    alt[60][5] = 8_000.0
    alt[61][1] = 120.0
    alt[61][2] = 192.0
    alt[61][3] = 118.0
    alt[61][4] = 186.0
    alt[61][5] = 8_000.0
    series = {"BTC": _as(btc), "ALT": _as(alt)}
    sigs = build_signals(series, min_qvol=1_000.0)
    hit = [s for s in sigs if s.signal == "2024-03-01"]
    assert hit, "expected a signal on the spike eve"
    assert hit[0].entry == "2024-03-02"
    assert hit[0].hit50
    assert hit[0].r1 < 0.30
    # The +55% close is the entry day, so it must not be baked into the signal return.
    assert abs(hit[0].r1 - 0.20) < 1e-9


def test_sleeve_books_take_profit_on_the_fifty_day() -> None:
    btc = _bars(80, close=50_000.0, vol=10.0)
    alt = _bars(80, close=100.0, vol=1_000.0)
    alt[60][1] = 100.0
    alt[60][2] = 122.0
    alt[60][3] = 100.0
    alt[60][4] = 120.0
    alt[60][5] = 8_000.0
    alt[61][1] = 120.0
    alt[61][2] = 192.0
    alt[61][3] = 118.0
    alt[61][4] = 186.0
    series_map = {"BTC": _as(btc), "ALT": _as(alt)}
    sigs = build_signals(series_map, min_qvol=1_000.0)
    by_day: dict[str, list[int]] = {}
    for i, s in enumerate(sigs):
        by_day.setdefault(s.signal, []).append(i)
    gate = Gate("r1_15", r1_min=0.15, vol_min=2.0)
    pick = _build_pick(
        sigs,
        by_day,
        gate,
        "r1",
        require_btc=False,
        gap_cap=None,
        btc_on=set(),
    )
    assert pick.get("2024-03-01") is not None
    bases = _index_bases(series_map)
    cal = sorted(series_map["BTC"].dates)
    out = simulate(
        bases,
        sigs,
        pick,
        Exit("tp50_hs12_d1", tp=0.50, hard=0.12, hold=1, trail=0.0),
        cal,
        book=1_700.0,
        arm_start="2024-02-01",
        arm_end="2024-03-15",
        keep_trades=True,
    )
    assert out["trades"] == 1
    assert out["hits"] == 1
    assert out["precision"] == 1.0
    assert out["pnl"] > 600.0
    assert out["trades_detail"][0]["reason"] == "take_profit"
    assert out["trades_detail"][0]["base"] == "ALT"


def _as(rows: list[list[float]]):
    from bot.research.moonshot_preimage.day50 import _as_series

    return _as_series(rows)
