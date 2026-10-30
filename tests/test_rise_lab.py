"""Forward-rise tags: continuation is a name that is already running."""

from __future__ import annotations

from bot.research.rise_lab.measure import RiseSpec, _tags, collect, summarize


def test_summary_reports_the_median_high_and_the_boom_rate() -> None:
    stats = summarize([0.10, 0.60, 0.20], [0.05, -0.10, 0.00], boom=0.50)
    assert stats["n"] == 3.0
    assert stats["high_median"] == 0.20
    assert stats["p_high_boom"] == 1 / 3
    assert stats["close_median"] == 0.0


def test_continuation_is_the_hot_trend_not_the_quiet_tape() -> None:
    running = _tags(
        {
            "r3": 0.50,
            "r10": 0.40,
            "xs10": 0.30,
            "quiet10": False,
            "r3_15": True,
            "xs25": True,
            "trend": True,
            "brk20": False,
            "day6": True,
            "vol2": False,
        },
        wake=False,
    )
    assert "continuation" in running
    assert "r3_hot" in running
    assert "live_xs" in running
    assert "quiet" not in running

    quiet = _tags(
        {
            "r3": -0.02,
            "r10": 0.04,
            "xs10": 0.01,
            "quiet10": True,
            "r3_15": False,
            "xs25": False,
            "trend": False,
            "brk20": False,
            "day6": False,
            "vol2": False,
        },
        wake=True,
    )
    assert {"liquid", "quiet", "wake", "r3_neg"} <= quiet
    assert "continuation" not in quiet


def test_collect_tags_a_three_day_rip_and_leaves_btc_out() -> None:
    day = 86_400_000
    t0 = 1_704_067_200_000

    def series(closes: list[float], vol: float) -> list[list[float]]:
        rows = []
        for i, close in enumerate(closes):
            prev = closes[i - 1] if i else close
            rows.append([t0 + i * day, prev, max(prev, close), min(prev, close), close, vol])
        return rows

    flat = [100.0] * 70
    # 60 quiet days, then a three-day jump large enough to clear +15% and +25% vs flat BTC.
    alt = [10.0] * 60 + [10.0, 11.0, 13.0, 16.0, 16.0, 16.0, 16.0, 16.0, 16.0, 16.0]
    ohlc = {"BTC": series(flat, 10.0), "AAA": series(alt, 5_000.0)}
    events = collect(ohlc, RiseSpec())
    assert events
    assert all(event["base"] == "AAA" for event in events)
    ripped = [event for event in events if "continuation" in event["tags"]]
    assert ripped
    assert ripped[0]["day"] >= "2024-03-01"
