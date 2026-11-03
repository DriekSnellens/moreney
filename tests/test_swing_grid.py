"""Locked swing grid. No network."""

from __future__ import annotations

from bot.research.swing_grid.grid import (  # noqa: I001
    BOOK_EUR,
    BOUNCE_5,
    BOUNCE_10,
    CAPACITY,
    CLIP,
    CUSHION,
    DOUBLE_BREAK,
    EARLY_BARS,
    HOLD_SESSIONS,
    LOOKBACK,
    MAKER,
    MAX_STALE_DAYS,
    MIN_BARS,
    MIN_CHOP,
    MIN_QUOTE_EUR,
    PRIOR_FRAC,
    RETEST_INNER,
    RETEST_MIN_RANGE,
    RETEST_WARMUP,
    ROOM,
    RUNG_FRACS,
    SCALE_FIRST,
    SCRATCH_AFTER,
    SHALLOW_MAX,
    SHALLOW_RUNG,
    SMA_WINDOW,
    STOP_THROUGH,
    VWAP_BAND,
    VWAP_WARMUP,
    Choice,
    Session,
    _advance_hold,
    _enter_hold_day,
    _taker_exit,
    choose_name,
    day_chop,
    run_priced_bounce,
    run_session,
    simulate,
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
    assert STOP_THROUGH == 0.02
    assert SHALLOW_MAX == 0.04
    assert HOLD_SESSIONS == 3
    assert EARLY_BARS == 16
    assert PRIOR_FRAC == 0.50
    assert ROOM == 0.003
    assert SMA_WINDOW == 20
    assert CLIP == 0.005
    assert DOUBLE_BREAK == 0.005
    assert SCALE_FIRST == 0.02
    assert SHALLOW_RUNG == 0.35
    assert SCRATCH_AFTER == 64
    assert VWAP_WARMUP == 4
    assert VWAP_BAND == 0.01
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
    volumes: tuple[float, ...] = (),
) -> Session:
    if bars is None:
        pad = [(close, close, close, close) for _ in range(n)]
        pad[0] = (open_, open_, open_, open_)
        pad[1] = (open_, high, open_, open_)
        pad[2] = (open_, open_, low, open_)
        pad[-1] = (close, close, close, close)
        bars = tuple(pad)
    return Session(day, open_, high, low, close, quote, bars, volumes)


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


def test_priced_bounce_uses_the_passed_distances() -> None:
    bars = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 109.0, 100.0, 108.0),
        (108.0, 104.0, 103.0, 103.5),
    )
    session = _session("2026-01-06", 110, 110, 100, 103.5, bars=bars)
    choice = _choice()
    at_low = run_priced_bounce(session, choice, BOOK_EUR, cushion=0.0, target=0.03)
    assert at_low.maker_exits == 1
    assert at_low.flattens == 0
    wide_bid = run_priced_bounce(session, choice, BOOK_EUR, cushion=0.09, target=0.03)
    assert wide_bid.filled is False
    far_offer = run_priced_bounce(session, choice, BOOK_EUR, cushion=0.0, target=0.08)
    assert far_offer.maker_exits == 0
    assert far_offer.flattens == 1
    same = run_priced_bounce(session, choice, BOOK_EUR, cushion=CUSHION, target=BOUNCE_5)
    bounce = run_session(session, choice, BOOK_EUR, "bounce5")
    assert same.end_equity == bounce.end_equity
    assert same.maker_exits == bounce.maker_exits
    assert same.flattens == bounce.flattens


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


def test_confirm_waits_for_a_bar_that_holds_the_low() -> None:
    early = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 109.0, 100.0, 104.0),
    )
    session = _session("2026-01-06", 110, 110, 100, 104, bars=early)
    assert run_session(session, _choice(), BOOK_EUR, "confirm5").filled is False
    bars = [
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 109.0, 101.0, 104.0),
        (104.0, 105.0, 101.0, 103.0),
        (103.0, 107.0, 103.0, 106.0),
    ]
    # Bar 2 is the first bar allowed to buy. A new low on that path must cancel it.
    broke = list(bars)
    broke[2] = (104.0, 104.0, 95.0, 96.0)
    broke[3] = (102.0, 103.0, 100.0, 101.0)
    cancelled = _session("2026-01-06", 110, 110, 95, 101, bars=tuple(broke))
    assert run_session(cancelled, _choice(), BOOK_EUR, "confirm5").filled is False
    held = _session("2026-01-06", 110, 110, 100, 106, bars=tuple(bars))
    out = run_session(held, _choice(), BOOK_EUR, "confirm5")
    assert out.maker_exits == 1
    assert out.end_equity > BOOK_EUR


def test_stop_exits_through_the_low_and_ignores_a_later_crash() -> None:
    bars = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 109.0, 101.0, 104.0),
        (100.0, 100.0, 97.0, 99.0),
        (90.0, 90.0, 80.0, 85.0),
    )
    session = _session("2026-01-06", 110, 110, 80, 85, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "stop5")
    qty = BOOK_EUR * (1.0 - MAKER) / 101.0
    proceeds, _px = _taker_exit(qty, 98.0, 1_000_000.0)
    assert out.maker_exits == 0
    assert out.flattens == 1
    assert abs(out.end_equity - proceeds) < 1e-6
    both = (
        bars[0],
        bars[1],
        (100.0, 120.0, 97.0, 110.0),
    )
    spanned = _session("2026-01-06", 110, 120, 97, 110, bars=both)
    stopped = run_session(spanned, _choice(), BOOK_EUR, "stop5")
    assert stopped.maker_exits == 0
    assert stopped.flattens == 1


def test_shallow_skips_a_dump_and_takes_a_small_dip() -> None:
    dump = (
        (100.0, 100.0, 90.0, 92.0),
        (92.0, 93.0, 91.0, 92.0),
    )
    crashed = _session("2026-01-06", 100, 100, 90, 92, bars=dump)
    assert run_session(crashed, _choice(), BOOK_EUR, "shallow5").filled is False
    dip = (
        (100.0, 100.0, 97.0, 98.0),
        (99.0, 99.0, 97.5, 98.0),
        (98.0, 104.0, 98.0, 103.0),
    )
    small = _session("2026-01-06", 100, 104, 97, 103, bars=dip)
    out = run_session(small, _choice(), BOOK_EUR, "shallow5")
    assert out.maker_exits == 1
    assert out.end_equity > BOOK_EUR


def test_hold_can_sell_the_target_on_a_later_session() -> None:
    first = _session(
        "2026-01-06",
        110,
        110,
        100,
        103,
        bars=(
            (110.0, 110.0, 100.0, 108.0),
            (108.0, 108.0, 101.0, 103.0),
        ),
    )
    cash, pos, _pnl, exits = _enter_hold_day(first, BOOK_EUR, 1_000_000.0, "AAA")
    assert exits == 0 and pos is not None and pos.seen == 1
    second = _session(
        "2026-01-07",
        103,
        104,
        102,
        103,
        bars=((103.0, 104.0, 102.0, 103.0),),
    )
    cash, pos, _made, exits, forced = _advance_hold(second, pos, cash)
    assert pos is not None and exits == 0 and forced == 0.0
    third = _session(
        "2026-01-08",
        103,
        107,
        103,
        106,
        bars=((103.0, 107.0, 103.0, 106.0),),
    )
    cash, pos, made, exits, forced = _advance_hold(third, pos, cash)
    assert pos is None and exits == 1 and made > 0 and forced == 0.0
    assert cash > BOOK_EUR


def test_liquid_rank_prefers_turnover_over_chop() -> None:
    wild = [_session(f"2026-01-0{i}", 100, 120, 80, 100, quote=200_000) for i in range(1, 7)]
    calm = [_session(f"2026-01-0{i}", 100, 106, 98, 100, quote=5_000_000) for i in range(1, 7)]
    book = {"AAA": wild, "BBB": calm}
    chop = choose_name(book, "2026-01-06", BOOK_EUR, ("AAA", "BBB"))
    liquid = choose_name(book, "2026-01-06", BOOK_EUR, ("AAA", "BBB"), "liquid")
    assert chop is not None and chop.base == "AAA"
    assert liquid is not None and liquid.base == "BBB"


def test_early_bid_expires_and_reclaim_sells_the_open() -> None:
    late = [(110.0, 110.0, 110.0, 110.0) for _ in range(20)]
    late[0] = (110.0, 110.0, 100.0, 108.0)
    late[18] = (108.0, 108.0, 101.0, 105.0)
    missed = _session("2026-01-06", 110, 110, 100, 105, bars=tuple(late))
    assert run_session(missed, _choice(), BOOK_EUR, "early5").filled is False
    bars = [
        (100.0, 100.0, 90.0, 95.0),
        (96.0, 96.0, 90.5, 94.0),
        (94.0, 96.0, 94.0, 95.0),
        (95.0, 101.0, 95.0, 100.0),
    ]
    session = _session("2026-01-06", 100, 101, 90, 100, bars=tuple(bars))
    out = run_session(session, _choice(), BOOK_EUR, "reclaim")
    buy_px = 90.0 * (1.0 + CUSHION)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * session.open * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6
    short = _session("2026-01-06", 100, 96, 90, 95, bars=tuple(bars[:3]))
    assert run_session(short, _choice(), BOOK_EUR, "reclaim").maker_exits == 0


def test_prior_buys_half_the_old_range_and_sells_the_old_close() -> None:
    choice = _choice()
    buy_px = choice.mid * (1.0 - PRIOR_FRAC * choice.range_frac)
    assert abs(buy_px - 90.0) < 1e-9
    bars = (
        (100.0, 100.0, 89.0, 95.0),
        (95.0, 100.0, 95.0, 99.0),
    )
    session = _session("2026-01-06", 100, 100, 89, 99, bars=bars)
    out = run_session(session, choice, BOOK_EUR, "prior")
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * choice.mid * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6
    gapped = _session("2026-01-06", 80, 85, 70, 82, bars=((80.0, 85.0, 70.0, 82.0),))
    assert run_session(gapped, choice, BOOK_EUR, "prior").filled is False


def _trend_book() -> dict[str, list[Session]]:
    up: list[Session] = []
    flat: list[Session] = []
    for offset in range(1, 22):
        day = f"2026-01-{offset:02d}"
        close = 100.0 if offset < 20 else 130.0
        up.append(_session(day, close, close * 1.05, close * 0.95, close))
        flat.append(_session(day, 100, 105, 95, 100))
    return {"UP": up, "FLAT": flat}


def test_sma_filter_keeps_only_a_close_above_its_average() -> None:
    book = _trend_book()
    kept = choose_name(book, "2026-01-21", BOOK_EUR, ("FLAT", "UP"), above_sma=True)
    assert kept is not None and kept.base == "UP"
    blocked = choose_name({"FLAT": book["FLAT"]}, "2026-01-21", BOOK_EUR, ("FLAT",), above_sma=True)
    assert blocked is None


def test_quiet_day_skips_a_range_that_is_already_wide() -> None:
    choice = Choice("AAA", 0.10, 0.02, 100.0, 1_000_000.0)
    bars = (
        (100.0, 110.0, 90.0, 100.0),
        (100.0, 100.0, 90.5, 95.0),
    )
    session = _session("2026-01-06", 100, 110, 90, 95, bars=bars)
    assert run_session(session, choice, BOOK_EUR, "bounce5").filled is True
    assert run_session(session, choice, BOOK_EUR, "quiet5").filled is False


def test_clip_buys_under_the_open_and_sells_half_a_percent_higher() -> None:
    bars = (
        (100.0, 100.0, 99.4, 99.7),
        (99.7, 100.2, 99.6, 100.0),
    )
    session = _session("2026-01-06", 100, 100.2, 99.4, 100, bars=bars)
    out = run_session(session, _choice(), BOOK_EUR, "clip")
    buy_px = 100.0 * (1.0 - CLIP)
    sell_px = buy_px * (1.0 + CLIP)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * sell_px * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6


def test_double_buys_the_second_test_and_a_break_resets_the_count() -> None:
    choice = _choice()
    bars = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 108.0, 100.5, 105.0),
        (106.0, 106.0, 100.8, 104.0),
        (104.0, 107.0, 103.0, 106.0),
    )
    session = _session("2026-01-06", 110, 110, 100, 106, bars=bars)
    out = run_session(session, choice, BOOK_EUR, "double5")
    buy_px = 100.0 * (1.0 + CUSHION)
    sell_px = buy_px * (1.0 + BOUNCE_5)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * sell_px * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6
    first = _session("2026-01-06", 110, 110, 100, 105, bars=bars[:2])
    assert run_session(first, choice, BOOK_EUR, "double5").filled is False
    reset = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 108.0, 100.5, 105.0),
        (105.0, 105.0, 99.0, 100.0),
        (100.0, 100.0, 99.5, 99.8),
    )
    broken = _session("2026-01-06", 110, 110, 99, 99.8, bars=reset)
    assert run_session(broken, choice, BOOK_EUR, "double5").filled is False
    ref = 99.0
    buy_px = ref * (1.0 + CUSHION)
    sell_px = buy_px * (1.0 + BOUNCE_5)
    second = reset + (
        (102.0, 102.0, 99.9, 101.0),
        (101.0, 106.0, 100.0, 105.0),
    )
    filled = _session("2026-01-06", 110, 110, 99, 105, bars=second)
    again = run_session(filled, choice, BOOK_EUR, "double5")
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    assert again.maker_exits == 1
    assert abs(again.end_equity - qty * sell_px * (1.0 - MAKER)) < 1e-6


def test_scale_sells_half_at_two_percent_and_the_rest_at_five() -> None:
    choice = _choice()
    buy_px = 100.0 * (1.0 + CUSHION)
    first_px = buy_px * (1.0 + SCALE_FIRST)
    second_px = buy_px * (1.0 + BOUNCE_5)
    bars = (
        (100.0, 100.0, 100.0, 100.0),
        (110.0, 110.0, 100.5, 105.0),
        (105.0, 104.0, 103.0, 103.5),
        (103.5, 107.0, 103.0, 106.0),
    )
    session = _session("2026-01-06", 100, 110, 100, 106, bars=bars)
    out = run_session(session, choice, BOOK_EUR, "scale")
    half = BOOK_EUR * (1.0 - MAKER) / buy_px / 2.0
    expected = half * first_px * (1.0 - MAKER) + half * second_px * (1.0 - MAKER)
    assert out.maker_exits == 2
    assert out.flattens == 0
    assert abs(out.end_equity - expected) < 1e-6
    same_bar = _session(
        "2026-01-06",
        100,
        120,
        100,
        115,
        bars=((100.0, 100.0, 100.0, 100.0), (110.0, 120.0, 100.5, 115.0)),
    )
    held = run_session(same_bar, choice, BOOK_EUR, "scale")
    assert held.maker_exits == 0
    assert held.flattens == 1


def test_shallow_rung_keeps_half_the_book_and_skips_the_deep_bid() -> None:
    choice = _choice()
    bars = (
        (100.0, 101.0, 92.0, 100.0),
        (100.0, 108.0, 99.0, 107.0),
    )
    session = _session("2026-01-06", 100, 108, 92, 107, bars=bars)
    shallow = run_session(session, choice, BOOK_EUR, "shallow_rung")
    strict = run_session(session, choice, BOOK_EUR, "strict")
    buy_px = 100.0 * (1.0 - SHALLOW_RUNG * choice.range_frac)
    sell_px = 100.0 * (1.0 + SHALLOW_RUNG * choice.range_frac)
    qty = (BOOK_EUR / 2.0) * (1.0 - MAKER) / buy_px
    expected = BOOK_EUR / 2.0 + qty * sell_px * (1.0 - MAKER)
    assert shallow.maker_exits == 1
    assert shallow.flattens == 0
    assert abs(shallow.end_equity - expected) < 1e-6
    assert abs(shallow.end_equity - strict.end_equity) < 1e-6
    gap = _session("2026-01-06", 90, 100, 80, 95, bars=((90.0, 100.0, 80.0, 95.0),))
    assert run_session(gap, choice, BOOK_EUR, "shallow_rung").filled is False
    assert run_session(gap, choice, BOOK_EUR, "strict").filled is True


def test_scratch_sells_the_fill_after_64_bars_and_not_before() -> None:
    choice = _choice()
    bars = [(104.0, 104.0, 103.0, 103.5) for _ in range(SCRATCH_AFTER + 1)]
    bars[0] = (110.0, 110.0, 100.0, 108.0)
    bars[1] = (108.0, 108.0, 100.5, 105.0)
    bars[SCRATCH_AFTER] = (100.5, 102.0, 100.2, 101.5)
    session = _session("2026-01-06", 110, 110, 100, 101.5, bars=tuple(bars))
    out = run_session(session, choice, BOOK_EUR, "scratch")
    buy_px = 100.0 * (1.0 + CUSHION)
    expected = BOOK_EUR * (1.0 - MAKER) * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert out.flattens == 0
    assert abs(out.end_equity - expected) < 1e-6
    assert run_session(session, choice, BOOK_EUR, "bounce5").maker_exits == 0
    early = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 108.0, 100.5, 105.0),
        (105.0, 107.0, 104.0, 106.0),
    )
    hit = _session("2026-01-06", 110, 110, 100, 106, bars=early)
    sold = run_session(hit, choice, BOOK_EUR, "scratch")
    sell_px = buy_px * (1.0 + BOUNCE_5)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    assert sold.maker_exits == 1
    assert abs(sold.end_equity - qty * sell_px * (1.0 - MAKER)) < 1e-6


def test_vwap_buys_under_completed_bars_and_ignores_the_fill_bar() -> None:
    choice = _choice()
    bars = [(100.0, 100.0, 100.0, 100.0) for _ in range(VWAP_WARMUP + 2)]
    bars[VWAP_WARMUP] = (100.0, 100.0, 90.0, 90.0)
    bars[VWAP_WARMUP + 1] = (99.0, 100.0, 98.0, 100.0)
    volumes = tuple(1.0 for _ in bars)
    volumes = volumes[:VWAP_WARMUP] + (1_000.0,) + volumes[VWAP_WARMUP + 1 :]
    session = _session(
        "2026-01-06", 100, 100, 90, 100, bars=tuple(bars), volumes=volumes
    )
    out = run_session(session, choice, BOOK_EUR, "vwap")
    buy_px = 100.0 * (1.0 - VWAP_BAND)
    qty = BOOK_EUR * (1.0 - MAKER) / buy_px
    expected = qty * 100.0 * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6
    short = _session(
        "2026-01-06",
        100,
        100,
        90,
        90,
        bars=tuple(bars[: VWAP_WARMUP + 1]),
        volumes=volumes[: VWAP_WARMUP + 1],
    )
    assert run_session(short, choice, BOOK_EUR, "vwap").maker_exits == 0


def test_carry_keeps_the_paired_sell_and_flattens_at_the_window() -> None:
    quiet = tuple((95.0, 96.0, 94.0, 95.0) for _ in range(MIN_BARS - 1))
    entry = _session(
        "2026-01-06",
        100,
        101,
        92,
        95,
        bars=((100.0, 101.0, 92.0, 95.0), *quiet),
    )
    later = _session("2026-01-07", 95, 108, 94, 107, bars=((95.0, 108.0, 94.0, 107.0),))
    book = {"AAA": [*_priors(), entry, later]}
    carried = simulate(book, universe=("AAA",), start="2026-01-06", end="2026-01-07", mode="carry")
    buy_px = 100.0 * (1.0 - 0.35 * 0.20)
    sell_px = 100.0 * (1.0 + 0.35 * 0.20)
    qty = (BOOK_EUR / 2.0) * (1.0 - MAKER) / buy_px
    expected = BOOK_EUR / 2.0 + qty * sell_px * (1.0 - MAKER)
    assert carried.maker_exits == 1
    assert carried.flattens == 0
    assert abs(carried.end_equity - expected) < 1e-6
    one = simulate(book, universe=("AAA",), start="2026-01-06", end="2026-01-06", mode="carry")
    strict = simulate(book, universe=("AAA",), start="2026-01-06", end="2026-01-06", mode="strict")
    assert one.flattens == 1 and one.maker_exits == 0
    assert abs(one.end_equity - strict.end_equity) < 1e-6
    assert carried.end_equity > strict.end_equity


def test_mid_exit_sells_the_prior_close_with_half_the_book() -> None:
    choice = _choice()
    bars = (
        (100.0, 101.0, 92.0, 96.0),
        (96.0, 101.0, 95.0, 100.0),
    )
    session = _session("2026-01-06", 100, 101, 92, 100, bars=bars)
    out = run_session(session, choice, BOOK_EUR, "mid_exit")
    buy_px = choice.mid * (1.0 - SHALLOW_RUNG * choice.range_frac)
    qty = (BOOK_EUR / 2.0) * (1.0 - MAKER) / buy_px
    expected = BOOK_EUR / 2.0 + qty * choice.mid * (1.0 - MAKER)
    assert out.maker_exits == 1
    assert abs(out.end_equity - expected) < 1e-6
    assert run_session(session, choice, BOOK_EUR, "shallow_rung").maker_exits == 0


def test_second_day_needs_a_lower_low_and_then_buys_the_bounce() -> None:
    def row(day: str, low: float) -> Session:
        return _session(day, 100, 110, low, 100)

    today = _session("2026-01-06", 100, 110, 90, 100)
    down = [row(f"2026-01-0{i}", 90 if i < 5 else 80) for i in range(1, 6)]
    flat = [row(f"2026-01-0{i}", 90) for i in range(1, 6)]
    kept = choose_name({"AAA": [*down, today]}, "2026-01-06", BOOK_EUR, ("AAA",), lower_low=True)
    blocked = choose_name(
        {"AAA": [*flat, today]}, "2026-01-06", BOOK_EUR, ("AAA",), lower_low=True
    )
    assert kept is not None and kept.base == "AAA"
    assert blocked is None
    bars = (
        (110.0, 110.0, 100.0, 108.0),
        (108.0, 108.0, 100.5, 105.0),
        (105.0, 107.0, 104.0, 106.0),
    )
    tape = _session("2026-01-06", 110, 110, 100, 106, bars=bars)
    assert run_session(tape, _choice(), BOOK_EUR, "second_day") == run_session(
        tape, _choice(), BOOK_EUR, "bounce5"
    )


def test_hold_pays_the_round_trip_and_oracle_uses_the_extremes() -> None:
    session = _session("2026-01-06", 100, 110, 90, 100)
    held = run_session(session, _choice(), BOOK_EUR, "hold")
    oracle = run_session(session, _choice(), BOOK_EUR, "oracle")
    assert held.end_equity < BOOK_EUR
    assert oracle.end_equity > held.end_equity
    assert oracle.maker_pnl > 0
