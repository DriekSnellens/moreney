"""Choose one sentiment strategy from the recent tape, then deploy it.

The menu is fixed: follow the label, fade it, always long, always short, or
stay flat. At each UTC midnight the chooser may use only votes whose 24h
exit is already known. It takes the menu item with the highest mean net over
the prior 14 days when that mean is positive and the sample has 20 votes.
Otherwise it stays flat. A second book does the same choice per coin, with
5 prior votes. Flat names earn zero and stay in the day's average.

The headline store starts in August 2026, so this cannot retune the live
residual book. Nothing is armed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.alphai_sentiment_accuracy.payoff import (
    BOOK_EUR,
    FEE,
    _ohlc,
    entry_index,
    walk_exit,
)
from bot.research.alphai_sentiment_accuracy.score import HORIZONS

DAY_MS = 24 * 60 * 60 * 1000
LOOKBACK_DAYS = 14
LOOKBACK_MS = LOOKBACK_DAYS * DAY_MS
MIN_MARKET = 20
MIN_COIN = 5
H24 = dict(HORIZONS)["24h"]
# Earlier names win an exact tie. Flat wins whenever nothing is strictly positive.
STRATEGIES = ("with_label", "flip", "long", "short")
OUT_PATH = Path("bot/research/alphai_sentiment_accuracy/SELECTOR.md")


@dataclass(frozen=True)
class Vote:
    entry_ms: int
    base: str
    label_side: int
    long_net: float
    short_net: float


@dataclass
class PathResult:
    name: str
    end_eur: float
    max_dd: float
    n_days: int
    mean_day: float
    choice_days: dict[str, int] = field(default_factory=dict)
    choice_votes: dict[str, int] = field(default_factory=dict)


def strategy_net(vote: Vote, name: str) -> float:
    """Net of one already-scored vote under a menu item."""
    if name == "with_label":
        return vote.long_net if vote.label_side > 0 else vote.short_net
    if name == "flip":
        return vote.short_net if vote.label_side > 0 else vote.long_net
    if name == "long":
        return vote.long_net
    if name == "short":
        return vote.short_net
    return 0.0


def choose_strategy(past: list[Vote], min_trades: int) -> str:
    """Best positive menu item. Too few votes, or a non-positive mean, is flat."""
    if len(past) < min_trades:
        return "flat"
    best = "flat"
    best_mean = 0.0
    for name in STRATEGIES:
        nets = [strategy_net(vote, name) for vote in past]
        mean = float(np.mean(nets))
        if mean > best_mean:
            best = name
            best_mean = mean
    return best


def _past(votes: list[Vote], day: int, base: str | None = None) -> list[Vote]:
    start = day - LOOKBACK_MS
    return [
        vote
        for vote in votes
        if start <= vote.entry_ms < day and (base is None or vote.base == base)
    ]


def deploy_days(votes: list[Vote]) -> list[int]:
    """Midnights whose prior 14 days already hold enough scored votes."""
    days = sorted({vote.entry_ms for vote in votes})
    return [day for day in days if len(_past(votes, day)) >= MIN_MARKET]


def simulate(votes: list[Vote], mode: str, *, start_eur: float = BOOK_EUR) -> PathResult:
    """Compound one fully invested day at a time.

    ``mode`` is ``selector``, ``per_coin``, or a menu name. Each day earns the
    average net of that day's votes. A flat choice contributes zero, so cash
    dilutes the day. Days before the shared warmup are left out of every book.
    """
    by_day: dict[int, list[Vote]] = defaultdict(list)
    for vote in votes:
        by_day[vote.entry_ms].append(vote)
    equity = float(start_eur)
    peak = equity
    max_dd = 0.0
    day_rets: list[float] = []
    choice_days: dict[str, int] = defaultdict(int)
    choice_votes: dict[str, int] = defaultdict(int)
    for day in deploy_days(votes):
        todays = by_day[day]
        if mode == "selector":
            choice = choose_strategy(_past(votes, day), MIN_MARKET)
            choice_days[choice] += 1
            nets = [strategy_net(vote, choice) for vote in todays]
        elif mode == "per_coin":
            nets = []
            for vote in todays:
                choice = choose_strategy(_past(votes, day, vote.base), MIN_COIN)
                choice_votes[choice] += 1
                nets.append(strategy_net(vote, choice))
        else:
            choice_days[mode] += 1
            nets = [strategy_net(vote, mode) for vote in todays]
        day_ret = float(np.mean(nets)) if nets else 0.0
        equity *= 1.0 + day_ret
        if equity > peak:
            peak = equity
        if peak > 0.0:
            drop = (peak - equity) / peak
            if drop > max_dd:
                max_dd = drop
        day_rets.append(day_ret)
    mean_day = float(np.mean(day_rets)) if day_rets else 0.0
    return PathResult(
        name=mode,
        end_eur=equity,
        max_dd=max_dd,
        n_days=len(day_rets),
        mean_day=mean_day,
        choice_days=dict(choice_days),
        choice_votes=dict(choice_votes),
    )


def _fmt_path(path: PathResult) -> str:
    ret = path.end_eur / BOOK_EUR - 1.0
    return (
        f"| {path.name} | {path.n_days} | {path.end_eur:,.2f} | {100 * ret:+.1f}% | "
        f"{100 * path.max_dd:.1f}% | {100 * path.mean_day:+.3f}% |"
    )


def _fmt_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "—"
    order = ("flat",) + STRATEGIES
    parts = [f"{name} {counts[name]}" for name in order if counts.get(name)]
    return ", ".join(parts)


def _reading(selector: PathResult, fixed: list[PathResult]) -> str:
    best = max(fixed, key=lambda path: path.end_eur)
    if selector.end_eur <= BOOK_EUR and best.end_eur <= BOOK_EUR:
        return (
            "The selector and every fixed rule finish at or below the start "
            "on the days the chooser is allowed to trade."
        )
    if selector.end_eur < best.end_eur:
        return (
            f"The selector ends at €{selector.end_eur:,.2f}. "
            f"`{best.name}` ends at €{best.end_eur:,.2f} on these same days. "
            "That fixed rule is visible only after the window. "
            "The selector never sees the later days when it chooses."
        )
    return (
        f"The selector ends at €{selector.end_eur:,.2f}, at or above the fixed "
        "rules on these days. One window of headlines is not a pass to arm it."
    )


def _load_votes() -> tuple[list[Vote], int]:
    from bot.research.alphai_sentiment_accuracy.__main__ import (  # noqa: PLC0415
        BARS,
        _daily_votes,
        _load_calls,
    )

    calls, _directional, _neutral = _load_calls()
    missing = 0
    votes: list[Vote] = []
    for base, rows in sorted(calls.items()):
        path = BARS / f"{base}.json"
        if not path.exists():
            missing += len(rows)
            continue
        ts, opens, highs, lows, closes = _ohlc(path)
        for midnight, side in _daily_votes(rows):
            i = entry_index(ts, midnight, H24)
            if i is None:
                continue
            long = walk_exit(ts, opens, highs, lows, closes, i, 1, 0.0, 0.0, H24, FEE)
            short = walk_exit(ts, opens, highs, lows, closes, i, -1, 0.0, 0.0, H24, FEE)
            if long is None or short is None:
                continue
            votes.append(
                Vote(
                    entry_ms=midnight,
                    base=base,
                    label_side=side,
                    long_net=long.net,
                    short_net=short.net,
                )
            )
    return votes, missing


def main() -> None:
    votes, missing = _load_votes()
    modes = ("selector", "per_coin", *STRATEGIES)
    paths = [simulate(votes, mode) for mode in modes]
    by_name = {path.name: path for path in paths}
    selector = by_name["selector"]
    fixed = [by_name[name] for name in STRATEGIES]
    if votes:
        first = datetime.fromtimestamp(min(v.entry_ms for v in votes) / 1000, UTC).date()
        last = datetime.fromtimestamp(max(v.entry_ms for v in votes) / 1000, UTC).date()
        span = f"{first.isoformat()} to {last.isoformat()}"
    else:
        span = "—"
    lines = [
        "# AlphaI sentiment as a strategy chooser",
        "",
        "One majority vote per coin per UTC day. The entry is the next midnight",
        "open and the exit is the open 24 hours later, with the Bitvavo taker",
        "on both sides. The chooser then picks one item from a fixed menu:",
        "follow the label, fade the label, always long, always short, or flat.",
        "",
        f"The lookback is {LOOKBACK_DAYS} calendar days and only votes that have",
        "already exited. The market chooser needs",
        f"{MIN_MARKET} of those votes and a strictly positive mean. The per-coin",
        f"chooser needs {MIN_COIN} votes on that coin. An exact tie keeps the",
        "earlier menu item. Flat names earn zero inside that day's average.",
        "The book starts at €20,000 and reinvests the day's average.",
        "Fixed rules are scored on the same days. These widths were set before",
        "the run. Nothing is armed.",
        "",
        f"Scored daily votes {len(votes)}. Labels with no 15m file are omitted",
        f"from the vote build ({missing} directional rows had no file).",
        f"Vote midnights run {span}. Deploy days {selector.n_days}.",
        "",
        "| book | days | end € | return | max DD | mean day |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for path in paths:
        lines.append(_fmt_path(path))
    lines += [
        "",
        "### What the chooser deployed",
        "",
        f"Market chooser, days: {_fmt_counts(selector.choice_days)}.",
        f"Per coin, votes: {_fmt_counts(by_name['per_coin'].choice_votes)}.",
        "",
        _reading(selector, fixed),
        "",
        "The live residual book is not in this menu. The headline file does not",
        "cover the two-year residual window, so a selector fit here cannot",
        "replace that book.",
        "",
    ]
    text = "\n".join(lines)
    OUT_PATH.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
