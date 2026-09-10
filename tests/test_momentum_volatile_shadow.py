"""Volatile shadow paper sim — would-have buys outside the core 16."""

from __future__ import annotations

from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, DeskConfig
from bot.live.momentum_volatile_shadow import (
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


def test_shadow_config_points_at_volatile_pool():
    cfg = shadow_config(
        DeskConfig(
            decision_hours_utc=(7, 13),
            clip_eur=1300.0,
            book_eur=4000.0,
            min_volume_eur=1_000_000.0,
        )
    )
    assert set(cfg.universe) == set(volatile_universe())
    assert cfg.clip_eur == 1300.0
    assert cfg.min_volume_eur == 500_000.0
    assert set(cfg.universe) <= set(cfg.clusters)


def test_build_volatile_shadow_reports_days(monkeypatch):
    from bot.live import momentum_volatile_shadow as mod

    uni = ("HYPE", "WLD")
    monkeypatch.setattr(mod, "VOLATILE_POOL", uni)

    n = 4 * 96
    start = T0 - DAY_MS
    btc, hype, wld = [], [], []
    for i in range(n):
        ts = start + i * BAR_MS
        hours = (ts - T0) / 3_600_000
        btc.append([ts, 100.0, 100.2, 99.8, 100.0, 5e6])
        if ts < T0:
            px = 10.0
        elif hours < 20:
            px = 10.0 * (1.06 + 0.002 * max(0.0, hours))
        else:
            px = 10.0 * 1.01
        hype.append([ts, px, px * 1.001, px * 0.999, px, 3e6])
        wld.append([ts, 5.0, 5.01, 4.99, 5.0, 3e6])

    candles = {"BTC": btc, "HYPE": hype, "WLD": wld}
    monkeypatch.setattr(mod, "load_candles", lambda *a, **k: candles)

    payload = build_volatile_shadow(
        days=2,
        end_ms=T0 + 2 * DAY_MS,
        live_cfg=DeskConfig(
            decision_hours_utc=(7, 13),
            clip_eur=500.0,
            book_eur=2000.0,
            min_volume_eur=0.0,
            skip_weekend_entries=False,
            day_loss_limit_eur=500.0,
            week_loss_limit_eur=2000.0,
            universe=uni,
        ),
    )
    assert payload["ok"] and payload["shadow"]
    assert payload["universe"] == list(uni)
    assert isinstance(payload["days"], list)
    html = render_volatile_shadow_html(payload)
    assert "SHADOW" in html
    assert "geen" in html.lower() or "Geen" in html or "Zou kopen" in html or "Volatile" in html
