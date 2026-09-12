"""Volatile shadow — AlphaI-first paper book, independent of the core 16."""

from __future__ import annotations

import json

from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, AlphaIView, DeskConfig
from bot.live.momentum_volatile_shadow import (
    VolatileShadowConfig,
    build_volatile_shadow,
    render_volatile_shadow_html,
    shadow_config,
    volatile_universe,
)

DAY_MS = 86_400_000
T0 = 1_780_000_000_000 // DAY_MS * DAY_MS


def test_volatile_universe_excludes_core():
    uni = volatile_universe()
    assert uni
    assert not set(uni) & set(DEFAULT_UNIVERSE)
    assert "HYPE" in uni and "WLD" in uni


def test_shadow_config_is_not_core_desk():
    cfg = shadow_config(
        DeskConfig(
            decision_hours_utc=(7, 13),
            clip_eur=1300.0,
            book_eur=4000.0,
            min_volume_eur=1_000_000.0,
            max_from_high=0.02,
        )
    )
    assert isinstance(cfg, VolatileShadowConfig)
    assert set(cfg.universe) == set(volatile_universe())
    assert cfg.require_alphai_green is True
    assert cfg.clip_eur == 650.0
    assert cfg.alphai_flip_exits is True
    assert cfg.max_positions == 1
    assert cfg.min_alphai_score == 30.0
    assert cfg.hard_stop_pct == 0.05
    assert cfg.time_exit_hours_green == 48.0
    assert cfg.conviction_sizing is True
    assert cfg.decision_hours_utc == (7, 13, 16)
    assert cfg.book_eur == 2000.0
    assert cfg.min_volume_eur == 400_000.0
    assert cfg.max_from_high == 0.05  # softer than core 2%
    assert cfg.alphai_clip_mult == 1.4
    assert set(cfg.universe) <= set(cfg.clusters)


def test_load_shadow_alphai_filters_to_volatile_pool(tmp_path):
    from bot.live.momentum_volatile_shadow import load_shadow_alphai

    path = tmp_path / "alphai.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-10T12:00:00+00:00",
                "macro_caution": True,
                "picks": [
                    {"base": "WLD", "score": 40},
                    {"base": "ADA", "score": 50},
                    {"base": "LINK", "score": 30},
                ],
                "avoid": [{"base": "HYPE", "score": -20}],
                "watch": [{"base": "ONDO", "score": 5}, {"base": "HYPE", "score": 1}],
            }
        ),
        encoding="utf-8",
    )
    view, meta = load_shadow_alphai(path)
    assert view.macro_caution is True
    assert "WLD" in view.picks and "ONDO" in view.picks
    assert "ADA" not in view.picks and "LINK" not in view.picks
    assert "HYPE" in view.avoid and "HYPE" not in view.picks
    assert "ADA" in meta["core_alphai_ignored"] and "LINK" in meta["core_alphai_ignored"]
    assert meta["loaded"] is True


def _candles(uni: tuple[str, ...]) -> dict[str, list]:
    n = 4 * 96
    start = T0 - DAY_MS
    out: dict[str, list] = {"BTC": []}
    for b in uni:
        out[b] = []
    for i in range(n):
        ts = start + i * BAR_MS
        hours = (ts - T0) / 3_600_000
        out["BTC"].append([ts, 100.0, 100.2, 99.8, 100.0, 5e6])
        for b in uni:
            if b == "WLD":
                px = 5.0 * (1.06 if ts >= T0 else 1.0)
                if ts >= T0 and hours < 20:
                    px = 5.0 * (1.06 + 0.002 * max(0.0, hours))
            else:
                px = 10.0 * (1.01 if ts >= T0 else 1.0)
            out[b].append([ts, px, px * 1.001, px * 0.999, px, 3e6])
    return out


def test_build_volatile_shadow_buys_only_alphai_green(monkeypatch):
    from bot.live import momentum_volatile_shadow as mod

    uni = ("HYPE", "WLD")
    monkeypatch.setattr(mod, "VOLATILE_POOL", uni)
    monkeypatch.setattr(
        mod,
        "load_shadow_alphai",
        lambda path=None: (
            AlphaIView(picks=frozenset({"WLD"}), avoid=frozenset({"HYPE"}), macro_caution=False),
            {
                "loaded": True,
                "generated_at": "test",
                "effective_picks": ["WLD"],
                "effective_avoid": ["HYPE"],
                "core_alphai_ignored": ["ADA"],
                "macro_caution": False,
                "note": "test",
                "picks": [],
                "avoid": [],
                "watch": [],
                "scores": {"WLD": 40.0},
            },
        ),
    )
    monkeypatch.setattr(mod, "load_candles", lambda *a, **k: _candles(uni))

    payload = build_volatile_shadow(
        days=2,
        end_ms=T0 + 2 * DAY_MS,
        live_cfg=DeskConfig(
            decision_hours_utc=(7, 13),
            clip_eur=1300.0,
            book_eur=4000.0,
            min_volume_eur=0.0,
            skip_weekend_entries=False,
        ),
    )
    assert payload["ok"] and payload["shadow"]
    assert payload["mode"] == "alphai_first_volatile"
    assert payload["config"]["require_alphai_green"] is True
    assert "WLD" in payload["alphai"]["effective_picks"]
    buys = [b["base"] for d in payload["days"] for b in d["would_buy"]]
    assert "WLD" in buys
    assert "HYPE" not in buys
    html = render_volatile_shadow_html(payload)
    assert "los van" in html.lower() or "AlphaI-first" in html
    assert "SHADOW" in html


def test_no_alphai_green_means_no_buys(monkeypatch):
    from bot.live import momentum_volatile_shadow as mod

    uni = ("HYPE", "WLD")
    monkeypatch.setattr(mod, "VOLATILE_POOL", uni)
    monkeypatch.setattr(
        mod,
        "load_shadow_alphai",
        lambda path=None: (
            AlphaIView(picks=frozenset(), avoid=frozenset(), macro_caution=True),
            {
                "loaded": True,
                "generated_at": "test",
                "effective_picks": [],
                "effective_avoid": [],
                "core_alphai_ignored": ["ADA", "LINK"],
                "macro_caution": True,
                "note": "core only",
                "picks": [],
                "avoid": [],
                "watch": [],
                "scores": {},
            },
        ),
    )
    monkeypatch.setattr(mod, "load_candles", lambda *a, **k: _candles(uni))

    payload = build_volatile_shadow(
        days=2,
        end_ms=T0 + 2 * DAY_MS,
        live_cfg=DeskConfig(decision_hours_utc=(7, 13), skip_weekend_entries=False),
    )
    buys = [b for d in payload["days"] for b in d["would_buy"]]
    assert buys == []
    assert payload["summary"]["trades"] == 0
    assert payload["now"]["block"] == "no_alphai_volatile_picks"


def test_smart_exit_flips_on_alphai_avoid():
    from bot.live.momentum_desk import AlphaIView, Position
    from bot.live.momentum_volatile_shadow import VolatileShadowConfig, _evaluate_volatile_exit

    cfg = VolatileShadowConfig(alphai_flip_exits=True)
    pos = Position(
        base="RAY",
        entry_price=1.0,
        quantity=10,
        notional_eur=10,
        opened_ms=0,
        peak=1.05,
    )
    bar = [0, 1.0, 1.05, 0.95, 1.02, 1e6]
    alphai = AlphaIView(picks=frozenset(), avoid=frozenset({"RAY"}), macro_caution=False)
    decision = _evaluate_volatile_exit(pos, bar, cfg, alphai)
    assert decision is not None
    assert decision.reason == "alphai_flip"
    assert decision.urgent is True


def test_rank_rejects_chase_extended():
    from bot.live.momentum_desk import AlphaIView, BaseStats
    from bot.live.momentum_volatile_shadow import VolatileShadowConfig, _rank_volatile

    cfg = VolatileShadowConfig(
        require_alphai_green=True,
        min_alphai_score=0.0,
        weak_score_needs_excess=0.0,
        max_chase_ret_24h=0.12,
        prefer_pullback_from_high=0.008,
        alphai_scores={"RAY": 80.0},
    )
    alphai = AlphaIView(picks=frozenset({"RAY"}), avoid=frozenset(), macro_caution=False)
    stats = {
        "RAY": BaseStats(
            base="RAY",
            price=1.0,
            ret_24h=0.20,
            from_high=-0.001,
            volume_eur=5_000_000.0,
        )
    }
    cands, rejected = _rank_volatile(stats, 0.0, cfg, alphai)
    assert cands == []
    assert rejected
    why = rejected[0]["why"]
    assert any("chase" in w for w in why)


def test_conviction_sizing_scales_clip():
    from bot.live.momentum_desk import AlphaIView
    from bot.live.momentum_volatile_shadow import (
        VolatileShadowConfig,
        _select_volatile,
        _VolCandidate,
    )

    cfg = VolatileShadowConfig(
        clip_eur=650.0,
        alphai_clip_mult=1.0,
        conviction_sizing=True,
        strong_alphai_score=60.0,
        max_positions=2,
        top_n=2,
    )
    alphai = AlphaIView(picks=frozenset({"RAY", "ENA"}), avoid=frozenset(), macro_caution=False)
    strong = _VolCandidate("RAY", 0.02, 0.03, -0.01, 1e6, 80.0, 100.0, ("alphai_green",))
    weak = _VolCandidate("ENA", 0.02, 0.03, -0.01, 1e6, 20.0, 50.0, ("alphai_green",))
    planned = _select_volatile([strong, weak], cfg, held=set(), blocked=set(), alphai=alphai)
    by = {p["base"]: p["clip_eur"] for p in planned}
    assert by["RAY"] > by["ENA"]


def test_rank_rejects_fee_thin_excess():
    """Marginal RS vs BTC below min_excess is rejected (fee-drag guard)."""
    from bot.live.momentum_desk import AlphaIView, BaseStats
    from bot.live.momentum_volatile_shadow import VolatileShadowConfig, _rank_volatile

    cfg = VolatileShadowConfig(
        require_alphai_green=True,
        min_alphai_score=0.0,
        weak_score_needs_excess=0.0,
        min_excess=0.008,
        max_chase_ret_24h=0.50,
        prefer_pullback_from_high=0.0,
        alphai_scores={"RAY": 80.0},
        min_volume_eur=0.0,
        min_ret_24h=-1.0,
        max_from_high=1.0,
    )
    alphai = AlphaIView(picks=frozenset({"RAY"}), avoid=frozenset(), macro_caution=False)
    stats = {
        "RAY": BaseStats(
            base="RAY",
            price=1.0,
            ret_24h=0.004,
            from_high=-0.01,
            volume_eur=5_000_000.0,
        )
    }
    cands, rejected = _rank_volatile(stats, 0.0, cfg, alphai)
    assert cands == []
    assert any("excess_low" in w for w in rejected[0]["why"])


def test_rank_accepts_excess_above_fee_floor():
    from bot.live.momentum_desk import AlphaIView, BaseStats
    from bot.live.momentum_volatile_shadow import VolatileShadowConfig, _rank_volatile

    cfg = VolatileShadowConfig(
        require_alphai_green=True,
        min_alphai_score=0.0,
        min_excess=0.008,
        max_chase_ret_24h=0.50,
        prefer_pullback_from_high=0.0,
        alphai_scores={"RAY": 80.0},
        min_volume_eur=0.0,
        min_ret_24h=-1.0,
        max_from_high=1.0,
    )
    alphai = AlphaIView(picks=frozenset({"RAY"}), avoid=frozenset(), macro_caution=False)
    stats = {
        "RAY": BaseStats(
            base="RAY",
            price=1.0,
            ret_24h=0.02,
            from_high=-0.01,
            volume_eur=5_000_000.0,
        )
    }
    cands, _rejected = _rank_volatile(stats, 0.0, cfg, alphai)
    assert len(cands) == 1 and cands[0].base == "RAY"
