from __future__ import annotations

from bot.research.bear_sleeve.engine import (
    PERP_FEE,
    PERP_SLIP,
    BearSpec,
    Frame,
    _weakest,
    pair,
    phase_average,
    run,
)

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC


def _series(prices, t0=T0):
    return [[t0 + i * DAY, p, p * 1.01, p * 0.99, p, 1.0] for i, p in enumerate(prices)]


def _ohlc(n=120):
    btc = [100.0 + 0.5 * i for i in range(n)]  # steady uptrend
    weak = [100.0 * (0.99**i) for i in range(n)]  # steady -1%/day
    strong = [100.0 * (1.01**i) for i in range(n)]
    return {"BTC": _series(btc), "WEAK": _series(weak), "STRONG": _series(strong)}


def test_frame_aligns_on_btc_calendar_and_computes_ret_sma():
    ohlc = _ohlc(30)
    ohlc["GAPPY"] = _series([10.0] * 10)  # only the first 10 days
    f = Frame.build(ohlc)
    assert len(f.dates) == 30 and f.dates[0] == "2024-01-01"
    assert f.close["GAPPY"][9] == 10.0 and f.close["GAPPY"][10] is None
    assert abs(f.ret("BTC", 10, 10, 0) - (105.0 / 100.0 - 1.0)) < 1e-12
    assert f.ret("BTC", 5, 10, 0) is None
    assert f.sma("BTC", 9, 10) == 102.25 and f.sma("BTC", 8, 10) is None


def test_weakest_ranks_by_lookback_return_and_honours_floor():
    f = Frame.build(_ohlc(60))
    spec = BearSpec(name="t", kind="short_weakest", lookback=15, top_n=2, mom_floor=-0.03)
    spec = BearSpec(**{**spec.__dict__, "require_below_own_sma": 0})
    picks = _weakest(f, 59, spec, ["BTC", "WEAK", "STRONG"])
    assert [b for b, _ in picks] == ["WEAK"]  # BTC/STRONG are above the -3% floor
    loose = BearSpec(**{**spec.__dict__, "mom_floor": 1.0})
    assert [b for b, _ in _weakest(f, 59, loose, ["BTC", "WEAK", "STRONG"])] == ["WEAK", "BTC"]


def test_short_weakest_earns_the_decline_minus_costs_and_funding():
    ohlc = _ohlc(90)
    # BTC far below SMA so the bear gate is on: replace with a downtrend
    ohlc["BTC"] = _series([200.0 - i for i in range(90)])
    spec = BearSpec(
        name="t",
        kind="short_weakest",
        gate_sma=20,
        lookback=10,
        top_n=1,
        require_below_own_sma=0,
        rebalance_days=1000,
        hard_stop=0.0,
    )
    fund = {"WEAK": {d: 0.0001 for d in Frame.build(ohlc).dates}}
    r = run(ohlc, spec, start="2024-02-01", end="2024-03-30", book_eur=10_000, funding=fund)
    assert r.n_trades == 1 and r.days_active > 40
    assert r.pnl_eur > 0 and r.funding_eur > 0
    assert abs(r.fees_eur - 10_000 * (PERP_FEE + PERP_SLIP)) < 1e-6
    assert r.by_year["2024"]["pnl_eur"] == round(r.pnl_eur, 0)


def test_hard_stop_flattens_a_short_that_runs_against_us():
    n = 60
    ohlc = {
        "BTC": _series([200.0 - i for i in range(n)]),
        "UP": _series(
            [100.0 * (0.98**i) for i in range(30)]
            + [100.0 * (0.98**29) * (1.03**i) for i in range(30)]
        ),
    }
    spec = BearSpec(
        name="t",
        kind="short_weakest",
        gate_sma=20,
        lookback=10,
        require_below_own_sma=0,
        rebalance_days=1000,
        hard_stop=0.10,
    )
    r = run(ohlc, spec, start="2024-01-25", end="2024-02-29", book_eur=10_000)
    assert r.n_trades == 2  # entered once, stopped once, never re-entered (clock not due)
    assert r.days_active < r.days_total


def test_capitulation_buys_after_drop_and_exits_on_hold_or_take_profit():
    prices = [100.0] * 20 + [85.0, 84.0, 83.0] + [90.0, 95.0] + [95.0] * 20
    ohlc = {"BTC": _series(prices)}
    spec = BearSpec(
        name="t",
        kind="capitulation",
        gate_mode="always",
        drop_days=3,
        drop_pct=-0.12,
        hold_days=5,
        take_profit=0.06,
    )
    r = run(ohlc, spec, start="2024-01-02", end="2024-02-10", book_eur=10_000)
    assert r.n_trades == 2 and r.pnl_eur > 0
    assert r.days_active <= 5


def test_pair_is_dollar_neutral_and_phase_average_matches_run_shape():
    ohlc = _ohlc(120)
    spec = pair("p", top_n=1, lookback=10, rebalance_days=3)
    single = run(ohlc, spec, start="2024-01-20", end="2024-04-20", book_eur=10_000)
    assert single.n_trades >= 2 and single.pnl_eur > 0  # long BTC up, short WEAK down
    stag = phase_average(ohlc, spec, start="2024-01-20", end="2024-04-20", book_eur=10_000)
    assert stag.name.endswith("@staggered3") and stag.pnl_eur > 0
    assert stag.curve[0][0] >= single.curve[0][0]
    assert set(stag.by_year) == {"2024"}
