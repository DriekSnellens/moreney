"""12k clip + 12k Donchian mix lab."""

from __future__ import annotations

from bot.live.momentum_donchian import DonchianConfig
from bot.research.clip_donch_mix.engine import (
    combine_books,
    run_clip_donch_mix,
    run_donchian_sleeve,
)
from bot.research.clip_exit_lab.engine import DRY


def _bars(n: int, start: float, step: float, vol: float = 20_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000
    for i in range(n):
        hi = px + abs(step) * 1.2
        lo = px - abs(step) * 0.3
        rows.append([t0 + i * 86_400_000, px, hi, lo, px, vol])
        px += step
    return rows


def test_combined_equity_is_sum_of_books() -> None:
    a = {
        "strategy": "a",
        "model": "dry",
        "pnl_eur": 1.0,
        "max_dd_pct": 0.1,
        "calmar": 1.0,
        "n_trades": 1,
        "end_hold": "cash",
        "year_pnl": {},
        "curve": [["2024-03-01", 100.0], ["2024-03-02", 110.0]],
    }
    b = {
        "strategy": "b",
        "model": "dry",
        "pnl_eur": 2.0,
        "max_dd_pct": 0.1,
        "calmar": 1.0,
        "n_trades": 1,
        "end_hold": "cash",
        "year_pnl": {},
        "curve": [["2024-03-01", 50.0], ["2024-03-02", 40.0]],
    }
    mix = combine_books([a, b], start_eur=150.0, name="mix")
    assert mix["end_eur"] == 150.0
    assert mix["curve"][0][1] == 150.0
    assert mix["curve"][1][1] == 150.0


def test_donchian_sleeve_runs_on_up_tape() -> None:
    btc = _bars(80, 100.0, 0.8)
    eth = _bars(80, 10.0, 0.2)
    ohlc = {"BTC": btc, "ETH": eth}
    cfg = DonchianConfig(name="donch10", title="t", channel=10, exit_n=5, friday_flatten=False)
    row = run_donchian_sleeve(
        ohlc,
        cfg,
        start="2024-02-20",
        end="2024-03-20",
        book_eur=6_000.0,
        model=DRY,
        keep_curve=True,
    )
    assert row["n_days"] > 0
    assert row["end_eur"] > 0
    assert "curve" in row


def test_mix_12k_books_total_24k() -> None:
    btc = _bars(80, 100.0, 0.5)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.05)}
    block = run_clip_donch_mix(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        clip_eur=12_000.0,
        donch_eur=12_000.0,
        model=DRY,
    )
    assert block["clip_12k"]["start_eur"] == 12_000.0
    assert block["donch_pair_12k"]["start_eur"] == 12_000.0
    assert block["mix_12_12"]["start_eur"] == 24_000.0
    assert block["clip_24k"]["start_eur"] == 24_000.0
    mix_end = block["mix_12_12"]["end_eur"]
    parts = block["clip_12k"]["end_eur"] + block["donch_pair_12k"]["end_eur"]
    assert abs(mix_end - parts) < 1.0
