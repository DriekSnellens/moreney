"""Match a spot strategy to AlphaI coin news and market news.

The menu and the rule were fixed before the tape was run.

Each UTC day has two facts, both known at the next midnight and neither
read from a later headline:

* coin — majority of that coin's directional labels that day
  (bull, bear, or quiet when there is no majority)
* market — majority of the macro feed's ``market_sentiment_alignment``
  that day (bull, bear, mixed on a tie, quiet when nothing classifies)

A day with a quiet coin and a quiet market is cash. Every other pair is a
context. The menu for a context is hold 24h, a 10% trail out to 7 days, or
hold 7 days. All three are spot buys filled on the first 15-minute open at
or after the next midnight, with the Bitvavo 0.25% taker on both sides.
Bitvavo spot cannot short, so the menu has no short.

At each midnight the matcher may use only exits that have already happened,
and only exits from the same context. A strategy needs 8 of those exits and
a positive mean net. The highest such mean wins. An exact tie keeps the
earlier name in the menu. Otherwise the bot stays in cash. It learns the
counterfactual result of every strategy, including the ones it did not take.

Nothing here is armed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from bot.research.alphai_sentiment_accuracy.payoff import (
    BOOK_EUR,
    FEE,
    N_SLOTS,
    SLOT_EUR,
    entry_index,
    walk_exit,
)
from bot.research.alphai_sentiment_accuracy.score import HORIZONS, majority_side, side_of

MIN_TRADES = 8
# Earlier names win an exact tie. Cash is the fallback, not a candidate.
STRATEGIES: tuple[str, ...] = ("hold_24h", "trail_7d", "hold_7d")
_SPEC: dict[str, tuple[int, float]] = {
    "hold_24h": (dict(HORIZONS)["24h"], 0.0),
    "trail_7d": (dict(HORIZONS)["7d"], 0.10),
    "hold_7d": (dict(HORIZONS)["7d"], 0.0),
}
Ohlc = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def market_side(alignment: str) -> int | None:
    """+1 bullish, -1 bearish, 0 neutral, None when the phrase is not a label.

    ``risk-off`` and ``risk-on`` are read before the first word, because the
    macro feed often puts them at the start of a longer sentence.
    """
    raw = str(alignment or "").strip().lower()
    if not raw:
        return None
    if "risk-off" in raw or "risk off" in raw:
        return -1
    if "risk-on" in raw or "risk on" in raw:
        return 1
    token = raw.split()[0].strip(".,;:")
    return side_of(token)


def coin_state(sides: Sequence[int]) -> str:
    vote = majority_side([int(side) for side in sides if int(side) != 0])
    if vote is None:
        return "quiet"
    return "bull" if vote > 0 else "bear"


def market_state(sides: Sequence[int]) -> str:
    directional = [int(side) for side in sides if int(side) != 0]
    if not directional:
        return "quiet"
    vote = majority_side(directional)
    if vote is None:
        return "mixed"
    return "bull" if vote > 0 else "bear"


def strategy_for_news(
    coin: str,
    market: str,
    completed: Mapping[str, Sequence[float]],
    *,
    available: Sequence[str] = STRATEGIES,
    min_trades: int = MIN_TRADES,
) -> str:
    """Strategy name, or ``cash``.

    ``completed`` maps a strategy to nets whose exit is already known.
    ``available`` is the strategies this coin can still fill today.
    """
    if coin == "quiet" and market == "quiet":
        return "cash"
    best = "cash"
    best_mean = 0.0
    allowed = set(available)
    for name in STRATEGIES:
        if name not in allowed:
            continue
        rows = [float(net) for net in completed.get(name) or ()]
        if len(rows) < min_trades:
            continue
        mean = sum(rows) / len(rows)
        if mean > best_mean:
            best = name
            best_mean = mean
    return best


@dataclass(frozen=True)
class Episode:
    decision_ms: int
    base: str
    coin: str
    market: str
    nets: tuple[tuple[str, float, int], ...]

    @property
    def context(self) -> tuple[str, str]:
        return (self.coin, self.market)

    def net_of(self, name: str) -> tuple[float, int] | None:
        for strategy, net, exit_ms in self.nets:
            if strategy == name:
                return net, exit_ms
        return None


@dataclass(frozen=True)
class Taken:
    entry_ms: int
    exit_ms: int
    base: str
    net: float
    score: float
    strategy: str


def next_midnight_ms(day: str) -> int:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
    return int(start.timestamp() * 1000)


def strategy_nets(bars: Ohlc, decision_ms: int) -> tuple[tuple[str, float, int], ...]:
    """Counterfactual spot nets for one coin from one midnight. Long only."""
    ts, opens, highs, lows, closes = bars
    found: list[tuple[str, float, int]] = []
    for name in STRATEGIES:
        horizon, trail = _SPEC[name]
        index = entry_index(ts, decision_ms, horizon)
        if index is None:
            continue
        fill = walk_exit(ts, opens, highs, lows, closes, index, 1, 0.0, trail, horizon, FEE)
        if fill is None:
            continue
        found.append((name, float(fill.net), int(fill.exit_ms)))
    return tuple(found)


def build_episodes(
    coin_sides: Mapping[str, Mapping[str, Sequence[int]]],
    market_sides: Mapping[str, Sequence[int]],
    bars: Mapping[str, Ohlc],
    *,
    universe: Sequence[str],
) -> list[Episode]:
    """One episode per candidate coin per news day. Quiet/quiet is omitted."""
    days = set(market_sides)
    for by_day in coin_sides.values():
        days.update(by_day)
    episodes: list[Episode] = []
    desk = set(universe)
    for day in sorted(days):
        market = market_state(market_sides.get(day) or ())
        named = {base for base, by_day in coin_sides.items() if day in by_day}
        for base in sorted(desk | named):
            if base not in bars:
                continue
            coin = coin_state(coin_sides.get(base, {}).get(day) or ())
            if coin == "quiet" and market == "quiet":
                continue
            nets = strategy_nets(bars[base], next_midnight_ms(day))
            if not nets:
                continue
            episodes.append(
                Episode(
                    decision_ms=next_midnight_ms(day),
                    base=base,
                    coin=coin,
                    market=market,
                    nets=nets,
                )
            )
    return episodes


def _mean(rows: Sequence[float]) -> float:
    return sum(rows) / len(rows) if rows else 0.0


def matched_trades(episodes: Sequence[Episode], *, min_trades: int = MIN_TRADES) -> list[Taken]:
    """Walk forward. An exit teaches the context only after it has happened."""
    pending: list[tuple[int, tuple[str, str], str, float]] = []
    for episode in episodes:
        for name, net, exit_ms in episode.nets:
            pending.append((exit_ms, episode.context, name, net))
    pending.sort(key=lambda row: (row[0], row[1], row[2]))
    learned: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    cursor = 0
    taken: list[Taken] = []
    ordered = sorted(episodes, key=lambda episode: (episode.decision_ms, episode.base))
    for episode in ordered:
        while cursor < len(pending) and pending[cursor][0] < episode.decision_ms:
            _exit_ms, context, name, net = pending[cursor]
            learned[context][name].append(net)
            cursor += 1
        completed = learned.get(episode.context) or {}
        available = [name for name, _net, _exit in episode.nets]
        choice = strategy_for_news(
            episode.coin,
            episode.market,
            completed,
            available=available,
            min_trades=min_trades,
        )
        got = episode.net_of(choice)
        if choice == "cash" or got is None:
            continue
        net, exit_ms = got
        past = completed.get(choice) or []
        taken.append(
            Taken(
                entry_ms=episode.decision_ms,
                exit_ms=exit_ms,
                base=episode.base,
                net=net,
                score=_mean(past),
                strategy=choice,
            )
        )
    return taken


def control_trades(episodes: Sequence[Episode], rule: str) -> list[Taken]:
    """A fixed rule, so the matcher has something to beat."""
    taken: list[Taken] = []
    for episode in episodes:
        name = _control_name(rule, episode.coin)
        if name is None:
            continue
        got = episode.net_of(name)
        if got is None:
            continue
        net, exit_ms = got
        taken.append(
            Taken(
                entry_ms=episode.decision_ms,
                exit_ms=exit_ms,
                base=episode.base,
                net=net,
                score=0.0,
                strategy=name,
            )
        )
    return taken


def _control_name(rule: str, coin: str) -> str | None:
    if rule == "bull_24h":
        return "hold_24h" if coin == "bull" else None
    if rule == "bull_7d":
        return "hold_7d" if coin == "bull" else None
    if rule == "news_24h":
        return "hold_24h" if coin in {"bull", "bear"} else None
    raise ValueError(rule)


def deploy_fixed(
    rows: Sequence[Taken],
    *,
    n_slots: int = N_SLOTS,
    slot_eur: float = SLOT_EUR,
) -> tuple[int, float]:
    """Up to ``n_slots`` names. Higher past score is filled first."""
    ordered = sorted(rows, key=lambda row: (row.entry_ms, -row.score, row.base, row.exit_ms))
    open_slots: list[tuple[int, str]] = []
    taken = 0
    pnl = 0.0
    for row in ordered:
        open_slots = [(until, name) for until, name in open_slots if until > row.entry_ms]
        if any(name == row.base for _, name in open_slots):
            continue
        if len(open_slots) >= n_slots:
            continue
        open_slots.append((row.exit_ms, row.base))
        taken += 1
        pnl += row.net * slot_eur
    return taken, pnl


def deploy_compound(
    rows: Sequence[Taken],
    *,
    n_slots: int = N_SLOTS,
    start_eur: float = BOOK_EUR,
) -> tuple[int, float, float]:
    """Same slots, with the stake resized to one quarter of equity."""
    ordered = sorted(rows, key=lambda row: (row.entry_ms, -row.score, row.base, row.exit_ms))
    open_slots: list[tuple[int, str, float, float]] = []
    cash = float(start_eur)
    peak = cash
    max_dd = 0.0
    taken = 0

    def equity() -> float:
        return cash + sum(notional for _, _, notional, _net in open_slots)

    def mark() -> None:
        nonlocal peak, max_dd
        eq = equity()
        if eq > peak:
            peak = eq
        if peak > 0.0:
            max_dd = max(max_dd, (peak - eq) / peak)

    for row in ordered:
        still: list[tuple[int, str, float, float]] = []
        for until, name, notional, held_net in open_slots:
            if until > row.entry_ms:
                still.append((until, name, notional, held_net))
            else:
                cash += notional * max(0.0, 1.0 + held_net)
        open_slots = still
        mark()
        if any(name == row.base for _, name, _, _ in open_slots):
            continue
        if len(open_slots) >= n_slots or cash <= 0.0 or equity() <= 0.0:
            continue
        notional = min(cash, equity() / n_slots)
        cash -= notional
        open_slots.append((row.exit_ms, row.base, notional, row.net))
        taken += 1
    for _until, _name, notional, held_net in open_slots:
        cash += notional * max(0.0, 1.0 + held_net)
    open_slots = []
    mark()
    return taken, cash, max_dd


def choice_counts(rows: Sequence[Taken]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.strategy] += 1
    return dict(counts)
