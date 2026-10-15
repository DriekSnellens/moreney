from __future__ import annotations

from bot.research.crowd_lab.engine import (
    PERP_FEE,
    PERP_SLIP,
    SPOT_FEE,
    SPOT_SLIP,
    Bars,
    Funding,
    Strategy,
    event_study,
    run,
)
from bot.research.crowd_lab.strategies import grid_bot

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01


def _rows(prices: list[float], *, vol: float = 1.0) -> list[list[float]]:
    out = []
    for k, p in enumerate(prices):
        out.append([T0 + k * DAY, p, p * 1.02, p * 0.98, p, vol])
    return out


def _bars(**series: list[float]) -> Bars:
    return Bars.build({b: _rows(px) for b, px in series.items()}, anchor=next(iter(series)))


def test_rsi_bounds_and_sma():
    up = [100 + i for i in range(40)]
    bars = _bars(BTC=up)
    rsi = bars.rsi("BTC", 14)
    assert rsi[-1] is not None and rsi[-1] == 100.0
    assert bars.sma("BTC", 5)[-1] == sum(up[-5:]) / 5
    assert bars.rsi("BTC", 14)[5] is None


def test_long_trade_books_fees_and_slippage():
    px = [100.0] * 5 + [100.0, 110.0, 110.0, 110.0, 110.0]
    bars = _bars(BTC=px)
    fired = {"done": False}

    def entry(_b: Bars, _base: str, i: int) -> bool:
        if i == 4 and not fired["done"]:
            fired["done"] = True
            return True
        return False

    strat = Strategy("one", entry, max_bars=2, max_pos=1)
    r = run(bars, strat, start="2024-01-01", end="2024-01-10", book_eur=20_000)
    assert r.n_trades == 1
    p = r.positions[0]
    assert p.i_entry == 5 and p.i_exit == 7 and p.reason == "time"
    assert p.px_entry == 100.0 * (1 + SPOT_SLIP)
    assert p.px_exit == 110.0 * (1 - SPOT_SLIP)
    assert abs(p.fees - 2 * 20_000 * SPOT_FEE) < 1e-6
    assert r.pnl_eur > 0 and abs(r.pnl_eur - p.pnl) < 1e-9


def test_short_perp_pays_positive_funding_and_hits_stop():
    px = [100.0] * 5 + [100.0, 100.5, 104.0, 104.0]
    bars = _bars(ETH=px)
    funding = Funding({"ETH": [(T0 + k * 8 * 3_600_000, 0.0001) for k in range(60)]})

    def entry(_b: Bars, _base: str, i: int) -> bool:
        return i == 4

    strat = Strategy("s", entry, side=-1, venue="perp", stop_pct=0.03, max_bars=10, max_pos=1)
    r = run(bars, strat, start="2024-01-01", end="2024-01-09", book_eur=10_000, funding=funding)
    p = r.positions[0]
    assert p.reason == "stop" and p.i_exit == 7
    assert abs(p.px_exit - p.px_entry * 1.03 * (1 + PERP_SLIP)) < 1e-9
    # short receives positive funding: 6 settlements over two days at 1bp on 10k
    assert p.funding < 0 and abs(p.funding + 6 * 10_000 * 0.0001) < 1e-6
    assert abs(p.fees - 2 * 10_000 * PERP_FEE) < 1e-6
    assert r.pnl_eur < 0


def test_spot_cannot_short():
    bars = _bars(BTC=[100.0] * 10)
    try:
        run(bars, Strategy("x", lambda *_: False, side=-1), start="2024-01-01", end="2024-01-10")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_event_study_forward_returns():
    px = [100.0, 100.0, 100.0, 110.0, 121.0, 121.0, 121.0]
    bars = _bars(BTC=px)
    e = event_study(
        bars,
        "ev",
        lambda _b, _base, i: i == 2,
        horizons=(1, 2),
        start="2024-01-01",
        end="2024-01-07",
    )
    # entry at the open of bar 3 (110); horizon h is close[i+h] versus that open
    assert e.n == 1
    assert abs(e.horizons[1]["mean"] - 0.0) < 1e-9
    assert abs(e.horizons[2]["mean"] - 0.10) < 1e-9


def test_grid_bot_range_and_reset():
    # oscillate inside a +-10% grid: one buy level filled, sold one step higher
    px = [100.0, 98.5, 99.6, 98.5, 99.6, 150.0, 150.0]
    bars = _bars(BTC=px)
    g = grid_bot(bars, "BTC", start="2024-01-01", end="2024-01-07", levels=10, step=0.01)
    assert g.resets == 1
    assert g.fills >= 4
    # two round trips of ~1% gross on 1k each minus ~0.7% costs each: small positive
    assert -10 < g.pnl_eur < 30
