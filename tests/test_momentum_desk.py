"""Daily Momentum Desk — decision core, backtester and live order path."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bot.live.momentum_desk import (
    summarize_regime_pnl,
    BAR_MS,
    BARS_PER_DAY,
    AlphaIView,
    DeskConfig,
    ExitDecision,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    evaluate_exit,
    is_decision_time,
    rank_candidates,
    alphai_entry_clip_mult,
    select_entries,
    universe_stats,
)
from bot.live.momentum_runner import (
    Fill,
    MomentumDeskRunner,
    OrderState,
    RunnerOptions,
    engine_settings_for_desk,
)
from bot.research.momentum_backtest.engine import simulate

DAY_MS = 86_400_000
T0 = 1_780_000_000_000 // DAY_MS * DAY_MS  # midnight UTC
# Most tests use a 2-3 coin universe where breadth is trivially 0 or 1; keep
# tape-strength sizing out of the way unless a test targets it.
FLAT_SIZING = {"strong_clip_mult": 1.0, "weak_clip_mult": 1.0}


def _series(start_ms: int, bars: int, start_px: float, drift_per_bar: float, *, vol: float = 0.0):
    rows = []
    px = start_px
    for i in range(bars):
        o = px
        px = px * (1 + drift_per_bar)
        h = max(o, px) * (1 + vol)
        lo = min(o, px) * (1 - vol)
        rows.append([start_ms + i * BAR_MS, o, h, lo, px, 1000.0])
    return rows


# ------------------------------------------------------------------ core


def test_bar_stats_uses_closed_bars_only_and_time_window():
    start = T0 - 2 * DAY_MS
    rows = _series(start, 2 * BARS_PER_DAY + 1, 100.0, 0.0005)
    stats = bar_stats("X", rows, T0)
    assert stats is not None
    # Last closed bar is the one ending exactly at T0; the in-progress bar is ignored.
    last_closed = next(r for r in rows if r[0] + BAR_MS == T0)
    assert stats.price == pytest.approx(last_closed[4])
    ref = [r for r in rows if r[0] < T0 - BARS_PER_DAY * BAR_MS][-1]
    assert stats.ret_24h == pytest.approx(last_closed[4] / ref[4] - 1)
    assert stats.volume_eur > 0


def test_bar_stats_rejects_thin_or_stale_series():
    rows = _series(T0 - 2 * DAY_MS, 20, 100.0, 0.0)
    assert bar_stats("X", rows, T0) is None
    # Stale: last bar closed 3h before T0.
    rows = _series(T0 - 2 * DAY_MS, 2 * BARS_PER_DAY - 12, 100.0, 0.0)
    assert bar_stats("X", rows, T0) is None


def _universe(returns: dict[str, float], btc_ret: float = 0.0, *, from_high: float = 0.0):
    cfg = DeskConfig(universe=tuple(returns), **FLAT_SIZING)
    candles = {}
    n = 2 * BARS_PER_DAY
    for base, ret in {**returns, "BTC": btc_ret}.items():
        drift = (1 + ret) ** (1 / BARS_PER_DAY) - 1
        rows = _series(T0 - 2 * DAY_MS, n, 100.0, drift)
        if from_high and base != "BTC":
            # Spike the high of the last closed bar so price sits below the 24h high.
            rows[-1][2] = rows[-1][4] * (1 + from_high)
        candles[base] = rows
    return cfg, candles


def test_regime_blocks_on_weak_btc_or_breadth_when_soft_disabled():
    cfg, candles = _universe({"A": 0.03, "B": -0.02, "C": -0.01}, btc_ret=0.01)
    cfg = cfg.with_overrides(soft_regime_on_weak_tape=False)
    alts = universe_stats(candles, T0, cfg)
    btc = bar_stats("BTC", candles["BTC"], T0)
    regime = classify_regime(btc, alts, cfg)
    assert not regime.ok and not regime.soft and "breadth_weak" in regime.reasons

    cfg, candles = _universe({"A": 0.03, "B": 0.02}, btc_ret=-0.02)
    cfg = cfg.with_overrides(soft_regime_on_weak_tape=False)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert not regime.ok and not regime.soft and "btc_weak" in regime.reasons


def test_soft_regime_allows_alphai_picks_on_single_weak_factor():
    """Single soft-fail (breadth only) stays open for AlphaI picks at half clip."""
    cfg, candles = _universe({"A": 0.04, "B": -0.02, "C": -0.01}, btc_ret=0.01)
    cfg = cfg.with_overrides(
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        min_volume_eur=0.0,
        clip_eur=1000.0,
        alphai_clip_mult=1.0,
        strong_clip_mult=1.0,
        weak_clip_mult=1.0,
    )
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert regime.ok and regime.soft
    assert "breadth_weak" in regime.reasons and "btc_weak" not in regime.reasons
    view = AlphaIView(picks=frozenset({"A"}), avoid=frozenset())
    cands = rank_candidates(alts, regime.btc_ret or 0.0, cfg, alphai=view)
    entries = select_entries(cands, regime, cfg, held_bases=[], alphai=view)
    assert [e.base for e in entries] == ["A"]
    assert entries[0].clip_eur == pytest.approx(500.0)
    assert "soft_regime" in entries[0].reasons
    assert entries[0].entry_ctx.get("regime_label") == "soft"
    # Non-picks stay blocked under soft regime.
    view2 = AlphaIView(picks=frozenset(), avoid=frozenset())
    cands2 = rank_candidates(alts, regime.btc_ret or 0.0, cfg, alphai=view2)
    assert select_entries(cands2, regime, cfg, held_bases=[], alphai=view2) == []


def test_double_weak_tape_idles_by_default():
    """BTC + breadth both soft-fail → idle (no force-longs into dead tape)."""
    cfg, candles = _universe({"A": 0.04, "B": -0.02, "C": -0.01}, btc_ret=-0.02)
    cfg = cfg.with_overrides(soft_regime_on_weak_tape=True, min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert not regime.ok and not regime.soft
    assert "weak_tape_idle" in regime.reasons
    view = AlphaIView(picks=frozenset({"A"}))
    cands = rank_candidates(alts, regime.btc_ret or 0.0, cfg, alphai=view)
    assert select_entries(cands, regime, cfg, held_bases=[], alphai=view) == []
    # Opt-out restores legacy soft double-weak behaviour.
    soft_cfg = cfg.with_overrides(weak_tape_idle_on_double=False)
    soft = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, soft_cfg)
    assert soft.ok and soft.soft


def test_soft_regime_idles_under_macro_caution():
    """Soft tape + AlphaI macro caution → idle (survival; no soft+reduce entries)."""
    cfg, candles = _universe({"A": 0.04, "B": -0.02, "C": -0.01}, btc_ret=0.01)
    cfg = cfg.with_overrides(
        soft_regime_on_weak_tape=True,
        soft_regime_idle_on_macro_caution=True,
        min_volume_eur=0.0,
        clip_eur=1000.0,
    )
    alts = universe_stats(candles, T0, cfg)
    view = AlphaIView(macro_caution=True, picks=frozenset({"A"}))
    regime = classify_regime(
        bar_stats("BTC", candles["BTC"], T0), alts, cfg, alphai=view
    )
    assert not regime.ok and not regime.soft
    assert "soft_macro_idle" in regime.reasons
    cands = rank_candidates(alts, regime.btc_ret or 0.0, cfg, alphai=view)
    assert select_entries(cands, regime, cfg, held_bases=[], alphai=view) == []


def test_rank_and_select_apply_excess_cluster_and_alphai_rules():
    cfg, candles = _universe(
        {"SOL": 0.06, "AVAX": 0.05, "LINK": 0.04, "XRP": 0.012, "OP": 0.03}, 0.01
    )
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert regime.ok and regime.breadth == 1.0
    view = AlphaIView(avoid=frozenset({"AVAX"}), picks=frozenset({"LINK"}))
    cands = rank_candidates(alts, regime.btc_ret, cfg, alphai=view)
    names = [c.base for c in cands]
    assert "AVAX" not in names  # veto
    assert "XRP" not in names  # excess 0.2pp < 1.5pp
    assert names[0] == "SOL"
    entries = select_entries(cands, regime, cfg, held_bases=[], alphai=view)
    # breadth 1.0 -> top_n_broad=3, but OP excess ~2.0pp sits on the new
    # min_excess/fee floor and is dropped; AVAX vetoed. SOL + LINK remain.
    assert [e.base for e in entries] == ["SOL", "LINK"]
    link = next(e for e in entries if e.base == "LINK")
    assert link.clip_eur == pytest.approx(650.0)
    # Holding SOL blocks the whole L1 cluster.
    entries = select_entries(cands, regime, cfg, held_bases=["SOL"], alphai=view)
    assert [e.base for e in entries] == ["LINK"]


def test_from_high_and_macro_caution_rules():
    cfg, candles = _universe({"SOL": 0.05}, 0.0, from_high=0.03)
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    assert alts["SOL"].from_high == pytest.approx(-0.03 / 1.03, rel=1e-3)
    assert rank_candidates(alts, 0.0, cfg) == []
    cfg, candles = _universe({"SOL": 0.05}, 0.0)
    cfg = cfg.with_overrides(min_volume_eur=0.0)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    # Under macro caution + pick-gate, only AlphaI picks may enter.
    cfg = cfg.with_overrides(macro_caution_requires_alphai_pick=True)
    view = AlphaIView(macro_caution=True, picks=frozenset({"SOL"}))
    cands = rank_candidates(alts, 0.0, cfg, alphai=view)
    entries = select_entries(cands, regime, cfg, held_bases=[], alphai=view)
    assert entries[0].clip_eur == pytest.approx(455.0)  # 500 * alphai_clip 1.3 * macro 0.7
    assert "macro_reduce" in entries[0].reasons and "alphai_pick" in entries[0].reasons
    # Non-picks are skipped while macro caution + reduce + pick-gate is active.
    skipped = select_entries(
        rank_candidates(alts, 0.0, cfg, alphai=AlphaIView(macro_caution=True)),
        regime,
        cfg,
        held_bases=[],
        alphai=AlphaIView(macro_caution=True, picks=frozenset()),
    )
    assert skipped == []
    blocked = classify_regime(
        bar_stats("BTC", candles["BTC"], T0),
        alts,
        cfg.with_overrides(macro_caution_mode="block"),
        alphai=view,
    )
    assert not blocked.ok and "alphai_macro_block" in blocked.reasons


def test_macro_caution_can_allow_non_picks_when_flag_off():
    cfg, candles = _universe({"SOL": 0.05}, 0.0)
    cfg = cfg.with_overrides(min_volume_eur=0.0, macro_caution_requires_alphai_pick=False)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    view = AlphaIView(macro_caution=True, picks=frozenset())
    entries = select_entries(
        rank_candidates(alts, 0.0, cfg, alphai=view),
        regime,
        cfg,
        held_bases=[],
        alphai=view,
    )
    assert len(entries) == 1
    assert "macro_reduce" in entries[0].reasons


def test_requires_alphai_pick_blocks_tape_only_entries():
    cfg, candles = _universe({"SOL": 0.05}, 0.0)
    cfg = cfg.with_overrides(min_volume_eur=0.0, requires_alphai_pick=True)
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    empty = AlphaIView(picks=frozenset())
    assert (
        select_entries(
            rank_candidates(alts, 0.0, cfg, alphai=empty),
            regime,
            cfg,
            held_bases=[],
            alphai=empty,
        )
        == []
    )
    picked = AlphaIView(picks=frozenset({"SOL"}))
    entries = select_entries(
        rank_candidates(alts, 0.0, cfg, alphai=picked),
        regime,
        cfg,
        held_bases=[],
        alphai=picked,
    )
    assert len(entries) == 1 and entries[0].base == "SOL"
    assert "alphai_pick" in entries[0].reasons


def test_desk_config_from_settings_wires_requires_alphai_pick():
    from bot.core.config import Settings
    from bot.live.momentum_runner import desk_config_from_settings

    cfg = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_requires_alphai_pick=True,
        )
    )
    assert cfg.requires_alphai_pick is True
    cfg_off = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_requires_alphai_pick=False,
        )
    )
    assert cfg_off.requires_alphai_pick is False


def test_desk_config_from_settings_wires_chase_gate():
    from bot.core.config import Settings
    from bot.live.momentum_runner import desk_config_from_settings

    cfg = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_max_chase_ret_24h=0.09,
            momentum_desk_chase_near_high=0.008,
            momentum_desk_requires_alphai_pick=False,
        )
    )
    assert cfg.max_chase_ret_24h == 0.09
    assert cfg.chase_near_high == 0.008
    assert cfg.requires_alphai_pick is False


def test_desk_config_from_settings_wires_macro_knobs(monkeypatch):
    from bot.core.config import Settings
    from bot.live.momentum_runner import desk_config_from_settings

    monkeypatch.setenv("MOMENTUM_DESK_MACRO_CAUTION_MODE", "reduce")
    monkeypatch.setenv("MOMENTUM_DESK_SOFT_REGIME_IDLE_ON_MACRO_CAUTION", "true")
    monkeypatch.setenv("MOMENTUM_DESK_MACRO_CAUTION_REQUIRES_ALPHAI_PICK", "false")
    settings = Settings(
        momentum_desk_macro_caution_mode="reduce",
        momentum_desk_soft_regime_idle_on_macro_caution=True,
        momentum_desk_macro_caution_requires_alphai_pick=False,
    )
    cfg = desk_config_from_settings(settings)
    assert cfg.macro_caution_mode == "reduce"
    assert cfg.soft_regime_idle_on_macro_caution is True
    assert cfg.macro_caution_requires_alphai_pick is False
    # Explicit kwargs win over ambient process env leftovers.
    cfg2 = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_macro_caution_mode="reduce",
            momentum_desk_soft_regime_idle_on_macro_caution=True,
            momentum_desk_macro_caution_requires_alphai_pick=False,
        )
    )
    assert cfg2.macro_caution_mode == "reduce"
    assert cfg2.soft_regime_idle_on_macro_caution is True
    assert cfg2.macro_caution_requires_alphai_pick is False


def test_desk_config_from_settings_wires_trail_and_early_stop():
    from bot.core.config import Settings
    from bot.live.momentum_runner import desk_config_from_settings

    cfg = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_trail_pct=0.05,
            momentum_desk_trail_tight_after=0.0,
            momentum_desk_early_stop_pct=0.0,
            momentum_desk_early_stop_until_peak=0.0,
        )
    )
    assert cfg.trail_pct == 0.05
    assert cfg.trail_tight_after == 0.0
    assert cfg.early_stop_pct == 0.0
    assert cfg.early_stop_until_peak == 0.0

    cfg_on = desk_config_from_settings(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            momentum_desk_trail_pct=0.05,
            momentum_desk_early_stop_pct=0.02,
            momentum_desk_early_stop_until_peak=0.015,
        )
    )
    assert cfg_on.early_stop_pct == 0.02
    assert cfg_on.early_stop_until_peak == 0.015


def test_exit_rules_hard_stop_trail_ratchet_and_time():
    cfg = DeskConfig(
        trail_pct=0.03,
        trail_tight_after=0.03,
        trail_tight_pct=0.015,
        hard_stop_pct=0.03,
        midflat_hours=0.0,  # isolate the full time_exit path
        green_deadline_hours=0.0,  # isolate from time-to-green
    )
    pos = Position("X", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos, [T0, 100, 101, 99, 100.5, 1], cfg) is None
    # Peak 102 -> 3% trail = 98.94; close 99 holds.
    assert evaluate_exit(pos, [T0 + BAR_MS, 100.5, 102, 98.95, 99.0, 1], cfg) is None
    assert pos.peak == 102
    # Peak ratchets to 104 (+4% >= tight_after) -> trail 1.5% = 102.44; close 102.3 exits.
    d = evaluate_exit(pos, [T0 + 2 * BAR_MS, 99, 104, 99, 102.3, 1], cfg)
    assert d is not None and d.reason == "trail" and not d.urgent
    pos2 = Position("Y", 100.0, 5.0, 500.0, T0, 100.0)
    d = evaluate_exit(pos2, [T0, 100, 100, 96, 96.9, 1], cfg)
    assert d is not None and d.reason == "hard_stop" and d.urgent
    pos3 = Position("Z", 100.0, 5.0, 500.0, T0, 100.0)
    late = T0 + int(cfg.time_exit_hours * 3_600_000) - BAR_MS
    assert evaluate_exit(pos3, [late - BAR_MS, 100, 100.2, 99.9, 100.1, 1], cfg) is None
    d = evaluate_exit(pos3, [late, 100, 100.2, 99.9, 100.1, 1], cfg)
    assert d is not None and d.reason == "time_exit"
    pos4 = Position("W", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos4, [late, 100, 101, 100, 100.8, 1], cfg) is None  # above BE -> keep


def test_midflat_exits_fee_flat_before_full_time_exit():
    cfg = DeskConfig(
        midflat_hours=16.0, time_exit_hours=24.0, fee_rt=0.003, green_deadline_hours=0.0
    )
    pos = Position("Z", 100.0, 5.0, 500.0, T0, 100.0)
    mid = T0 + int(16 * 3_600_000) - BAR_MS
    assert evaluate_exit(pos, [mid - BAR_MS, 100, 100.2, 99.9, 100.2, 1], cfg) is None
    d = evaluate_exit(pos, [mid, 100, 100.2, 99.9, 100.1, 1], cfg)
    assert d is not None and d.reason == "midflat"
    # Above fee_rt after midflat: hold until the full time exit (or trail).
    pos2 = Position("W", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos2, [mid, 100, 101, 100, 100.5, 1], cfg) is None


def test_early_stop_stages_until_peak_then_hard_stop():
    cfg = DeskConfig(
        hard_stop_pct=0.03,
        early_stop_pct=0.015,
        early_stop_until_peak=0.005,
        trail_pct=0.10,
        trail_tight_after=0.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        exit_on_touch=True,
    )
    # Never confirmed: −1.5% early stop fires before −3% hard stop.
    pos = Position("E", 100.0, 5.0, 500.0, T0, 100.0)
    d = evaluate_exit(pos, [T0, 100, 100.2, 98.4, 98.5, 1], cfg)
    assert d is not None and d.reason == "early_stop" and d.urgent
    assert d.price == pytest.approx(98.5)
    # After peak ≥ +0.5%, early stop unlocks → −3% hard stop only.
    pos2 = Position("E", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos2, [T0, 100, 100.6, 100.0, 100.5, 1], cfg) is None
    assert pos2.peak == pytest.approx(100.6)
    d = evaluate_exit(pos2, [T0 + BAR_MS, 100.5, 100.5, 98.4, 98.5, 1], cfg)
    assert d is None  # −1.6% still above −3% hard stop
    d = evaluate_exit(pos2, [T0 + 2 * BAR_MS, 98.5, 98.5, 96.8, 97.0, 1], cfg)
    assert d is not None and d.reason == "hard_stop" and d.price == pytest.approx(97.0)


def test_no_green_exits_when_peak_never_confirms():
    cfg = DeskConfig(
        green_deadline_hours=4.0,
        green_min_peak=0.01,
        hard_stop_pct=0.05,
        trail_pct=0.10,
        midflat_hours=0.0,
        time_exit_hours=24.0,
        fee_rt=0.003,
    )
    deadline = T0 + int(4 * 3_600_000) - BAR_MS
    pos = Position("G", 100.0, 5.0, 500.0, T0, 100.0)
    # Peak only +0.4% by deadline → no_green.
    assert evaluate_exit(pos, [deadline - BAR_MS, 100, 100.4, 99.8, 100.2, 1], cfg) is None
    d = evaluate_exit(pos, [deadline, 100.2, 100.3, 99.9, 100.1, 1], cfg)
    assert d is not None and d.reason == "no_green" and not d.urgent
    # Peak ≥ +1% by deadline → hold (even if close is fee-flat).
    pos2 = Position("G", 100.0, 5.0, 500.0, T0, 100.0)
    assert evaluate_exit(pos2, [deadline - BAR_MS, 100, 101.2, 100.5, 100.8, 1], cfg) is None
    assert evaluate_exit(pos2, [deadline, 100.8, 100.9, 100.0, 100.2, 1], cfg) is None


def test_fade_state_fires_on_fast_eta_to_zero():
    from bot.live.momentum_desk import FadeState, update_fade_state, unrealized_net_eur

    cfg = DeskConfig(
        fade_eta_sec=180.0,
        fade_confirm_sec=20.0,
        fade_smooth_sec=40.0,
        fade_min_peak_eur=15.0,
        fade_min_peak_pct=0.012,
        fade_min_giveback_eur=5.0,
        fee_rt=0.003,
        hard_stop_pct=0.10,
        trail_pct=0.10,
        green_deadline_hours=0.0,
    )
    # ~€1300 clip: qty 13 @ 100.
    pos = Position("X", 100.0, 13.0, 1300.0, T0, 100.0, entry_fee_eur=1.95)
    st = FadeState()
    # Peak ~+€26 net at 102.5.
    peak_mark = 102.5
    st, d = update_fade_state(
        st,
        net_eur=unrealized_net_eur(pos, peak_mark, cfg),
        gross_return=pos.gross_return(peak_mark),
        now=1000.0,
        cfg=cfg,
    )
    assert d is None and st.peak_net_eur >= 15.0
    # Fast cascade toward zero: still green but ETA ≪ 180s.
    fade_mark = 101.2
    net = unrealized_net_eur(pos, fade_mark, cfg)
    assert net > 0.0
    st, d = update_fade_state(
        st, net_eur=net, gross_return=pos.gross_return(fade_mark), now=1004.0, cfg=cfg
    )
    assert d is None and st.breach_since is not None  # breach armed
    st, d = update_fade_state(
        st, net_eur=net * 0.55, gross_return=0.006, now=1025.0, cfg=cfg
    )
    assert d is not None and d.reason == "fade_fast" and d.urgent
    # Disabled when fade_eta_sec=0.
    off = cfg.with_overrides(fade_eta_sec=0.0)
    st2, d2 = update_fade_state(
        FadeState(), net_eur=30.0, gross_return=0.03, now=1.0, cfg=off
    )
    assert d2 is None and st2.peak_net_eur == 0.0


def test_fade_state_keeps_slow_runners():
    from bot.live.momentum_desk import FadeState, update_fade_state

    cfg = DeskConfig(
        fade_eta_sec=180.0,
        fade_confirm_sec=20.0,
        fade_smooth_sec=40.0,
        fade_min_peak_eur=15.0,
        fade_min_giveback_eur=5.0,
        fee_rt=0.003,
    )
    st = FadeState()
    st, _ = update_fade_state(st, net_eur=40.0, gross_return=0.03, now=0.0, cfg=cfg)
    # Gentle drift: ~€2 over 60s → ETA ≫ 180s.
    st, d = update_fade_state(st, net_eur=38.0, gross_return=0.028, now=60.0, cfg=cfg)
    assert d is None and st.breach_since is None
    st, d = update_fade_state(st, net_eur=36.0, gross_return=0.026, now=120.0, cfg=cfg)
    assert d is None and st.breach_since is None


def test_entry_fee_buffer_and_chase_reject():
    cfg = DeskConfig(
        min_excess=0.010,
        entry_fee_buffer_mult=6.0,
        fee_rt=0.003,
        max_chase_ret_24h=0.09,
        chase_near_high=0.008,
        min_volume_eur=0.0,
    )
    # Excess 1.5% clears min_excess but not fee×6 (1.8%) — rejected.
    thin = {"A": _stats("A", 0.015)}
    assert rank_candidates(thin, 0.0, cfg) == []
    # Excess 2.0% clears fee floor.
    ok = rank_candidates({"B": _stats("B", 0.020)}, 0.0, cfg)
    assert [c.base for c in ok] == ["B"]
    # Extended + glued to high → chase reject.
    from bot.live.momentum_desk import BaseStats

    chase = {
        "C": BaseStats(
            base="C", price=100.0, ret_24h=0.10, from_high=-0.002, volume_eur=5e6
        )
    }
    assert rank_candidates(chase, 0.0, cfg) == []
    # Same extension but pulled back under the high → allowed.
    pulled = {
        "C": BaseStats(
            base="C", price=100.0, ret_24h=0.10, from_high=-0.015, volume_eur=5e6
        )
    }
    assert [c.base for c in rank_candidates(pulled, 0.0, cfg)] == ["C"]


def test_strong_clip_requires_quality_or_alphai():
    from bot.live.momentum_desk import RegimeDecision

    cfg = DeskConfig(
        clip_eur=1000.0,
        strong_clip_mult=1.3,
        weak_clip_mult=1.0,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        min_volume_eur=0.0,
        min_excess=0.015,
        entry_fee_buffer_mult=0.0,
    )
    strong = RegimeDecision(True, 0.01, 0.9, ())
    mediocre = rank_candidates({"SOL": _stats("SOL", 0.025)}, 0.0, cfg)
    e = select_entries(mediocre, strong, cfg, held_bases=[])[0]
    assert e.clip_eur == pytest.approx(1000.0)
    assert "breadth_strong_gated" in e.reasons
    quality = rank_candidates({"SOL": _stats("SOL", 0.05)}, 0.0, cfg)
    e2 = select_entries(quality, strong, cfg, held_bases=[])[0]
    assert e2.clip_eur == pytest.approx(1300.0) and "breadth_strong" in e2.reasons
    view = AlphaIView(picks=frozenset({"SOL"}))
    picked = select_entries(
        rank_candidates({"SOL": _stats("SOL", 0.025)}, 0.0, cfg, alphai=view),
        strong,
        cfg,
        held_bases=[],
        alphai=view,
    )[0]
    # alphai_pick still sizes up even below strong_clip_min_excess
    assert picked.clip_eur == pytest.approx(1000 * 1.3 * 1.3)


def test_exit_on_touch_uses_low_and_prior_peak():
    cfg = DeskConfig(trail_pct=0.03, trail_tight_after=0.0, hard_stop_pct=0.03, exit_on_touch=True)
    # Wick to -3.2% but close back at -1%: touch mode stops out at the level,
    # close mode holds.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 100.0)
    d = evaluate_exit(pos, [T0, 100, 100.5, 96.8, 99.0, 1], cfg)
    assert d is not None and d.reason == "hard_stop" and d.price == pytest.approx(97.0)
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 100.0)
    assert (
        evaluate_exit(pos, [T0, 100, 100.5, 96.8, 99.0, 1], cfg.with_overrides(exit_on_touch=False))
        is None
    )
    # Trail is measured against the peak known before the bar; a gap below the
    # level fills at the open.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 110.0)
    d = evaluate_exit(pos, [T0, 105.0, 108.0, 104.0, 107.0, 1], cfg)
    assert d is not None and d.reason == "trail" and d.price == pytest.approx(105.0)
    # No touch: the peak still ratchets up.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 110.0)
    assert evaluate_exit(pos, [T0, 110.0, 112.0, 109.0, 111.0, 1], cfg) is None
    assert pos.peak == 112.0
    # Every-bar decisions fire on each 15m boundary.
    assert is_decision_time(T0 + 5 * BAR_MS, cfg.with_overrides(decision_every_bar=True))
    assert not is_decision_time(T0 + 5 * BAR_MS, cfg)


def test_restrict_by_volume_keeps_top_k():
    from bot.live.momentum_desk import BaseStats, restrict_by_volume

    stats = {
        b: BaseStats(b, 1.0, 0.0, 0.0, vol) for b, vol in {"A": 5e6, "B": 1e6, "C": 3e6}.items()
    }
    assert set(restrict_by_volume(stats, DeskConfig())) == {"A", "B", "C"}
    assert set(restrict_by_volume(stats, DeskConfig(universe_top_by_volume=2))) == {"A", "C"}


def test_default_universe_is_the_core_sixteen():
    from bot.live.momentum_desk import DEFAULT_CLUSTERS, DEFAULT_UNIVERSE

    assert len(DEFAULT_UNIVERSE) == 16
    assert set(DEFAULT_UNIVERSE) <= set(DEFAULT_CLUSTERS)
    for base in ("HYPE", "TAO", "WLD", "PEPE", "ONDO"):
        assert base not in DEFAULT_UNIVERSE


def test_risk_ledger_day_week_limits_and_pause():
    cfg = DeskConfig(
        day_loss_limit_eur=40, week_loss_limit_eur=100, pause_hours_after_week_limit=48
    )
    led = RiskLedger.from_dict(cfg, None)
    assert led.entries_allowed(T0) == (True, "ok")
    led.note_close(-41, T0)
    assert led.entries_allowed(T0) == (False, "day_loss_limit")
    assert led.entries_allowed(T0 + DAY_MS)[0] is True
    led.note_close(-60, T0 + DAY_MS)  # week total -101 -> pause 48h
    assert led.entries_allowed(T0 + DAY_MS) == (False, "week_loss_pause")
    assert led.entries_allowed(T0 + DAY_MS + 47 * 3_600_000)[0] is False
    assert led.entries_allowed(T0 + 3 * DAY_MS + 1)[0] is True or led.week_realized_eur > -100
    led.note_entry("SOL", T0 + 3 * DAY_MS)
    assert led.blocked_bases(T0 + 3 * DAY_MS, 1) == {"SOL"}
    restored = RiskLedger.from_dict(cfg, led.to_dict())
    assert restored.to_dict() == led.to_dict()


def test_is_decision_time():
    cfg = DeskConfig(decision_hours_utc=(0, 12))
    assert is_decision_time(T0, cfg)
    assert is_decision_time(T0 + 12 * 3_600_000, cfg)
    assert not is_decision_time(T0 + 3_600_000, cfg)
    assert not is_decision_time(T0 + BAR_MS, cfg)


def test_decision_interval_fires_each_minute_on_weekdays():
    cfg = DeskConfig(decision_hours_utc=(7,), decision_interval_sec=60.0)
    # Thursday 00:01 UTC is a decision slot under interval mode (hours ignored).
    assert is_decision_time(T0 + 60_000, cfg)
    assert not is_decision_time(T0 + 30_000, cfg)
    # Saturday still blocked when weekend entries are skipped.
    sat = T0 + 2 * DAY_MS
    assert not is_decision_time(sat + 60_000, cfg)
    assert is_decision_time(sat + 60_000, cfg.with_overrides(skip_weekend_entries=False))


def test_decision_slot_due_interval_and_next_iso(tmp_path):
    """Live runner fires once per minute and advances next_decision."""
    from bot.live.momentum_runner import Holding, MomentumDeskRunner, RunnerOptions

    clock = FakeClock(T0 / 1000 + 90.0)  # mid-minute past a Thursday midnight
    cfg = DeskConfig(decision_interval_sec=60.0, decision_hours_utc=(7,), **FLAT_SIZING)
    opts = RunnerOptions(
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
    )
    r = MomentumDeskRunner(cfg, FakeGateway(), options=opts, clock=clock, sleep=clock.sleep)
    slot = r._decision_slot_due(int(clock() * 1000))
    assert slot == (T0 + 60_000)
    r.last_decision_hour_ms = slot
    assert r._decision_slot_due(int(clock() * 1000)) is None
    nxt = r._next_decision_iso(int(clock() * 1000))
    assert nxt.startswith(datetime.fromtimestamp((T0 + 120_000) / 1000, UTC).isoformat()[:16])


def test_weekend_entries_skipped_but_exits_unaffected():
    from bot.live.momentum_desk import is_entry_weekday, is_scheduled_hour

    cfg = DeskConfig(decision_hours_utc=(7, 13))
    thu, sat, sun, mon = (T0 + k * DAY_MS for k in (0, 2, 3, 4))
    assert is_entry_weekday(thu, cfg) and is_entry_weekday(mon, cfg)
    assert not is_entry_weekday(sat, cfg) and not is_entry_weekday(sun, cfg)
    assert is_decision_time(thu + 7 * 3_600_000, cfg)
    assert not is_decision_time(sat + 7 * 3_600_000, cfg)
    assert not is_scheduled_hour(sun + 13 * 3_600_000, cfg)
    assert is_decision_time(sat + 7 * 3_600_000, cfg.with_overrides(skip_weekend_entries=False))
    # Exit evaluation has no calendar: a Saturday bar still triggers the stop.
    pos = Position("X", 100.0, 5.0, 500.0, sat, 100.0)
    d = evaluate_exit(pos, [sat + BAR_MS, 100, 100, 96, 96.5, 1], cfg)
    assert d is not None and d.reason == "hard_stop"


def test_breadth_scales_clip_up_on_strong_tape_and_down_on_thin_tape():
    from bot.live.momentum_desk import RegimeDecision, breadth_clip_mult, max_clip_mult

    cfg = DeskConfig(clip_eur=1000.0, strong_clip_mult=1.3, weak_clip_mult=0.7)
    assert breadth_clip_mult(0.9, cfg) == (1.3, "breadth_strong")
    assert breadth_clip_mult(0.75, cfg) == (1.0, "")
    assert breadth_clip_mult(0.6, cfg) == (0.7, "breadth_weak")
    assert max_clip_mult(cfg) == pytest.approx(1.3 * 1.3 * 1.15)
    assert max_clip_mult(cfg.with_overrides(outcome_size_enabled=False)) == pytest.approx(
        1.3 * 1.3
    )
    cands = rank_candidates(
        {"SOL": _stats("SOL", 0.05)}, 0.0, cfg.with_overrides(min_volume_eur=0.0)
    )
    strong = RegimeDecision(True, 0.01, 0.9, ())
    thin = RegimeDecision(True, 0.01, 0.6, ())
    e_strong = select_entries(cands, strong, cfg, held_bases=[])[0]
    e_thin = select_entries(cands, thin, cfg, held_bases=[])[0]
    assert e_strong.clip_eur == pytest.approx(1300.0) and "breadth_strong" in e_strong.reasons
    assert e_thin.clip_eur == pytest.approx(700.0) and "breadth_weak" in e_thin.reasons
    # AlphaI pick and strong tape stack.
    picked = select_entries(
        rank_candidates(
            {"SOL": _stats("SOL", 0.05)},
            0.0,
            cfg.with_overrides(min_volume_eur=0.0),
            alphai=AlphaIView(picks=frozenset({"SOL"})),
        ),
        strong,
        cfg,
        held_bases=[],
        alphai=AlphaIView(picks=frozenset({"SOL"})),
    )[0]
    assert picked.clip_eur == pytest.approx(1000 * 1.3 * 1.3)


def _stats(base: str, ret: float):
    from bot.live.momentum_desk import BaseStats

    return BaseStats(base=base, price=100.0, ret_24h=ret, from_high=0.0, volume_eur=5e6)


# ------------------------------------------------------------- backtest


def test_simulate_enters_leader_and_exits_on_trail():
    cfg = DeskConfig(universe=("SOL", "LINK"), min_volume_eur=0.0, clip_eur=500.0, **FLAT_SIZING)
    n = 5 * BARS_PER_DAY
    candles = {
        "BTC": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
        "LINK": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
        "SOL": _series(T0 - 2 * DAY_MS, n, 100.0, 0.0),
    }
    # SOL: +5% over the day before T0, then keeps rising 1%/bar for 6 bars and drops 4%.
    sol = candles["SOL"]
    day_idx = [i for i, r in enumerate(sol) if T0 - DAY_MS <= r[0] < T0]
    for k, i in enumerate(day_idx):
        px = 100.0 * (1 + 0.05 * (k + 1) / len(day_idx))
        sol[i][1:5] = [px, px, px, px]
    after = [i for i, r in enumerate(sol) if r[0] >= T0]
    px = sol[day_idx[-1]][4]
    for k, i in enumerate(after):
        px = px * (1.01 if k < 6 else (0.96 if k == 6 else 1.0))
        sol[i][1:5] = [px, px, px, px]
    res = simulate(candles, cfg, start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS)
    assert [d.entries for d in res.decisions if d.entries] == [["SOL@500"]]
    assert len(res.closed) == 1
    t = res.closed[0]
    assert t.base == "SOL" and t.reason == "trail"
    assert t.peak_return > 0.05 and t.net_eur > 0
    # Breadth is 0.5 (SOL up, LINK flat) -> regime ON with top_n=2 but LINK has no excess.
    assert res.decisions[0].regime_ok
    # Book cap mirrors the live router: shrink to the cash left, skip below 50%.
    capped = simulate(
        candles, cfg.with_overrides(book_eur=300.0), start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS
    )
    assert capped.closed[0].notional_eur == pytest.approx(300.0)
    starved = simulate(
        candles, cfg.with_overrides(book_eur=200.0), start_ms=T0 - BAR_MS, end_ms=T0 + DAY_MS
    )
    assert starved.closed == []


# ------------------------------------------------------------ live path


@dataclass
class FakeGateway:
    bid: float = 100.0
    ask: float = 100.2
    fill_maker_after_polls: int | None = None  # None = never fills as maker
    partial_on_cancel: bool = False
    # Fraction of each taker order that fills immediately (rest canceled empty).
    taker_fill_frac: float = 1.0
    # After this many partial taker fills, subsequent takers fill fully (models
    # an escalating chase that finally clears the book).
    taker_partial_count: int = 10**9
    free_by_base: dict[str, float] | None = None  # None = do not report (no clamp)
    placed: list[dict] = field(default_factory=list)
    _orders: dict[str, dict] = field(default_factory=dict)
    _polls: int = 0

    async def best_bid_ask(self, symbol):
        return self.bid, self.ask

    async def base_free(self, base: str):
        if self.free_by_base is None:
            return None
        return float(self.free_by_base.get(base.upper(), 0.0))

    async def place_limit(self, symbol, side, qty, price, *, post_only):
        oid = f"o{len(self.placed) + 1}"
        self.placed.append({"side": side, "qty": qty, "price": price, "post_only": post_only})
        if not post_only:
            n_taker = sum(1 for p in self.placed if not p["post_only"])
            frac = (
                float(self.taker_fill_frac)
                if n_taker <= int(self.taker_partial_count)
                else 1.0
            )
            filled = max(0.0, min(qty, qty * frac))
            self._orders[oid] = {
                "status": "closed" if filled + 1e-12 >= qty else "open",
                "filled": filled,
                "avg": price if filled > 0 else None,
                "fee": filled * price * 0.0025,
                "qty": qty,
                "price": price,
            }
            o = self._orders[oid]
            return OrderState(oid, o["status"], o["filled"], o["avg"], o["fee"])
        self._orders[oid] = {
            "status": "open",
            "filled": 0.0,
            "avg": None,
            "fee": 0.0,
            "qty": qty,
            "price": price,
        }
        return OrderState(oid, "open", 0.0, None, 0.0)

    async def fetch_order(self, order_id, symbol):
        o = self._orders[order_id]
        self._polls += 1
        if (
            o["status"] == "open"
            and self.fill_maker_after_polls is not None
            and self._polls >= self.fill_maker_after_polls
        ):
            o.update(
                status="closed", filled=o["qty"], avg=o["price"], fee=o["qty"] * o["price"] * 0.0015
            )
        return OrderState(order_id, o["status"], o["filled"], o["avg"], o["fee"])

    async def cancel_order(self, order_id, symbol):
        o = self._orders[order_id]
        if o["status"] == "open":
            if self.partial_on_cancel and o["filled"] <= 0:
                # Venue filled part of it just before the cancel landed; the
                # cancel reply itself (like Bitvavo's) says nothing about it.
                o.update(
                    filled=o["qty"] * 0.4, avg=o["price"], fee=o["qty"] * 0.4 * o["price"] * 0.0015
                )
            o["status"] = "canceled"
        return OrderState(order_id, "open", o["filled"], o["avg"], o["fee"])


class FakeClock:
    def __init__(self, start: float) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    async def sleep(self, sec: float) -> None:
        self.t += sec


class FakeFeed:
    def __init__(self, rows_by_base):
        self.rows = rows_by_base
        self.last_price_calls: list[str] = []

    async def candles(self, base, limit):
        return self.rows[base][-limit:]

    async def last_price(self, base):
        self.last_price_calls.append(base)
        rows = self.rows.get(base) or []
        return float(rows[-1][4]) if rows else None


def _runner(tmp_path: Path, gw, clock, feed=None, opt_kwargs=None, **cfg_kwargs) -> MomentumDeskRunner:
    cfg = DeskConfig(**{**FLAT_SIZING, **cfg_kwargs})
    opts = RunnerOptions(
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        buy_rest_sec=60.0,
        repeg_sec=20.0,
        poll_sec=5.0,
        **(opt_kwargs or {}),
    )
    return MomentumDeskRunner(
        cfg, gw, options=opts, feed=feed or FakeFeed({}), clock=clock, sleep=clock.sleep
    )


def test_buy_rests_as_maker_then_falls_back_to_taker(tmp_path):
    gw = FakeGateway()
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert isinstance(fill, Fill) and fill.taker
    makers = [p for p in gw.placed if p["post_only"]]
    assert len(makers) == 3  # 60s rest / 20s repeg
    assert all(p["price"] == 100.0 for p in makers)
    taker = gw.placed[-1]
    assert not taker["post_only"] and taker["price"] == pytest.approx(100.2 * 1.002)
    assert fill.notional == pytest.approx(500.0, rel=1e-3)


def test_partial_maker_fill_before_cancel_is_settled_from_refetch(tmp_path):
    gw = FakeGateway(partial_on_cancel=True)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert fill is not None and fill.taker
    makers = [p for p in gw.placed if p["post_only"]]
    # Each re-peg only re-posts the unfilled remainder.
    assert makers[1]["qty"] == pytest.approx(makers[0]["qty"] * 0.6)
    assert fill.notional == pytest.approx(500.0, rel=2e-3)
    assert fill.fee_eur > 0


def test_buy_fills_as_maker_without_taker(tmp_path):
    gw = FakeGateway(fill_maker_after_polls=2)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._buy("SOL", 500.0))
    assert fill is not None and not fill.taker
    assert fill.avg_price == 100.0 and len(gw.placed) == 1


def test_small_quantity_sell_is_still_sent(tmp_path):
    # 0.2 coins at 100 EUR = 20 EUR notional: qty < 5 must not be mistaken for
    # a sub-minimum remainder before any fill exists.
    gw = FakeGateway(fill_maker_after_polls=1)
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._sell("ETH", 0.2, urgent=False))
    assert fill is not None and fill.qty == pytest.approx(0.2) and not fill.taker
    assert len(gw.placed) == 1


def test_urgent_sell_goes_straight_to_taker(tmp_path):
    gw = FakeGateway()
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock)
    fill = asyncio.run(r._sell("SOL", 5.0, urgent=True))
    assert fill is not None and fill.taker and len(gw.placed) == 1
    assert gw.placed[0]["price"] == pytest.approx(100.0 * 0.998)


def test_large_urgent_sell_is_sliced(tmp_path):
    # €10k notional / €2.5k slices → 4 child taker orders when the book fills each.
    gw = FakeGateway()
    clock = FakeClock(T0 / 1000)
    r = _runner(tmp_path, gw, clock, opt_kwargs={"sell_slice_eur": 2500.0})
    fill = asyncio.run(r._sell("SOL", 100.0, urgent=True))
    assert fill is not None and fill.qty == pytest.approx(100.0)
    takers = [p for p in gw.placed if not p["post_only"]]
    assert len(takers) == 4
    assert all(p["qty"] == pytest.approx(25.0) for p in takers)


def test_sell_chase_escalates_cross_until_flat(tmp_path):
    # Each taker only fills 40%; chase rounds must deepen the cross and finish.
    gw = FakeGateway(taker_fill_frac=0.4, taker_partial_count=1)
    clock = FakeClock(T0 / 1000)
    r = _runner(
        tmp_path,
        gw,
        clock,
        opt_kwargs={
            "sell_slice_eur": 0.0,  # single child per round
            "sell_chase_rounds": 4,
            "sell_chase_step_bps": 15.0,
            "taker_cross_bps": 20.0,
        },
    )
    fill = asyncio.run(r._sell("SOL", 10.0, urgent=True))
    assert fill is not None and fill.qty == pytest.approx(10.0, abs=1e-6)
    takers = [p for p in gw.placed if not p["post_only"]]
    assert len(takers) >= 2
    # Cross deepens after the thin first fill: 20 → 35 bps …
    prices = [p["price"] for p in takers]
    assert prices[0] == pytest.approx(100.0 * (1 - 0.0020))
    assert prices[1] == pytest.approx(100.0 * (1 - 0.0035))
    assert takers[0]["qty"] == pytest.approx(10.0)
    assert takers[1]["qty"] == pytest.approx(6.0)


def test_patient_trail_sell_rests_then_chases_partials(tmp_path):
    # Trail exits stay patient (maker first) but still chase leftovers to flat.
    gw = FakeGateway(taker_fill_frac=0.5, taker_partial_count=1)
    clock = FakeClock(T0 / 1000)
    r = _runner(
        tmp_path,
        gw,
        clock,
        opt_kwargs={"sell_slice_eur": 0.0, "sell_chase_rounds": 3, "sell_rest_sec": 20.0},
    )
    fill = asyncio.run(r._sell("ETH", 8.0, urgent=False))
    assert fill is not None and fill.qty == pytest.approx(8.0, abs=1e-6)
    assert any(p["post_only"] for p in gw.placed)
    assert any(not p["post_only"] for p in gw.placed)


def test_tick_exits_on_closed_bar_and_persists_ledger(tmp_path):
    cfg_kwargs = dict(hard_stop_pct=0.03, trail_pct=0.03)
    entry_ms = T0
    # Bars: entry bar flat, then a bar closing -3.5% (hard stop).
    rows = [
        [entry_ms, 100, 100, 100, 100, 1],
        [entry_ms + BAR_MS, 100, 100, 96, 96.5, 1],
        [entry_ms + 2 * BAR_MS, 96.5, 96.6, 96.4, 96.5, 1],
    ]
    gw = FakeGateway(bid=96.4, ask=96.6)
    clock = FakeClock((entry_ms + 2 * BAR_MS + 20_000) / 1000)
    r = _runner(tmp_path, gw, clock, feed=FakeFeed({"SOL": rows}), **cfg_kwargs)
    from bot.live.momentum_runner import Holding

    r.holdings.append(
        Holding(Position("SOL", 100.0, 5.0, 500.0, entry_ms, 100.0, 0.75, "test"), "h1", entry_ms)
    )
    asyncio.run(r.tick())
    assert r.holdings == []
    assert r.trade_count == 1 and r.realized_total_eur < -15
    ledger = [line for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert '"event": "exit"' in ledger[-1] and '"reason": "hard_stop"' in ledger[-1]
    assert r.ledger.day_realized_eur == pytest.approx(r.realized_total_eur)
    # Restart restores the risk ledger from disk.
    r2 = _runner(tmp_path, gw, clock, **cfg_kwargs)
    assert r2.trade_count == 1 and r2.ledger.day_realized_eur == pytest.approx(r.realized_total_eur)


def test_decision_runs_once_per_hour_and_enters(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    gw = FakeGateway(bid=100.0, ask=100.2, fill_maker_after_polls=1)
    clock = FakeClock((T0 + 60_000) / 1000)
    r = _runner(
        tmp_path, gw, clock, feed=FakeFeed(candles), universe=("SOL", "LINK"), min_volume_eur=0.0
    )
    asyncio.run(r.tick())
    assert [h.pos.base for h in r.holdings] == ["SOL"]
    assert r.last_regime["entries"] == ["SOL"]
    assert r.ledger.entries_today == {"SOL": 1}
    placed = len(gw.placed)
    asyncio.run(r.tick())  # same hour -> no second decision
    assert len(gw.placed) == placed
    status = r.status()
    assert status["positions"][0]["base"] == "SOL" and status["risk"]["entries_allowed"]


def test_manual_decision_preview_then_execute(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    gw = FakeGateway(bid=100.0, ask=100.2, fill_maker_after_polls=1)
    # 00:32 UTC: past the scheduled slot's grace window, so no scheduled decision.
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r = _runner(
        tmp_path, gw, clock, feed=FakeFeed(candles), universe=("SOL", "LINK"), min_volume_eur=0.0
    )
    asyncio.run(r.tick())
    assert r.holdings == [] and r.last_regime == {}
    scheduled_marker = r.last_decision_hour_ms
    preview = asyncio.run(r.decide_now(execute=False))
    assert preview["trigger"] == "manual" and not preview["executed"]
    assert preview["entries"] == ["SOL"] and preview["planned"][0]["clip_eur"] == 500.0
    assert any(x["base"] == "LINK" and x["why"] == "excess_low" for x in preview["rejected"])
    assert r.holdings == [] and gw.placed == [] and not (tmp_path / "ledger.jsonl").exists()
    done = asyncio.run(r.decide_now(execute=True))
    assert done["executed"] and [h.pos.base for h in r.holdings] == ["SOL"]
    assert r.last_regime["trigger"] == "manual"
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "decision"' in ledger[0] and '"event": "entry"' in ledger[-1]
    # Same base is blocked for the rest of the day; the scheduled hour is untouched.
    again = asyncio.run(r.decide_now(execute=True))
    assert again["entries"] == [] and len(r.holdings) == 1
    assert r.last_decision_hour_ms == scheduled_marker


def test_preview_rows_and_commit_binding(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=300.0,
        okx_cash=1900.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    preview = asyncio.run(r.decide_now(execute=False))
    row = preview["planned"][0]
    assert row["base"] == "SOL" and row["venue"] == "okx" and row["clip_eur"] == 500.0
    assert row["price"] > 0 and row["qty"] == pytest.approx(500.0 / row["price"], rel=1e-6)
    assert row["hard_stop_eur"] == pytest.approx(-500 * 0.03 - 500 * 0.003)
    assert row["break_even"] > row["price"] > row["hard_stop_price"]
    # Commit bound to a different set than the desk would choose -> refused.
    res = asyncio.run(r.decide_now(execute=True, expect_bases=["LINK"]))
    assert res.get("mismatch") and res["expected"] == ["LINK"] and r.holdings == []
    assert not gws["okx"].placed
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "commit_rejected"' in ledger[-1]
    # Matching commit executes.
    res = asyncio.run(r.decide_now(execute=True, expect_bases=["sol"]))
    assert not res.get("mismatch") and [h.pos.base for h in r.holdings] == ["SOL"]


def test_manager_commit_runs_in_background(tmp_path):
    from bot.live.momentum_runner import MomentumDeskManager

    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=2000.0,
        okx_cash=0.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )

    async def scenario():
        m = MomentumDeskManager()
        assert m.commit(["SOL"])["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        out = m.commit(["SOL"])
        assert out["ok"] and m.commit(["SOL"])["reason"] == "commit_in_progress"
        await m._commit_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(scenario())
    assert status["commit"]["done"] and status["commit"]["result"]["entries"] == ["SOL"]
    assert [h.pos.base for h in r.holdings] == ["SOL"]


def test_dashboard_renders_preview_with_scenarios_and_commit_form():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "venues": ["bitvavo", "okx"],
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [],
        "risk": {"entries_allowed": True},
        "last_regime": {},
        "cash_eur": 4000.0,
    }
    preview = {
        "at": "2026-09-07T17:45:00+00:00",
        "ok": True,
        "btc_ret": -0.0089,
        "breadth": 0.625,
        "reasons": [],
        "risk_block": "",
        "alphai": {"macro_caution": True, "avoid": ["ETH"], "picks": []},
        "rejected": [{"base": "DOT", "excess": 0.12, "from_high": -0.02, "why": "far_from_high"}],
        "planned": [
            {
                "base": "FET",
                "venue": "bitvavo",
                "clip_eur": 420.0,
                "price": 0.5,
                "qty": 840.0,
                "fee_in_eur": 0.63,
                "fee_out_eur": 0.63,
                "break_even": 0.5015,
                "hard_stop_price": 0.485,
                "hard_stop_eur": -13.86,
                "hard_stop_pct": 0.03,
                "trail_pct": 0.03,
                "reasons": ["excess=+0.08", "macro_reduce"],
            }
        ],
    }
    html = render_momentum_dashboard(status, [], preview=preview).body.decode()
    assert "Simulatie" in html and "FET" in html and "exit +5%" in html
    # +5% on 420 minus 1.26 fees = +19.74; hard stop -3% = -13.86
    assert "+19.74 €" in html and "-13.86 €" in html
    assert 'action="/live/momentum/commit?bases=FET&amp;at=2026-09-07T17:45:00+00:00"' in html
    assert "echt geld" in html and 'http-equiv="refresh"' not in html
    assert "<script" not in html
    # Commit in progress hides the button and shows the notice.
    status["commit"] = {"started_at": "2026-09-07T17:50:00+00:00", "bases": ["FET"], "done": False}
    html = render_momentum_dashboard(status, [], preview=preview).body.decode()
    assert "Uitvoering bezig" in html and "/live/momentum/commit?" not in html
    # Empty preview: nothing to commit, but the simulate button stays.
    html = render_momentum_dashboard(
        status, [], preview={**preview, "planned": [], "ok": False, "reasons": ["btc_weak"]}
    ).body.decode()
    assert "niets kopen" in html and "btc_weak" in html and "Simuleer beslissing nu" in html


def test_dashboard_renders_positions_decision_and_ledger():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "dry_run": False,
        "venue": "bitvavo",
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [
            {
                "holding_id": "h-dot",
                "base": "DOT",
                "entry_price": 3.2,
                "quantity": 156.25,
                "notional_eur": 500,
                "age_h": 2.5,
                "peak_return": 0.055,
                "mark": 3.3,
                "gross_return": 0.03125,
                "unrealized_net_eur": 14.1,
                "entry_reason": "excess=+0.09",
            }
        ],
        "risk": {"day_realized_eur": -3.0, "week_realized_eur": 12.0, "entries_allowed": True},
        "last_regime": {
            "at": "2026-09-08T00:00:00+00:00",
            "ok": True,
            "btc_ret": 0.006,
            "breadth": 0.9,
            "reasons": [],
            "candidates": [{"base": "DOT", "excess": 0.09, "from_high": -0.002}],
            "entries": ["DOT"],
            "alphai": {"macro_caution": True, "avoid": ["XRP"], "picks": []},
        },
        "cash_eur": 1500.0,
        "exposure_eur": 515.6,
        "equity_eur": 2015.6,
        "realized_total_eur": 9.0,
        "trade_count": 2,
        "unrealized_net_eur": 14.1,
        "next_decision": "2026-09-09T00:00:00+00:00",
    }
    rows = [
        {
            "ts": "2026-09-08T00:00:40+00:00",
            "event": "entry",
            "base": "DOT",
            "price": 3.2,
            "notional_eur": 500,
            "fee_eur": 0.75,
            "reason": "excess=+0.09",
        },
        {
            "ts": "2026-09-08T01:30:00+00:00",
            "event": "exit",
            "base": "SOL",
            "price": 90.1,
            "notional_eur": 507,
            "fee_eur": 0.76,
            "reason": "trail",
            "gross_return": 0.0156,
            "peak_return": 0.0495,
            "net_eur": 6.3,
        },
    ]
    html = render_momentum_dashboard(status, rows).body.decode()
    assert "LIVE" in html and "REGIME ON" in html
    assert "DOT" in html and "trail" in html and "2,015.60" in html
    # Ratchet active (peak 5.5% >= tight_after) -> tight trail shown.
    assert "(2.0%)" in html or "(2.5%)" in html  # WR pack 2.0%; legacy fixtures 2.5%
    assert "Refill na exit" in html
    assert "Fade ETA" in html
    assert "Exit-ladder" in html
    assert "/live/momentum/status" in html and "Marks live elke 3s" in html
    assert 'data-live="open-pnl"' in html
    # Sell button is a GET to the confirmation step, never a direct POST.
    assert 'name="sell" value="h-dot"' in html and "/live/momentum/sell" not in html
    confirm = render_momentum_dashboard(status, rows, sell="h-dot").body.decode()
    assert "Verkoop bevestigen" in confirm
    assert "<script" not in confirm  # hold still while confirming — no marks poll
    assert 'action="/live/momentum/sell?holding_id=h-dot"' in confirm
    assert "holding_id=h-dot&amp;urgent=1" in confirm
    assert 'http-equiv="refresh"' not in confirm  # page holds still while confirming
    gone = render_momentum_dashboard(status, rows, sell="nope").body.decode()
    assert "niet (meer) gevonden" in gone
    busy = dict(
        status,
        manual_exit={"base": "DOT", "done": False, "started_at": "2026-09-08T01:00:00+00:00"},
    )
    html_busy = render_momentum_dashboard(busy, rows).body.decode()
    assert "Verkoop DOT bezig" in html_busy and "disabled" in html_busy
    done = dict(
        status,
        manual_exit={
            "base": "DOT",
            "done": True,
            "finished_at": "2026-09-08T01:01:00+00:00",
            "result": {"ok": True, "partial": False},
        },
    )
    assert "DOT verkocht om" in render_momentum_dashboard(done, rows).body.decode()


def test_manual_sell_books_exit_and_runs_in_background(tmp_path):
    from bot.live.momentum_runner import MomentumDeskManager

    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 32 * 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=2000.0,
        okx_cash=0.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )

    async def scenario():
        await r.decide_now(execute=True)
        assert [h.pos.base for h in r.holdings] == ["SOL"]
        hid = r.holdings[0].holding_id
        assert r.status()["positions"][0]["holding_id"] == hid
        assert (await r.sell_now("nope"))["reason"] == "unknown_holding"
        m = MomentumDeskManager()
        assert m.sell(hid)["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        assert m.sell("nope")["reason"] == "unknown_holding"
        out = m.sell(hid)
        assert out["ok"] and m.sell(hid)["reason"] == "sell_in_progress"
        await m._sell_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(scenario())
    me = status["manual_exit"]
    assert me["done"] and me["result"]["ok"] and me["result"]["base"] == "SOL"
    assert r.holdings == [] and r.trade_count == 1
    ledger = [json.loads(line) for line in Path(r.opt.ledger_path).read_text().splitlines()]
    exits = [row for row in ledger if row.get("event") == "exit"]
    assert len(exits) == 1 and exits[0]["reason"] == "manual" and exits[0]["base"] == "SOL"
    assert [o["side"] for o in gws["bitvavo"].placed] == ["buy", "sell"]


def test_engine_settings_cap_notional_to_clip():
    from bot.core.config import Settings

    s = Settings(exchange_name="stub", execution_mode="paper")
    cfg = DeskConfig(clip_eur=500.0, alphai_clip_mult=1.3, max_positions=3, strong_clip_mult=1.0)
    out = engine_settings_for_desk(s, cfg, "bitvavo")
    assert out.live_micro_max_notional_eur == pytest.approx(500 * 1.3 * 1.15 + 1)
    # Strong-tape multiplier stacks on the AlphaI multiplier in the cap.
    strong = engine_settings_for_desk(s, cfg.with_overrides(strong_clip_mult=1.3), "bitvavo")
    assert strong.live_micro_max_notional_eur == pytest.approx(500 * 1.3 * 1.3 * 1.15 + 1)
    assert out.live_micro_symbols == "*" and out.live_micro_venues == "bitvavo"
    assert out.live_micro_max_open_orders_per_venue == 4
    multi = engine_settings_for_desk(s, cfg, "Bitvavo, okx,bitvavo")
    assert multi.live_micro_venues == "bitvavo,okx"


# ------------------------------------------------------- multi-venue routing


class CashGateway(FakeGateway):
    def __init__(self, cash: float, **kwargs):
        super().__init__(**kwargs)
        self.cash = cash

    async def quote_balance_eur(self):
        return self.cash


def _multi_runner(tmp_path, clock, bitvavo_cash, okx_cash, feed=None, **cfg_kwargs):
    cfg_kwargs = {**FLAT_SIZING, **cfg_kwargs}
    gws = {
        "bitvavo": CashGateway(bitvavo_cash, fill_maker_after_polls=1),
        "okx": CashGateway(okx_cash, bid=100.05, ask=100.25, fill_maker_after_polls=1),
    }
    cfg = DeskConfig(**cfg_kwargs)
    opts = RunnerOptions(
        venues=("bitvavo", "okx"),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        buy_rest_sec=60.0,
        repeg_sec=20.0,
        poll_sec=5.0,
    )
    r = MomentumDeskRunner(
        cfg,
        None,
        options=opts,
        feed=feed or FakeFeed({}),
        clock=clock,
        sleep=clock.sleep,
        gateways=gws,
    )
    return r, gws


def test_route_prefers_primary_and_overflows_to_second_venue(tmp_path):
    clock = FakeClock(T0 / 1000)
    r, _ = _multi_runner(tmp_path, clock, bitvavo_cash=700.0, okx_cash=1900.0)
    asyncio.run(r._refresh_cash())
    assert r.cash_eur == pytest.approx(2600.0)
    # Primary has the clip -> primary, even though OKX is richer.
    assert r._route_entry(600.0) == ("bitvavo", 600.0)
    r.cash_by_venue["bitvavo"] = 300.0
    # Primary short -> second venue takes the full clip.
    assert r._route_entry(600.0) == ("okx", 600.0)
    # Nobody can fund the clip -> richest venue with leftover cash.
    r.cash_by_venue["okx"] = 400.0
    venue, clip = r._route_entry(600.0)
    assert venue == "okx" and 395.0 < clip < 400.0
    # Residual still meaningful (above absolute floor) even if << planned clip.
    r.cash_by_venue["bitvavo"] = 816.0
    r.cash_by_venue["okx"] = 620.0
    venue, clip = r._route_entry(1690.0)
    assert venue == "bitvavo" and 810.0 < clip <= 816.0
    # Too little everywhere -> skip.
    r.cash_by_venue["bitvavo"] = 40.0
    r.cash_by_venue["okx"] = 50.0
    assert r._route_entry(1690.0) is None


def test_multi_venue_entry_and_exit_use_position_venue(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=200.0,
        okx_cash=1900.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    asyncio.run(r.tick())
    assert [h.pos.venue for h in r.holdings] == ["okx"]
    assert gws["bitvavo"].placed == [] and gws["okx"].placed
    assert r.cash_by_venue["okx"] < 1900.0 - 499.0
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "entry"' in ledger[-1] and '"venue": "okx"' in ledger[-1]
    status = r.status()
    assert status["positions"][0]["venue"] == "okx" and status["venues"] == ["bitvavo", "okx"]
    # Restart keeps the venue on the position; the exit goes to that venue.
    r2, gws2 = _multi_runner(tmp_path, clock, bitvavo_cash=200.0, okx_cash=1400.0, clip_eur=500.0)
    assert r2.holdings[0].pos.venue == "okx"
    fill = asyncio.run(r2._exit(r2.holdings[0], ExitDecision("trail", 0.01, False)))
    assert fill is not None and fill.qty > 0
    assert gws2["bitvavo"].placed == [] and [p["side"] for p in gws2["okx"].placed] == ["sell"]


def test_entry_skipped_when_no_venue_can_fund(tmp_path):
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=40.0,
        okx_cash=50.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
    )
    asyncio.run(r.tick())
    assert r.holdings == [] and not gws["bitvavo"].placed and not gws["okx"].placed
    ledger = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert '"event": "entry_skipped"' in ledger[-1] and "insufficient_cash" in ledger[-1]


def test_entry_uses_residual_cash_on_richest_venue(tmp_path):
    """After preferred venues can't fund the full clip, still buy with leftover."""
    cfg, candles = _universe({"SOL": 0.06, "LINK": 0.0}, 0.0)
    clock = FakeClock((T0 + 60_000) / 1000)
    r, gws = _multi_runner(
        tmp_path,
        clock,
        bitvavo_cash=816.0,
        okx_cash=620.0,
        feed=FakeFeed(candles),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=1690.0,
    )
    asyncio.run(r.tick())
    assert len(r.holdings) == 1
    h = r.holdings[0]
    assert h.pos.base == "SOL"
    assert h.pos.venue == "bitvavo"
    assert 800.0 <= h.pos.notional_eur <= 816.0
    assert "clip_reduced" in (h.pos.entry_reason or "")
    assert gws["okx"].placed == []
    assert gws["bitvavo"].placed


def test_resume_flag_round_trips_venues(tmp_path):
    from bot.live.momentum_runner import _read_flag, _write_flag

    state = str(tmp_path / "state.json")
    _write_flag(state, running=True, dry_run=False, venue="bitvavo", venues=["bitvavo", "okx"])
    flag = _read_flag(state)
    assert flag["venues"] == ["bitvavo", "okx"] and flag["venue"] == "bitvavo"
    from bot.live.momentum_runner import parse_venues

    assert parse_venues(flag["venues"]) == ("bitvavo", "okx")
    assert parse_venues("") == ("bitvavo",)


# ------------------------------------------------------- AlphaI on the exit side


def test_alphai_avoid_tightens_trail_but_does_not_dump():
    cfg = DeskConfig(trail_pct=0.03, trail_tight_after=0.0, trail_tight_pct=0.015)
    bearish = AlphaIView(avoid=frozenset({"SOL"}))
    # Peak 104, close 102.5: -1.44% from peak -> inside both trails, hold.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 102.4, 102.5, 1], cfg, alphai=bearish) is None
    # -2.0% from peak: normal trail (3%) holds, AlphaI-tightened trail (1.5%) exits.
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg) is None
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    d = evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg, alphai=bearish)
    assert d is not None and d.reason == "trail_alphai" and not d.urgent
    # A bearish headline on another base changes nothing.
    pos = Position("LINK", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], cfg, alphai=bearish) is None
    # Switch off -> plain trail semantics.
    off = cfg.with_overrides(alphai_avoid_tightens_trail=False)
    pos = Position("SOL", 100.0, 5.0, 500.0, T0, 104.0)
    assert evaluate_exit(pos, [T0, 103, 104, 101.8, 101.92, 1], off, alphai=bearish) is None


def test_from_exchange_order_nets_fee_charged_in_base():
    from types import SimpleNamespace

    from bot.live.momentum_runner import _from_exchange_order

    order = SimpleNamespace(
        id="1",
        status="filled",
        filled_quantity=956.5779,
        average_price=1.2367,
        fee_cost=1.9131558,
        fee_currency="XRP",
    )
    st = _from_exchange_order(order, "XRPEUR")
    assert st.fee_base_qty == pytest.approx(1.9131558)
    assert st.fee_eur == pytest.approx(1.9131558 * 1.2367)
    assert st.filled_qty == pytest.approx(956.5779)


def test_sell_clamps_to_free_balance_when_fee_was_taken_in_base(tmp_path):
    """OKX credits fill_qty - fee_in_base; selling the book qty used to fail."""
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding

    clock = FakeClock((T0 + 60_000) / 1000)
    # Venue only has 4.9 of the 5.0 the book thinks it holds.
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"SOL": 4.9})
    r = _runner(tmp_path, gw, clock, universe=("SOL",), clip_eur=500.0, min_volume_eur=0.0)
    r.holdings = [
        Holding(
            pos=Position("SOL", 100.0, 5.0, 500.0, T0, 100.0, venue="bitvavo"),
            holding_id="h1",
        )
    ]
    r.marks["SOL"] = 100.0
    r._gws = {"bitvavo": gw}

    async def go():
        return await r.sell_now("h1", urgent=True)

    out = asyncio.run(go())
    assert out["ok"] and out["sold_qty"] == pytest.approx(4.9)
    assert r.holdings == []
    assert gw.placed[-1]["side"] == "sell" and gw.placed[-1]["qty"] == pytest.approx(4.9)


def test_exit_dust_or_no_balance_drops_ghost_holding(tmp_path):
    """Venue free=0 must not leave a qty=0 ghost polluting open PnL."""
    from bot.live.momentum_desk import ExitDecision, Position
    from bot.live.momentum_runner import Holding

    clock = FakeClock((T0 + 60_000) / 1000)
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"FET": 0.0})
    r = _runner(tmp_path, gw, clock, universe=("FET",), clip_eur=500.0, min_volume_eur=0.0)
    r.holdings = [
        Holding(
            pos=Position(
                "FET",
                0.14,
                0.0,
                0.0,
                T0,
                0.14,
                entry_fee_eur=40.47,
                venue="bitvavo",
            ),
            holding_id="ghost1",
        )
    ]
    r.marks["FET"] = 0.145
    r._gws = {"bitvavo": gw}
    before = r.realized_total_eur

    async def go():
        return await r._exit(r.holdings[0], ExitDecision("fade_fast", -0.01, urgent=True))

    fill = asyncio.run(go())
    assert fill is None
    assert r.holdings == []
    assert r.realized_total_eur == pytest.approx(before)  # no phantom fee loss
    st = r.status()
    assert st["positions"] == []
    assert st["unrealized_net_eur"] == pytest.approx(0.0)
    ledger = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert '"event": "exit"' in ledger and "dust_or_no_balance" in ledger
    assert "exit_failed" not in ledger
    assert not gw.placed


def test_reset_operator_numbers_archives_ledger_and_zeros_counters(tmp_path):
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding

    clock = FakeClock(T0 / 1000)
    gw = FakeGateway()
    r = _runner(tmp_path, gw, clock, universe=("SOL",), clip_eur=500.0, min_volume_eur=0.0)
    r.realized_total_eur = -403.19
    r.trade_count = 14
    r.last_regime = {"entries": ["UNI"]}
    r.ledger.day_realized_eur = -504.0
    r.ledger.week_realized_eur = -484.0
    r.ledger.entries_today = {"UNI": 1}
    r._ledger_append({"event": "exit", "net_eur": -100.0, "base": "UNI"})
    r.holdings = [
        Holding(
            pos=Position(
                "SOL",
                100.0,
                10.0,
                1000.0,
                T0,
                100.0,
                entry_fee_eur=1.0,
                venue="bitvavo",
            ),
            holding_id="keep1",
        )
    ]

    out = r.reset_operator_numbers()
    assert out["ok"] is True
    assert out["prior_realized_total_eur"] == pytest.approx(-403.19)
    assert out["prior_trade_count"] == 14
    assert out["archived_ledger"]
    assert Path(out["archived_ledger"]).exists()
    assert r.realized_total_eur == 0.0
    assert r.trade_count == 0
    assert r.last_regime == {}
    assert r.ledger.day_realized_eur == 0.0
    assert r.ledger.week_realized_eur == 0.0
    assert r.ledger.entries_today == {}
    assert len(r.holdings) == 1
    fresh = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "dashboard_reset" in fresh
    assert '"event": "exit"' not in fresh


def test_reconcile_external_inventory_clears_gone_holding(tmp_path):
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding

    clock = FakeClock((T0 + 60_000) / 1000)
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"SOL": 0.0})
    r = _runner(tmp_path, gw, clock, universe=("SOL",), clip_eur=500.0, min_volume_eur=0.0)
    r.holdings = [
        Holding(
            pos=Position("SOL", 100.0, 5.0, 500.0, T0, 105.0, entry_fee_eur=1.0, venue="bitvavo"),
            holding_id="ext1",
        )
    ]
    r.marks["SOL"] = 110.0
    r._gws = {"bitvavo": gw}

    closed = asyncio.run(r.reconcile_external_inventory())
    assert len(closed) == 1
    assert closed[0]["reason"] == "manual_external"
    assert closed[0]["net_eur"] == pytest.approx(5.0 * (110.0 - 100.0) - 1.0)
    assert r.holdings == []
    assert r.status()["positions"] == []


def test_dashboard_open_positions_render_before_heroes():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "dry_run": False,
        "venue": "bitvavo",
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [
            {
                "holding_id": "h1",
                "base": "SOL",
                "entry_price": 100.0,
                "quantity": 5.0,
                "notional_eur": 500,
                "age_h": 1.0,
                "peak_return": 0.02,
                "mark": 101.0,
                "gross_return": 0.01,
                "unrealized_net_eur": 4.0,
                "entry_reason": "excess=+0.05",
            },
            {
                "holding_id": "ghost",
                "base": "FET",
                "entry_price": 0.14,
                "quantity": 0.0,
                "notional_eur": 0.0,
                "age_h": 1.0,
                "peak_return": 0.0,
                "mark": 0.145,
                "gross_return": 0.0,
                "unrealized_net_eur": -40.0,
                "entry_reason": "ghost",
            },
        ],
        "equity_eur": 20_000.0,
        "exposure_eur": 500.0,
        "unrealized_net_eur": 4.0,
        "risk": {"day_realized_eur": 0.0, "entries_allowed": True},
        "last_regime": {},
        "next_decision": "2026-09-18T07:00:00+00:00",
    }
    html = render_momentum_dashboard(status, []).body.decode()
    pos_i = html.find("Open posities</h2>")
    hero_i = html.find('class="pulse hero-grid"')
    assert pos_i != -1 and hero_i != -1 and pos_i < hero_i
    assert "SOL" in html
    assert 'data-holding="ghost"' not in html
    assert ">FET<" not in html
    assert "positionsChanged" in html
    assert 'class="masthead"' in html
    assert 'class="panel"' in html
    assert "rise-in" in html


def test_sell_all_sells_every_holding(tmp_path):
    clock = FakeClock((T0 + 60_000) / 1000)
    gw = FakeGateway(fill_maker_after_polls=1, free_by_base={"SOL": 5.0, "LINK": 4.0})
    r = _runner(tmp_path, gw, clock, universe=("SOL", "LINK"), clip_eur=500.0, min_volume_eur=0.0)
    from bot.live.momentum_desk import Position
    from bot.live.momentum_runner import Holding, MomentumDeskManager

    r.holdings = [
        Holding(pos=Position("SOL", 100.0, 5.0, 500.0, T0, 100.0), holding_id="a"),
        Holding(pos=Position("LINK", 100.0, 4.0, 400.0, T0, 100.0), holding_id="b"),
    ]
    r.marks = {"SOL": 100.0, "LINK": 100.0}
    r._gws = {"bitvavo": gw}

    async def go():
        m = MomentumDeskManager()
        assert m.sell_all()["reason"] == "not_running"
        m._runner = r
        m._task = asyncio.create_task(asyncio.sleep(10))
        out = m.sell_all(urgent=True)
        assert out["ok"] and m.sell_all()["reason"] == "sell_in_progress"
        await m._sell_task
        m._task.cancel()
        return m.status()

    status = asyncio.run(go())
    me = status["manual_exit"]
    assert me["done"] and me["all"] and me["result"]["sold"] == 2
    assert r.holdings == []


def test_daily_report_flags_missed_hour_and_early_manual():
    from bot.live.momentum_daily_report import build_daily_report, report_as_dict
    from bot.live.momentum_desk import BARS_PER_DAY

    cfg = DeskConfig(
        decision_hours_utc=(7, 13),
        universe=("SOL", "LINK"),
        min_volume_eur=0.0,
        clip_eur=500.0,
        skip_weekend_entries=False,
        strong_clip_mult=1.0,
        weak_clip_mult=1.0,
        max_from_high=0.02,
        # Pin the trail the afternoon dump was written against (~3% from peak).
        trail_pct=0.03,
        trail_tight_after=0.0,
    )
    # Build candles: BTC flat, SOL strong on hour 10 only path.
    n = 3 * BARS_PER_DAY
    start = T0 - 2 * DAY_MS
    candles = {
        "BTC": _series(start, n, 100.0, 0.0),
        "LINK": _series(start, n, 100.0, 0.0),
        "SOL": _series(start, n, 100.0, 0.0),
    }
    # Pump SOL so hour-10 stats show excess and near high.
    sol = candles["SOL"]
    for _i, row in enumerate(sol):
        if T0 <= row[0] < T0 + DAY_MS:
            hours = (row[0] - T0) / 3_600_000
            # Rise through morning; afternoon dump triggers trail after manual exit.
            mult = 1.0 + 0.04 + 0.002 * max(0, hours) if hours < 10 else 1.02
            row[1] = row[2] = row[3] = row[4] = 100.0 * mult
            if hours >= 10:
                row[3] = 100.0 * 1.02  # low
    day = datetime.fromtimestamp(T0 / 1000, UTC).date()
    ledger = [
        {
            "ts": f"{day.isoformat()}T07:00:10+00:00",
            "event": "decision",
            "ok": False,
            "btc_ret": -0.02,
            "breadth": 0.2,
            "reasons": ["btc_weak"],
            "entries": [],
            "candidates": [],
        },
        {
            "ts": f"{day.isoformat()}T07:30:00+00:00",
            "event": "entry",
            "base": "SOL",
            "qty": 5.0,
            "price": 104.0,
            "notional_eur": 520.0,
        },
        {
            "ts": f"{day.isoformat()}T09:00:00+00:00",
            "event": "exit",
            "base": "SOL",
            "qty": 5.0,
            "price": 104.5,
            "net_eur": 1.0,
            "reason": "manual",
            "peak_return": 0.02,
        },
    ]
    report = build_daily_report(
        day=day,
        cfg=cfg,
        candles_by_base=candles,
        ledger_rows=ledger,
        now_ms=T0 + DAY_MS - BAR_MS,
    )
    d = report_as_dict(report)
    assert d["day"] == day.isoformat()
    assert isinstance(d["missed_entries"], list)
    assert d["exits"] and d["entries"]
    sol_ops = [o for o in d["exit_opportunities"] if o["base"] == "SOL"]
    assert sol_ops and sol_ops[0]["kind"] == "early_manual"
    assert sol_ops[0]["auto_net_eur"] is not None
    assert sol_ops[0]["delta_eur"] is not None


def test_dashboard_sell_all_and_report_render():
    from bot.live.momentum_dashboard import render_momentum_dashboard

    status = {
        "running": True,
        "venues": ["bitvavo"],
        "config": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in DeskConfig().__dict__.items()
        },
        "positions": [
            {
                "holding_id": "h1",
                "base": "SOL",
                "venue": "bitvavo",
                "entry_price": 100.0,
                "quantity": 5,
                "peak_return": 0.02,
                "mark": 101.0,
                "gross_return": 0.01,
                "unrealized_net_eur": 4.0,
                "age_h": 1.0,
                "entry_reason": "x",
            }
        ],
        "risk": {"day_realized_eur": 0, "week_realized_eur": 0, "entries_allowed": True},
        "cash_eur": 1000,
        "exposure_eur": 500,
        "equity_eur": 1500,
        "realized_total_eur": 0,
        "trade_count": 0,
        "unrealized_net_eur": 4.0,
        "next_decision": "2026-09-09T13:00:00+00:00",
    }
    html = render_momentum_dashboard(status, []).body.decode()
    assert "sticky-actions" in html and "Daily report" in html and "Verkoop alles" in html
    assert "Moreney" in html and ("Netto verdiend" in html or "Deze week" in html)
    # Volatile sleeve is off by default (core-only desk).
    assert "Volatile ledger" not in html
    assert "Start volatile" not in html
    assert "Desk sleeves" not in html
    assert "/live/momentum/earnings" in html
    assert "pos-cards" in html and 'name="sell" value="h1"' in html
    with_vol = render_momentum_dashboard(
        status, [], show_volatile=True, volatile={"running": False}, volatile_ledger_rows=[]
    ).body.decode()
    assert "Volatile ledger" in with_vol and "Desk sleeves" in with_vol
    confirm = render_momentum_dashboard(status, [], sell_all=True).body.decode()
    assert "Alles verkopen?" in confirm and "/live/momentum/sell-all" in confirm
    report = {
        "day": "2026-09-09",
        "summary": "test",
        "realized_net_eur": 12.0,
        "decisions": [],
        "entries": [],
        "exits": [],
        "missed_entries": [
            {
                "hour_utc": 16,
                "bases": ["DOT"],
                "btc_ret": 0.01,
                "breadth": 0.8,
                "scheduled": False,
                "note": "buiten schema",
                "hypothetical": [
                    {"base": "DOT", "net_eur": 20.0, "reason": "trail", "status": "closed"}
                ],
            }
        ],
        "exit_opportunities": [],
    }
    rep = render_momentum_dashboard(status, [], report=report).body.decode()
    assert "Gemiste instappen" in rep and "DOT" in rep and 'http-equiv="refresh"' not in rep


def test_decide_resizes_clip_to_venue_cash_before_plan(tmp_path):
    """Decide path rewrites entry clips to what venues can actually fund."""
    clock = FakeClock(T0 / 1000)
    r, _ = _multi_runner(tmp_path, clock, bitvavo_cash=816.0, okx_cash=620.0)
    asyncio.run(r._refresh_cash())
    from bot.live.momentum_desk import Entry

    # Simulate select_entries output, then apply the same resize block as _decide.
    entries = [
        Entry(base="ETH", clip_eur=1690.0, score=0.05, reasons=("alphai_pick",)),
        Entry(base="LINK", clip_eur=500.0, score=0.04, reasons=("excess=+0.0400",)),
    ]
    sized = []
    for entry in entries:
        route = r._route_entry(entry.clip_eur)
        if route is None:
            continue
        _venue, clip = route
        reasons = entry.reasons
        if clip + 1e-9 < entry.clip_eur:
            reasons = tuple(reasons) + ("clip_reduced",)
        sized.append(
            Entry(base=entry.base, clip_eur=round(clip, 2), score=entry.score, reasons=reasons)
        )
    assert sized[0].base == "ETH"
    assert "clip_reduced" in sized[0].reasons
    assert 810.0 < sized[0].clip_eur <= 816.0
    # LINK fits on primary → unchanged clip, no resize tag.
    assert sized[1].clip_eur == 500.0
    assert "clip_reduced" not in sized[1].reasons


def test_refresh_marks_uses_holding_venue_bbo_not_bitvavo_ticker(tmp_path):
    """OKX inventory must be marked from the OKX book, not Bitvavo tape."""
    from bot.live.momentum_runner import Holding

    class TrapFeed:
        async def last_price(self, base):
            return 999.0

        async def candles(self, base, limit):
            return [[T0, 999.0, 999.0, 999.0, 999.0, 1.0]]

    clock = FakeClock(T0 / 1000)
    okx = FakeGateway(bid=50.0, ask=50.2)
    bitvavo = FakeGateway(bid=1.0, ask=1.1)
    cfg = DeskConfig(**FLAT_SIZING)
    opts = RunnerOptions(
        venues=("bitvavo", "okx"),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
    )
    r = MomentumDeskRunner(
        cfg,
        bitvavo,
        options=opts,
        feed=TrapFeed(),
        clock=clock,
        sleep=clock.sleep,
        gateways={"okx": okx},
    )
    r.holdings = [
        Holding(
            Position("SOL", 48.0, 10.0, 480.0, T0, 48.0, venue="okx"),
            "h-okx",
            T0,
        )
    ]
    asyncio.run(r.refresh_marks())
    assert r.marks["SOL"] == pytest.approx(50.1)
    assert r.mark_sources["SOL"] == "okx_bbo"
    st = r.status()
    assert st["positions"][0]["mark"] == pytest.approx(50.1)
    assert st["positions"][0]["mark_source"] == "okx_bbo"


def test_manage_exits_fires_fade_fast_urgent_sell(tmp_path):
    """Dense venue marks → fade_fast urgent exit without waiting for a 15m bar."""
    from bot.live.momentum_runner import Holding

    class QuietFeed:
        async def last_price(self, base):
            return None

        async def candles(self, base, limit):
            # Mid-bar: not bar_ready for trail; fade must still fire.
            return [[T0, 100.0, 103.0, 99.0, 101.0, 1.0]]

    clock = FakeClock(T0 / 1000 + 60.0)  # 60s into the forming bar
    gw = FakeGateway(bid=102.4, ask=102.6)
    cfg = DeskConfig(
        fade_eta_sec=120.0,
        fade_confirm_sec=8.0,
        fade_smooth_sec=20.0,
        fade_min_peak_eur=10.0,
        fade_min_giveback_eur=3.0,
        hard_stop_pct=0.20,
        trail_pct=0.20,
        green_deadline_hours=0.0,
        fee_rt=0.003,
        **FLAT_SIZING,
    )
    opts = RunnerOptions(
        venues=("bitvavo",),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        sell_rest_sec=1.0,
        sell_slice_eur=0.0,
    )
    r = MomentumDeskRunner(
        cfg, gw, options=opts, feed=QuietFeed(), clock=clock, sleep=clock.sleep
    )
    r.holdings = [
        Holding(
            Position("X", 100.0, 13.0, 1300.0, T0, 100.0, entry_fee_eur=1.95, venue="bitvavo"),
            "h-fade",
            last_bar_ms=T0 - BAR_MS,
        )
    ]

    async def scenario():
        await r.refresh_marks()
        await r._manage_exits(int(clock() * 1000))
        assert r.holdings and r.holdings[0].fade.peak_net_eur >= 10.0
        # Crash the book toward flat while still green.
        gw.bid, gw.ask = 100.9, 101.1
        clock.t += 4.0
        await r.refresh_marks()
        await r._manage_exits(int(clock() * 1000))
        assert r.holdings[0].fade.breach_since is not None
        gw.bid, gw.ask = 100.7, 100.9
        clock.t += 9.0
        await r.refresh_marks()
        await r._manage_exits(int(clock() * 1000))

    asyncio.run(scenario())
    assert r.holdings == []
    ledger = [json.loads(line) for line in Path(r.opt.ledger_path).read_text().splitlines()]
    exits = [row for row in ledger if row.get("event") == "exit"]
    assert len(exits) == 1 and exits[0]["reason"] == "fade_fast"
    assert any(not o["post_only"] for o in gw.placed)  # urgent taker



def test_refill_on_exit_triggers_decide_outside_hours(tmp_path):
    """After an exit frees a slot, refill decides immediately even off-hour."""
    from bot.live.momentum_runner import Holding

    clock = FakeClock(T0 / 1000 + 10 * 3600 + 30 * 60)  # Thu 10:30 UTC — not 7/13/16
    gw = FakeGateway(bid=110.0, ask=110.2, fill_maker_after_polls=1)
    cfg = DeskConfig(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        refill_on_exit=True,
        max_positions=2,
        hard_stop_pct=0.20,
        trail_pct=0.20,
        **FLAT_SIZING,
    )
    opts = RunnerOptions(
        venues=("bitvavo",),
        state_path=str(tmp_path / "state.json"),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        alphai_recommendations_path=None,
        sell_rest_sec=1.0,
        sell_slice_eur=0.0,
    )
    r = MomentumDeskRunner(cfg, gw, options=opts, clock=clock, sleep=clock.sleep)
    r.holdings = [
        Holding(
            Position(
                "SOL",
                100.0,
                10.0,
                1000.0,
                T0,
                112.0,
                entry_fee_eur=1.5,
                venue="bitvavo",
            ),
            "h-refill",
            last_bar_ms=T0,
        )
    ]
    calls: list[str] = []

    async def fake_decide(t_ms, now_ms, *, execute, trigger, expect_bases=None):
        calls.append(trigger)
        return {"ok": True, "trigger": trigger}

    r._decide = fake_decide  # type: ignore[method-assign]

    async def scenario():
        fill = await r._exit(r.holdings[0], ExitDecision("trail", 0.10, False))
        assert fill is not None
        assert r.holdings == []
        assert r._refill_pending is True
        # Off-hour: scheduled decide must not fire; refill must.
        assert r._decision_slot_due(int(clock() * 1000)) is None
        await r._maybe_refill(int(clock() * 1000))
        assert calls == ["refill"]
        assert r._refill_pending is False

    asyncio.run(scenario())


def test_alphai_conviction_size_overlay_scales_clip_without_changing_membership():
    """Conviction sizing shrinks weak/mixed picks but keeps the same entry set."""
    from datetime import UTC, datetime

    cfg, candles = _universe({"ETH": 0.05, "SOL": 0.045, "LINK": 0.04}, btc_ret=0.01)
    cfg = cfg.with_overrides(
        min_volume_eur=0.0,
        clip_eur=1000.0,
        alphai_clip_mult=1.3,
        alphai_clip_mult_min=1.0,
        alphai_size_mode="conviction",
        strong_clip_mult=1.0,
        weak_clip_mult=1.0,
        max_positions=3,
        top_n=3,
        top_n_broad=3,
    )
    alts = universe_stats(candles, T0, cfg)
    regime = classify_regime(bar_stats("BTC", candles["BTC"], T0), alts, cfg)
    assert regime.ok
    payload = {
        "generated_at": datetime.fromtimestamp(T0 / 1000, UTC).isoformat(),
        "macro_caution": False,
        "picks": [
            {
                "base": "ETH",
                "score": 40,
                "rank": 1,
                "bullish_headlines": ["a", "b", "c"],
                "bearish_headlines": [],
            },
            {
                "base": "SOL",
                "score": 10,
                "rank": 2,
                "bullish_headlines": ["a"],
                "bearish_headlines": ["b", "c"],
            },
        ],
        "price_confirm_scales": {"ETH": 1.0, "SOL": 0.4},
    }
    view = AlphaIView.from_recommendations(payload)
    cands = rank_candidates(alts, regime.btc_ret or 0.0, cfg, alphai=view)
    entries = select_entries(
        cands, regime, cfg, held_bases=[], alphai=view, now_ms=T0
    )
    by = {e.base: e for e in entries}
    assert set(by) >= {"ETH", "SOL"}
    assert by["ETH"].clip_eur > by["SOL"].clip_eur
    assert by["ETH"].clip_eur <= 1300.0
    assert by["SOL"].clip_eur >= 1000.0
    assert any(r.startswith("alphai_conv=") for r in by["SOL"].reasons)

    binary = cfg.with_overrides(alphai_size_mode="binary")
    entries_b = select_entries(cands, regime, binary, held_bases=[], alphai=view, now_ms=T0)
    assert {e.base for e in entries_b} == {e.base for e in entries}
    assert all(e.clip_eur == pytest.approx(1300.0) for e in entries_b if e.base in view.picks)


def test_alphai_stale_board_disables_size_boost():
    cfg = DeskConfig(alphai_size_mode="conviction", alphai_clip_mult=1.3, alphai_stale_minutes=45.0)
    from datetime import UTC, datetime

    gen = datetime.fromtimestamp(T0 / 1000, UTC).isoformat()
    view = AlphaIView.from_recommendations(
        {
            "generated_at": gen,
            "picks": [{"base": "ETH", "score": 50, "rank": 1}],
        }
    )
    fresh = alphai_entry_clip_mult("ETH", view, cfg, now_ms=T0 + 5 * 60_000)
    assert fresh[0] == pytest.approx(1.3)
    stale_ms = T0 + 2 * 60 * 60 * 1000
    stale = alphai_entry_clip_mult("ETH", view, cfg, now_ms=stale_ms)
    assert stale[0] == pytest.approx(1.0)
    assert "alphai_stale" in stale[1]


def test_summarize_regime_pnl_buckets_by_entry_label():
    rows = [
        {"net_eur": 10.0, "hold_h": 2.0, "entry_ctx": {"regime_label": "strong"}},
        {"net_eur": -5.0, "hold_h": 1.0, "entry_ctx": {"regime_label": "soft"}},
        {"net_eur": 3.0, "hold_h": 4.0, "entry_ctx": {"regime_label": "soft"}},
        {"net_eur": -2.0, "hold_h": 1.0, "entry_reason": "soft_regime,alphai_pick"},
    ]
    out = summarize_regime_pnl(rows)
    assert out["strong"]["n"] == 1 and out["strong"]["net_eur"] == 10.0
    assert out["soft"]["n"] == 3 and out["soft"]["net_eur"] == -4.0
    assert out["soft"]["wins"] == 1
