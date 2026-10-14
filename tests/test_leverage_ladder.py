from __future__ import annotations

from bot.research.factor_scoreboard.leverage import (
    MMR,
    PERP_FEE,
    PERP_SLIP,
    SPOT_FEE,
    positions_from_trades,
    replay_leverage,
)

DAY = 86_400_000
T0 = 1_704_067_200_000  # 2024-01-01


def _series(prices, lows=None):
    lows = lows or [p * 0.99 for p in prices]
    rows = enumerate(zip(prices, lows, strict=True))
    return [[T0 + i * DAY, p, p * 1.01, lo, p, 1.0] for i, (p, lo) in rows]


def test_positions_pair_buys_with_the_next_sell():
    trades = [
        {"date": "2024-01-02", "side": "buy", "base": "AAA", "px": 10.0},
        {"date": "2024-01-05", "side": "sell", "base": "AAA", "px": 12.0},
        {"date": "2024-01-08", "side": "buy", "base": "BBB", "px": 5.0},
    ]
    pos = positions_from_trades(trades)
    assert len(pos) == 1 and pos[0].base == "AAA" and pos[0].exit_px == 12.0


def test_leverage_scales_return_and_costs_without_liquidation():
    ohlc = {"BTC": _series([100.0] * 10), "AAA": _series([10.0, 10.0, 11.0, 12.0] + [12.0] * 6)}
    trades = [
        {"date": "2024-01-02", "side": "buy", "base": "AAA", "px": 10.0},
        {"date": "2024-01-04", "side": "sell", "base": "AAA", "px": 12.0},
    ]
    pos = positions_from_trades(trades)
    kw = {"funding": None, "start": "2024-01-01", "end": "2024-01-10", "book_eur": 1000.0}
    spot = replay_leverage(pos, ohlc, leverage=1.0, perp=False, **kw)
    perp2 = replay_leverage(pos, ohlc, leverage=2.0, perp=True, **kw)
    assert spot.pnl_eur == round(1000 * (0.2 - 2 * SPOT_FEE), 0)
    assert perp2.pnl_eur == round(1000 * (2 * 0.2 - 2 * 2 * (PERP_FEE + PERP_SLIP)), 0)
    assert perp2.liquidations == 0 and perp2.max_dd_pct <= 2 * 2 * (PERP_FEE + PERP_SLIP)


def test_intraday_gap_beyond_margin_liquidates_the_cross_book():
    lows = [9.9, 9.9, 9.9, 10.0 * (1 - (1 / 3 - MMR) - 0.01)] + [11.9] * 6
    ohlc = {"BTC": _series([100.0] * 10), "AAA": _series([10.0] * 4 + [12.0] * 6, lows)}
    trades = [
        {"date": "2024-01-02", "side": "buy", "base": "AAA", "px": 10.0},
        {"date": "2024-01-06", "side": "sell", "base": "AAA", "px": 12.0},
    ]
    pos = positions_from_trades(trades)
    kw = {"funding": None, "start": "2024-01-01", "end": "2024-01-10", "book_eur": 1000.0}
    assert replay_leverage(pos, ohlc, leverage=2.0, perp=True, **kw).liquidations == 0
    r3 = replay_leverage(pos, ohlc, leverage=3.0, perp=True, **kw)
    assert r3.liquidations == 1 and r3.pnl_eur < -900
