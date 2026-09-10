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
