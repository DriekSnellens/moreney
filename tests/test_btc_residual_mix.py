"""BTC + residual-weekly mix lab."""

from __future__ import annotations

from bot.research.btc_residual_mix.engine import _targets, pick_residual, run_btc_residual
from bot.research.clip_exit_lab.engine import DRY
from bot.research.clip_exit_lab.policies import ExitPolicy


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


def test_alt_stop_fires_on_dumped_sleeve() -> None:
    btc = _bars(70, 100.0, 0.4)
    eth_up = _bars(50, 10.0, 0.25)
    t1 = eth_up[-1][0] + 86_400_000
    eth_dn = _bars(20, eth_up[-1][4], -0.9)
    for i, r in enumerate(eth_dn):
        r[0] = t1 + i * 86_400_000
    ohlc = {"BTC": btc, "ETH": eth_up + eth_dn}
    row = run_btc_residual(
        ohlc,
        start="2024-02-01",
        end="2024-03-10",
        book_eur=20_000.0,
        btc_frac=0.5,
        flatten="none",
        model=DRY,
        policy=ExitPolicy(name="alt_stop_8", alt_stop_pct=0.08),
    )
    assert row["n_overlay_exits"] >= 1


def test_owner_pack_names_are_unique() -> None:
    from bot.research.btc_residual_mix.engine import owner_pack_specs

    names = [s["name"] for s in owner_pack_specs()]
    assert names
    assert len(names) == len(set(names))


def test_owner_grid_ranks_calmar_on_up_tape() -> None:
    from bot.research.btc_residual_mix.engine import run_owner_grid

    btc = _bars(80, 100.0, 0.5)
    ohlc = {"BTC": btc, "ETH": _bars(80, 10.0, 0.4)}
    grid = run_owner_grid(
        ohlc,
        start="2024-02-20",
        end="2024-03-20",
        book_eur=20_000.0,
        model=DRY,
        include_clip=False,
        include_donch=False,
    )
    assert grid["n_packs"] == len(grid["ranked"])
    assert grid["n_packs"] >= 10
    calmars = [float(r["calmar"]) for r in grid["ranked"]]
    assert calmars == sorted(calmars, reverse=True)
    assert grid["best"]
