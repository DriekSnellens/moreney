"""BTC + residual-weekly mix lab."""

from __future__ import annotations

from bot.research.btc_residual_mix.engine import _targets, pick_residual, run_btc_residual
from bot.research.clip_exit_lab.engine import DRY


def _bars(n: int, start: float, step: float, vol: float = 200_000.0) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    t0 = 1_704_067_200_000
    for i in range(n):
        hi = px + abs(step) * 1.2
        lo = max(0.01, px - abs(step) * 0.3)
        rows.append([t0 + i * 86_400_000, px, hi, lo, px, vol])
        px = max(0.01, px + step)
    return rows


def test_targets_regime_goes_full_residual_when_btc_down() -> None:
    btc, alt = _targets(winner="ETH", risk_on=False, btc_frac=0.75, flatten="regime")
    assert btc == ""
    assert alt == "ETH"
    btc, alt = _targets(winner="ETH", risk_on=True, btc_frac=0.75, flatten="regime")
    assert btc == "BTC"
    assert alt == "ETH"


def test_targets_hold_keeps_btc_when_sma_down() -> None:
    btc, alt = _targets(winner="ETH", risk_on=False, btc_frac=0.5, flatten="none")
    assert btc == "BTC"
    assert alt == "ETH"


def test_combo_holds_btc_and_alt_on_up_tape() -> None:
    btc = _bars(80, 100.0, 0.5)
    eth = _bars(80, 10.0, 0.4)
    ohlc = {"BTC": btc, "ETH": eth}
    start = pick_residual(ohlc, "2024-02-20")
    assert start["want"] in {"ETH", "BTC"}
    row = run_btc_residual(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        book_eur=20_000.0,
        btc_frac=0.75,
        flatten="none",
        model=DRY,
        keep_weeks=True,
    )
    assert row["n_days"] > 0
    assert row["end_eur"] > 0
    assert "weeks" in row


def test_residual_100_starts_at_20k() -> None:
    btc = _bars(80, 100.0, 0.4)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.05)}
    row = run_btc_residual(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        book_eur=20_000.0,
        btc_frac=0.0,
        flatten="none",
        model=DRY,
    )
    assert row["start_eur"] == 20_000.0
    assert row["n_days"] > 0
