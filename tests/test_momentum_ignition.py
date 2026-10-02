"""Paper ignition sleeve — early-signal + trail (desk universe)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

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
    assert cfg.decision_interval_sec == 900.0
    runner = IgnitionPaperRunner(
        cfg,
        state_path="/tmp/ign-test-state.json",
        ledger_path="/tmp/ign-test-ledger.jsonl",
        venues=venues,
        allow_live=True,  # requested, but runner must stay paper
    )
    assert runner._primary_venue() == "okx"
    # Without gateways, allow_live request stays paper.
    assert runner.paper_only is True
    assert runner.allow_live is False
    st = runner.status()
    assert st["target_venue"] == "okx"
    assert st["venues"] == ["okx"]
    assert st["allow_live"] is False
    assert st["config"]["decision_interval_sec"] == 900.0


def test_ignition_live_arms_when_gateway_present(tmp_path: Path) -> None:
    from bot.live.momentum_ignition import IgnitionConfig
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner

    class FakeGw:
        pass

    cfg = IgnitionConfig()
    runner = IgnitionPaperRunner(
        cfg,
        state_path=str(tmp_path / "ign.json"),
        ledger_path=str(tmp_path / "ign.jsonl"),
        venues=("okx",),
        allow_live=True,
        gateways={"okx": FakeGw()},
    )
    assert runner.allow_live is True
    assert runner.dry_run is False
    assert runner.paper_only is False
    assert runner._desk() == "ignition"
    assert runner.status()["mode"] == "ignition_live"


def test_ignition_decision_slot_fires_on_interval() -> None:
    from bot.live.momentum_ignition import IgnitionConfig
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner

    cfg = IgnitionConfig(decision_interval_sec=900.0, decision_hours_utc=(0,))
    runner = IgnitionPaperRunner(
        cfg,
        state_path="/tmp/ign-slot-state.json",
        ledger_path="/tmp/ign-slot-ledger.jsonl",
        venues=("okx",),
    )
    t0 = datetime(2026, 10, 2, 9, 10, tzinfo=UTC)
    slot = runner._decision_slot_due(t0, last_slot=None)
    assert slot is not None
    assert runner._decision_slot_due(t0, last_slot=slot) is None
    later = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    nxt = runner._decision_slot_due(later, last_slot=slot)
    assert nxt is not None
    assert nxt > slot


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


@pytest.mark.asyncio
async def test_ignition_venue_truth_flags_inventory_not_powder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.core.config import Settings
    from bot.core.models import Balance, PortfolioSnapshot
    from bot.funding.multi_venue import portfolio_snapshot_to_venue
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner, config_from_settings

    cfg = config_from_settings(Settings())
    runner = IgnitionPaperRunner(
        cfg,
        state_path=str(tmp_path / "ign.json"),
        ledger_path=str(tmp_path / "ign.jsonl"),
        venues=("okx",),
    )

    async def fake_prices() -> dict[str, Decimal]:
        return {"ALT": Decimal("1.80")}

    async def fake_balances(settings, venues, **kwargs):  # noqa: ANN001
        prices = kwargs.get("prices_eur") or {}
        return [
            portfolio_snapshot_to_venue(
                venues[0],
                PortfolioSnapshot(
                    balances=[
                        Balance(asset="EUR", free=Decimal("0.90"), locked=Decimal("0")),
                        Balance(asset="ALT", free=Decimal("1000"), locked=Decimal("0")),
                    ],
                    equity_usd=Decimal("1"),
                ),
                prices_eur=prices,
            )
        ]

    monkeypatch.setattr("bot.funding.multi_venue.fetch_public_eur_prices", fake_prices)
    monkeypatch.setattr(
        "bot.funding.multi_venue.fetch_live_venue_balances", fake_balances
    )
    truth = await runner._refresh_venue_truth(force=True)
    assert truth["online"] is True
    assert truth["free_quote_eur"] == 0.9
    assert truth["inventory_mtm_eur"] == 1800.0
    assert truth["inventory_advice"] == "sell_inventory_for_powder"
    st = runner.status()
    assert st["venue_cash_eur"] == 0.9
    assert st["venue_inventory_eur"] == 1800.0
    assert st["deployable_live_eur"] == 0.9
    assert "verkopen" in (st.get("inventory_advice_nl") or "").lower()
    assert "paper" in (st.get("live_caption") or "").lower()
