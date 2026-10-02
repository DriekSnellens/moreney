"""Paper ignition sleeve — early-signal + trail (desk universe)."""

from __future__ import annotations

from datetime import UTC, datetime

from bot.live.momentum_ignition import (
    IgnitionConfig,
    IgnitionPosition,
    evaluate_ignition,
    score_ignition_day,
    trail_exit,
)


def _bars(
    n: int,
    start: float,
    *,
    step: float = 0.0,
    vol: float = 5_000.0,
    day_open_boost: float = 0.0,
    last_high_boost: float = 0.0,
) -> list[list[float]]:
    rows: list[list[float]] = []
    px = start
    day_ms = 86_400_000
    t0 = 1_700_000_000_000
    for i in range(n):
        o = px
        c = px + step
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        rows.append([t0 + i * day_ms, o, h, l, c, vol])
        px = c
    if day_open_boost and rows:
        # Make the last day a strong up-day from a lower open.
        last = rows[-1]
        open_px = float(last[4]) / (1.0 + day_open_boost)
        last[1] = open_px
        last[3] = min(open_px, float(last[3]))
    if last_high_boost and len(rows) > 2:
        # Ensure prior highs sit below the last close (breakout).
        close = float(rows[-1][4])
        for r in rows[:-1]:
            r[2] = min(float(r[2]), close * (1.0 - last_high_boost))
    return rows


def test_score_ignition_detects_early_signal() -> None:
    # Quiet base, then a liquid breakout day with +8% and 3× volume.
    base = _bars(40, 10.0, step=0.01, vol=2_000.0)
    base[-1][5] = 20_000.0  # volume spike
    # Force day return ≥ 6% and breakout.
    close = float(base[-1][4])
    base[-1][1] = close / 1.08
    for r in base[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    btc = _bars(40, 100.0, step=0.5, vol=50_000.0)
    cfg = IgnitionConfig(
        min_median_qvol_eur=1_000.0,
        min_day_qvol_eur=1_000.0,
        quiet_max=0.20,
    )
    scored = score_ignition_day(base, btc, cfg)
    assert scored is not None
    assert scored["early_signal"] is True
    assert scored["brk20"] is True
    assert scored["day_ret"] >= 0.06
    assert scored["vol_x"] >= 2.0


def test_evaluate_enters_top_signal_when_risk_on() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    eth[-1][5] = 30_000.0
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    quiet = _bars(60, 5.0, step=0.0, vol=1_000.0)
    cfg = IgnitionConfig(
        universe=("ETH", "ADA"),
        min_median_qvol_eur=500.0,
        min_day_qvol_eur=500.0,
        quiet_max=0.25,
        min_points=1,
        book_eur=2_000.0,
        require_btc_sma=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth, "ADA": quiet},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["ok"] is True
    assert out["risk_on"] is True
    assert out["entries"]
    assert out["entries"][0]["base"] == "ETH"
    assert out["entries"][0]["notional_eur"] == 2000.0


def test_btc_below_sma_blocks_entries() -> None:
    btc = _bars(60, 200.0, step=-2.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.5, vol=20_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.12
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    cfg = IgnitionConfig(
        universe=("ETH",),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.5,
        min_points=1,
        require_btc_sma=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_on"] is False
    assert out["risk_block"] == "btc_below_sma50"
    assert out["entries"] == []


def test_slots_full_blocks_second_entry() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.3, vol=20_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    cfg = IgnitionConfig(
        universe=("ETH",),
        max_positions=1,
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.5,
        min_points=1,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth},
        cfg,
        held=["ETH"],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["risk_block"] == "slots_full"
    assert out["entries"] == []


def test_trail_exit_fires_after_giveback() -> None:
    pos = IgnitionPosition(
        base="ETH",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=1,
        peak_px=120.0,
    )
    cfg = IgnitionConfig(trail_pct=0.15)
    # 15% off peak 120 → stop at 102.
    assert trail_exit(pos, 103.0, cfg) is None
    hit = trail_exit(pos, 102.0, cfg)
    assert hit is not None
    assert hit["reason"] == "ignition_trail"


def test_ignition_defaults_to_okx_venue() -> None:
    from bot.core.config import Settings
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner, config_from_settings
    from bot.live.momentum_runner import parse_venues

    venues = parse_venues(Settings().momentum_ignition_venues)
    assert venues == ("okx",)
    assert Settings().momentum_ignition_allow_live is False
    cfg = config_from_settings(Settings())
    runner = IgnitionPaperRunner(
        cfg,
        state_path="/tmp/ign-test-state.json",
        ledger_path="/tmp/ign-test-ledger.jsonl",
        venues=venues,
        allow_live=True,  # requested, but runner must stay paper
    )
    assert runner._primary_venue() == "okx"
    assert runner.paper_only is True
    assert runner.allow_live is False
    st = runner.status()
    assert st["target_venue"] == "okx"
    assert st["venues"] == ["okx"]
    assert st["allow_live"] is False


def test_no_per_coin_hardcodes_in_ignition_modules() -> None:
    from pathlib import Path

    roots = [
        Path("bot/live/momentum_ignition.py"),
        Path("bot/live/momentum_ignition_runner.py"),
    ]
    banned = ('base == "', "base=='", '== "UNI"', '== "SOL"', '== "ETH"')
    for path in roots:
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{path} hardcodes {needle}"
