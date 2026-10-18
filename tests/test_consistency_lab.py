from __future__ import annotations

from datetime import date, timedelta

from bot.research.consistency_lab.engine import consistency, period_returns, sum_curves


def _curve(vals: list[float], start: str = "2026-01-05") -> list[tuple[str, float]]:
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(vals)]


def test_weekly_buckets_use_iso_weeks_and_bucket_start_equity() -> None:
    # Mon 2026-01-05 .. Sun 01-11 flat at 100, next week ends at 110, third week falls to 99.
    vals = [100.0] * 7 + [102.0, 104.0, 106.0, 108.0, 110.0, 110.0, 110.0] + [99.0] * 7
    curve = _curve(vals)
    weeks = period_returns(curve, lambda d: str(date.fromisoformat(d).isocalendar()[1]), 100.0)
    assert [round(p, 4) for _, _, p in weeks] == [0.0, 0.1, -0.1]
    c = consistency(curve, 100.0)
    assert c["weeks"] == 3
    assert c["green_weeks_pct"] == round(1 / 3, 3)
    assert c["red_weeks_pct"] == round(1 / 3, 3)
    assert c["flat_weeks_pct"] == round(1 / 3, 3)
    assert c["green_active_weeks_pct"] == 0.5
    assert c["worst_week_pct"] == -0.1
    assert c["longest_red_weeks"] == 1
    assert c["months"] == 1 and c["red_months_pct"] == 1.0 and c["worst_month_pct"] == -0.01


def test_red_streak_and_drawdown() -> None:
    vals = [100.0] * 7 + [90.0] * 7 + [81.0] * 7 + [85.0] * 7
    c = consistency(_curve(vals), 100.0)
    assert c["longest_red_weeks"] == 2
    assert abs(c["max_dd_pct"] - 0.19) < 1e-9
    assert c["worst_week_eur"] == -10.0


def test_sum_curves_aligns_on_shortest() -> None:
    a = _curve([10.0, 11.0, 12.0])
    b = _curve([5.0, 5.0])
    s = sum_curves([a, b])
    assert s == [("2026-01-05", 15.0), ("2026-01-06", 16.0)]
