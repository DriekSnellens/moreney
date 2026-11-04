"""The buy list is the agreement of the tactics, filled on the next open."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.research.multi_tactic_buy.engine import (
    CoinVote,
    bullish_counts,
    choose,
    run_desk,
)


def _vote(
    *,
    tsmom: float | None = None,
    ctrend: float | None = None,
    residual: float | None = None,
    excess: float | None = None,
) -> CoinVote:
    return CoinVote(tsmom=tsmom, ctrend=ctrend, residual=residual, excess=excess)


def test_two_votes_buy_and_one_vote_does_not() -> None:
    votes = {
        "AAA": _vote(tsmom=0.20, ctrend=0.05, excess=0.02),
        "BBB": _vote(residual=0.10, excess=0.08),
        "CCC": _vote(tsmom=0.04, residual=0.06, excess=0.09),
    }
    assert choose("agree", votes, {}, min_votes=2, n_names=4) == ["CCC", "AAA"]
    assert choose("agree", votes, {}, min_votes=3, n_names=4) == []
    assert choose("tsmom", votes, {}, min_votes=1, n_names=4) == ["AAA", "CCC"]
    assert choose("residual", votes, {}, min_votes=1, n_names=1) == ["BBB"]


def test_news_silence_is_not_a_yes() -> None:
    votes = {
        "AAA": _vote(tsmom=0.2, ctrend=0.1, excess=0.05),
        "BBB": _vote(),
    }
    assert choose("agree_news", votes, {}, min_votes=2, n_names=4) == []
    assert choose("agree_news", votes, {"AAA": 3}, min_votes=2, n_names=4) == ["AAA"]
    assert choose("news", votes, {"AAA": 3, "BBB": 5}, min_votes=1, n_names=1) == ["BBB"]


def test_bullish_window_ignores_an_older_headline_and_a_tie() -> None:
    sides = {
        "AAA": {"2026-08-01": [1, 1]},
        "BBB": {"2026-08-10": [1, -1]},
        "CCC": {"2026-08-09": [-1], "2026-08-10": [1, 1]},
    }
    counts = bullish_counts(sides, "2026-08-10", window=7)
    assert "AAA" not in counts
    assert "BBB" not in counts
    assert counts["CCC"] == 2


def _daily(closes: list[float]) -> list[list[float]]:
    start = datetime(2024, 6, 1, tzinfo=UTC)
    rows = []
    prev = float(closes[0])
    for i, close in enumerate(closes):
        c = float(close)
        ts = int((start + timedelta(days=i)).timestamp() * 1000)
        rows.append([ts, prev, max(prev, c), min(prev, c), c, 1_000_000.0])
        prev = c
    return rows


def test_buy_fills_on_the_next_open() -> None:
    closes = [100.0 + i for i in range(8)]
    ohlc = {"BTC": _daily(closes), "AAA": _daily(closes)}
    start = "2024-06-06"

    def picks(_date: str) -> list[str]:
        return ["AAA"]

    row = run_desk(
        ohlc,
        start=start,
        end="2024-06-08",
        picks_on=picks,
        name="probe",
        book_eur=20_000.0,
        n_names=4,
        rebalance_days=30,
        trail_pct=0.0,
        btc_sma=2,
    )
    buys = [trade for trade in row["trades"] if trade["side"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["date"] == "2024-06-07"
    assert buys[0]["base"] == "AAA"
    assert row["end_hold"] == "AAA"
