"""The matcher uses only exits that have already happened in the same news pair."""

from __future__ import annotations

from bot.research.news_strategy_match.engine import (
    Episode,
    Taken,
    coin_state,
    deploy_fixed,
    market_side,
    market_state,
    matched_trades,
    strategy_for_news,
)


def _episode(
    decision_ms: int,
    base: str,
    coin: str,
    market: str,
    nets: tuple[tuple[str, float, int], ...],
) -> Episode:
    return Episode(decision_ms=decision_ms, base=base, coin=coin, market=market, nets=nets)


def test_market_phrase_and_states() -> None:
    assert market_side("negative") == -1
    assert market_side("risk-off for defense-tech supply") == -1
    assert market_side("risk on after the print") == 1
    assert market_side("") is None
    assert coin_state([1, 1, -1]) == "bull"
    assert coin_state([1, -1]) == "quiet"
    assert market_state([1, -1]) == "mixed"
    assert market_state([]) == "quiet"


def test_quiet_pair_is_cash_and_a_short_sample_is_cash() -> None:
    past = {"hold_24h": [0.02] * 8}
    assert strategy_for_news("quiet", "quiet", past) == "cash"
    assert strategy_for_news("bull", "bear", {"hold_24h": [0.02] * 7}) == "cash"
    assert strategy_for_news("bull", "bear", {"hold_24h": [-0.01] * 8}) == "cash"
    assert strategy_for_news("bull", "bear", past) == "hold_24h"


def test_tie_keeps_the_earlier_strategy() -> None:
    past = {"hold_24h": [0.02] * 8, "hold_7d": [0.02] * 8, "trail_7d": [0.01] * 8}
    assert strategy_for_news("bull", "bull", past) == "hold_24h"


def test_walk_waits_until_eight_exits_are_known() -> None:
    episodes = []
    for i in range(9):
        decision = i * 100
        exit_ms = decision + 10
        episodes.append(_episode(decision, f"C{i}", "bull", "bull", (("hold_24h", 0.02, exit_ms),)))
    taken = matched_trades(episodes, min_trades=8)
    assert [row.base for row in taken] == ["C8"]
    assert taken[0].strategy == "hold_24h"


def test_higher_score_fills_the_only_slot() -> None:
    rows = [
        Taken(1, 5, "AAA", 0.01, 0.01, "hold_24h"),
        Taken(1, 5, "BBB", 0.04, 0.03, "hold_24h"),
    ]
    n, pnl = deploy_fixed(rows, n_slots=1, slot_eur=100.0)
    assert n == 1
    assert pnl == 4.0
