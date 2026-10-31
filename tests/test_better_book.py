"""Scale-in, runner trail, and the locked bar. No tape required."""

from __future__ import annotations

from bot.research.better_book.bar import (
    beats_both,
    hits_margin,
    judge_family,
    one_trade_ok,
    region_ok,
)
from bot.research.better_book.search import largest_new_trip, round_trips
from bot.research.btc_residual_mix.engine import _scale_in_orders
from bot.research.clip_exit_lab.engine import Book, Lot, _overlay_orders
from bot.research.clip_exit_lab.policies import ExitPolicy


def _cell(name: str, full: float, is_: float, oos: float, dd: float = 0.30) -> dict:
    def w(pnl: float) -> dict:
        return {"pnl_eur": pnl, "max_dd_pct": dd, "n_trades": 10}

    return {"name": name, "full": w(full), "is": w(is_), "oos": w(oos)}


def test_bar_requires_region_margin_and_more_than_one_trip() -> None:
    base = _cell("baseline", 100.0, 60.0, 40.0, dd=0.30)
    # 2 of 4 beat both windows. Half of 4 is 2, so the region holds.
    cells = [
        _cell("a", 200.0, 80.0, 70.0),
        _cell("b", 130.0, 70.0, 50.0),
        _cell("c", 90.0, 50.0, 30.0),
        _cell("d", 80.0, 40.0, 20.0),
    ]
    assert beats_both(cells[0], base)
    assert hits_margin(cells[0], base)
    assert not hits_margin(cells[1], base)
    assert region_ok(cells, base)
    assert one_trade_ok(100.0, 50.0)
    assert not one_trade_ok(100.0, 70.0)
    found = judge_family("scale_in", cells, base, new_trip={"a": 50.0})
    assert found.cleared == ("a",)
    blocked = judge_family("scale_in", cells, base, new_trip={"a": 90.0})
    assert blocked.cleared == ()


def test_keep_entry_does_not_rewrite_the_lock_anchor() -> None:
    book = Book(10_000.0)
    book.buy("ETH", "alt", 4_000.0, 100.0, 0.0, opened_ms=1)
    book.buy("ETH", "alt", 4_000.0, 150.0, 0.0, opened_ms=2, keep_entry=True)
    assert book.lots["ETH"].entry_px == 100.0
    assert book.lots["ETH"].qty == 4_000.0 / 100.0 + 4_000.0 / 150.0


def _eth_rows() -> list[list[float]]:
    # Monday 2024-01-01, then a high that arms a runner and a close under 10%.
    t0 = 1_704_067_200_000
    rows = []
    px = 100.0
    for i in range(6):
        rows.append([t0 + i * 86_400_000, px, px, px, px, 1_000_000.0])
    # Day 6 is Sunday. Peak 160, close 140: 12.5% off the high, +40% on entry
    # only if entry is 100 and peak includes 160.
    rows.append([t0 + 6 * 86_400_000, 150.0, 160.0, 140.0, 140.0, 1_000_000.0])
    return rows


def test_runner_trail_stays_wide_until_the_giveback_matches() -> None:
    ohlc = {"ETH": _eth_rows()}
    date = "2024-01-07"
    now_ms = int(ohlc["ETH"][-1][0])
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=100.0, opened_ms=now_ms
    )
    tight = ExitPolicy(name="t", alt_trail_pct=0.10)
    assert any(o.reason == "alt_trail" for o in _overlay_orders(book, ohlc, date, tight, now_ms))

    book.lots["ETH"].peak_px = 100.0
    wide = ExitPolicy(
        name="w", alt_trail_pct=0.10, alt_runner_arm_pct=0.40, alt_runner_trail_pct=0.20
    )
    assert _overlay_orders(book, ohlc, date, wide, now_ms) == []

    book.lots["ETH"].peak_px = 100.0
    # 25% off the 160 high is a close of 120, which does fire the 20% runner trail.
    ohlc["ETH"][-1][4] = 120.0
    fired = _overlay_orders(book, ohlc, date, wide, now_ms)
    assert any(o.reason == "alt_trail" for o in fired)


def test_weekday_gate_blocks_a_midweek_trail() -> None:
    ohlc = {"ETH": _eth_rows()}
    # 2024-01-07 is a Sunday. Force the decision clock to Wednesday 2024-01-03
    # while the close is still through the trail, so only the weekday gate differs.
    date = "2024-01-07"
    wednesday = 1_704_240_000_000  # 2024-01-03 00:00 UTC
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=100.0, opened_ms=wednesday
    )
    daily = ExitPolicy(name="d", alt_trail_pct=0.10)
    friday = ExitPolicy(name="f", alt_trail_pct=0.10, alt_trail_weekdays=(4,))
    assert any(o.reason == "alt_trail" for o in _overlay_orders(book, ohlc, date, daily, wednesday))
    book.lots["ETH"].peak_px = 100.0
    assert _overlay_orders(book, ohlc, date, friday, wednesday) == []


def test_scale_in_adds_only_for_the_leader_through_the_arm() -> None:
    t0 = 1_704_067_200_000
    btc, eth = [], []
    for i in range(40):
        btc.append([t0 + i * 86_400_000, 100.0, 101.0, 99.0, 100.0, 5_000_000.0])
        px = 10.0 + i * 0.4
        eth.append([t0 + i * 86_400_000, px, px + 0.2, px - 0.1, px, 5_000_000.0])
    ohlc = {"BTC": btc, "ETH": eth}
    date = "2024-02-09"
    close = eth[-1][4]
    book = Book(10_000.0)
    book.lots["ETH"] = Lot(
        base="ETH",
        qty=50.0,
        role="alt",
        entry_px=close / 1.10,
        peak_px=close,
        opened_ms=t0,
        scale_open=True,
    )
    orders = _scale_in_orders(
        book,
        ohlc,
        date,
        add_arm_pct=0.08,
        alt_frac=1.0,
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        sma_n=50,
        near_high_weight=0.0,
        skip_bases=set(),
    )
    assert len(orders) == 1
    assert orders[0].reason == "scale_in"
    assert orders[0].keep_entry is True
    assert orders[0].notional > 50

    shy = _scale_in_orders(
        book,
        ohlc,
        date,
        add_arm_pct=0.50,
        alt_frac=1.0,
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        sma_n=50,
        near_high_weight=0.0,
        skip_bases=set(),
    )
    assert shy == []


def test_round_trip_twin_is_not_a_new_trip() -> None:
    buy = {
        "side": "buy",
        "base": "ETH",
        "px": 10.0,
        "eur": 1000.0,
        "date": "2024-05-01",
        "reason": "residual",
    }
    sell = {
        "side": "sell",
        "base": "ETH",
        "px": 12.0,
        "eur": 1200.0,
        "date": "2024-05-10",
        "reason": "alt_trail",
    }
    trips = round_trips([buy, sell])
    assert len(trips) == 1
    assert abs(trips[0]["pnl"] - 200.0) < 1e-6
    assert largest_new_trip([buy, sell], [buy, sell]) == 0.0
    other = dict(sell)
    other["date"] = "2024-06-20"
    other["eur"] = 1800.0
    other["px"] = 18.0
    assert largest_new_trip([buy, other], [buy, sell]) > 0


def _pierce_tape() -> dict[str, list[list[float]]]:
    """Two closes under the 10% trail, then one close back above it."""
    t0 = 1_704_067_200_000
    rows: list[list[float]] = []
    for i in range(6):
        rows.append([t0 + i * 86_400_000, 100.0, 100.0, 100.0, 100.0, 1_000_000.0])
    # Sunday: high 160, close 140 is 12.5% off the high.
    rows.append([t0 + 6 * 86_400_000, 150.0, 160.0, 130.0, 140.0, 1_000_000.0])
    # Monday: still through the trail (peak stays 160).
    rows.append([t0 + 7 * 86_400_000, 140.0, 145.0, 135.0, 140.0, 1_000_000.0])
    # Tuesday: close 150 is only 6.25% off 160, so the streak must clear.
    rows.append([t0 + 8 * 86_400_000, 148.0, 152.0, 146.0, 150.0, 1_000_000.0])
    return {"ETH": rows}


def test_trail_confirm_waits_for_a_second_close_at_the_same_level() -> None:
    ohlc = _pierce_tape()
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=100.0, opened_ms=1
    )
    policy = ExitPolicy(name="c2", alt_trail_pct=0.10, alt_trail_confirm=2)
    first = _overlay_orders(book, ohlc, "2024-01-07", policy, ohlc["ETH"][6][0])
    assert first == []
    assert book.lots["ETH"].trail_streak == 1
    second = _overlay_orders(book, ohlc, "2024-01-08", policy, ohlc["ETH"][7][0])
    assert any(o.reason == "alt_trail" for o in second)

    book.lots["ETH"].peak_px = 100.0
    book.lots["ETH"].trail_streak = 0
    book.lots["ETH"].trail_banked = False
    assert _overlay_orders(book, ohlc, "2024-01-07", policy, ohlc["ETH"][6][0]) == []
    recovered = _overlay_orders(book, ohlc, "2024-01-09", policy, ohlc["ETH"][8][0])
    assert recovered == []
    assert book.lots["ETH"].trail_streak == 0


def test_half_pierce_banks_half_and_keeps_the_rest() -> None:
    ohlc = _pierce_tape()
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=100.0, opened_ms=1
    )
    policy = ExitPolicy(
        name="h", alt_trail_pct=0.10, alt_trail_confirm=2, alt_trail_first_frac=0.5
    )
    first = _overlay_orders(book, ohlc, "2024-01-07", policy, ohlc["ETH"][6][0])
    assert len(first) == 1
    assert first[0].reason == "alt_trail_half"
    assert abs(first[0].qty - 5.0) < 1e-9
    assert book.lots["ETH"].trail_banked is True
    # The overlay does not fill. A second close through the level sells the rest.
    second = _overlay_orders(book, ohlc, "2024-01-08", policy, ohlc["ETH"][7][0])
    assert any(o.reason == "alt_trail" and o.qty == 0.0 for o in second)


def _bar(ts: int, open_: float, high: float, low: float, close: float) -> list[float]:
    return [ts, open_, high, low, close, 1_000_000.0]


def test_touch_sells_at_the_prior_peak_level_when_the_low_trades_it() -> None:
    t0 = 1_704_067_200_000
    rows = [_bar(t0 + i * 86_400_000, 100.0, 100.0, 100.0, 100.0) for i in range(6)]
    # Prior peak is 160. Low 140 trades 144; the close 152 is only 5% off.
    rows.append(_bar(t0 + 6 * 86_400_000, 150.0, 155.0, 140.0, 152.0))
    ohlc = {"ETH": rows}
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=160.0, opened_ms=t0
    )
    touch = ExitPolicy(name="touch", alt_trail_pct=0.10, alt_trail_on_touch=True)
    orders = _overlay_orders(book, ohlc, "2024-01-07", touch, rows[-1][0])
    assert len(orders) == 1
    assert orders[0].reason == "alt_trail_touch"
    assert abs(orders[0].same_bar_px - 144.0) < 1e-9

    book.lots["ETH"].peak_px = 160.0
    close_rule = ExitPolicy(name="close", alt_trail_pct=0.10)
    assert _overlay_orders(book, ohlc, "2024-01-07", close_rule, rows[-1][0]) == []

    # Gap through the level fills at the open, not at the level.
    rows[-1] = _bar(t0 + 6 * 86_400_000, 130.0, 132.0, 120.0, 128.0)
    book.lots["ETH"].peak_px = 160.0
    gap = _overlay_orders(book, ohlc, "2024-01-07", touch, rows[-1][0])
    assert len(gap) == 1
    assert abs(gap[0].same_bar_px - 130.0) < 1e-9
