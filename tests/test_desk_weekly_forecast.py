"""Weekly desk forecast helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.live.desk_weekly_forecast import (
    forecast_is_stale,
    generate_weekly_forecast,
    load_forecast,
    maybe_refresh_forecast,
)


class _Settings:
    desk_weekly_forecast_enabled = True
    desk_weekly_forecast_path = ""
    desk_weekly_forecast_hour_local = 7
    alphai_daily_recommendations_path = "data/alphai/daily_recommendations.json"
    alphai_daily_recommendations_min_relevance = 6
    alphai_daily_recommendations_top_n = 8
    alphai_daily_recommendations_hour = 12


def test_forecast_is_stale_across_morning() -> None:
    nl = ZoneInfo("Europe/Amsterdam")
    report = {
        "as_of_local": datetime(2026, 9, 15, 8, 0, tzinfo=nl).isoformat(),
    }
    morning = datetime(2026, 9, 16, 8, 0, tzinfo=nl)
    assert forecast_is_stale(report, now=morning, hour_local=7) is True
    same_day = datetime(2026, 9, 15, 18, 0, tzinfo=nl)
    assert forecast_is_stale(report, now=same_day, hour_local=7) is False


def test_generate_weekly_forecast_writes_file(tmp_path: Path, monkeypatch) -> None:
    settings = _Settings()
    settings.desk_weekly_forecast_path = str(tmp_path / "forecast.json")
    # Avoid network AlphaI refresh side effects beyond existing cache.
    monkeypatch.setattr(
        "bot.live.desk_weekly_forecast._alphai_bundle",
        lambda _s: {
            "macro_caution": True,
            "generated_at": "2026-09-16T12:00:00+00:00",
            "session_id": "test",
            "headline_count": 2,
            "picks": [{"base": "LTC", "score": 10, "note": ""}],
            "avoid": [{"base": "ETH", "score": -10, "note": ""}],
            "headlines": ["Senate CLARITY Act fails"],
        },
    )
    monkeypatch.setattr(
        "bot.live.desk_weekly_forecast._fetch_bitvavo_24h",
        lambda base, timeout=6.0: {
            "base": base,
            "last": 65000.0 if base == "BTC" else 2000.0,
            "open": 66000.0 if base == "BTC" else 2100.0,
            "high": 67000.0,
            "low": 64000.0,
            "ret_24h": -0.015,
        },
    )
    monkeypatch.setattr(
        "bot.live.desk_weekly_forecast._desk_regime_snapshot",
        lambda: {
            "regime_label": "weak",
            "btc_ret": -0.012,
            "breadth": 0.12,
            "reasons": ["btc_weak", "breadth_weak", "weak_tape_idle"],
            "at": "2026-09-16T07:00:00+00:00",
        },
    )
    report = generate_weekly_forecast(settings)
    assert report["ok"] is True
    assert report["bias"] == "cautious"
    assert "week_view" in report
    assert len(report["scenarios"]) == 3
    loaded = load_forecast(settings.desk_weekly_forecast_path)
    assert loaded is not None
    assert loaded["bias_label"]


def test_maybe_refresh_skips_fresh(tmp_path: Path, monkeypatch) -> None:
    settings = _Settings()
    path = tmp_path / "forecast.json"
    settings.desk_weekly_forecast_path = str(path)
    nl = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 9, 16, 15, 0, tzinfo=nl)
    path.write_text(
        '{"as_of_local":"2026-09-16T08:00:00+02:00","bias":"cautious","week_view":"x"}',
        encoding="utf-8",
    )
    called = {"n": 0}

    def _fake_gen(_settings, now=None):  # noqa: ANN001
        called["n"] += 1
        return {"ok": True, "fresh": True}

    monkeypatch.setattr(
        "bot.live.desk_weekly_forecast.generate_weekly_forecast", _fake_gen
    )
    out = maybe_refresh_forecast(settings, force=False, now=now)
    assert out["refreshed"] is False
    assert called["n"] == 0
