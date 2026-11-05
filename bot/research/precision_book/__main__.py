"""PnL of every locked way to trade the precision ladder.

Usage::

    .venv/bin/python -m bot.research.precision_book
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.precision_book.engine import (
    DEPLOYS,
    EXITS,
    LIVE_DD,
    LIVE_END,
    LIVE_PNL,
    LIVE_TRADES,
    LIVE_YEAR,
    YEARS,
    Book,
    run_tape,
    tape_calendar,
)
from bot.research.week_book.engine import Coin, make_coin

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/precision_book/BOOK.md")
NAMES = {
    "near": "slot bij de high",
    "fresh": "high, nog niet gestegen",
    "reversal": "grootste daling over 7 dagen",
    "size": "omzet die dag",
    "logit10": "model op +10%",
    "logit20": "model op +20%",
    "hold": "elke geschikte naam",
}
SLEEVES = {"all": "alle", "large": "groot", "obscure": "obscure"}
DEPLOY_NL = {"vol": "hele boek", "kap": "max 2% van de omzet"}
EXIT_NL = {
    "week": "open na 7 dagen",
    "stop15": "stop op −15%",
    "doel10": "verkoop op +10%",
    "doel50": "verkoop op +50%",
}
# The ladder's headline cells, fixed by that study's n≥100 rule.
HEADS = (
    ("size", "all", "1 naam", "een groene week"),
    ("reversal", "obscure", "1%", "+10%"),
    ("logit20", "large", "10%", "+20% en +50%"),
)


def _load(path: Path) -> Coin | None:
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


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%".replace(".", ",")


def _day(stamp: int) -> str:
    return datetime.fromtimestamp(int(stamp) / 1000, UTC).strftime("%Y-%m-%d")


def _row(book: Book) -> str:
    years = " | ".join(_eur(book.year_pnl.get(year, 0.0)) for year in YEARS)
    return (
        f"| {NAMES[book.score]} | {SLEEVES[book.sleeve]} | {book.slice} "
        f"| {DEPLOY_NL[book.deploy]} | {EXIT_NL[book.exit]} "
        f"| {_eur(book.equity)} | {_eur(book.pnl)} | {_pct(book.dd)} "
        f"| {_pct(book.mean_frac)} | {book.traded} | {years} |"
    )


def _table(lines: list[str], books: list[Book]) -> None:
    lines.append(
        "| score | mand | selectie | inzet | uitstap | eind | PnL | terugval "
        "| deel | weken | 2024 | 2025 | 2026 |"
    )
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for book in books:
        lines.append(_row(book))


def _name(book: Book) -> str:
    return (
        f"{NAMES[book.score]}, {SLEEVES[book.sleeve]}, {book.slice}, "
        f"{DEPLOY_NL[book.deploy]}, {EXIT_NL[book.exit]}"
    )


def _sentence(book: Book) -> str:
    years = ", ".join(f"{year} {_eur(book.year_pnl.get(year, 0.0))}" for year in YEARS)
    return (
        f"{_name(book)} eindigt op {_eur(book.equity)} "
        f"(PnL {_eur(book.pnl)}, terugval {_pct(book.dd)}, "
        f"inzet {_pct(book.mean_frac)}, {book.traded} weken, {years}). "
        f"Zonder taker en zonder impact eindigt dezelfde regel op {_eur(book.gross_equity)}."
    )


def _closing(books: list[Book]) -> str:
    ranked = sorted(books, key=lambda book: book.equity, reverse=True)
    winners = [book for book in ranked if book.clears]
    invested = [book for book in ranked if book.mean_frac >= 0.25 and book.score != "hold"]
    parts = []
    if winners:
        best = winners[0]
        parts.append(f"Het boek dat de lat haalt en het hoogst eindigt: {_sentence(best)}")
        if best.equity > LIVE_END:
            parts.append(
                f"Die eindstand ligt boven het live residual-boek van {_eur(LIVE_END)}."
            )
        else:
            parts.append(
                f"Het live residual-boek eindigt op {_eur(LIVE_END)} en blijft hoger."
            )
    else:
        parts.append("Geen regel haalt de lat.")
        if invested:
            parts.append(f"Het hoogste boek dat minstens een kwart inzet: {_sentence(invested[0])}")
        steady = [
            book
            for book in ranked
            if book.score != "hold"
            and all(book.year_pnl.get(year, 0.0) > 0.0 for year in YEARS)
        ]
        if steady:
            parts.append(f"Hoogste boek dat in elk jaar positief is: {_sentence(steady[0])}")
        else:
            parts.append("Geen boek is in 2024, 2025 en 2026 alle drie positief.")
        parts.append(
            f"Het live residual-boek eindigt op {_eur(LIVE_END)}, "
            f"PnL {_eur(LIVE_PNL)}, terugval {_pct(LIVE_DD)}, {LIVE_TRADES} rondes, "
            + ", ".join(f"{year} {_eur(LIVE_YEAR[year])}" for year in YEARS)
            + ". Dat boek blijft voor."
        )
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(path: Path, listed: int, first: int, last: int, books: list[Book]) -> None:
    lines = [
        "# Wat de aanwijzing doet voor de PnL",
        "",
        f"Bitvavo heeft hier {listed} dagelijkse namen. Het venster loopt van",
        f"{_day(first)} tot {_day(last)}. De namen zijn dezelfde als in de",
        "precisieladder: het slot bij de high, die high alleen als de naam",
        "over 7 dagen nog niet steeg, de grootste daling over 7 dagen, de",
        "omzet die dag, en twee logistische modellen die alleen afgesloten",
        "weken zien. De selectie is 1 naam en de bovenste 1%, 5%, 10% en 20%.",
        "Mand: alle namen vanaf €10.000, groot vanaf €1.000.000, en obscure",
        "daartussen. De eerste 26 weken trainen alleen.",
        "",
        "Twee inzetten en vier uitstappen stonden vast voor deze draai. Het",
        "hele boek wordt gelijk verdeeld. De andere inzet geeft een naam",
        "maximaal 2% van zijn omzet die dag, de rest blijft cash. De uitstap",
        "is de open na 7 dagen, een stop op −15%, of een verkoop zodra de",
        "koers +10% of +50% heeft aangetikt. Een gat door die grens vult op",
        "de open. Kosten zijn 0,25% taker per kant plus impact. Het boek",
        "begint op €20.000.",
        "",
        "De lat: minstens 30 weken, gemiddeld minstens een kwart van het",
        "kapitaal ingezet, eind boven €20.000, eind boven het boek dat elke",
        "geschikte naam in die mand aanhoudt met dezelfde inzet en uitstap,",
        "en in 2024, 2025 en 2026 elk minstens acht weken en een plus.",
        "Niets wordt aangezet.",
        "",
        "## Live",
        "",
        "Het boek dat aan staat is het residual-pakket uit tactic lab,",
        "venster 2024-03-16 t/m 2026-10-03, start €20.000, één munt. De regel",
        "is 10-daags excess met één dag overslaan, klok van 7 dagen, trail",
        "10% en lock +12%/+6%. Dit rapport meet een andere regel op een",
        "andere mand. De eindstand hiernaast is de gepubliceerde live-stand.",
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
        "## De drie koppen uit de precisie",
        "",
        "Dit zijn de cellen met de hoogste precisie bij minstens 100 keuzes.",
        "Hun PnL staat erbij voor elke inzet en elke uitstap.",
        "",
    ]
    _table(
        lines,
        [
            book
            for score, sleeve, slice_name, _label in HEADS
            for book in books
            if book.score == score and book.sleeve == sleeve and book.slice == slice_name
        ],
    )
    winners = [book for book in books if book.clears]
    lines += ["", "## Boeken die de lat halen", ""]
    if winners:
        _table(lines, sorted(winners, key=lambda book: book.equity, reverse=True))
    else:
        lines.append("Geen enkel boek haalt de lat.")
    lines += ["", "## Hoogste tien eindstanden", ""]
    _table(lines, sorted(books, key=lambda book: book.equity, reverse=True)[:10])
    for kind in EXITS:
        for deploy in DEPLOYS:
            lines += ["", f"## {EXIT_NL[kind]}, {DEPLOY_NL[deploy]}", ""]
            _table(
                lines,
                [
                    book
                    for book in books
                    if book.exit == kind and book.deploy == deploy
                ],
            )
    lines += ["", "Er is niets aangezet.", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    coins = []
    listed = 0
    for path in sorted(DAILY.glob("*.json")):
        listed += 1
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    dates = tape_calendar(coins)
    books = run_tape(coins, dates)
    _write(OUT, listed, int(dates[0]), int(dates[-1]), books)
    print(_closing(books))


if __name__ == "__main__":
    main()
