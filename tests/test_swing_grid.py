"""Locked swing grid. No network."""

from __future__ import annotations

from bot.research.swing_grid.grid import (
    BOOK_EUR,
    BOUNCE_5,
    BOUNCE_10,
    CAPACITY,
    CUSHION,
    LOOKBACK,
    MAKER,
    MAX_STALE_DAYS,
    MIN_BARS,
    MIN_CHOP,
    MIN_QUOTE_EUR,
    RETEST_INNER,
    RETEST_MIN_RANGE,
    RETEST_WARMUP,
    RUNG_FRACS,
    Choice,
    Session,
    choose_name,
    day_chop,
    run_session,
)


def test_thresholds_stay_locked() -> None:
    assert LOOKBACK == 5
    assert MIN_CHOP == 0.02
    assert MIN_QUOTE_EUR == 100_000.0
    assert MAX_STALE_DAYS == 2
    assert MIN_BARS == 48
    assert RUNG_FRACS == (0.35, 0.70)
    assert CAPACITY == 0.10
    assert RETEST_WARMUP == 16
    assert RETEST_INNER == 0.20
    assert RETEST_MIN_RANGE == 0.02
    assert CUSHION == 0.01
    assert BOUNCE_5 == 0.05
    assert BOUNCE_10 == 0.10
    assert BOOK_EUR == 20_000.0


def test_round_trip_day_outranks_a_straight_trend() -> None:
    chop = day_chop(100, 110, 100, 100)
    trend = day_chop(100, 110, 100, 110)
    assert chop == 0.10
    assert trend == 0.0


def _session(
    day: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    quote: float = 1_000_000.0,
    n: int = MIN_BARS,
    bars: tuple[tuple[float, float, float, float], ...] | None = None,
) -> Session:
    if bars is None:
        pad = [(close, close, close, close) for _ in range(n)]
        pad[0] = (open_, open_, open_, open_)
        pad[1] = (open_, high, open_, open_)
        pad[2] = (open_, open_, low, open_)
        pad[-1] = (close, close, close, close)
        bars = tuple(pad)
    return Session(day, open_, high, low, close, quote, bars)


def _priors(base_day: str = "2026-01-0") -> list[Session]:
    return [
        _session(f"{base_day}{i}", 100, 110, 90, 100)
        for i in range(1, 6)
    ]


def test_selection_uses_prior_sessions_only() -> None:
    aaa = [
        *_priors(),
        _session("2026-01-06", 100, 101, 99, 100),
    ]
    bbb = [
        _session(f"2026-01-0{i}", 100, 100, 100, 100) for i in range(1, 6)
    ]
    bbb.append(_session("2026-01-06", 100, 150, 50, 100))
    choice = choose_name({"AAA": aaa, "BBB": bbb}, "2026-01-06", BOOK_EUR, ("BBB", "AAA"))
    assert choice is not None
    assert choice.base == "AAA"
    assert choice.mid == 100
    assert abs(choice.range_frac - 0.20) < 1e-12


def test_stale_history_and_a_tie_break_alphabetically() -> None:
    fresh = _priors() + [_session("2026-01-06", 100, 110, 90, 100)]
    stale = [
        _session(f"2026-01-0{i}", 100, 110, 90, 100) for i in range(1, 6)
    ]
    stale.append(_session("2026-01-20", 100, 110, 90, 100))
    missed = choose_name({"AAA": stale}, "2026-01-20", BOOK_EUR, ("AAA",))
    assert missed is None
    tied = choose_name(
        {"BBB": fresh, "AAA": fresh},
        "2026-01-06",
        BOOK_EUR,
        ("BBB", "AAA"),
    )
    assert tied is not None and tied.base == "AAA"


def test_capacity_drops_a_book_that_is_too_big_for_the_tape() -> None:
    # Median quote is €150,000. A €10,000 rung fits; a €20,000 rung does not.
    rows = [
        _session(f"2026-01-0{i}", 100, 110, 90, 100, quote=150_000) for i in range(1, 7)
    ]
    small = choose_name({"AAA": rows}, "2026-01-06", BOOK_EUR, ("AAA",))
    large = choose_name({"AAA": rows}, "2026-01-06", 40_000.0, ("AAA",))
    assert small is not None
    assert large is None


def _choice() -> Choice:
    return Choice("AAA", 0.20, 0.20, 100.0, 1_000_000.0)


def test_strict_refuses_a_same_bar_round_trip() -> None:
    bar = ((100.0, 108.0, 92.0, 107.0),)
    session = _session("2026-01-06", 100, 108, 92, 107, bars=bar)
    strict = run_session(session, _choice(), BOOK_EUR, "strict")
    path = run_session(session, _choice(), BOOK_EUR, "path")
    assert strict.maker_exits == 0
    assert strict.flattens == 1
    assert path.maker_exits == 1
    assert path.flattens == 0
    assert path.end_equity > strict.end_equity


def test_strict_completes_a_round_trip_on_a_later_bar() -> None:
    bars = (
        (100.0, 101.0, 92.0, 100.0),
        (100.0, 108.0, 99.0, 107.0),
    )
    session = _session("2026-01-06", 100, 108, 92, 107, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "strict")
    assert out.maker_exits == 1
    assert out.flattens == 0
    assert out.end_equity > BOOK_EUR


def test_a_gap_through_the_bid_is_not_a_maker_entry() -> None:
    # Both rungs (93 and 86) are already through at an open of 80, so neither rests.
    bars = ((80.0, 100.0, 70.0, 95.0),)
    session = _session("2026-01-06", 80, 100, 70, 95, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "strict")
    assert out.filled is False
    assert out.end_equity == BOOK_EUR


def test_retest_freezes_the_peak_from_completed_bars() -> None:
    bars = [(100.0, 100.0, 100.0, 100.0) for _ in range(18)]
    bars[0] = (100.0, 100.0, 90.0, 95.0)
    bars[1] = (95.0, 100.0, 95.0, 100.0)
    bars[16] = (100.0, 120.0, 92.0, 100.0)
    bars[17] = (100.0, 99.0, 98.0, 99.0)
    session = _session("2026-01-06", 100, 120, 90, 99, bars=tuple(bars))
    out = run_session(session, _choice(), BOOK_EUR, "retest")
    assert out.maker_exits == 1
    assert out.flattens == 0
    assert out.end_equity > 21_000


def test_bounce_buys_above_the_printed_low_and_sells_five_percent_later() -> None:
    bars = [(110.0, 110.0, 110.0, 110.0) for _ in range(4)]
    bars[0] = (110.0, 110.0, 100.0, 108.0)
    bars[1] = (108.0, 109.0, 101.0, 108.0)
    bars[2] = (104.0, 106.1, 103.0, 105.0)
    session = _session("2026-01-06", 110, 110, 100, 105, bars=tuple(bars))
    out = run_session(session, _choice(), BOOK_EUR, "bounce5")
    buy_px = 100.0 * (1.0 + CUSHION)
    sell_px = buy_px * (1.0 + BOUNCE_5)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * sell_px * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert out.flattens == 0
    assert abs(out.end_equity - expected) < 1e-6
    # A 10% target is not reached on this tape, so the lot is closed at the last price.
    wider = run_session(session, _choice(), BOOK_EUR, "bounce10")
    assert wider.maker_exits == 0
    assert wider.flattens == 1


def test_bounce_does_not_use_the_low_of_the_fill_bar() -> None:
    bars = (
        (110.0, 110.0, 105.0, 108.0),
        (100.0, 101.0, 90.0, 95.0),
    )
    session = _session("2026-01-06", 110, 110, 90, 95, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "bounce5")
    assert out.filled is False
    assert out.end_equity == BOOK_EUR


def test_bounce_does_not_sell_on_the_entry_bar() -> None:
    bars = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 130.0, 101.0, 120.0),
    )
    session = _session("2026-01-06", 110, 130, 100, 120, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "bounce5")
    assert out.maker_exits == 0
    assert out.flattens == 1


def test_hold_pays_the_round_trip_and_oracle_uses_the_extremes() -> None:
    session = _session("2026-01-06", 100, 110, 90, 100)
    held = run_session(session, _choice(), BOOK_EUR, "hold")
    oracle = run_session(session, _choice(), BOOK_EUR, "oracle")
    assert held.end_equity < BOOK_EUR
    assert oracle.end_equity > held.end_equity
    assert oracle.maker_pnl > 0
