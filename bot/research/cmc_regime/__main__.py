"""CoinMarketCap regimes against the live residual book.

Usage::

    CMC_API_KEY=... .venv/bin/python -m bot.research.cmc_regime
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.cmc_regime.engine import (
    LIVE_DD,
    LIVE_END,
    LIVE_PNL,
    LIVE_TRADES,
    LIVE_YEAR,
    YEARS,
    Book,
    Market,
    Point,
    Quote,
    run_regimes,
    run_scores,
)
from bot.research.cmc_regime.fetch import GLOBAL, QUOTES, ensure
from bot.research.week_book.engine import DAY_MS, Coin, make_coin, master_dates

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/cmc_regime/CMC.md")
SELECT_NL = {
    "alles": "elke liquide naam",
    "mom7": "hoogste stijging 7 dagen",
    "omzet": "hoogste omzet",
    "top5": "vijf hoogste stijgingen",
    "cmc7": "CMC-stijging 7 dagen",
    "cmc_cap": "CMC-marktkapitaal",
    "bv7": "Bitvavo-stijging 7 dagen",
}
REGIME_NL = {
    "altijd": "elke week",
    "mcap+": "marktkapitaal stijgt",
    "mcap-": "marktkapitaal daalt",
    "dom+": "dominantie stijgt",
    "dom-": "dominantie daalt",
    "seizoen": "kapitaal stijgt, dominantie daalt",
    "btc_sterk": "kapitaal en dominantie stijgen",
    "luid": "wereldvolume minstens 1,5×",
    "stil": "wereldvolume hoogstens 0,7×",
    "top1": "1 naam",
    "top5": "5 namen",
}


def _load_coin(path: Path) -> Coin | None:
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    return make_coin(
        path.stem,
        cols[0].astype(np.int64),
        cols[1],
        cols[2],
        cols[3],
        cols[4],
        cols[4] * cols[5],
    )


def _load_market() -> tuple[Market, list[dict]]:
    raw = json.loads(GLOBAL.read_text())
    points = [
        Point(int(row["ts"]), float(row["mcap"]), float(row["volume"]), float(row["btc_dom"]))
        for row in raw["points"]
    ]
    return Market(points), raw["points"]


def _load_quotes() -> tuple[dict[str, dict[int, Quote]], dict]:
    raw = json.loads(QUOTES.read_text())
    series: dict[str, dict[int, Quote]] = {}
    for base, meta in raw["names"].items():
        rows: dict[int, Quote] = {}
        for point in meta["points"]:
            ts, _price, mcap, _vol, pct = point
            pct_f = float("nan") if pct is None else float(pct)
            rows[int(ts)] = Quote(int(ts), float(mcap), pct_f)
        series[base] = rows
    return series, raw


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    sign = "−" if value < 0 else ""
    text = f"{abs(100.0 * value):.1f}".replace(".", ",")
    return f"{sign}{text}%"


def _day(stamp: int) -> str:
    return datetime.fromtimestamp(int(stamp) / 1000, UTC).strftime("%Y-%m-%d")


def _row(book: Book) -> str:
    years = " | ".join(_eur(book.year_pnl.get(year, 0.0)) for year in YEARS)
    return (
        f"| {SELECT_NL[book.selection]} | {REGIME_NL[book.regime]} "
        f"| {_eur(book.equity)} | {_eur(book.pnl)} | {_pct(book.dd)} "
        f"| {book.traded} | {years} |"
    )


def _table(lines: list[str], books: list[Book]) -> None:
    lines.append("| boek | filter | eind | PnL | terugval | weken | 2024 | 2025 | 2026 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for book in books:
        lines.append(_row(book))


def _change(points: list[dict], days: int) -> float:
    last = points[-1]
    target = int(last["ts"]) - days * DAY_MS
    prior = next((row for row in points if int(row["ts"]) == target), None)
    if prior is None or float(prior["mcap"]) <= 0.0:
        return float("nan")
    return float(last["mcap"]) / float(prior["mcap"]) - 1.0


def _sentence(book: Book) -> str:
    years = ", ".join(f"{year} {_eur(book.year_pnl.get(year, 0.0))}" for year in YEARS)
    return (
        f"{SELECT_NL[book.selection]}, {REGIME_NL[book.regime]}, "
        f"eind {_eur(book.equity)} (PnL {_eur(book.pnl)}, terugval {_pct(book.dd)}, "
        f"{book.traded} weken, {years}). "
        f"Zonder taker en zonder impact eindigt dezelfde regel op {_eur(book.gross_equity)}."
    )


def _closing(books: list[Book]) -> str:
    ranked = sorted(
        (book for book in books if book.regime != "altijd"),
        key=lambda book: book.equity,
        reverse=True,
    )
    winners = [book for book in ranked if book.clears]
    parts = []
    if winners:
        best = winners[0]
        parts.append(f"Het filter dat de lat haalt en het hoogst eindigt: {_sentence(best)}")
    else:
        parts.append("Geen filter haalt de lat.")
        if ranked:
            parts.append(f"Het hoogste filter: {_sentence(ranked[0])}")
    steady = [
        book
        for book in ranked
        if all(book.year_pnl.get(year, 0.0) > 0.0 for year in YEARS)
    ]
    if steady:
        parts.append(f"Hoogste filter dat in elk jaar positief is: {_sentence(steady[0])}")
    else:
        parts.append("Geen filter is in 2024, 2025 en 2026 alle drie positief.")
    best_end = ranked[0].equity if ranked else 0.0
    if best_end > LIVE_END:
        parts.append(f"Die eindstand ligt boven het live residual-boek van {_eur(LIVE_END)}.")
    else:
        parts.append(
            f"Het live residual-boek eindigt op {_eur(LIVE_END)}, PnL {_eur(LIVE_PNL)}, "
            f"terugval {_pct(LIVE_DD)}, {LIVE_TRADES} rondes, "
            + ", ".join(f"{year} {_eur(LIVE_YEAR[year])}" for year in YEARS)
            + ". Dat boek blijft voor."
        )
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(
    path: Path,
    listed: int,
    points: list[dict],
    matched: int,
    books: list[Book],
    scores: list[Book],
) -> None:
    last = points[-1]
    dom = f"{float(last['btc_dom']):.1f}".replace(".", ",")
    eth = f"{float(last['eth_dom']):.1f}".replace(".", ",")
    lines = [
        "# CoinMarketCap als filter op het wekelijkse boek",
        "",
        f"Bitvavo heeft hier {listed} dagelijkse namen. CoinMarketCap telt op",
        f"{_day(int(last['ts']))} {int(last['active'])} actieve munten, een",
        f"marktkapitaal van {_eur(float(last['mcap']))} en een dominantie van",
        f"de grootste munt van {dom}%.",
        f"De dominantie van de tweede munt is {eth}%.",
        f"Over 30 dagen veranderde het marktkapitaal {_pct(_change(points, 30))},",
        f"over 90 dagen {_pct(_change(points, 90))}.",
        "",
        "De dagelijkse stempel van CoinMarketCap is de Bitvavo-slot van de",
        "vorige sessie. Dat is gecontroleerd op de prijs van de grootste munt:",
        "de stempel van middernacht valt samen met die slotkoers. De koop is",
        "de volgende open, de verkoop de open zeven dagen later. Een latere",
        "stempel blijft onzichtbaar.",
        "",
        "Het filter stond vast: marktkapitaal omhoog of omlaag over 7 dagen,",
        "dominantie omhoog of omlaag in procentpunten, beide combinaties, en",
        "wereldvolume minstens 1,5 keer of hoogstens 0,7 keer het gemiddelde",
        "van de zeven dagen ervoor. Daaronder ligt een vast Bitvavo-boek:",
        "elke liquide naam, de hoogste stijging over 7 dagen, de hoogste",
        "omzet, of de vijf hoogste stijgingen. Liquide is minstens €1.000.000",
        "omzet en geen peg. Start €20.000. Kosten zijn 0,25% taker per kant",
        "plus impact.",
        "",
        "De lat: minstens 30 weken, eind boven €20.000, eind boven hetzelfde",
        "boek zonder filter, en in 2024, 2025 en 2026 elk minstens acht weken",
        "en een plus. Niets wordt aangezet.",
        "",
        "## Live",
        "",
        "Het boek dat aan staat is het residual-pakket uit tactic lab,",
        "venster 2024-03-16 t/m 2026-10-03, één munt, trail 10% en lock",
        "+12%/+6%. De eindstand is de gepubliceerde live-stand.",
        "",
        "| boek | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| live residual | {_eur(LIVE_END)} | {_eur(LIVE_PNL)} | {_pct(LIVE_DD)} "
        f"| {LIVE_TRADES} | {_eur(LIVE_YEAR[2024])} | {_eur(LIVE_YEAR[2025])} "
        f"| {_eur(LIVE_YEAR[2026])} |",
        "",
        "## Uitkomst",
        "",
        _closing(books),
        "",
        "## Elke filter",
        "",
    ]
    _table(lines, books)
    lines += [
        "",
        "## Twaalf maanden muntquotes",
        "",
        f"Deze sleutel geeft muntkoersen vanaf 2025-11-05. {matched} Bitvavo-namen",
        "vielen samen met een CoinMarketCap-id: de euro-prijs week in de mediaan",
        "hoogstens 25% af van de Bitvavo-slot. Botsende symbolen houden het id",
        "waarvan de prijs het dichtst bij die slot ligt. De scores zijn de",
        "7-daagse CMC-verandering, het CMC-marktkapitaal, en de eigen",
        "Bitvavo-stijging over 7 dagen, op dezelfde weken en met dezelfde",
        "kosten. 2024 heeft hier geen trades. Dit venster vervangt het live",
        "boek niet.",
        "",
    ]
    _table(lines, scores)
    lines += ["", "Er is niets aangezet.", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    coins = []
    listed = 0
    for path in sorted(DAILY.glob("*.json")):
        listed += 1
        coin = _load_coin(path)
        if coin is not None:
            coins.append(coin)
    ensure(coins)
    market, points = _load_market()
    series, raw = _load_quotes()
    dates = master_dates(coins)
    books = run_regimes(coins, dates, market)
    scores = run_scores(coins, dates, series)
    _write(OUT, listed, points, len(raw["names"]), books, scores)
    print(_closing(books))


if __name__ == "__main__":
    main()
