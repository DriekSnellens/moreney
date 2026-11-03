"""Equal-weight sleeve. News counts decide the names. Nothing is armed."""

from __future__ import annotations

import bisect
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.core.venue_fees import VENUE_TAKER_FEE
from bot.integrations.alphai.parse import AlphaIHeadline

SLEEVE_EUR = 10_000.0
MAX_NAMES = 4
TONE_DAYS = 7
MIN_RELEVANCE = 6
MIN_QUOTE_EUR = 100_000.0
LOOKBACK = 5
MAX_STALE_DAYS = 2
MIN_BARS = 48
MIN_CHOP = 0.02
CAPACITY = 0.10
IMPACT_K = 0.02
IMPACT_CAP = 0.03
TAKER = float(VENUE_TAKER_FEE["bitvavo"])
DUST_EUR = 1.0
MODES = ("four", "all", "chop", "book")


@dataclass(frozen=True)
class Session:
    day: str
    open: float
    high: float
    low: float
    close: float
    quote: float
    n_bars: int


@dataclass(frozen=True)
class Run:
    mode: str
    start: str
    end: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    name_days: int
    buys: int
    sells: int
    largest_day: float
    names: tuple[tuple[str, int, float], ...]


@dataclass
class _Lot:
    qty: float = 0.0
    cost: float = 0.0
    adv: float = 0.0
    realized: float = 0.0
    days: int = 0


def publication_date(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return raw[:10] if len(raw) >= 10 else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _gap(earlier: str, later: str) -> int:
    left = datetime.strptime(earlier, "%Y-%m-%d").date()
    right = datetime.strptime(later, "%Y-%m-%d").date()
    return (right - left).days


def headline_from_row(row: Mapping[str, Any]) -> tuple[str, AlphaIHeadline] | None:
    published = publication_date(row.get("published"))
    uid = str(row.get("uid") or "")
    if published is None or not uid:
        return None
    raw = row.get("sentiments") or {}
    if not isinstance(raw, dict):
        return None
    sentiments = {str(ticker): str(sentiment) for ticker, sentiment in raw.items()}
    headline = AlphaIHeadline(
        uid=uid,
        title=str(row.get("title") or uid),
        relevance=int(row.get("relevance") or 0),
        category=str(row.get("category") or "other"),
        tickers=tuple(sentiments),
        sentiments=sentiments,
        published_at=str(row.get("published") or published),
    )
    return published, headline


def load_headlines(path: Path) -> dict[str, list[AlphaIHeadline]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected archive at {path}")
    grouped: dict[str, list[AlphaIHeadline]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = headline_from_row(row)
        if parsed is None:
            continue
        day, headline = parsed
        grouped[day].append(headline)
    return dict(grouped)


def sessions_from_rows(rows: Sequence[Sequence[float]]) -> list[Session]:
    buckets: dict[str, list[Sequence[float]]] = {}
    for row in rows:
        if len(row) < 6:
            continue
        day = datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(row)
    out: list[Session] = []
    for day in sorted(buckets):
        bars = sorted(buckets[day], key=lambda item: int(item[0]))
        quote = sum(float(bar[5]) * float(bar[4]) for bar in bars)
        out.append(
            Session(
                day=day,
                open=float(bars[0][1]),
                high=max(float(bar[2]) for bar in bars),
                low=min(float(bar[3]) for bar in bars),
                close=float(bars[-1][4]),
                quote=quote,
                n_bars=len(bars),
            )
        )
    return out


def load_books(cache: Path, universe: Sequence[str]) -> dict[str, list[Session]]:
    books: dict[str, list[Session]] = {}
    for base in universe:
        path = cache / f"{base}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            books[base] = sessions_from_rows(rows)
    return books


def _chop(session: Session) -> float:
    if session.open <= 0 or session.high < session.low:
        return 0.0
    span = session.high - session.low - abs(session.close - session.open)
    return span / session.open


def usable_sessions(sessions: Sequence[Session], min_bars: int) -> list[Session]:
    rows = [
        session
        for session in sessions
        if session.n_bars >= min_bars and session.open > 0 and session.quote > 0
    ]
    rows.sort(key=lambda session: session.day)
    return rows


def _prior(usable: Sequence[Session], day: str, min_quote: float) -> tuple[float, float] | None:
    """Median quote and mean chop from the five usable sessions before ``day``."""
    cut = bisect.bisect_left(usable, day, key=lambda session: session.day)
    if cut < LOOKBACK:
        return None
    window = usable[cut - LOOKBACK : cut]
    if _gap(window[-1].day, day) > MAX_STALE_DAYS:
        return None
    adv = float(statistics.median(session.quote for session in window))
    if adv < min_quote:
        return None
    chop = sum(_chop(session) for session in window) / LOOKBACK
    return adv, chop


def _slip(notional: float, adv: float) -> float:
    if adv <= 0:
        return IMPACT_CAP
    return min(IMPACT_CAP, IMPACT_K * math.sqrt(max(0.0, notional) / adv))


def counts_for(
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    day: str,
    universe: Sequence[str],
) -> dict[str, tuple[int, int]]:
    """Bullish and bearish counts on publication days strictly before ``day``."""
    start = _shift(day, -TONE_DAYS)
    end = _shift(day, -1)
    allowed = set(universe)
    bull: Counter[str] = Counter()
    bear: Counter[str] = Counter()
    for story_day, stories in grouped.items():
        if not start <= story_day <= end:
            continue
        for headline in stories:
            if headline.relevance < MIN_RELEVANCE or headline.category == "insider":
                continue
            for base in headline.bullish_bases():
                if base in allowed:
                    bull[base] += 1
            for base in headline.bearish_bases():
                if base in allowed:
                    bear[base] += 1
    return {base: (bull[base], bear[base]) for base in universe}


def tone_names(counts: Mapping[str, tuple[int, int]]) -> list[str]:
    ranked: list[tuple[int, str]] = []
    for base, (bull, bear) in counts.items():
        if bull >= 1 and bull > bear:
            ranked.append((-(bull - bear), base))
    ranked.sort()
    return [base for _, base in ranked]


def _ranked(
    usable: Mapping[str, Sequence[Session]],
    day: str,
    universe: Sequence[str],
    mode: str,
    min_quote: float,
) -> list[str]:
    if mode == "book":
        return [
            base
            for base in universe
            if base in usable and _prior(usable[base], day, min_quote) is not None
        ]
    if mode == "chop":
        scored: list[tuple[float, str]] = []
        for base in universe:
            sessions = usable.get(base)
            if not sessions:
                continue
            prior = _prior(sessions, day, min_quote)
            if prior is None or prior[1] < MIN_CHOP:
                continue
            scored.append((-prior[1], base))
        scored.sort()
        return [base for _, base in scored]
    return []


def _buy(cash: float, lot: _Lot, spend: float, price: float, adv: float) -> tuple[float, float]:
    spend = min(spend, cash)
    if spend < DUST_EUR or price <= 0:
        return cash, 0.0
    fill = price * (1.0 + _slip(spend, adv))
    got = spend * (1.0 - TAKER) / fill
    lot.qty += got
    lot.cost += spend
    lot.adv = adv
    return cash - spend, spend


def _sell(cash: float, lot: _Lot, qty: float, price: float) -> tuple[float, float]:
    if qty <= 0 or lot.qty <= 0 or price <= 0:
        return cash, 0.0
    qty = min(qty, lot.qty)
    gross = qty * price
    if gross < DUST_EUR:
        return cash, 0.0
    fill = price * (1.0 - _slip(gross, lot.adv))
    proceeds = qty * fill * (1.0 - TAKER)
    released = lot.cost * (qty / lot.qty)
    lot.realized += proceeds - released
    lot.qty -= qty
    lot.cost -= released
    if lot.qty <= 1e-12:
        lot.qty = 0.0
        lot.cost = 0.0
    return cash + proceeds, proceeds


def _mark(cash: float, held: Mapping[str, _Lot], prices: Mapping[str, float]) -> float:
    equity = cash
    for base, lot in held.items():
        if lot.qty > 0 and base in prices:
            equity += lot.qty * prices[base]
    return equity


def simulate(
    books: Mapping[str, Sequence[Session]],
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    *,
    universe: Sequence[str],
    mode: str,
    start: str,
    end: str,
    min_bars: int = MIN_BARS,
    min_quote: float = MIN_QUOTE_EUR,
) -> Run:
    usable = {base: usable_sessions(sessions, min_bars) for base, sessions in books.items()}
    index = {
        base: {session.day: session for session in sessions}
        for base, sessions in books.items()
    }
    days = sorted(
        {
            session.day
            for base in universe
            for session in books.get(base, ())
            if start <= session.day <= end
        }
    )
    cash = SLEEVE_EUR
    held: dict[str, _Lot] = {base: _Lot() for base in universe}
    peak = SLEEVE_EUR
    max_dd = 0.0
    name_days = 0
    buys = 0
    sells = 0
    largest = 0.0
    marked = SLEEVE_EUR
    for offset, day in enumerate(days):
        prices = {
            base: session.open
            for base, sessions in index.items()
            if (session := sessions.get(day)) is not None and session.open > 0
        }
        if mode in {"four", "all"}:
            ranked = tone_names(counts_for(grouped, day, universe))
        else:
            ranked = _ranked(usable, day, universe, mode, min_quote)
        chosen: list[tuple[str, float]] = []
        for base in ranked:
            sessions = usable.get(base)
            if sessions is None or base not in prices:
                continue
            prior = _prior(sessions, day, min_quote)
            if prior is None:
                continue
            chosen.append((base, prior[0]))
            limit = MAX_NAMES if mode in {"four", "chop"} else None
            if limit is not None and len(chosen) >= limit:
                break
        gross = _mark(cash, held, prices)
        share = gross / len(chosen) if chosen else 0.0
        targets = {base: min(share, CAPACITY * adv) for base, adv in chosen}
        adv_of = dict(chosen)
        for base, lot in held.items():
            price = prices.get(base)
            if price is None or lot.qty <= 0:
                continue
            have = lot.qty * price
            want = targets.get(base, 0.0)
            if have > want + DUST_EUR:
                cash, proceeds = _sell(cash, lot, (have - want) / price, price)
                if proceeds >= DUST_EUR:
                    sells += 1
        for base, _adv in chosen:
            price = prices[base]
            lot = held[base]
            have = lot.qty * price
            want = targets[base]
            if want > have + DUST_EUR:
                cash, spent = _buy(cash, lot, want - have, price, adv_of[base])
                if spent >= DUST_EUR:
                    buys += 1
        closes = {
            base: session.close
            for base, sessions in index.items()
            if (session := sessions.get(day)) is not None and session.close > 0
        }
        for lot in held.values():
            if lot.qty > 0:
                lot.days += 1
                name_days += 1
        if offset == len(days) - 1:
            for base, lot in held.items():
                price = closes.get(base)
                if price is None or lot.qty <= 0:
                    continue
                cash, proceeds = _sell(cash, lot, lot.qty, price)
                if proceeds >= DUST_EUR:
                    sells += 1
        equity = _mark(cash, held, closes)
        day_pnl = equity - marked
        if abs(day_pnl) >= abs(largest):
            largest = day_pnl
        marked = equity
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    names = tuple(
        (base, lot.days, lot.realized)
        for base, lot in sorted(held.items(), key=lambda item: (-item[1].realized, item[0]))
        if lot.days or abs(lot.realized) > 1e-9
    )
    return Run(
        mode=mode,
        start=days[0] if days else start,
        end=days[-1] if days else end,
        pnl=marked - SLEEVE_EUR,
        end_equity=marked,
        max_dd=max_dd,
        days=len(days),
        name_days=name_days,
        buys=buys,
        sells=sells,
        largest_day=largest,
        names=names,
    )


def live_basket(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Names the published rollup would rank. Today's partial bucket is included."""
    ranked: list[tuple[int, str]] = []
    for base, payload in payloads.items():
        bull = int(payload.get("bullish") or 0)
        bear = int(payload.get("bearish") or 0)
        if bull >= 1 and bull > bear:
            ranked.append((-(bull - bear), base))
    ranked.sort()
    return [base for _, base in ranked]
