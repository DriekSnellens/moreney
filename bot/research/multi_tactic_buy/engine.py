"""Buy a coin only when several tactics already on the desk agree.

The rules below were fixed before the tape was run. Nothing here searches
a threshold, a horizon, or a coin.

Three yes/no tactics, each coin-agnostic, each using only the close of the
signal day:

* tsmom — 28-day own return positive, excess versus BTC positive, close
  above its own 50-day average, 20-day quote volume at least €80k.
* ctrend — equal-weight mean of the 7, 14, 28 and 56 day own returns
  positive, same average and volume gate.
* residual — 10-day excess versus BTC, skip one day, above 3.5%, close
  above its own 50-day average, same volume gate. Rank among the yeses
  uses the live near-high blend of 0.25.

A coin is bought when at least two of those three say yes. The strict book
requires all three. Each tactic alone, with the same clock, is the control.
A fourth book adds the AlphaI label: the headlines of the last seven UTC
days must have a bullish majority. Silence and a tie are not a yes.

Shared execution, so the comparison is the buy rule: weekly clock, signal
on the close, fill on the next open, at most four equal stakes, 10% trail
from the peak, flat when BTC closes under its 50-day average. Costs are the
wet Bitvavo fill (0.25% taker plus impact). No per-coin branch.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_btc_rs_clip import (
    high_proximity,
    quote_vol,
    residual_rank_score,
    rs_excess,
    sma,
)
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_sentiment_accuracy.score import majority_side, side_of
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    bar_date,
    fill_px,
    metrics,
    rows_through,
    year_pnl,
)
from bot.research.tactic_lab.engine import TacticSpec, _score

Ohlc = Mapping[str, Sequence[Sequence[float]]]
PicksOn = Callable[[str], list[str]]

N_NAMES = 4
REBALANCE_DAYS = 7
TRAIL_PCT = 0.10
BTC_SMA = 50
MIN_QVOL = 80_000.0
NEAR_HIGH = 0.25
NEWS_DAYS = 7

TSMOM = TacticSpec(
    name="tsmom",
    kind="tsmom",
    lookbacks=(28,),
    skip=1,
    excess_floor=0.0,
    require_sma=50,
    min_qvol_eur=MIN_QVOL,
)
CTREND = TacticSpec(
    name="ctrend",
    kind="ctrend",
    lookbacks=(7, 14, 28, 56),
    skip=1,
    require_sma=50,
    min_qvol_eur=MIN_QVOL,
)


@dataclass(frozen=True)
class CoinVote:
    """One coin on one signal day. A None score is a no."""

    tsmom: float | None
    ctrend: float | None
    residual: float | None
    excess: float | None

    @property
    def tactics(self) -> frozenset[str]:
        names: list[str] = []
        if self.tsmom is not None:
            names.append("tsmom")
        if self.ctrend is not None:
            names.append("ctrend")
        if self.residual is not None:
            names.append("residual")
        return frozenset(names)


def _base_ticker(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.endswith("-USD"):
        return text[: -len("-USD")]
    return text


def load_headline_sides(path: Path) -> dict[str, dict[str, list[int]]]:
    """Per coin, per UTC day: the directional headline sides.

    Neutrals and unknown labels are dropped. The day is the headline's own
    UTC date, so a signal on that close can see it and the next open cannot
    see the next day's tape.
    """
    raw = json.loads(path.read_text())
    seen: set[tuple[str, str]] = set()
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in raw.get("rows") or []:
        uid = str(row.get("uid") or "")
        published = str(row.get("published") or "")
        try:
            stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        day = stamp.astimezone(UTC).date().isoformat()
        for ticker, sentiment in (row.get("sentiments") or {}).items():
            base = _base_ticker(str(ticker))
            if not base or (uid, base) in seen:
                continue
            seen.add((uid, base))
            side = side_of(str(sentiment))
            if side is None or side == 0:
                continue
            out[base][day].append(side)
    return {base: dict(days) for base, days in out.items()}


def bullish_counts(
    sides: Mapping[str, Mapping[str, Sequence[int]]],
    date: str,
    *,
    window: int = NEWS_DAYS,
) -> dict[str, int]:
    """Coins whose last ``window`` UTC days have a bullish majority.

    The value is the number of bullish labels in that window, used only to
    rank the news-only book. A tie, or a week with no headline, is absent.
    """
    end = datetime.strptime(date, "%Y-%m-%d").date()
    days = [(end - timedelta(days=offset)).isoformat() for offset in range(window)]
    out: dict[str, int] = {}
    for base, by_day in sides.items():
        labels: list[int] = []
        for day in days:
            labels.extend(int(side) for side in by_day.get(day) or ())
        if majority_side(labels) != 1:
            continue
        out[base] = sum(1 for side in labels if side > 0)
    return out


def vote_coin(ohlc: Ohlc, base: str, date: str) -> CoinVote:
    """The three tactics on one coin. Residual does not require an own-return gate."""
    tsmom = _score(ohlc, TSMOM, base, date)
    ctrend = _score(ohlc, CTREND, base, date)
    rows = rows_through(ohlc.get(base) or [], date)
    closes = [float(row[4]) for row in rows if float(row[4]) > 0]
    btc = [float(row[4]) for row in rows_through(ohlc.get("BTC") or [], date) if float(row[4]) > 0]
    excess = rs_excess(closes, btc, lb=10, skip=1) if closes else None
    residual: float | None = None
    if excess is not None and quote_vol(rows) >= MIN_QVOL and closes:
        level = sma(closes, 50)
        if level is not None and closes[-1] > level and excess > 0.035:
            highs = [float(row[2]) for row in rows if len(row) >= 3]
            residual = residual_rank_score(
                float(excess), high_proximity(highs, closes[-1]), weight=NEAR_HIGH
            )
    return CoinVote(
        tsmom=None if tsmom is None else float(tsmom),
        ctrend=None if ctrend is None else float(ctrend),
        residual=residual,
        excess=None if excess is None else float(excess),
    )


def panel_on(ohlc: Ohlc, date: str, universe: Sequence[str]) -> dict[str, CoinVote]:
    out: dict[str, CoinVote] = {}
    for base in universe:
        if base == "BTC" or base not in ohlc:
            continue
        out[base] = vote_coin(ohlc, base, date)
    return out


def choose(
    kind: str,
    votes: Mapping[str, CoinVote],
    bullish: Mapping[str, int],
    *,
    min_votes: int,
    n_names: int,
) -> list[str]:
    """Rank the coins the locked rule allows, strongest first."""
    ranked: list[tuple[tuple[float, ...], str]] = []
    for base, vote in votes.items():
        key = _rank_key(kind, vote, bullish.get(base), min_votes=min_votes)
        if key is None:
            continue
        ranked.append((key, base))
    ranked.sort(key=lambda item: item[1])
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [base for _, base in ranked[: max(1, n_names)]]


def _rank_key(
    kind: str,
    vote: CoinVote,
    bullish_n: int | None,
    *,
    min_votes: int,
) -> tuple[float, ...] | None:
    if kind == "tsmom":
        return None if vote.tsmom is None else (vote.tsmom,)
    if kind == "ctrend":
        return None if vote.ctrend is None else (vote.ctrend,)
    if kind == "residual":
        return None if vote.residual is None else (vote.residual,)
    if kind == "news":
        return None if bullish_n is None else (float(bullish_n),)
    agreed = len(vote.tactics)
    if agreed < min_votes:
        return None
    if kind == "agree_news" and bullish_n is None:
        return None
    if kind not in {"agree", "agree_news"}:
        return None
    excess = -1.0e9 if vote.excess is None else vote.excess
    return (float(agreed), excess)


def _days(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def run_desk(
    ohlc: Ohlc,
    *,
    start: str,
    end: str,
    picks_on: PicksOn,
    name: str,
    book_eur: float = 20_000.0,
    n_names: int = N_NAMES,
    rebalance_days: int = REBALANCE_DAYS,
    trail_pct: float = TRAIL_PCT,
    btc_sma: int = BTC_SMA,
    model: FillModel = WET,
) -> dict[str, Any]:
    """Compounding book. ``picks_on`` is called only on a due rebalance."""
    timeline = [row for row in (ohlc.get("BTC") or []) if bar_date(row) <= end]
    book = Book(book_eur)
    legs: dict[str, dict[str, float]] = {}
    pending_sells: list[tuple[str, str]] = []
    pending_buys: list[tuple[str, float]] = []
    last_clock: str | None = None
    trades: list[dict[str, str]] = []
    day_equity: dict[str, float] = {}

    def _adv(base: str, date: str) -> float:
        return quote_vol(rows_through(ohlc.get(base) or [], date))

    def _bar(base: str, date: str) -> Sequence[float] | None:
        rows = rows_through(ohlc.get(base) or [], date)
        if not rows or bar_date(rows[-1]) != date:
            return None
        return rows[-1]

    def _mark(date: str) -> float:
        px_map: dict[str, float] = {}
        for base in list(book.lots):
            row = _bar(base, date)
            if row is not None and float(row[4]) > 0:
                px_map[base] = float(row[4])
        return book.mark(px_map)

    for row in timeline:
        date = bar_date(row)
        ts = int(row[0])
        for base, reason in pending_sells:
            live = _bar(base, date)
            lot = book.lots.get(base)
            if live is None or lot is None:
                continue
            raw = float(live[1])
            px = fill_px(raw, "sell", notional=lot.qty * raw, adv=_adv(base, date), model=model)
            book.sell(base, px, model.fee_side)
            trades.append({"date": date, "side": "sell", "base": base, "reason": reason})
            legs.pop(base, None)
        pending_sells = []
        for base, notional in pending_buys:
            live = _bar(base, date)
            if live is None or base in book.lots or notional <= 1.0:
                continue
            raw = float(live[1])
            px = fill_px(raw, "buy", notional=notional, adv=_adv(base, date), model=model)
            spent = book.buy(base, name, notional, px, model.fee_side, opened_ms=ts)
            if spent <= 0:
                continue
            legs[base] = {"entry": px, "peak": max(px, float(live[2]))}
            trades.append({"date": date, "side": "buy", "base": base, "reason": name})
        pending_buys = []
        if date < start:
            continue
        if date > end:
            break

        for base, leg in list(legs.items()):
            live = _bar(base, date)
            if live is None or base not in book.lots:
                continue
            leg["peak"] = max(leg["peak"], float(live[2]))
            if (
                trail_pct > 0
                and leg["peak"] > 0
                and float(live[4]) <= leg["peak"] * (1.0 - trail_pct)
            ):
                pending_sells.append((base, "trail"))

        btc_closes = [float(item[4]) for item in rows_through(ohlc.get("BTC") or [], date)]
        level = sma(btc_closes, btc_sma) if btc_closes else None
        risk_on = level is not None and btc_closes[-1] > level
        due = last_clock is None or _days(last_clock, date) >= rebalance_days
        if any(reason == "trail" for _, reason in pending_sells):
            last_clock = date
            due = False
        if due:
            last_clock = date
            picks = picks_on(date) if risk_on else []
            held = set(legs)
            for base in list(legs):
                if base not in picks:
                    pending_sells.append((base, "clock"))
            selling = {base for base, _reason in pending_sells}
            fresh = [base for base in picks if base not in held or base in selling]
            if fresh:
                each = _mark(date) / max(1, n_names)
                for base in fresh:
                    pending_buys.append((base, each))

        if date >= start:
            day_equity[date] = _mark(date)

    curve = list(day_equity.items())
    sold = sum(1 for trade in trades if trade["side"] == "sell")
    return {
        "strategy": name,
        "model": model.name,
        "start": start,
        "end": end,
        "trades": trades,
        "year_pnl": year_pnl([day for day, _eq in curve], [eq for _day, eq in curve], book_eur),
        "end_hold": ",".join(sorted(legs)) if legs else "cash",
        **metrics(
            [eq for _day, eq in curve],
            start_eur=book_eur,
            n_trades=sold,
            n_days=len(curve),
        ),
    }


def weekly_overlap(
    ohlc: Ohlc,
    *,
    start: str,
    end: str,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    step_days: int = REBALANCE_DAYS,
) -> dict[str, int]:
    """How often the three tactics agree, on a fixed weekly grid.

    The grid ignores trails, so the count is a property of the signals.
    """
    dates = [bar_date(row) for row in (ohlc.get("BTC") or []) if start <= bar_date(row) <= end]
    counts = {"weeks": 0, "any_two": 0, "any_three": 0, "tsmom": 0, "ctrend": 0, "residual": 0}
    last: str | None = None
    for date in dates:
        if last is not None and _days(last, date) < step_days:
            continue
        last = date
        counts["weeks"] += 1
        panel = panel_on(ohlc, date, universe)
        tallies = {"tsmom": 0, "ctrend": 0, "residual": 0}
        two = False
        three = False
        for vote in panel.values():
            names = vote.tactics
            for tactic in names:
                tallies[tactic] += 1
            two = two or len(names) >= 2
            three = three or len(names) >= 3
        for tactic, n in tallies.items():
            if n:
                counts[tactic] += 1
        if two:
            counts["any_two"] += 1
        if three:
            counts["any_three"] += 1
    return counts
