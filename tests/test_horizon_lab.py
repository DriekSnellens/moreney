"""Day and week horizon decisions, on synthetic bars."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.horizon_lab.engine import (
    DaySpec,
    HorizonSpec,
    day_session_actions,
    run_day_4h,
    run_horizon,
    week_actions,
)


def test_week_clock_keeps_runners_and_does_not_churn_the_same_leader() -> None:
    assert week_actions(
        held="AAA", runner=True, pick="BBB", keep_runners=True, pending_exit=False
    ) == (None, None)
    assert week_actions(
        held="AAA", runner=True, pick="BBB", keep_runners=False, pending_exit=False
    ) == ("AAA", "BBB")
    assert week_actions(
        held="AAA", runner=False, pick="AAA", keep_runners=False, pending_exit=False
    ) == (None, None)
    assert week_actions(
        held="AAA", runner=False, pick="BBB", keep_runners=True, pending_exit=False
    ) == ("AAA", "BBB")
    assert week_actions(
        held=None, runner=False, pick="BBB", keep_runners=True, pending_exit=False
    ) == (None, "BBB")
    assert week_actions(
        held="AAA", runner=False, pick="BBB", keep_runners=False, pending_exit=True
    ) == (None, None)


def test_day_session_rolls_and_graduates() -> None:
    assert day_session_actions(
        held="AAA",
        runner=False,
        pick="BBB",
        week_busy=False,
        only_when_week_flat=True,
        graduate=False,
    ) == ("AAA", "BBB")
    assert day_session_actions(
        held="AAA",
        runner=True,
        pick="BBB",
        week_busy=False,
        only_when_week_flat=True,
        graduate=True,
    ) == (None, None)
    assert day_session_actions(
        held=None,
        runner=False,
        pick="BBB",
        week_busy=True,
        only_when_week_flat=True,
        graduate=False,
    ) == (None, None)
    assert day_session_actions(
        held="AAA",
        runner=False,
        pick="BBB",
        week_busy=True,
        only_when_week_flat=True,
        graduate=False,
    ) == ("AAA", None)


def _daily(start: str, closes: list[float], *, volume: float = 1_000_000.0) -> list[list[float]]:
    d0 = datetime.fromisoformat(start).replace(tzinfo=UTC)
    out: list[list[float]] = []
    prev = float(closes[0])
    for i, close in enumerate(closes):
        c = float(close)
        ts = int((d0 + timedelta(days=i)).timestamp() * 1000)
        o = prev
        out.append([ts, o, max(o, c), min(o, c), c, volume])
        prev = c
    return out


def _spec(**kw: object) -> HorizonSpec:
    base: dict[str, object] = dict(
        name="t",
        clock="daily",
        keep_runners=True,
        runner_arm=0.10,
        trail_pct=0.50,
        lock_arm=0.0,
        lock_floor=0.0,
        rebalance_days=1,
        lookback_days=1,
        skip_days=0,
        excess_floor=0.02,
        near_high_weight=0.0,
        require_alt_sma=False,
        sma_n=2,
        flatten_risk_off=True,
        min_qvol_eur=1.0,
        week_frac=1.0,
        day_frac=0.0,
        universe=("AAA", "BBB"),
    )
    base.update(kw)
    return HorizonSpec(**base)  # type: ignore[arg-type]


def _market() -> dict[str, list[list[float]]]:
    # Day 4 AAA becomes the leader and is bought day 5. Day 5 closes through
    # the runner arm. Day 6 BBB takes the lead.
    btc = [100 + i for i in range(10)]
    aaa = [100, 101, 102, 103, 130, 160, 162, 163, 164, 165]
    bbb = [100, 100, 100, 100, 100, 100, 140, 141, 142, 143]
    start = "2024-06-01"
    return {"BTC": _daily(start, btc), "AAA": _daily(start, aaa), "BBB": _daily(start, bbb)}


def test_week_replay_keeps_a_runner_when_the_leader_changes() -> None:
    ohlc = _market()
    kept = run_horizon(
        ohlc,
        _spec(keep_runners=True),
        start="2024-06-01",
        end="2024-06-10",
        keep_trades=True,
        keep_holds=True,
    )
    cut = run_horizon(
        ohlc,
        _spec(name="cut", keep_runners=False),
        start="2024-06-01",
        end="2024-06-10",
        keep_trades=True,
        keep_holds=True,
    )
    # 8 June is the open after BBB takes the lead. The day after that, BBB's
    # own one-day excess falls through the floor and the cut book goes to cash.
    assert dict(kept["holds"])["2024-06-08"] == "week:AAA"
    assert dict(cut["holds"])["2024-06-08"] == "week:BBB"
    assert kept["end_hold"] == "week:AAA"
    assert not any(t["reason"] == "week_clock" for t in kept["trades"])
    assert any(t["reason"] == "week_clock" and t["base"] == "AAA" for t in cut["trades"])


def test_same_leader_is_not_round_tripped() -> None:
    btc = [100 + i for i in range(8)]
    aaa = [100 * (1.05**i) for i in range(8)]
    ohlc = {"BTC": _daily("2024-06-01", btc), "AAA": _daily("2024-06-01", aaa)}
    row = run_horizon(
        ohlc,
        _spec(universe=("AAA",), keep_runners=False),
        start="2024-06-01",
        end="2024-06-08",
        keep_trades=True,
    )
    assert any(t["reason"] == "week_entry" for t in row["trades"])
    assert not any(t["reason"] == "week_clock" for t in row["trades"])


def test_risk_off_flattens_a_runner() -> None:
    btc = [100, 101, 102, 103, 104, 105, 106, 40, 30]
    aaa = [100, 101, 102, 103, 140, 180, 182, 183, 184]
    ohlc = {
        "BTC": _daily("2024-06-01", btc),
        "AAA": _daily("2024-06-01", aaa),
    }
    row = run_horizon(
        ohlc,
        _spec(universe=("AAA",), keep_runners=True),
        start="2024-06-01",
        end="2024-06-09",
        keep_trades=True,
    )
    assert any(t["reason"] == "btc_below_sma50" for t in row["trades"])
    assert row["end_hold"] == "cash"


def test_day_session_pays_the_round_trip_and_graduate_keeps_the_close() -> None:
    btc = [100 + i for i in range(8)]
    aaa = [100 * (1.06**i) for i in range(8)]
    ohlc = {"BTC": _daily("2024-06-01", btc), "AAA": _daily("2024-06-01", aaa)}
    session = run_horizon(
        ohlc,
        _spec(
            universe=("AAA",),
            week_frac=0.0,
            day_frac=1.0,
            day_only_when_flat=False,
            day_graduate=False,
            day_lookback=1,
            day_skip=0,
            day_floor=0.02,
            runner_arm=0.50,
        ),
        start="2024-06-01",
        end="2024-06-08",
        keep_trades=True,
    )
    assert session["n_day_entries"] >= 2
    assert session["reasons"]["day_flat"] >= 1
    graduated = run_horizon(
        ohlc,
        _spec(
            universe=("AAA",),
            week_frac=0.0,
            day_frac=1.0,
            day_only_when_flat=True,
            day_graduate=True,
            day_lookback=1,
            day_skip=0,
            day_floor=0.02,
            runner_arm=0.05,
            keep_runners=True,
        ),
        start="2024-06-01",
        end="2024-06-08",
        keep_trades=True,
    )
    assert graduated["end_hold"] == "week:AAA"
    assert "day_flat" not in graduated["reasons"]


def test_4h_day_book_exits_on_the_next_utc_open() -> None:
    start = datetime(2024, 6, 3, tzinfo=UTC)
    step = 4 * 3_600_000
    n = 18
    btc_px = [100 * (1.01**i) for i in range(n)]
    alt_px = [100.0] * n
    alt_px[6] = 130.0
    for i in range(7, n):
        alt_px[i] = 130.0 * (1.002 ** (i - 6))

    def pack(closes: list[float]) -> list[list[float]]:
        rows = []
        prev = closes[0]
        for i, c in enumerate(closes):
            ts = int(start.timestamp() * 1000) + i * step
            o = prev
            rows.append([ts, o, max(o, c), min(o, c), c, 1_000_000.0])
            prev = c
        return rows

    ohlc = {"BTC": pack(btc_px), "AAA": pack(alt_px)}
    spec = DaySpec(
        name="t",
        decision_hour=0,
        lookback=2,
        skip=0,
        excess_floor=0.02,
        sma_bars=3,
        btc_sma_bars=3,
        min_qvol_eur=1.0,
        require_alt_sma=False,
        universe=("AAA",),
    )
    row = run_day_4h(
        ohlc, spec, start="2024-06-03", end="2024-06-06", book_eur=10_000.0, keep_trades=True
    )
    buys = [t for t in row["trades"] if t["side"] == "buy"]
    sells = [t for t in row["trades"] if t["reason"] == "day_flat"]
    assert buys and sells
    assert buys[0]["date"] == "2024-06-04"
    assert sells[0]["date"] == "2024-06-05"
    assert buys[0]["ts"] < sells[0]["ts"]
