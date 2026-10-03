"""AlphaI pack-compare helpers — real picks only, no proxy."""

from __future__ import annotations

from bot.research.alphai_pack_compare.alphai_map import (
    build_alt_allow,
    picks_asof_hour,
)
from bot.research.alphai_pack_compare.engine import pack_specs, pareto_front


def test_picks_asof_hour_uses_session_before_cutoff() -> None:
    sessions = [
        {
            "generated_at": "2026-09-28T06:50:00+00:00",
            "session_id": "2026-09-28T06:50",
            "picks": [{"base": "SOL"}, {"base": "ETH"}],
        },
        {
            "generated_at": "2026-09-28T12:00:00+00:00",
            "session_id": "2026-09-28T12:00",
            "picks": [{"base": "NEAR"}],
        },
    ]
    daily = picks_asof_hour(sessions, hour_utc=7, universe=("SOL", "ETH", "NEAR", "BTC"))
    assert daily["2026-09-28"] == {"SOL", "ETH"}


def test_alt_allow_empty_before_first_real_day() -> None:
    allow = build_alt_allow(
        start="2026-09-18",
        end="2026-09-20",
        ohlc_dates=["2026-09-18", "2026-09-19", "2026-09-20"],
        daily_picks={"2026-09-19": {"SOL"}},
    )
    assert allow["2026-09-18"] == set()
    assert allow["2026-09-19"] == {"SOL"}
    assert allow["2026-09-20"] == {"SOL"}


def test_pareto_and_pack_specs() -> None:
    rows = [
        {"name": "a", "pnl_eur": 100.0, "max_dd_pct": 0.20},
        {"name": "b", "pnl_eur": 80.0, "max_dd_pct": 0.05},
        {"name": "c", "pnl_eur": 50.0, "max_dd_pct": 0.10},
    ]
    front = pareto_front(rows)
    names = {r["name"] for r in front}
    assert names == {"a", "b"}
    assert any(p["name"] == "live_residual_full" for p in pack_specs())
