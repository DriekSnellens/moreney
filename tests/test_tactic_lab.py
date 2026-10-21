"""Published momentum tactics buy a rising name and respect an AlphaI list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.tactic_lab.engine import TacticSpec, run_tactic


def _daily(closes: list[float]) -> list[list[float]]:
    start = datetime(2024, 6, 1, tzinfo=UTC)
    rows = []
    prev = float(closes[0])
    for i, close in enumerate(closes):
        c = float(close)
        ts = int((start + timedelta(days=i)).timestamp() * 1000)
        rows.append([ts, prev, max(prev, c), min(prev, c), c, 1_000_000.0])
        prev = c
    return rows


def _spec(**kw: object) -> TacticSpec:
    base: dict[str, object] = dict(
        name="t",
        kind="tsmom",
        lookbacks=(2,),
        skip=1,
        rebalance_days=3,
        excess_floor=0.0,
        require_sma=0,
        trail_pct=0.0,
        btc_sma=3,
        min_qvol_eur=1.0,
        universe=("AAA", "BBB"),
    )
    base.update(kw)
    return TacticSpec(**base)  # type: ignore[arg-type]


def test_tsmom_buys_the_riser_and_skips_the_faller() -> None:
    n = 12
    btc = [100 + i for i in range(n)]
    aaa = [100 + i * 3 for i in range(n)]
    bbb = [100 - i for i in range(n)]
    ohlc = {"BTC": _daily(btc), "AAA": _daily(aaa), "BBB": _daily(bbb)}
    row = run_tactic(
        ohlc, _spec(), start="2024-06-01", end="2024-06-20", book_eur=10_000.0, keep_trades=True
    )
    buys = [t["base"] for t in row["trades"] if t["side"] == "buy"]
    assert buys and set(buys) == {"AAA"}


def test_alphai_intersect_blocks_a_riser_that_was_not_picked() -> None:
    n = 12
    btc = [100 + i for i in range(n)]
    aaa = [100 + i * 3 for i in range(n)]
    bbb = [100 + i * 2 for i in range(n)]
    ohlc = {"BTC": _daily(btc), "AAA": _daily(aaa), "BBB": _daily(bbb)}
    allow = {f"2024-06-{day:02d}": {"BBB"} for day in range(1, 21)}
    row = run_tactic(
        ohlc,
        _spec(),
        start="2024-06-01",
        end="2024-06-20",
        book_eur=10_000.0,
        alphai_allow=allow,
        alphai_from="2024-06-01",
        keep_trades=True,
    )
    buys = [t["base"] for t in row["trades"] if t["side"] == "buy"]
    assert buys and set(buys) == {"BBB"}


def test_ctrend_does_not_trade_before_every_horizon_exists() -> None:
    n = 10
    btc = [100 + i for i in range(n)]
    aaa = [100 + i * 2 for i in range(n)]
    ohlc = {"BTC": _daily(btc), "AAA": _daily(aaa)}
    row = run_tactic(
        ohlc,
        _spec(kind="ctrend", lookbacks=(7, 14), universe=("AAA",)),
        start="2024-06-01",
        end="2024-06-20",
        book_eur=10_000.0,
        keep_trades=True,
    )
    assert row["end_hold"] == "cash"
    assert not row["trades"]
