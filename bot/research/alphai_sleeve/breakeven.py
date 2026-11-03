"""Top ten bullish alts, scratched at break-even on a bearish week.

Nothing is armed. Reproduce with ``python -m bot.research.alphai_sleeve.breakeven``.
"""

from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.core.venue_fees import VENUE_MAKER_FEE
from bot.integrations.alphai.parse import AlphaIHeadline
from bot.research.alphai_sleeve.book import (
    CAPACITY,
    DUST_EUR,
    LOOKBACK,
    MAX_STALE_DAYS,
    MIN_BARS,
    SLEEVE_EUR,
    TAKER,
    TONE_DAYS,
    _gap,
    _shift,
    _slip,
    counts_for,
    load_headlines,
)
from bot.research.alphai_sleeve.universe import (
    INTRADAY,
    LISTED_MIN_QUOTE,
    alt_bases,
    fetch_markets,
)

TOP_N = 10
MIN_ORDER_EUR = 5.0
MAKER = float(VENUE_MAKER_FEE["bitvavo"])
ARCHIVE = Path("data/research/alphai_crypto_news.json")
OUT = Path(__file__).with_name("BREAKEVEN.md")
MODES = ("scratch", "hold")

Bar = tuple[float, float, float, float]


@dataclass(frozen=True)
class SessionBars:
    day: str
    quote: float
    n_bars: int
    bars: tuple[Bar, ...]


@dataclass(frozen=True)
class Run:
    mode: str
    pnl: float
    end_equity: float
    max_dd: float
    days: int
    bearish_days: int
    buys: int
    scratch_maker: int
    scratch_cross: int
    scratch_pnl: float
    rotations: int
    rotation_pnl: float
    flattens: int
    flatten_pnl: float
    open_into_flatten: int
    largest_day: float
    names: tuple[tuple[str, float], ...]


@dataclass
class _Lot:
    qty: float = 0.0
    cost: float = 0.0
    adv: float = 0.0
    scratch: bool = False
    last: float = 0.0
    realized: float = 0.0


@dataclass
class _Book:
    cash: float
    held: dict[str, _Lot] = field(default_factory=dict)
    buys: int = 0
    scratch_maker: int = 0
    scratch_cross: int = 0
    scratch_pnl: float = 0.0
    rotations: int = 0
    rotation_pnl: float = 0.0
    flattens: int = 0
    flatten_pnl: float = 0.0
    by_name: dict[str, float] = field(default_factory=lambda: defaultdict(float))


def bearish_week(counts: Mapping[str, tuple[int, int]]) -> bool:
    """True when the prior week has strictly more bearish headlines."""
    bull = sum(pair[0] for pair in counts.values())
    bear = sum(pair[1] for pair in counts.values())
    return bear > bull


def top_names(
    counts: Mapping[str, tuple[int, int]],
    eligible: Sequence[str],
    n: int = TOP_N,
) -> list[str]:
    """Strongest bullish names, at most ``n``. Ties break alphabetically."""
    ranked: list[tuple[int, int, str]] = []
    for base in eligible:
        bull, bear = counts.get(base, (0, 0))
        if bull >= 1 and bull > bear:
            ranked.append((-(bull - bear), -bull, base))
    ranked.sort()
    return [base for _, _, base in ranked[:n]]


def break_even_price(cost: float, qty: float) -> float:
    """Maker sale that returns the euros spent, fees included."""
    if qty <= 0 or cost <= 0:
        return 0.0
    return cost / (qty * (1.0 - MAKER))


def sessions_from_rows(rows: Sequence[Sequence[float]], bar_from: str) -> list[SessionBars]:
    buckets: dict[str, list[Sequence[float]]] = defaultdict(list)
    for row in rows:
        if len(row) < 6:
            continue
        day = datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")
        buckets[day].append(row)
    out: list[SessionBars] = []
    for day in sorted(buckets):
        ordered = sorted(buckets[day], key=lambda item: int(item[0]))
        quote = sum(float(bar[5]) * float(bar[4]) for bar in ordered)
        if day >= bar_from:
            bars = tuple(
                (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4])) for bar in ordered
            )
        else:
            bars = ()
        out.append(SessionBars(day, quote, len(ordered), bars))
    return out


def load_tapes(
    cache: Path,
    bases: Sequence[str],
    bar_from: str,
) -> dict[str, list[SessionBars]]:
    books: dict[str, list[SessionBars]] = {}
    for offset, base in enumerate(bases, start=1):
        path = cache / f"{base}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            books[base] = sessions_from_rows(rows, bar_from)
        if offset % 40 == 0:
            print(f"tapes {offset}/{len(bases)}", flush=True)
    return books


def _adv(history: Sequence[tuple[str, float]], day: str, min_quote: float) -> float | None:
    cut = bisect.bisect_left(history, (day,))
    if cut < LOOKBACK:
        return None
    window = history[cut - LOOKBACK : cut]
    if _gap(window[-1][0], day) > MAX_STALE_DAYS:
        return None
    adv = float(statistics.median(quote for _day, quote in window))
    if adv < min_quote:
        return None
    return adv


def _taker_proceeds(qty: float, price: float, adv: float) -> float:
    gross = qty * price
    if gross < DUST_EUR:
        return 0.0
    return gross * (1.0 - _slip(gross, adv)) * (1.0 - TAKER)


def _realize(book: _Book, base: str, lot: _Lot, proceeds: float) -> float:
    pnl = proceeds - lot.cost
    lot.realized += pnl
    book.by_name[base] += pnl
    book.cash += proceeds
    lot.qty = 0.0
    lot.cost = 0.0
    lot.scratch = False
    return pnl


def _scratch_bar(lot: _Lot, bar: Bar) -> tuple[str, float] | None:
    """A resting break-even sell. The fill price is never the bar's high."""
    be = break_even_price(lot.cost, lot.qty)
    if be <= 0:
        return None
    open_, high, _low, _close = bar
    if open_ >= be:
        proceeds = _taker_proceeds(lot.qty, open_, lot.adv)
        if proceeds < DUST_EUR:
            return None
        return "cross", proceeds
    if high >= be:
        proceeds = lot.qty * be * (1.0 - MAKER)
        if proceeds < DUST_EUR:
            return None
        return "maker", proceeds
    return None


def _buy(book: _Book, base: str, spend: float, price: float, adv: float) -> None:
    fill = price * (1.0 + _slip(spend, adv))
    if fill <= 0:
        return
    qty = spend * (1.0 - TAKER) / fill
    lot = book.held.setdefault(base, _Lot())
    lot.qty += qty
    lot.cost += spend
    lot.adv = adv
    lot.last = price
    lot.scratch = False
    book.cash -= spend
    book.buys += 1


def _mark(book: _Book, closes: Mapping[str, float]) -> float:
    equity = book.cash
    for base, lot in book.held.items():
        if lot.qty <= 0:
            continue
        price = closes.get(base, lot.last)
        if price <= 0:
            continue
        equity += _taker_proceeds(lot.qty, price, lot.adv)
    return equity


def simulate(
    books: Mapping[str, Sequence[SessionBars]],
    grouped: Mapping[str, Sequence[AlphaIHeadline]],
    *,
    universe: Sequence[str],
    mode: str,
    start: str,
    end: str,
    min_bars: int = MIN_BARS,
    min_quote: float = LISTED_MIN_QUOTE,
) -> Run:
    if mode not in MODES:
        raise ValueError(mode)
    index = {base: {row.day: row for row in rows} for base, rows in books.items()}
    history = {
        base: tuple(
            (row.day, row.quote) for row in rows if row.n_bars >= min_bars and row.quote > 0
        )
        for base, rows in books.items()
    }
    days = sorted(
        {
            row.day
            for base in universe
            for row in books.get(base, ())
            if start <= row.day <= end and row.bars
        }
    )
    book = _Book(SLEEVE_EUR)
    peak = SLEEVE_EUR
    max_dd = 0.0
    largest = 0.0
    previous = SLEEVE_EUR
    bearish_days = 0
    open_into_flatten = 0
    for offset, day in enumerate(days):
        counts = counts_for(grouped, day, universe)
        bearish = bearish_week(counts)
        if bearish:
            bearish_days += 1
        eligible: list[str] = []
        advs: dict[str, float] = {}
        for base in universe:
            row = index.get(base, {}).get(day)
            if row is None or not row.bars:
                continue
            adv = _adv(history.get(base, ()), day, min_quote)
            if adv is None:
                continue
            eligible.append(base)
            advs[base] = adv
        chosen = top_names(counts, eligible)
        if bearish and mode == "scratch":
            for lot in book.held.values():
                if lot.qty > 0:
                    lot.scratch = True
        if not bearish:
            for base, lot in list(book.held.items()):
                row = index.get(base, {}).get(day)
                if lot.qty <= 0 or lot.scratch or base in chosen or row is None or not row.bars:
                    continue
                proceeds = _taker_proceeds(lot.qty, row.bars[0][0], advs.get(base, lot.adv))
                if proceeds < DUST_EUR:
                    continue
                pnl = _realize(book, base, lot, proceeds)
                book.rotations += 1
                book.rotation_pnl += pnl
            missing = [
                base
                for base in chosen
                if book.held.get(base) is None or book.held[base].qty <= 0
            ]
            if missing and book.cash >= MIN_ORDER_EUR:
                mark = book.cash
                for base, lot in book.held.items():
                    row = index.get(base, {}).get(day)
                    if lot.qty > 0 and row is not None and row.bars:
                        mark += lot.qty * row.bars[0][0]
                target = mark / TOP_N
                slot = min(target, book.cash / len(missing))
                for base in missing:
                    row = index[base][day]
                    if row.bars[0][0] <= 0:
                        continue
                    spend = min(slot, CAPACITY * advs[base], book.cash)
                    if spend < MIN_ORDER_EUR:
                        continue
                    _buy(book, base, spend, row.bars[0][0], advs[base])
        if mode == "scratch":
            for base, lot in list(book.held.items()):
                row = index.get(base, {}).get(day)
                if lot.qty <= 0 or not lot.scratch or row is None:
                    continue
                lot.adv = advs.get(base, lot.adv)
                for bar in row.bars:
                    hit = _scratch_bar(lot, bar)
                    if hit is None:
                        continue
                    kind, proceeds = hit
                    pnl = _realize(book, base, lot, proceeds)
                    book.scratch_pnl += pnl
                    if kind == "maker":
                        book.scratch_maker += 1
                    else:
                        book.scratch_cross += 1
                    break
        closes = {
            base: row.bars[-1][3]
            for base in universe
            if (row := index.get(base, {}).get(day)) is not None and row.bars
        }
        for base, lot in book.held.items():
            if lot.qty > 0 and base in closes:
                lot.last = closes[base]
        last = offset == len(days) - 1
        if last:
            for base, lot in list(book.held.items()):
                if lot.qty <= 0 or lot.last <= 0:
                    continue
                open_into_flatten += 1
                proceeds = _taker_proceeds(lot.qty, lot.last, lot.adv)
                if proceeds < DUST_EUR:
                    continue
                pnl = _realize(book, base, lot, proceeds)
                book.flattens += 1
                book.flatten_pnl += pnl
        equity = _mark(book, closes)
        change = equity - previous
        if abs(change) >= abs(largest):
            largest = change
        previous = equity
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    ranked = tuple(sorted(book.by_name.items(), key=lambda item: (-item[1], item[0])))
    return Run(
        mode=mode,
        pnl=book.cash - SLEEVE_EUR,
        end_equity=book.cash,
        max_dd=max_dd,
        days=len(days),
        bearish_days=bearish_days,
        buys=book.buys,
        scratch_maker=book.scratch_maker,
        scratch_cross=book.scratch_cross,
        scratch_pnl=book.scratch_pnl,
        rotations=book.rotations,
        rotation_pnl=book.rotation_pnl,
        flattens=book.flattens,
        flatten_pnl=book.flatten_pnl,
        open_into_flatten=open_into_flatten,
        largest_day=largest,
        names=tuple((base, pnl) for base, pnl in ranked if abs(pnl) > 1e-9),
    )


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _last_complete_day() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _line(run: Run) -> str:
    top = ", ".join(f"{base} {_eur(pnl)}" for base, pnl in run.names[:4]) or "—"
    return (
        f"| {run.mode} | {_eur(run.pnl)} | {_pct(run.max_dd)} | {run.bearish_days} | "
        f"{run.buys} | {run.scratch_maker} | {run.scratch_cross} | {_eur(run.scratch_pnl)} | "
        f"{run.rotations} | {_eur(run.rotation_pnl)} | {run.flattens} | "
        f"{_eur(run.flatten_pnl)} | {run.open_into_flatten} | {_eur(run.largest_day)} | {top} |"
    )


def _render(rows: Sequence[Run], *, n_listed: int, overlap: str, last: str) -> str:
    header = (
        "| mode | pnl | max DD | bearish days | buys | scratch at BE | "
        "scratch above BE | scratch pnl | rotations | rotation pnl | flattens | "
        "flatten pnl | still open | largest day | largest names |"
    )
    rule = (
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | --- |"
    )
    return "\n".join(
        [
            "# Break-even week",
            "",
            "Rules are the module docstring in `__init__.py`. Ten bullish alts.",
            "A bearish week rests sells at the fee-inclusive break-even.",
            f"Sleeve €{SLEEVE_EUR:,.0f} in {TOP_N} slots. Listed alts {n_listed}.",
            f"News overlap {overlap} → {last}. No path before 2026.",
            "",
            header,
            rule,
            "\n".join(_line(run) for run in rows),
            "",
            "The archive has no path before 2026. No cell clears both periods.",
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.alphai_sleeve.breakeven`",
            "",
        ]
    )


def main() -> None:
    last = _last_complete_day()
    grouped = load_headlines(ARCHIVE)
    overlap = _shift(min(grouped), TONE_DAYS)
    markets = fetch_markets()
    listed = alt_bases(markets)
    print(f"listed {len(listed)} overlap {overlap} → {last}", flush=True)
    books = load_tapes(INTRADAY, listed, overlap)
    print(f"loaded {len(books)}", flush=True)
    rows: list[Run] = []
    for mode in MODES:
        run = simulate(
            books,
            grouped,
            universe=listed,
            mode=mode,
            start=overlap,
            end=last,
        )
        rows.append(run)
        print(
            f"{mode:8} {_eur(run.pnl):>12} dd {_pct(run.max_dd):>6} "
            f"bear {run.bearish_days} scratch {run.scratch_maker}+{run.scratch_cross} "
            f"flat {run.flattens}",
            flush=True,
        )
    text = _render(rows, n_listed=len(listed), overlap=overlap, last=last)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
