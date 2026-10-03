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
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
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
    assert scored["entry_path"] == "classic"
    assert scored["trail_pct"] == cfg.trail_pct


def _coil_bars() -> list[list[float]]:
    """Wide history, tight recent range, then a modest 5d breakout day."""
    rows = _bars(60, 10.0, step=0.0, vol=3_000.0)
    # Early window: wide high/low span so 20d compress ratio can fire.
    for r in rows[:25]:
        mid = float(r[4])
        r[2] = mid * 1.12
        r[3] = mid * 0.88
    # Recent 5 bars before last: tight coil.
    for r in rows[-6:-1]:
        mid = float(r[4])
        r[2] = mid * 1.005
        r[3] = mid * 0.995
        r[1] = mid
    last = rows[-1]
    close = float(last[4]) * 1.03
    open_px = close / 1.03
    last[1] = open_px
    last[4] = close
    last[2] = close * 1.002
    last[3] = open_px * 0.998
    last[5] = 5_000.0  # ~1.67× median 3k
    # Prior highs below last close for brk5; keep 20d highs above so classic fails.
    for r in rows[-6:-1]:
        r[2] = min(float(r[2]), close * 0.995)
    for r in rows[:-6]:
        r[2] = max(float(r[2]), close * 1.05)
    return rows


def test_coil_signal_enters_when_classic_misses() -> None:
    from bot.live.momentum_ignition import effective_trail_pct

    coil = _coil_bars()
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        universe=("ALT",),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.12,
        day_ret_min=0.06,
        vol_mult_min=2.0,
        coil_entry_enabled=True,
        coil_trail_pct=0.25,
        time_max_days=0.0,
        require_btc_sma=True,
        min_points=3,
    )
    scored = score_ignition_day(coil, btc, cfg)
    assert scored is not None
    assert scored["early_signal"] is False
    assert scored["coil_signal"] is True
    assert scored["entry_path"] == "coil"
    assert scored["trail_pct"] == 0.25
    out = evaluate_ignition(
        {"BTC": btc, "ALT": coil},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["entries"]
    assert out["entries"][0]["base"] == "ALT"
    assert out["entries"][0]["entry_path"] == "coil"
    assert out["entries"][0]["trail_pct"] == 0.25
    pos = IgnitionPosition(
        base="ALT",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=1,
        peak_px=110.0,
        trail_pct=0.25,
        entry_path="coil",
    )
    assert effective_trail_pct(pos, cfg) == 0.25
    # Wider coil trail: 25% off 110 → 82.5.
    assert trail_exit(pos, 83.0, cfg) is None
    hit = trail_exit(pos, 82.5, cfg)
    assert hit is not None
    assert hit["entry_path"] == "coil"


def test_classic_preferred_over_coil_when_both_fire() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    classic = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(classic[-1][4])
    classic[-1][1] = close / 1.10
    classic[-1][5] = 30_000.0
    for r in classic[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    coil = _coil_bars()
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        universe=("ETH", "ALT"),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.25,
        min_points=1,
        coil_entry_enabled=True,
        require_btc_sma=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": classic, "ALT": coil},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["entries"]
    assert out["entries"][0]["base"] == "ETH"
    assert out["entries"][0]["entry_path"] == "classic"
    assert out["ranked"][0]["entry_path"] == "classic"


def test_evaluate_enters_top_signal_when_risk_on() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    eth[-1][5] = 30_000.0
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    quiet = _bars(60, 5.0, step=0.0, vol=1_000.0)
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        universe=("ETH", "ADA"),
        min_median_qvol_eur=500.0,
        min_day_qvol_eur=500.0,
        quiet_max=0.25,
        min_points=1,
        book_eur=2_000.0,
        max_positions=1,
        compound_sizing=False,
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


def test_compound_sizing_uses_cash_above_book() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    eth[-1][5] = 30_000.0
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        universe=("ETH",),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.25,
        min_points=1,
        book_eur=2_000.0,
        max_positions=1,
        compound_sizing=True,
        require_btc_sma=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth},
        cfg,
        held=[],
        cash_eur=8_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["entries"]
    assert out["entries"][0]["notional_eur"] == 8000.0


def test_two_slots_split_powder_across_entries() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    eth[-1][5] = 30_000.0
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    coil = _coil_bars()
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        universe=("ETH", "ALT"),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.25,
        min_points=1,
        book_eur=10_000.0,
        max_positions=2,
        compound_sizing=True,
        coil_entry_enabled=True,
        require_btc_sma=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth, "ALT": coil},
        cfg,
        held=[],
        cash_eur=10_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert len(out["entries"]) == 2
    assert out["entries"][0]["base"] == "ETH"
    assert out["entries"][0]["entry_path"] == "classic"
    assert out["entries"][1]["entry_path"] == "coil"
    assert out["entries"][0]["notional_eur"] == 5000.0
    assert out["entries"][1]["notional_eur"] == 5000.0


def test_btc_below_sma_blocks_entries() -> None:
    btc = _bars(60, 200.0, step=-2.0, vol=80_000.0)
    eth = _bars(60, 10.0, step=0.5, vol=20_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.12
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        coil_entry_enabled=True,
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
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        coil_entry_enabled=True,
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


def test_top_day_picks_strongest_liquid_day_ret() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    weak = _bars(60, 10.0, step=0.02, vol=3_000.0)
    strong = _bars(60, 5.0, step=0.01, vol=3_000.0)
    # Weak: +5% day; strong: +12% day — both liquid, no sniper gates.
    for rows, boost in ((weak, 0.05), (strong, 0.12)):
        close = float(rows[-1][4])
        rows[-1][1] = close / (1.0 + boost)
        rows[-1][5] = 30_000.0
    cfg = IgnitionConfig(
        entry_mode="top_day",
        universe=("WEAK", "STRONG"),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        trail_pct=0.08,
        time_max_days=2.0,
        require_btc_sma=True,
        min_points=1,
        requires_alphai_pick=True,
    )
    out = evaluate_ignition(
        {"BTC": btc, "WEAK": weak, "STRONG": strong},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
        alphai_picks=("WEAK", "STRONG"),
    )
    assert out["entry_mode"] == "top_day"
    assert out["entries"]
    assert out["entries"][0]["base"] == "STRONG"
    assert out["entries"][0]["entry_path"] == "top_day"
    assert out["entries"][0]["trail_pct"] == 0.08
    assert "alphai_pick" in out["entries"][0]["reasons"]
    assert out["want"] == "STRONG"


def test_top_day_alphai_gate_skips_raw_day_ret_leader() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    hot = _bars(60, 10.0, step=0.02, vol=3_000.0)
    pick = _bars(60, 5.0, step=0.01, vol=3_000.0)
    for rows, boost in ((hot, 0.20), (pick, 0.08)):
        close = float(rows[-1][4])
        rows[-1][1] = close / (1.0 + boost)
        rows[-1][5] = 30_000.0
    cfg = IgnitionConfig(
        entry_mode="top_day",
        universe=("HOT", "PICK"),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        requires_alphai_pick=True,
        require_btc_sma=True,
        min_points=1,
    )
    out = evaluate_ignition(
        {"BTC": btc, "HOT": hot, "PICK": pick},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
        alphai_picks=("PICK",),
    )
    assert out["ranked"][0]["base"] == "HOT"
    assert out["want"] == "PICK"
    assert out["entries"][0]["base"] == "PICK"
    assert any(r.get("reason") == "alphai_pick_required" for r in out["rejected"])


def test_time_exit_fires_after_max_days() -> None:
    opened = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000)
    pos = IgnitionPosition(
        base="ALT",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=opened,
        peak_px=110.0,
        entry_path="top_day",
        trail_pct=0.08,
    )
    cfg = IgnitionConfig(requires_alphai_pick=False, trail_pct=0.08, time_max_days=2.0, trail_ratchet_arm_pct=0.0)
    # Still inside window → no time exit (mark above trail).
    early = trail_exit(
        pos, 108.0, cfg, now=datetime(2026, 6, 2, 12, tzinfo=UTC)
    )
    assert early is None
    hit = trail_exit(pos, 108.0, cfg, now=datetime(2026, 6, 3, 1, tzinfo=UTC))
    assert hit is not None
    assert hit["reason"] == "ignition_time"
    assert hit["entry_path"] == "top_day"


def test_trail_exit_fires_after_giveback() -> None:
    from bot.live.momentum_ignition import effective_trail_pct

    pos = IgnitionPosition(
        base="ETH",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=1,
        peak_px=120.0,
    )
    cfg = IgnitionConfig(requires_alphai_pick=False, trail_pct=0.12, trail_ratchet_arm_pct=0.0, time_max_days=0.0)
    # 12% off peak 120 → stop at 105.6.
    assert trail_exit(pos, 106.0, cfg) is None
    hit = trail_exit(pos, 105.6, cfg)
    assert hit is not None
    assert hit["reason"] == "ignition_trail"


def test_trail_ratchet_tightens_after_big_runner() -> None:
    from bot.live.momentum_ignition import effective_trail_pct

    pos = IgnitionPosition(
        base="ETH",
        entry_price=100.0,
        notional_eur=1000.0,
        qty=10.0,
        opened_ms=1,
        peak_px=140.0,  # +40% ≥ 30% arm
    )
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        trail_pct=0.12,
        trail_ratchet_arm_pct=0.30,
        trail_ratchet_pct=0.10,
        time_max_days=0.0,
    )
    assert effective_trail_pct(pos, cfg) == 0.10
    # 10% off 140 → 126.
    assert trail_exit(pos, 127.0, cfg) is None
    hit = trail_exit(pos, 126.0, cfg)
    assert hit is not None
    assert hit["reason"] == "ignition_trail_ratchet"
    assert hit["trail_pct"] == 0.10


def test_near_miss_surfaces_single_gate_failures() -> None:
    btc = _bars(60, 100.0, step=1.0, vol=80_000.0)
    # Strong day + breakout but volume not 2×.
    eth = _bars(60, 10.0, step=0.02, vol=3_000.0)
    close = float(eth[-1][4])
    eth[-1][1] = close / 1.10
    eth[-1][5] = 4_000.0  # only ~1.3× median
    for r in eth[:-1]:
        r[2] = min(float(r[2]), close * 0.99)
    cfg = IgnitionConfig(requires_alphai_pick=False, 
        entry_mode="sniper",
        coil_entry_enabled=True,
        universe=("ETH",),
        min_median_qvol_eur=100.0,
        min_day_qvol_eur=100.0,
        quiet_max=0.5,
        min_points=1,
        vol_mult_min=2.0,
    )
    out = evaluate_ignition(
        {"BTC": btc, "ETH": eth},
        cfg,
        held=[],
        cash_eur=2_000.0,
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert out["entries"] == []
    assert out["near_miss"]
    assert out["near_miss"][0]["base"] == "ETH"
    assert out["near_miss"][0]["missing"] == "vol"
    assert "dichtbij ETH" in out["caption"]


def test_ignition_defaults_to_okx_venue() -> None:
    from bot.core.config import Settings
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner, config_from_settings
    from bot.live.momentum_runner import parse_venues

    venues = parse_venues(Settings().momentum_ignition_venues)
    assert venues == ("okx",)
    assert Settings().momentum_ignition_allow_live is False
    cfg = config_from_settings(Settings())
    assert cfg.decision_interval_sec == 0.0
    assert cfg.decision_hours_utc == (7, 13, 16)
    assert cfg.entry_mode == "top_day"
    assert cfg.trail_pct == 0.08
    assert cfg.time_max_days == 2.0
    assert cfg.trail_ratchet_arm_pct == 0.0
    assert cfg.coil_entry_enabled is False
    assert cfg.requires_alphai_pick is True
    assert cfg.block_alphai_avoid is True
    assert cfg.book_eur == 2_000.0
    assert cfg.max_positions == 1
    assert cfg.compound_sizing is True
    assert cfg.universe_mode == "ex_desk"
    assert cfg.quiet_max == 0.15
    assert "ETH" not in cfg.universe
    assert "FET" not in cfg.universe
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
    assert st["config"]["decision_interval_sec"] == 0.0
    assert st["config"]["decision_hours_utc"] == [7, 13, 16]
    assert st["config"]["universe_mode"] == "ex_desk"
    assert st["config"]["entry_mode"] == "top_day"
    assert st["config"]["requires_alphai_pick"] is True


def test_sniper_universe_excludes_rs_desk() -> None:
    from bot.live.momentum_ignition import (
        RS_DESK_BASES,
        build_sniper_universe,
        resolve_universe,
    )

    vols = {b: 1_000_000.0 for b in RS_DESK_BASES}
    vols.update(
        {
            "GRASS": 5_000_000.0,
            "ONDO": 4_000_000.0,
            "SEI": 3_000_000.0,
            "PEPE": 2_000_000.0,
        }
    )
    sniper = build_sniper_universe(vols, top_n=10, exclude=RS_DESK_BASES)
    assert sniper[0] == "GRASS"
    assert "ETH" not in sniper
    assert "SOL" not in sniper
    assert "FET" not in sniper
    cfg = IgnitionConfig(requires_alphai_pick=False, universe_mode="ex_desk", liquid_top_n=3)
    resolved = resolve_universe(cfg, volume_by_base=vols)
    assert resolved == ("GRASS", "ONDO", "SEI")
    desk = resolve_universe(IgnitionConfig(requires_alphai_pick=False, universe_mode="desk"), volume_by_base=vols)
    assert desk[0] == "ETH"
    assert "GRASS" not in desk


def test_ignition_live_arms_when_gateway_present(tmp_path: Path) -> None:
    from bot.live.momentum_ignition import IgnitionConfig
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner

    class FakeGw:
        pass

    cfg = IgnitionConfig(requires_alphai_pick=False, )
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

    # Short interval inside a buy hour: still hard-gated to decision_hours_utc.
    cfg = IgnitionConfig(
        requires_alphai_pick=False,
        decision_interval_sec=60.0,
        decision_hours_utc=(9,),
    )
    runner = IgnitionPaperRunner(
        cfg,
        state_path="/tmp/ign-slot-state.json",
        ledger_path="/tmp/ign-slot-ledger.jsonl",
        venues=("okx",),
    )
    t0 = datetime(2026, 10, 2, 9, 1, tzinfo=UTC)
    slot = runner._decision_slot_due(t0, last_slot=None)
    assert slot is not None
    assert runner._decision_slot_due(t0, last_slot=slot) is None
    later = datetime(2026, 10, 2, 9, 3, tzinfo=UTC)
    nxt = runner._decision_slot_due(later, last_slot=slot)
    assert nxt is not None
    assert nxt > slot
    # Outside the buy-hour grace, interval mode must not fire.
    assert runner._decision_slot_due(
        datetime(2026, 10, 2, 9, 10, tzinfo=UTC), last_slot=None
    ) is None
    assert runner._decision_slot_due(
        datetime(2026, 10, 2, 10, 0, tzinfo=UTC), last_slot=None
    ) is None


def test_ignition_buy_window_defaults_and_gate() -> None:
    from bot.core.config import Settings
    from bot.live.momentum_ignition import IgnitionConfig
    from bot.live.momentum_ignition_runner import IgnitionPaperRunner, config_from_settings

    cfg = config_from_settings(Settings())
    assert cfg.decision_hours_utc == (7, 13, 16)
    assert cfg.decision_interval_sec == 0.0
    runner = IgnitionPaperRunner(
        IgnitionConfig(requires_alphai_pick=False),
        state_path="/tmp/ign-buy-window.json",
        ledger_path="/tmp/ign-buy-window.jsonl",
        venues=("okx",),
    )
    assert runner._in_buy_window(datetime(2026, 10, 2, 7, 3, tzinfo=UTC))
    assert runner._in_buy_window(datetime(2026, 10, 2, 13, 0, tzinfo=UTC))
    assert runner._in_buy_window(datetime(2026, 10, 2, 16, 7, tzinfo=UTC))
    assert not runner._in_buy_window(datetime(2026, 10, 2, 7, 8, tzinfo=UTC))
    assert not runner._in_buy_window(datetime(2026, 10, 2, 12, 0, tzinfo=UTC))
    assert runner._decision_slot_due(
        datetime(2026, 10, 2, 7, 2, tzinfo=UTC), last_slot=None
    ) is not None
    assert (
        runner._decision_slot_due(
            datetime(2026, 10, 2, 8, 0, tzinfo=UTC), last_slot=None
        )
        is None
    )


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
