"""Clip exit overlay lab — synthetic 1d tape."""

from __future__ import annotations

from bot.research.clip_exit_lab.engine import DRY, Book, Lot, _overlay_orders, run_clip_exits
from bot.research.clip_exit_lab.policies import POLICIES, ExitPolicy


def _bars(n: int, start: float, step: float, vol: float = 20_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000  # 2024-01-01
    for i in range(n):
        rows.append([t0 + i * 86_400_000, px, px + abs(step), px - abs(step) * 0.4, px, vol])
        px += step
    return rows


def _pump_dump_alt() -> dict[str, list[list[float]]]:
    btc = _bars(80, 100.0, 0.4)
    eth = _bars(60, 10.0, 0.02)
    # 20d skip-1 excess vs BTC ≥ 8% then a sharp giveback.
    pump = _bars(12, eth[-1][4], 0.8)
    t0 = eth[-1][0] + 86_400_000
    for i, r in enumerate(pump):
        r[0] = t0 + i * 86_400_000
    dump = _bars(10, pump[-1][4], -1.2)
    t1 = pump[-1][0] + 86_400_000
    for i, r in enumerate(dump):
        r[0] = t1 + i * 86_400_000
    eth = eth + pump + dump
    return {"BTC": btc, "ETH": eth}


def test_policy_grid_unique_names() -> None:
    names = [p.name for p in POLICIES()]
    assert "live" in names
    assert len(names) == len(set(names))


def test_profit_lock_sells_when_close_falls_back_to_entry() -> None:
    ts = 1_704_067_200_000
    date = "2024-01-01"
    book = Book(0.0)
    book.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=10.0, peak_px=10.0, opened_ms=ts
    )
    ohlc = {
        "ETH": [[ts, 11.0, 12.0, 9.5, 10.0, 1_000_000.0]],
        "BTC": [[ts, 100.0, 101.0, 99.0, 100.0, 1_000_000.0]],
    }
    policy = ExitPolicy(name="lock", alt_lock_arm_pct=0.10, alt_lock_floor_pct=0.0)
    orders = _overlay_orders(book, ohlc, date, policy, ts)
    assert any(o.side == "sell" and o.reason == "alt_lock" for o in orders)

    quiet = Book(0.0)
    quiet.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=10.0, peak_px=10.0, opened_ms=ts
    )
    shallow = {
        "ETH": [[ts, 10.2, 10.5, 9.0, 9.2, 1_000_000.0]],
        "BTC": [[ts, 100.0, 101.0, 99.0, 100.0, 1_000_000.0]],
    }
    assert _overlay_orders(quiet, shallow, date, policy, ts) == []


def test_shallow_spike_sells_and_ignores_a_big_winner() -> None:
    ts = 1_704_067_200_000
    date = "2024-01-01"
    policy = ExitPolicy(
        name="spike",
        alt_spike_arm_pct=0.08,
        alt_spike_max_pct=0.15,
        alt_spike_giveback_pct=0.04,
        alt_trail_pct=0.10,
    )

    def book_at(high: float, close: float) -> list:
        book = Book(0.0)
        book.lots["ETH"] = Lot(
            base="ETH", qty=10.0, role="alt", entry_px=10.0, peak_px=10.0, opened_ms=ts
        )
        ohlc = {
            "ETH": [[ts, 10.0, high, close, close, 1_000_000.0]],
            "BTC": [[ts, 100.0, 101.0, 99.0, 100.0, 1_000_000.0]],
        }
        return _overlay_orders(book, ohlc, date, policy, ts)

    shallow = book_at(11.0, 10.5)
    assert any(o.side == "sell" and "alt_spike" in o.reason for o in shallow)
    assert book_at(20.0, 19.0) == []

    stale = Book(0.0)
    stale.lots["ETH"] = Lot(
        base="ETH", qty=10.0, role="alt", entry_px=10.0, peak_px=11.0, opened_ms=ts
    )
    fade = {
        "ETH": [[ts, 10.6, 10.8, 10.3, 10.4, 1_000_000.0]],
        "BTC": [[ts, 100.0, 101.0, 99.0, 100.0, 1_000_000.0]],
    }
    same_day = ExitPolicy(
        name="spike_day",
        alt_spike_arm_pct=0.08,
        alt_spike_max_pct=0.15,
        alt_spike_giveback_pct=0.04,
        alt_trail_pct=0.10,
        alt_spike_same_day=True,
    )
    assert _overlay_orders(stale, fade, date, same_day, ts) == []
    assert any(
        o.side == "sell" and "alt_spike" in o.reason
        for o in _overlay_orders(stale, fade, date, policy, ts)
    )
    done = Book(0.0)
    done.lots["ETH"] = Lot(
        base="ETH",
        qty=10.0,
        role="alt",
        entry_px=10.0,
        peak_px=10.0,
        opened_ms=ts,
        spike_done=True,
    )
    assert _overlay_orders(done, {
        "ETH": [[ts, 10.0, 11.0, 10.4, 10.5, 1_000_000.0]],
        "BTC": [[ts, 100.0, 101.0, 99.0, 100.0, 1_000_000.0]],
    }, date, policy, ts) == []
    btc = _bars(80, 100.0, 0.5)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.0, vol=1.0)}
    live = run_clip_exits(
        ohlc,
        ExitPolicy(name="live"),
        start="2024-02-20",
        end="2024-03-20",
        book_eur=20_000.0,
        model=DRY,
    )
    assert live["n_days"] > 0
    assert live["end_eur"] >= 19_000.0


def test_alt_trail_cuts_giveback() -> None:
    ohlc = _pump_dump_alt()
    start, end = "2024-02-15", "2024-03-22"
    live = run_clip_exits(
        ohlc,
        ExitPolicy(name="live"),
        start=start,
        end=end,
        book_eur=20_000.0,
        model=DRY,
    )
    trail = run_clip_exits(
        ohlc,
        ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10),
        start=start,
        end=end,
        book_eur=20_000.0,
        model=DRY,
    )
    assert trail["n_overlay_exits"] >= 1
    assert trail["pnl_eur"] >= live["pnl_eur"] - 1.0
