"""Structural profit on a rising name, filled when the bar trades through."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.research.horizon_lab.bank import BankSpec, level_exit, run_momentum_bank


def test_stop_wins_when_the_bar_trades_through_both_levels() -> None:
    both = level_exit(
        entry=100.0, open_=100.0, high=110.0, low=90.0, tp_pct=0.03, stop_pct=0.02
    )
    assert both is not None and both[0] == "stop" and abs(both[1] - 98.0) < 1e-9
    tp = level_exit(
        entry=100.0, open_=100.0, high=110.0, low=99.0, tp_pct=0.03, stop_pct=0.02
    )
    assert tp is not None and tp[0] == "tp" and abs(tp[1] - 103.0) < 1e-9
    gap = level_exit(
        entry=100.0, open_=110.0, high=112.0, low=108.0, tp_pct=0.03, stop_pct=0.02
    )
    assert gap is not None and gap[0] == "tp" and abs(gap[1] - 110.0) < 1e-9


def _bars(start: datetime, closes: list[float], *, high_pad: float = 0.0, low_pad: float = 0.0):
    step = 4 * 3_600_000
    rows = []
    prev = closes[0]
    for i, close in enumerate(closes):
        c = float(close)
        ts = int(start.timestamp() * 1000) + i * step
        o = float(prev)
        rows.append([ts, o, max(o, c) + high_pad, min(o, c) - low_pad, c, 1_000_000.0])
        prev = c
    return rows


def _spec(**kw: object) -> BankSpec:
    base: dict[str, object] = dict(
        name="t",
        lookback=2,
        skip=0,
        excess_floor=0.02,
        tp_pct=0.03,
        stop_pct=0.02,
        btc_sma_bars=3,
        min_qvol_eur=1.0,
        cooldown_bars=1,
        universe=("AAA",),
    )
    base.update(kw)
    return BankSpec(**base)  # type: ignore[arg-type]


def test_take_profit_fills_at_the_target_not_the_high() -> None:
    start = datetime(2024, 6, 3, tzinfo=UTC)
    btc = [100, 101, 102, 103, 104, 105]
    alt = [100, 100, 110, 115, 116, 117]
    # Bar 3 is the fill. Push its high through +3% without moving the close.
    alt_rows = _bars(start, alt)
    alt_rows[3][2] = 130.0
    ohlc = {"BTC": _bars(start, btc), "AAA": alt_rows}
    row = run_momentum_bank(
        ohlc, _spec(), start="2024-06-03", end="2024-06-06", book_eur=10_000.0, keep_trades=True
    )
    sells = [t for t in row["trades"] if t["side"] == "sell"]
    assert sells and sells[0]["reason"] == "tp"
    assert sells[0]["px"] < 120.0
    buy = next(t for t in row["trades"] if t["side"] == "buy")
    assert sells[0]["px"] > buy["px"]
    assert sells[0]["px"] < buy["px"] * 1.05


def test_both_levels_in_one_bar_book_the_stop() -> None:
    start = datetime(2024, 6, 3, tzinfo=UTC)
    btc = [100, 101, 102, 103, 104]
    alt = [100, 100, 110, 100, 100]
    rows = _bars(start, alt)
    rows[3][2] = 140.0
    rows[3][3] = 80.0
    ohlc = {"BTC": _bars(start, btc), "AAA": rows}
    row = run_momentum_bank(
        ohlc, _spec(), start="2024-06-03", end="2024-06-05", book_eur=10_000.0, keep_trades=True
    )
    sells = [t for t in row["trades"] if t["side"] == "sell"]
    assert sells and sells[0]["reason"] == "stop"
    assert row["pnl_eur"] < 0


def test_a_hold_does_not_bank_a_wick_through_a_round_level() -> None:
    start = datetime(2024, 6, 3, tzinfo=UTC)
    btc = [100, 101, 102, 103, 104, 105]
    alt = [100, 100, 110, 115, 116, 117]
    alt_rows = _bars(start, alt)
    alt_rows[3][2] = 130.0
    ohlc = {"BTC": _bars(start, btc), "AAA": alt_rows}
    row = run_momentum_bank(
        ohlc,
        _spec(tp_pct=0.0, stop_pct=0.0),
        start="2024-06-03",
        end="2024-06-06",
        book_eur=10_000.0,
        keep_trades=True,
    )
    assert not [t for t in row["trades"] if t["side"] == "sell"]
    assert row["end_hold"] == "AAA"


def test_a_rise_that_dies_is_sold_and_a_falling_name_is_not_bought() -> None:
    start = datetime(2024, 6, 3, tzinfo=UTC)
    btc = [100 + i for i in range(8)]
    aaa = [100, 100, 120, 118, 100, 100, 100, 100]
    bbb = [100, 99, 98, 97, 96, 95, 94, 93]
    ohlc = {
        "BTC": _bars(start, btc),
        "AAA": _bars(start, aaa),
        "BBB": _bars(start, bbb),
    }
    spec = _spec(
        universe=("AAA", "BBB"),
        tp_pct=0.50,
        stop_pct=0.50,
        fail_when_flat=True,
    )
    row = run_momentum_bank(
        ohlc, spec, start="2024-06-03", end="2024-06-06", book_eur=10_000.0, keep_trades=True
    )
    buys = [t["base"] for t in row["trades"] if t["side"] == "buy"]
    assert buys == ["AAA"]
    assert any(t["reason"] == "momentum_fail" for t in row["trades"] if t["side"] == "sell")
