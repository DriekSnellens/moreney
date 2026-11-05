"""Ask whether CoinMarketCap improves the book that is already live.

The book is the armed residual pack: one name, 10-day excess with one day
skipped, a 7-day clock, a 10% trail and a lock at +12%/+6%. Same costs.
The only new input is CoinMarketCap, and every gate was fixed before this
run.

Global gates use the print that matches the session close. A later print
stays hidden. They are: market cap up or down over seven days, dominance
up or down, both combinations, and world volume at least 1.5 times or at
most 0.7 times the prior seven prints.

Coin gates turn on only once twenty names have a print that day. Before
that the live rule is left alone, because this key has no coin history
there. Once the tape is on:

* cmc7 — the name's own seven-day CMC change is positive
* excess — that change beats the name with the largest CMC market cap
* top10, top20, top50 — the name is inside that market-cap sleeve

A gate beats the live book on this same tape only when it ends higher,
keeps a positive euro result in 2024, 2025 and 2026, has at least 30
round trips, and its drawdown is at most 15 percentage points worse.
Nothing is armed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from bot.research.cmc_regime.engine import (
    LIVE_DD,
    LIVE_END,
    LIVE_PNL,
    LIVE_TRADES,
    LIVE_YEAR,
    REGIMES,
    Market,
    Quote,
    allows,
)
from bot.research.week_book.engine import DAY_MS

ONLINE = 20
SLEEVES = (10, 20, 50)
MIN_TRADES = 30
DD_SLACK = 0.15
YEARS = ("2024", "2025", "2026")
Gate = Callable[[str, str], bool]


@dataclass
class Row:
    name: str
    end: float
    pnl: float
    dd: float
    trades: int
    year: dict[str, float]
    beats: bool = False


def session_ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def global_gate(market: Market, regime: str) -> Gate:
    def gate(date: str, _base: str) -> bool:
        return allows(regime, market.view(session_ms(date)))

    return gate


def _asof_rows(series: Mapping[str, Mapping[int, Quote]], asof: int) -> list[tuple[str, Quote]]:
    found: list[tuple[str, Quote]] = []
    for base, rows in series.items():
        row = rows.get(asof)
        if row is not None and row.mcap > 0.0:
            found.append((base, row))
    return found


def coin_gate(series: Mapping[str, Mapping[int, Quote]], kind: str) -> Gate:
    """Use the coin print once twenty names are present. Otherwise leave the trade."""
    cache: dict[int, list[tuple[str, Quote]]] = {}

    def cross(asof: int) -> list[tuple[str, Quote]]:
        if asof not in cache:
            cache[asof] = _asof_rows(series, asof)
        return cache[asof]

    def gate(date: str, base: str) -> bool:
        asof = session_ms(date) + DAY_MS
        rows = cross(asof)
        if len(rows) < ONLINE:
            return True
        own = series.get(base, {}).get(asof)
        if kind == "cmc7":
            return own is not None and math.isfinite(own.pct7) and own.pct7 > 0.0
        if kind == "excess":
            if own is None or not math.isfinite(own.pct7):
                return False
            leader = max(rows, key=lambda item: (item[1].mcap, item[0]))
            lead = leader[1].pct7
            if not math.isfinite(lead):
                return False
            return own.pct7 > lead
        width = int(kind.removeprefix("top"))
        ranked = sorted(rows, key=lambda item: (-item[1].mcap, item[0]))
        return base in {name for name, _row in ranked[:width]}

    return gate


def beats(row: Row, control: Row) -> bool:
    if row.trades < MIN_TRADES or row.end <= control.end:
        return False
    if row.dd > control.dd + DD_SLACK:
        return False
    return all(row.year.get(year, 0.0) > 0.0 for year in YEARS)


def gate_table(
    market: Market, series: Mapping[str, Mapping[int, Quote]]
) -> list[tuple[str, Gate | None]]:
    rows: list[tuple[str, Gate | None]] = [("live", None)]
    for regime in REGIMES:
        if regime == "altijd":
            continue
        rows.append((regime, global_gate(market, regime)))
    for kind in ("cmc7", "excess", *(f"top{n}" for n in SLEEVES)):
        rows.append((kind, coin_gate(series, kind)))
    return rows


# Re-exported so the report can cite the published pack without a second definition.
PUBLISHED_END = LIVE_END
PUBLISHED_PNL = LIVE_PNL
PUBLISHED_DD = LIVE_DD
PUBLISHED_TRADES = LIVE_TRADES
PUBLISHED_YEAR = LIVE_YEAR
