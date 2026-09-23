"""Tests for paper short-weakest sleeve (strategy + manager)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.core.config import Settings
from bot.live.momentum_desk import AlphaIView
from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    evaluate_short_exit,
    rank_weakest,
    select_shorts,
)
from bot.live.momentum_short_weakest_runner import (
    config_from_settings,
    get_short_weakest_desk_manager,
    reset_short_weakest_desk_manager,
)


@pytest.fixture(autouse=True)
def _reset_mgr():
    reset_short_weakest_desk_manager()
    yield
    reset_short_weakest_desk_manager()


def test_rank_weakest_prefers_most_negative_and_respects_alphai_blocks():
    closes = {
        "AAA": [100.0] + [100.0] * 14 + [70.0],
        "BBB": [100.0] + [100.0] * 14 + [85.0],
        "CCC": [100.0] + [100.0] * 14 + [99.0],
    }
    cfg = ShortWeakestConfig(
        universe=("AAA", "BBB", "CCC"),
        lookback_days=15,
        skip_days=0,
        bounce_block_pct=0.0,
        mom_floor=-0.05,
        top_n=3,
    )
    alphai = AlphaIView(picks=frozenset({"BBB"}), avoid=frozenset({"CCC"}))
    cands, rejected = rank_weakest(closes, cfg, alphai=alphai)
    bases = [c["base"] for c in cands]
    assert bases == ["AAA"]
    reasons = {r["base"]: r["reason"] for r in rejected}
    assert reasons["BBB"] == "alphai_long_pick"
    assert reasons["CCC"] in {"alphai_avoid", "mom_above_floor"}


def test_rank_weakest_skip_days_and_bounce_block():
    # skip=2 ends at the first 70; last day +14% bounce → blocked.
    closes = {"AAA": [100.0] * 15 + [70.0, 70.0, 80.0]}
    cfg = ShortWeakestConfig(
        universe=("AAA",),
        lookback_days=15,
        skip_days=2,
        bounce_block_pct=0.04,
        mom_floor=-0.05,
        top_n=1,
        alphai_enabled=False,
    )
    cands, rejected = rank_weakest(closes, cfg)
    assert cands == []
    assert rejected[0]["reason"] == "bounce_block"

    closes2 = {"AAA": [100.0] * 15 + [70.0, 70.0, 70.0]}
    cands2, _ = rank_weakest(closes2, cfg)
    assert [c["base"] for c in cands2] == ["AAA"]


def test_rank_weakest_excess_mode_vs_btc():
    btc = [100.0] * 16
    closes = {
        "BTC": btc,
        "AAA": [100.0] + [100.0] * 6 + [90.0],
        "BBB": [100.0] + [100.0] * 6 + [99.0],
        "CCC": [100.0] + [100.0] * 6 + [102.0],
    }
    cfg = ShortWeakestConfig(
        universe=("AAA", "BBB", "CCC"),
        idle_lookback_days=7,
        idle_excess_floor=-0.03,
        lookback_days=15,
        mom_floor=-0.08,
        skip_days=0,
        bounce_block_pct=0.0,
    )
    cands, rejected = rank_weakest(closes, cfg, mode="excess", btc_closes=btc)
    assert [c["base"] for c in cands] == ["AAA"]
    assert cands[0]["score"] < -0.03
    assert any(r["base"] == "BBB" for r in rejected)


def test_select_shorts_caps_max_weight():
    cfg = ShortWeakestConfig(top_n=3, max_weight=0.15, deploy_frac=1.0, min_notional_eur=50)
    cands = [
        {"base": "AAA", "mom": -0.2, "size_mult": 1.0, "reasons": []},
        {"base": "BBB", "mom": -0.15, "size_mult": 1.0, "reasons": []},
        {"base": "CCC", "mom": -0.12, "size_mult": 1.0, "reasons": []},
    ]
    planned = select_shorts(cands, cfg, cash_eur=5_000.0, held=set())
    assert len(planned) == 3
    for row in planned:
        assert row["notional_eur"] <= 5_000.0 * 0.15 + 1e-6


def test_evaluate_short_exit_trail_and_vol_spike():
    cfg = ShortWeakestConfig(
        trail_pct=0.18, hard_stop_pct=0.12, vol_spike_exit=True, vol_spike_mult=3.0
    )
    pos = ShortPosition(
        base="AAA",
        entry_price=100.0,
        notional_eur=500.0,
        opened_ms=0,
        peak_return=0.25,
        atr14=0.02,
    )
    decision = evaluate_short_exit(pos, mark=93.0, day_ret=0.0, cfg=cfg)
    assert decision is not None
    assert decision["reason"] == "trail"

    pos2 = ShortPosition(
        base="BBB", entry_price=100.0, notional_eur=500.0, opened_ms=0, atr14=0.02
    )
    spike = evaluate_short_exit(pos2, mark=100.0, day_ret=0.07, cfg=cfg)
    assert spike is not None
    assert spike["reason"] == "vol_spike"


def test_evaluate_short_exit_disabled_when_zero():
    cfg = ShortWeakestConfig(trail_pct=0.0, hard_stop_pct=0.0, vol_spike_exit=False)
    pos = ShortPosition(
        base="AAA",
        entry_price=100.0,
        notional_eur=500.0,
        opened_ms=0,
        peak_return=0.5,
        atr14=0.02,
    )
    assert evaluate_short_exit(pos, mark=150.0, day_ret=0.2, cfg=cfg) is None


def test_config_from_settings_reads_book():
    settings = Settings(
        momentum_short_weakest_book_eur=2500.0,
        momentum_short_weakest_top_n=2,
        momentum_short_weakest_trail_pct=0.0,
        momentum_short_weakest_max_weight=0.5,
    )
    cfg = config_from_settings(settings)
    assert cfg.book_eur == 2500.0
    assert cfg.top_n == 2
    assert cfg.trail_pct == 0.0
    assert cfg.max_weight == 0.5
    assert cfg.idle_fill_enabled is False
    assert cfg.rebalance_days == 7
    assert cfg.skip_days == 1


def test_bear_harvest_defaults():
    cfg = ShortWeakestConfig()
    assert cfg.top_n == 1
    assert cfg.rebalance_days == 7
    assert cfg.skip_days == 1
    assert cfg.bounce_block_pct == 0.02
    assert cfg.trail_pct == 0.0
    assert cfg.hard_stop_pct == 0.10
    assert cfg.vol_spike_exit is False
    assert cfg.max_weight == 0.5
    assert cfg.idle_fill_enabled is False
    assert cfg.only_when_core_idle is False
    assert cfg.cover_when_core_active is False
    assert cfg.sma_days == 20
    assert cfg.cover_on_bull is True


@pytest.mark.asyncio
async def test_manager_requires_enabled(tmp_path: Path):
    settings = Settings(
        momentum_short_weakest_enabled=False,
        momentum_short_weakest_state_path=str(tmp_path / "sw_state.json"),
        momentum_short_weakest_ledger_path=str(tmp_path / "sw_ledger.jsonl"),
    )
    mgr = get_short_weakest_desk_manager()
    res = await mgr.start(settings=settings)
    assert res["started"] is False


@pytest.mark.asyncio
async def test_manager_paper_start_stop(tmp_path: Path):
    settings = Settings(
        momentum_short_weakest_enabled=True,
        momentum_short_weakest_book_eur=20_000.0,
        momentum_short_weakest_state_path=str(tmp_path / "sw_state.json"),
        momentum_short_weakest_ledger_path=str(tmp_path / "sw_ledger.jsonl"),
        alphai_daily_recommendations_path=str(tmp_path / "alphai.json"),
    )
    (tmp_path / "alphai.json").write_text("{}", encoding="utf-8")
    mgr = get_short_weakest_desk_manager()
    res = await mgr.start(settings=settings)
    assert res.get("started") is True
    st = mgr.status()
    assert st["paper_only"] is True
    assert st["mode"] == "short_weakest_paper"
    assert st["book_eur"] == 20_000.0
    assert st["pack"]["name"] == "loop_sma20_short"
    assert st["pack"]["top_n"] == 1
    assert "role" in st
    assert "bear" in st
    stop = await mgr.stop()
    assert stop.get("stopped") is True


@pytest.mark.asyncio
async def test_paper_book_ignores_mix_zero_allocation(tmp_path: Path, monkeypatch):
    """Mix risk_on zeros the sleeve weight; paper book must keep its own €."""
    from bot.live import momentum_short_weakest_runner as mod
    from bot.live.momentum_short_weakest_runner import ShortWeakestPaperRunner

    cfg = ShortWeakestConfig(book_eur=14_000.0, universe=("AAA",), alphai_enabled=False)

    monkeypatch.setattr(
        mod, "fetch_daily_closes", lambda base, days=260: [(i, 100.0 + i) for i in range(80)]
    )
    monkeypatch.setattr(mod, "live_snapshot", lambda **_kw: {"label": "risk_on", "why": "uptrend"})
    monkeypatch.setattr(mod, "target_book", lambda _name, _mix: 0.0)
    monkeypatch.setattr(mod, "probe_core_desk", lambda: {"ok": True, "idle": True, "n_positions": 0})
    monkeypatch.setattr(mod, "load_alphai_view", lambda _path: (AlphaIView(), {}))
    monkeypatch.setattr(mod, "alphai_is_stale", lambda *_a, **_k: False)

    async def _no_marks(_self):
        return None

    monkeypatch.setattr(ShortWeakestPaperRunner, "_refresh_marks", _no_marks)

    runner = ShortWeakestPaperRunner(
        cfg,
        state_path=str(tmp_path / "s.json"),
        ledger_path=str(tmp_path / "l.jsonl"),
        alphai_path=str(tmp_path / "a.json"),
    )
    out = await runner.decide(execute=False)
    assert out.get("risk_block") == "btc_not_bear"
    assert out["allocator"]["target_eur"] == 14_000.0
    assert out["allocator"]["mix_target_eur"] == 0.0
    assert out["allocator"]["paper_independent"] is True
    st = runner.status()
    assert st["book_eur"] == 14_000.0
    assert st["paper_only"] is True
    assert st["risk"]["block_reason"] != "allocator_zero_book"
    assert "allocator_zero_book" not in str(out)
