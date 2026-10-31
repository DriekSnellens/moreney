"""Scale-in, runner trail, and the locked bar. No tape required."""

from __future__ import annotations

import math

from bot.research.better_book.bar import (
    beats_both,
    hits_margin,
    judge_family,
    one_trade_ok,
    region_ok,
)
from bot.research.better_book.search import largest_new_trip, round_trips
from bot.research.btc_residual_mix.engine import (
    _broke_recent_high,
    _scale_in_orders,
    _volume_expanded,
    block_fresh,
    btc_risk_on,
    crowd_alts,
    drop_down_gaps,
    drop_gapped_buys,
    extension_allows,
    hold_unless_gap,
    lead_lost,
    losing_exit,
    pick_residual,
    rearm_after_exit,
    ride_to_keep,
    strip_red_cut_while_leader,
    tide_alts,
    top_volume_universe,
    widen_trail_while_leader,
)
from bot.research.clip_exit_lab.engine import Book, Lot, Order, _overlay_orders
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


def _rank_tape() -> dict[str, list[list[float]]]:
    """BTC varies. BETA is 2x BTC. ALPHA is BTC plus a small daily drift."""
    t0 = 1_704_067_200_000
    n = 45
    btc_lr = [0.02 if i % 3 else -0.01 for i in range(n)]
    btc = [100.0]
    beta = [100.0]
    alpha = [100.0]
    for lr in btc_lr:
        btc.append(btc[-1] * math.exp(lr))
        beta.append(beta[-1] * math.exp(2.0 * lr))
        alpha.append(alpha[-1] * math.exp(lr + 0.003))
    # n+1 prices. Drop the seed so each series has n bars, one per return step.
    def bars(px: list[float]) -> list[list[float]]:
        out = []
        for i, p in enumerate(px[1:]):
            out.append([t0 + i * 86_400_000, p, p, p, p, 5_000_000.0])
        return out

    wild = []
    for i in range(n):
        shock = 0.12 if i % 2 == 0 else -0.12
        base = alpha[i + 1]
        wild.append(base * math.exp(shock if i < 25 else 0.0))
    def bars_px(px: list[float]) -> list[list[float]]:
        out = []
        for i, p in enumerate(px):
            out.append([t0 + i * 86_400_000, p, p * 1.01, p * 0.99, p, 5_000_000.0])
        return out

    return {
        "BTC": bars(btc),
        "BETA": bars(beta),
        "ALPHA": bars(alpha),
        "CALM": bars_px(list(alpha[1:])),
        "WILD": bars_px(wild),
    }


def test_beta_rank_prefers_alpha_over_a_levered_btc_copy() -> None:
    ohlc = _rank_tape()
    date = "2024-02-14"
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("BETA", "ALPHA"),
    )
    raw = pick_residual(ohlc, date, **common)
    adj = pick_residual(ohlc, date, rank_mode="beta", rank_window=30, **common)
    assert raw["wants"][0] == "BETA"
    assert adj["wants"][0] == "ALPHA"


def test_ir_rank_prefers_the_quieter_residual() -> None:
    ohlc = _rank_tape()
    date = "2024-02-14"
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("CALM", "WILD"),
    )
    pick = pick_residual(ohlc, date, rank_mode="ir", rank_window=30, **common)
    assert pick["wants"][0] == "CALM"


def _held(entry: float = 100.0) -> Book:
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=entry, peak_px=entry, opened_ms=1
    )
    return book


def test_rearm_only_after_a_green_close_or_a_lock() -> None:
    full = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_trail")
    partial = Order(side="sell", base="ETH", role="alt", adv=1.0, qty=4.0, reason="alt_trail")
    locked = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_lock+alt_trail")
    green = {"ETH": 110.0}
    red = {"ETH": 90.0}
    assert rearm_after_exit(_held(), [full], green, "green") is True
    assert rearm_after_exit(_held(), [full], red, "green") is False
    assert rearm_after_exit(_held(), [full], green, "green_next") is True
    assert rearm_after_exit(_held(), [full], green, "lock") is False
    assert rearm_after_exit(_held(), [locked], red, "lock") is True
    assert rearm_after_exit(_held(), [partial], green, "green") is False
    assert rearm_after_exit(_held(), [full], green, "") is False


def test_top_volume_universe_skips_a_short_history() -> None:
    t0 = 1_704_067_200_000
    def bars(n: int, vol: float) -> list[list[float]]:
        return [
            [t0 + i * 86_400_000, 10.0, 10.0, 10.0, 10.0, vol] for i in range(n)
        ]

    ohlc = {
        "BTC": bars(80, 1.0),
        "QUIET": bars(80, 100.0),
        "LOUD": bars(80, 10_000.0),
        "NEW": bars(20, 50_000.0),
    }
    # 80 bars from 2024-01-01 ends 2024-03-20.
    picked = top_volume_universe(ohlc, "2024-03-20", 1)
    assert picked == ("LOUD",)
    both = top_volume_universe(ohlc, "2024-03-20", 2)
    assert both[0] == "LOUD"
    assert "NEW" not in both


def test_green_pause_is_due_only_after_the_longer_wait() -> None:
    from bot.research.btc_residual_mix.engine import DAY_MS

    now = 1_700_000_000_000
    rebalance = 7
    pause = 14
    last = now + (pause - rebalance) * DAY_MS
    assert now - last < rebalance * DAY_MS
    assert (now + pause * DAY_MS) - last >= rebalance * DAY_MS
    assert (now + (pause - 1) * DAY_MS) - last < rebalance * DAY_MS


def test_tide_keeps_a_held_name_and_does_not_open_a_new_one() -> None:
    assert tide_alts(("SOL",), ("ETH",), tide_ok=False) == ("ETH",)
    assert tide_alts(("SOL",), (), tide_ok=False) == ()
    assert tide_alts(("SOL",), ("ETH",), tide_ok=True) == ("SOL",)


def test_btc_veto_gap_ignores_a_shallow_dip() -> None:
    assert btc_risk_on(99.0, 100.0) is False
    assert btc_risk_on(99.0, 100.0, gap=0.05) is True
    assert btc_risk_on(94.0, 100.0, gap=0.05) is False
    assert btc_risk_on(50.0, 100.0, gap=1.0) is True
    assert btc_risk_on(100.0, None) is False


def _slot_tape() -> dict[str, list[list[float]]]:
    t0 = 1_704_067_200_000
    n = 40

    def climb(end: float) -> list[list[float]]:
        out = []
        for i in range(n):
            px = 100.0 + (end - 100.0) * i / (n - 1)
            out.append([t0 + i * 86_400_000, px, px, px, px, 5_000_000.0])
        return out

    return {
        "BTC": climb(100.0),
        "HOT": climb(180.0),
        "MID": climb(140.0),
        "COOL": climb(115.0),
    }


def test_rank_slot_buys_a_cooler_qualifier_and_not_the_leader() -> None:
    ohlc = _slot_tape()
    date = "2024-02-09"
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("HOT", "MID", "COOL"),
    )
    assert pick_residual(ohlc, date, **common)["wants"] == ["HOT"]
    assert pick_residual(ohlc, date, rank_slot="second", **common)["wants"] == ["MID"]
    assert pick_residual(ohlc, date, rank_slot="third", **common)["wants"] == ["COOL"]
    assert pick_residual(ohlc, date, rank_slot="floor", **common)["wants"] == ["COOL"]
    alone = dict(common)
    alone["universe"] = ("HOT",)
    assert pick_residual(ohlc, date, rank_slot="second", **alone)["wants"] == []


def test_red_cut_sells_only_a_position_still_under_its_entry() -> None:
    t0 = 1_704_067_200_000
    day = 86_400_000
    rows = [_bar(t0 + i * day, 100.0, 100.0, 100.0, 90.0) for i in range(6)]
    ohlc = {"ETH": rows}
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=100.0, peak_px=100.0, opened_ms=t0
    )
    policy = ExitPolicy(name="red", alt_red_cut_days=4)
    # Four days later the close is still under the entry.
    late = _overlay_orders(book, ohlc, "2024-01-05", policy, t0 + 4 * day)
    assert len(late) == 1
    assert late[0].reason == "alt_red_cut"
    early = _overlay_orders(book, ohlc, "2024-01-03", policy, t0 + 2 * day)
    assert early == []
    rows[-1] = _bar(t0 + 5 * day, 100.0, 110.0, 100.0, 105.0)
    book.lots["ETH"].peak_px = 100.0
    green = _overlay_orders(book, ohlc, "2024-01-06", policy, t0 + 5 * day)
    assert green == []


def test_red_cut_stays_while_the_name_is_still_the_leader() -> None:
    cut = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_red_cut")
    both = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_trail+alt_red_cut")
    other = Order(side="sell", base="SOL", role="alt", adv=1.0, reason="alt_red_cut")
    kept = strip_red_cut_while_leader([cut, both, other], "ETH")
    assert [o.base for o in kept] == ["ETH", "SOL"]
    assert kept[0].reason == "alt_trail"
    assert kept[1].reason == "alt_red_cut"
    still = strip_red_cut_while_leader([cut], "")
    assert len(still) == 1
    assert still[0].reason == "alt_red_cut"


def test_gap_cap_cancels_the_buy_and_keeps_the_sell() -> None:
    t0 = 1_704_067_200_000
    day = 86_400_000
    # Prior close 100, next open 106 is a 6% gap.
    ohlc = {
        "ETH": [
            _bar(t0, 100.0, 100.0, 100.0, 100.0),
            _bar(t0 + day, 106.0, 108.0, 105.0, 107.0),
        ]
    }
    buy = Order(side="buy", base="ETH", role="alt", adv=1.0, notional=1_000.0, reason="residual")
    sell = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="rotate")
    kept = drop_gapped_buys([sell, buy], ohlc, "2024-01-02", 0.05)
    assert [o.side for o in kept] == ["sell"]
    small = drop_gapped_buys([buy], ohlc, "2024-01-02", 0.08)
    assert len(small) == 1


def test_lead_lost_keeps_a_green_leader_and_a_green_ex_leader_on_the_red_rule() -> None:
    assert lead_lost(
        "rank", base="ETH", leader="ETH", excess=0.05, close=90.0, entry=100.0, floor=0.035
    ) is False
    assert lead_lost(
        "rank", base="ETH", leader="SOL", excess=0.05, close=110.0, entry=100.0, floor=0.035
    ) is True
    assert lead_lost(
        "rank_red", base="ETH", leader="SOL", excess=0.05, close=110.0, entry=100.0, floor=0.035
    ) is False
    assert lead_lost(
        "rank_red", base="ETH", leader="SOL", excess=0.05, close=90.0, entry=100.0, floor=0.035
    ) is True
    assert lead_lost(
        "floor", base="ETH", leader="ETH", excess=0.02, close=110.0, entry=100.0, floor=0.035
    ) is True
    assert lead_lost(
        "floor", base="ETH", leader="", excess=None, close=110.0, entry=100.0, floor=0.035
    ) is True
    assert lead_lost(
        "", base="ETH", leader="SOL", excess=0.01, close=90.0, entry=100.0, floor=0.035
    ) is False


def test_keep_ride_does_not_open_a_new_name_and_needs_the_asked_state() -> None:
    assert ride_to_keep("green", green=True, is_leader=False) is True
    assert ride_to_keep("leader", green=False, is_leader=True) is True
    assert ride_to_keep("both", green=True, is_leader=False) is False
    assert ride_to_keep("both", green=True, is_leader=True) is True
    assert ride_to_keep("", green=True, is_leader=True) is False


def test_losing_exit_is_a_close_under_the_entry() -> None:
    assert losing_exit(90.0, 100.0) is True
    assert losing_exit(110.0, 100.0) is False
    assert losing_exit(0.0, 100.0) is False


def test_agree_skips_a_bounce_whose_longer_excess_is_negative() -> None:
    t0 = 1_704_067_200_000
    n = 80

    def bars(prices: list[float]) -> list[list[float]]:
        return [
            [t0 + i * 86_400_000, p, p, p, p, 5_000_000.0] for i, p in enumerate(prices)
        ]

    btc = [100.0] * n
    steady = [100.0 * (1.004 ** i) for i in range(n)]
    bounce = [200.0 - 1.5 * i for i in range(70)]
    bounce.extend([95.0 + 2.0 * (i - 69) for i in range(70, n)])
    ohlc = {"BTC": bars(btc), "STEADY": bars(steady), "BOUNCE": bars(bounce)}
    date = "2024-03-20"
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("BOUNCE", "STEADY"),
    )
    raw = pick_residual(ohlc, date, **common)
    agreed = pick_residual(ohlc, date, agree_days=40, **common)
    assert raw["wants"] == ["BOUNCE"]
    assert agreed["wants"] == ["STEADY"]


def test_volume_expansion_compares_the_last_twenty_days_with_the_twenty_before() -> None:
    quiet = [[0, 1, 1, 1, 10.0, 100.0] for _ in range(20)]
    loud = [[0, 1, 1, 1, 10.0, 400.0] for _ in range(20)]
    assert _volume_expanded(quiet + loud, 1.5) is True
    assert _volume_expanded(loud + quiet, 1.5) is False
    assert _volume_expanded(loud, 1.0) is False


def test_a_leader_keeps_the_position_until_the_wider_giveback() -> None:
    trail = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_trail")
    locked = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="alt_lock+alt_trail")
    other = Order(side="sell", base="SOL", role="alt", adv=1.0, reason="alt_trail")
    peaks = {"ETH": 100.0, "SOL": 100.0}
    # 12% off the peak. A 20% band keeps the leader and still sells the other name.
    held = widen_trail_while_leader(
        [trail, other], "ETH", wide=0.20, peaks=peaks, closes={"ETH": 88.0, "SOL": 88.0}
    )
    assert [o.base for o in held] == ["SOL"]
    # 22% off sells the leader too.
    sold = widen_trail_while_leader(
        [trail], "ETH", wide=0.20, peaks=peaks, closes={"ETH": 78.0}
    )
    assert len(sold) == 1
    assert sold[0].reason == "alt_trail"
    # The lock still sells inside the wide band.
    banked = widen_trail_while_leader(
        [locked], "ETH", wide=0.20, peaks=peaks, closes={"ETH": 88.0}
    )
    assert len(banked) == 1
    assert banked[0].reason == "alt_lock"


def test_crowd_gate_keeps_a_held_name_when_the_count_is_outside() -> None:
    assert crowd_alts(("SOL",), ("ETH",), count=6, bound=3, side="max") == ("ETH",)
    assert crowd_alts(("SOL",), (), count=6, bound=3, side="max") == ()
    assert crowd_alts(("SOL",), ("ETH",), count=2, bound=3, side="max") == ("SOL",)
    assert crowd_alts(("SOL",), ("ETH",), count=1, bound=3, side="min") == ("ETH",)
    assert crowd_alts(("SOL",), ("ETH",), count=4, bound=3, side="min") == ("SOL",)


def test_gap_down_cancels_the_buy_and_keeps_the_sell() -> None:
    t0 = 1_704_067_200_000
    day = 86_400_000
    ohlc = {
        "ETH": [
            _bar(t0, 100.0, 100.0, 100.0, 100.0),
            _bar(t0 + day, 94.0, 96.0, 93.0, 95.0),
        ]
    }
    buy = Order(side="buy", base="ETH", role="alt", adv=1.0, notional=1_000.0, reason="residual")
    sell = Order(side="sell", base="ETH", role="alt", adv=1.0, reason="rotate")
    kept = drop_down_gaps([sell, buy], ohlc, "2024-01-02", 0.04)
    assert [o.side for o in kept] == ["sell"]
    small = drop_down_gaps([buy], ohlc, "2024-01-02", 0.08)
    assert len(small) == 1


def test_a_small_lead_does_not_rotate_the_held_name() -> None:
    excess = {"ETH": 0.10, "SOL": 0.12}
    assert hold_unless_gap(("SOL",), ("ETH",), excess, gap=0.05) == ("ETH",)
    assert hold_unless_gap(("SOL",), ("ETH",), excess, gap=0.02) == ("SOL",)
    assert hold_unless_gap(("SOL",), (), excess, gap=0.05) == ("SOL",)
    assert hold_unless_gap((), ("ETH",), excess, gap=0.05) == ()
    # No excess on the held name: the rotation stands.
    assert hold_unless_gap(("SOL",), ("ETH",), {"SOL": 0.20}, gap=0.05) == ("SOL",)


def test_extension_band_blocks_a_new_name_and_keeps_a_flatten() -> None:
    assert extension_allows(0.04, side="max", bound=0.05) is True
    assert extension_allows(0.08, side="max", bound=0.05) is False
    assert extension_allows(0.06, side="min", bound=0.05) is True
    assert extension_allows(0.03, side="min", bound=0.05) is False
    assert extension_allows(None, side="max", bound=0.05) is False
    assert block_fresh(("SOL",), ("ETH",), allow=False) == ("ETH",)
    assert block_fresh(("SOL",), (), allow=False) == ()
    assert block_fresh((), ("ETH",), allow=False) == ()
    assert block_fresh(("ETH",), ("ETH",), allow=False) == ("ETH",)
    assert block_fresh(("SOL",), ("ETH",), allow=True) == ("SOL",)


def test_own_return_floor_drops_a_leader_that_only_beat_btc() -> None:
    t0 = 1_704_067_200_000
    n = 30

    def bars(closes: list[float], highs: list[float] | None = None) -> list[list[float]]:
        out = []
        for i, close in enumerate(closes):
            high = close if highs is None else highs[i]
            out.append([t0 + i * 86_400_000, close, high, close, close, 5_000_000.0])
        return out

    btc = [100.0] * n
    btc[28] = 80.0
    btc[29] = 80.0
    # Fell 2% while BTC fell 20%. Near the high, so the live score ranks it first.
    fall = [100.0] * n
    fall[28] = 98.0
    fall[29] = 98.0
    # Up 1% and far under an old high, so the near-high term ranks it second.
    rise = [100.0] * n
    rise[28] = 101.0
    rise[29] = 101.0
    rise_high = [100.0] * n
    rise_high[0] = 220.0
    ohlc = {
        "BTC": bars(btc),
        "FALL": bars(fall),
        "RISE": bars(rise, rise_high),
    }
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.25,
        universe=("FALL", "RISE"),
    )
    date = "2024-01-30"
    raw = pick_residual(ohlc, date, **common)
    floored = pick_residual(ohlc, date, own_floor=0.0, **common)
    assert raw["wants"] == ["FALL"]
    assert floored["wants"] == ["RISE"]


def test_acceleration_rank_prefers_a_rising_excess_and_decel_the_fade() -> None:
    t0 = 1_704_067_200_000
    n = 40

    def bars(closes: list[float]) -> list[list[float]]:
        return [
            [t0 + i * 86_400_000, c, c, c, c, 5_000_000.0] for i, c in enumerate(closes)
        ]

    btc = [100.0] * n
    rise = [100.0] * n
    rise[38] = 130.0
    rise[39] = 130.0
    fade = [100.0] * n
    fade[28] = 110.0
    fade[33] = 140.0
    fade[38] = 130.0
    fade[39] = 130.0
    ohlc = {"BTC": bars(btc), "RISE": bars(rise), "FADE": bars(fade)}
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("RISE", "FADE"),
        rank_window=5,
    )
    date = "2024-02-09"
    assert pick_residual(ohlc, date, rank_mode="accel", **common)["wants"] == ["RISE"]
    assert pick_residual(ohlc, date, rank_mode="decel", **common)["wants"] == ["FADE"]


def test_max_day_rank_can_prefer_or_avoid_the_spike() -> None:
    t0 = 1_704_067_200_000
    n = 30

    def bars(closes: list[float], volume: float = 5_000_000.0) -> list[list[float]]:
        return [
            [t0 + i * 86_400_000, c, c, c, c, volume] for i, c in enumerate(closes)
        ]

    btc = [100.0] * n
    steady = [100.0] * n
    for i in range(18, n):
        steady[i] = 100.0 + (i - 18) * 1.2
    spike = [100.0] * n
    spike[26] = 130.0
    spike[27] = 130.0
    spike[28] = 130.0
    spike[29] = 130.0
    ohlc = {"BTC": bars(btc), "STEADY": bars(steady), "SPIKE": bars(spike)}
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("STEADY", "SPIKE"),
        rank_window=10,
    )
    date = "2024-01-30"
    assert pick_residual(ohlc, date, rank_mode="max_high", **common)["wants"] == ["SPIKE"]
    assert pick_residual(ohlc, date, rank_mode="max_low", **common)["wants"] == ["STEADY"]


def test_liquidity_rank_picks_the_quieter_or_the_louder_qualifier() -> None:
    t0 = 1_704_067_200_000
    n = 30

    def bars(prices: list[float], volume: float) -> list[list[float]]:
        return [
            [t0 + i * 86_400_000, p, p, p, p, volume] for i, p in enumerate(prices)
        ]

    flat = [100.0] * n
    rising = [100.0 + i for i in range(n)]
    ohlc = {
        "BTC": bars(flat, 5_000_000.0),
        "QUIET": bars(rising, 200_000.0),
        "LOUD": bars(rising, 2_000_000.0),
    }
    common = dict(
        excess_floor=0.0,
        lookback_days=10,
        skip_days=1,
        n_alts=1,
        require_alt_sma=False,
        near_high_weight=0.0,
        universe=("QUIET", "LOUD"),
        rank_window=20,
    )
    date = "2024-01-30"
    assert pick_residual(ohlc, date, rank_mode="liq_low", **common)["wants"] == ["QUIET"]
    assert pick_residual(ohlc, date, rank_mode="liq_high", **common)["wants"] == ["LOUD"]


def test_a_close_through_the_prior_high_is_the_breakout_side() -> None:
    rows = [
        [0, 100.0, 100.0, 100.0, 100.0, 1.0],
        [1, 101.0, 104.0, 99.0, 103.0, 1.0],
        [2, 103.0, 106.0, 102.0, 105.0, 1.0],
    ]
    assert _broke_recent_high(rows, 1) is True
    assert _broke_recent_high(rows, 2) is True
    inside = [
        [0, 100.0, 110.0, 100.0, 100.0, 1.0],
        [1, 100.0, 108.0, 100.0, 105.0, 1.0],
    ]
    assert _broke_recent_high(inside, 1) is False
    assert _broke_recent_high(inside[:1], 1) is None
