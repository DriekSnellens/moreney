"""Clip + paper short-weakest diversification lab."""

from __future__ import annotations

from bot.research.clip_exit_lab.engine import DRY
from bot.research.clip_short_div.engine import (
    btc_gate_days,
    run_clip_short_div,
    run_short_weakest,
)


def _bars(n: int, start: float, step: float, vol: float = 20_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000
    for i in range(n):
        hi = px + abs(step) * 1.2
        lo = max(0.01, px - abs(step) * 0.3)
        rows.append([t0 + i * 86_400_000, px, hi, lo, px, vol])
        px = max(0.01, px + step)
    return rows


def _dump_tape() -> dict[str, list[list[float]]]:
    """BTC rolls over; one alt dumps hard enough to clear the mom floor."""
    btc_up = _bars(40, 100.0, 0.2)
    btc_dn = _bars(50, btc_up[-1][4], -0.7)
    t1 = btc_up[-1][0] + 86_400_000
    for i, r in enumerate(btc_dn):
        r[0] = t1 + i * 86_400_000
    eth_up = _bars(40, 10.0, 0.02)
    eth_dn = _bars(50, eth_up[-1][4], -0.12)
    for i, r in enumerate(eth_dn):
        r[0] = t1 + i * 86_400_000
    sol_up = _bars(40, 20.0, 0.04)
    sol_dn = _bars(50, sol_up[-1][4], -0.04)
    for i, r in enumerate(sol_dn):
        r[0] = t1 + i * 86_400_000
    return {
        "BTC": btc_up + btc_dn,
        "ETH": eth_up + eth_dn,
        "SOL": sol_up + sol_dn,
    }


def test_short_stays_cash_on_up_tape() -> None:
    btc = _bars(80, 100.0, 0.8)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.2)}
    row = run_short_weakest(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        book_eur=12_000.0,
        model=DRY,
        keep_curve=True,
    )
    assert row["n_days"] > 0
    assert row["n_trades"] == 0
    assert abs(row["end_eur"] - 12_000.0) < 1.0
    assert row["end_hold"] == "cash"


def test_short_deploys_on_dump_tape() -> None:
    ohlc = _dump_tape()
    row = run_short_weakest(
        ohlc,
        start="2024-01-15",
        end="2024-03-28",
        book_eur=12_000.0,
        model=DRY,
        keep_curve=True,
    )
    assert row["n_trades"] >= 1
    assert row["n_days_deployed"] >= 1
    assert row["end_eur"] > 0


def test_hard_stop_covers_bounce() -> None:
    ohlc = _dump_tape()
    # After the dump, rocket the weak alt so a short hits +10% adverse.
    last = ohlc["ETH"][-1]
    # 50% bounce vs last close so the open short (rebalanced ~5d earlier) clears +10%.
    t_ms = last[0] + 86_400_000
    px = last[4] * 1.5
    rocket = [t_ms, px, px * 1.03, last[4], px, 20_000.0]
    ohlc["ETH"] = ohlc["ETH"] + [rocket]
    ohlc["BTC"].append(
        [
            ohlc["BTC"][-1][0] + 86_400_000,
            ohlc["BTC"][-1][4],
            ohlc["BTC"][-1][4] * 1.01,
            ohlc["BTC"][-1][4] * 0.99,
            ohlc["BTC"][-1][4] * 0.99,
            20_000.0,
        ]
    )
    ohlc["SOL"].append(
        [
            ohlc["SOL"][-1][0] + 86_400_000,
            ohlc["SOL"][-1][4],
            ohlc["SOL"][-1][4] * 1.01,
            ohlc["SOL"][-1][4] * 0.99,
            ohlc["SOL"][-1][4],
            20_000.0,
        ]
    )
    row = run_short_weakest(
        ohlc,
        start="2024-01-15",
        end="2024-04-02",
        book_eur=12_000.0,
        model=DRY,
        keep_curve=True,
    )
    assert row["n_hard_stop"] >= 1


def test_gates_see_overlap_regime() -> None:
    ohlc = _dump_tape()
    gates = btc_gate_days(ohlc, start="2024-01-15", end="2024-03-28")
    assert gates["n_days"] > 0
    assert gates["short_gate_on"] >= 1


def test_mix_books_total_24k() -> None:
    ohlc = _dump_tape()
    block = run_clip_short_div(
        ohlc,
        start="2024-01-20",
        end="2024-03-20",
        total_eur=24_000.0,
        model=DRY,
    )
    assert block["clip_24k"]["start_eur"] == 24_000.0
    assert block["short_24k"]["start_eur"] == 24_000.0
    assert block["mix_clip12_short12"]["start_eur"] == 24_000.0
    mix_end = block["mix_clip12_short12"]["end_eur"]
    parts = block["clip_12k"]["end_eur"] + block["short_12k"]["end_eur"]
    assert abs(mix_end - parts) < 1.0
    cds = block["mix_clip12_short6_donch6"]["end_eur"]
    cds_parts = (
        block["clip_12k"]["end_eur"]
        + block["short_6k"]["end_eur"]
        + block["donch_6k"]["end_eur"]
    )
    assert abs(cds - cds_parts) < 1.0
