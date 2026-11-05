"""Walk-forward score for the next week's rise.

Usage::

    .venv/bin/python -m bot.research.rise_engine
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bot.research.rise_engine.engine import (
    FEATURES,
    Book,
    Engine,
    build,
    tape_calendar,
)
from bot.research.week_book.engine import Coin, make_coin

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/rise_engine/ENGINE.md")
MODELS = {
    "engine": "walk-forward",
    "near": "slot bij de high",
    "hold": "elke geschikte naam",
}
SLEEVES = {
    "all": "alle",
    "large": "groot",
    "obscure": "obscure",
}


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
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.1f}%".replace(".", ",")


def _num(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{value:.2f}".replace(".", ",")


def _lift(hit: float, base: float) -> str:
    if not np.isfinite(hit) or not np.isfinite(base) or base <= 0.0:
        return "—"
    return _num(hit / base)


def _write(path: Path, listed: int, engine: Engine) -> None:
    lines = [
        "# Engine voor de stijging van volgende week",
        "",
        f"Bitvavo heeft hier {listed} dagelijkse namen. De engine past elke",
        "week een lineair model op de rangen van de weken die al dicht zijn,",
        "en koopt het bovenste vijfde van de nieuwe week. De koop is de",
        "volgende open, de verkoop de open zeven dagen later.",
        "",
        "De score gebruikt zes dingen die op het slot bekend zijn: de afstand",
        "tot de high van 20 dagen, het rendement over 1, 7 en 28 dagen, de",
        "omzet tegen de eigen week, en de log van de omzet. Grote namen,",
        "minstens €1.000.000 die dag, krijgen een eigen model. Obscure namen,",
        "vanaf €10.000 en daaronder, ook. De eerste 26 weken zijn warmup.",
        "",
        "Ter vergelijking, op dezelfde weken: het bovenste vijfde op afstand",
        "tot de high, en elke geschikte naam. Een stijging is +10%, +20% of",
        "+50% tot de verkoop. Het boek begint op €20.000. Kosten zijn 0,25%",
        "taker per kant plus impact. Het haalt de lat alleen boven €20.000,",
        "met een plus in 2024, 2025 en 2026, en boven het boek dat alles in",
        "die mand aanhoudt. Niets wordt aangezet.",
        "",
        "## Herkenning",
        "",
        "Het getal naast een percentage is hoe vaak de keuze die stijging",
        "haalt, gedeeld door hoe vaak de hele mand hem haalt.",
        "",
        "| model | mand | +10% | × | +20% | × | +50% | × | bruto |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for book in engine.books:
        if book.model == "hold":
            continue
        lines.append(
            f"| {MODELS[book.model]} | {SLEEVES[book.sleeve]} "
            f"| {_pct(book.hit[0.10])} | {_lift(book.hit[0.10], book.base_hit[0.10])} "
            f"| {_pct(book.hit[0.20])} | {_lift(book.hit[0.20], book.base_hit[0.20])} "
            f"| {_pct(book.hit[0.50])} | {_lift(book.hit[0.50], book.base_hit[0.50])} "
            f"| {_pct(book.gross)} |"
        )
    lines += [
        "",
        "## Boek",
        "",
        "| model | mand | eind | PnL | terugval | weken | 2024 | 2025 | 2026 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for book in engine.books:
        lines.append(
            f"| {MODELS[book.model]} | {SLEEVES[book.sleeve]} | {_eur(book.equity)} "
            f"| {_eur(book.equity - 20_000.0)} | {_pct(book.dd)} | {book.weeks} "
            f"| {_eur(book.year_pnl.get(2024, 0.0))} "
            f"| {_eur(book.year_pnl.get(2025, 0.0))} "
            f"| {_eur(book.year_pnl.get(2026, 0.0))} |"
        )
    lines += [
        "",
        "## Gemiddelde coëfficiënt op de rang",
        "",
        "Positief wil zeggen: een hogere rang van die score ging in de",
        "voorbije weken samen met een hogere rang van het rendement erna.",
        "",
        "| mand | weken | " + " | ".join(FEATURES) + " |",
        "|---|---:|" + "---:|" * len(FEATURES),
    ]
    for fit in engine.fits:
        cells = " | ".join(_num(fit.coef[name]) for name in FEATURES)
        lines.append(f"| {SLEEVES[fit.sleeve]} | {fit.weeks} | {cells} |")
    lines += ["", _closing(engine.books), ""]
    path.write_text("\n".join(lines))


def _closing(books: list[Book]) -> str:
    cleared = [f"{MODELS[book.model]} {SLEEVES[book.sleeve]}" for book in books if book.clears]
    parts = []
    for sleeve, label in (("large", "Grote namen"), ("obscure", "Obscure namen")):
        book = next(row for row in books if row.model == "engine" and row.sleeve == sleeve)
        parts.append(
            f"{label}: de engine kiest namen waarvan {_pct(book.hit[0.10])} "
            f"binnen een week +10% haalt, tegen {_pct(book.base_hit[0.10])} "
            f"in de mand ({_lift(book.hit[0.10], book.base_hit[0.10])}×). "
            f"Het boek eindigt op {_eur(book.equity)}."
        )
    text = " ".join(parts)
    if cleared:
        text += " De lat wordt gehaald door " + ", ".join(cleared) + "."
    else:
        text += " Geen boek haalt de lat."
    text += " Er is niets aangezet."
    return text


def main() -> None:
    coins = []
    listed = 0
    for path in sorted(DAILY.glob("*.json")):
        listed += 1
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    print(f"listed {listed} loaded {len(coins)}", flush=True)
    engine = build(coins, tape_calendar(coins))
    _write(OUT, listed, engine)
    print(OUT.read_text())


if __name__ == "__main__":
    main()
