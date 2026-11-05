"""Precision of each fixed way of pointing at a riser.

Usage::

    .venv/bin/python -m bot.research.precision_ladder
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bot.research.precision_ladder.engine import (
    LABELS,
    headline,
    run_tape,
    tape_calendar,
)
from bot.research.week_book.engine import Coin, make_coin

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/precision_ladder/PRECISION.md")
NAMES = {
    "near": "slot bij de high",
    "fresh": "high, nog niet gestegen",
    "reversal": "grootste daling over 7 dagen",
    "size": "omzet die dag",
    "logit10": "model op +10%",
    "logit20": "model op +20%",
}
SLEEVES = {"all": "alle", "large": "groot", "obscure": "obscure"}


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


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.1f}%".replace(".", ",")


def _times(precision: float, base: float) -> str:
    if not np.isfinite(precision) or not np.isfinite(base) or base <= 0.0:
        return "—"
    return f"{precision / base:.2f}".replace(".", ",")


def _write(path: Path, listed: int, cells: list) -> None:
    lines = [
        "# Hoe vaak de aangewezen naam stijgt",
        "",
        f"Bitvavo heeft hier {listed} dagelijkse namen. Een stijger wordt",
        "gemeten van de volgende open tot de open zeven dagen later.",
        "Precisie is het aandeel van de aangewezen namen dat de stap haalt.",
        "Recall is het aandeel van alle stijgers dat in die keuze zat.",
        "De basis is het aandeel in de hele mand.",
        "",
        "De selectie staat vast: de hoogste naam, en de bovenste 1%, 5%,",
        "10% en 20% van de mand die week. Groot is minstens €1.000.000 omzet,",
        "obscure is €10.000 tot daaronder. Een week heeft minstens 10 namen.",
        "De eerste 26 weken trainen alleen.",
        "",
        "Zes scores: het slot tegen de high van 20 dagen, die high alleen bij",
        "namen die over 7 dagen nog niet stegen, de grootste 7-daagse daling,",
        "de omzet, en twee logistische modellen (op +10% en op +20%) die alleen",
        "afgesloten weken zien. De straf staat op 1, de klassen wegen gelijk.",
        "Het hoogste percentage met minstens 100 keuzes is de kop. Het hoogste",
        "percentage op een kleinere cel staat erbij, met het aantal. Niets",
        "wordt aangezet.",
        "",
        "## Precisie op +10%",
        "",
        "| score | mand | selectie | n | precisie | basis | × | recall | 2024 | 2025 | 2026 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in cells:
        year = cell.year_precision["+10%"]
        lines.append(
            f"| {NAMES[cell.score]} | {SLEEVES[cell.sleeve]} | {cell.slice} "
            f"| {cell.n} | {_pct(cell.precision['+10%'])} "
            f"| {_pct(cell.base['+10%'])} | {_times(cell.precision['+10%'], cell.base['+10%'])} "
            f"| {_pct(cell.recall['+10%'])} | {_pct(year[2024])} "
            f"| {_pct(year[2025])} | {_pct(year[2026])} |"
        )
    lines += [
        "",
        "## Eén naam, alle stappen",
        "",
        "| score | mand | groen | +10% | +20% | +50% | bruto |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for cell in cells:
        if cell.slice != "1 naam":
            continue
        lines.append(
            f"| {NAMES[cell.score]} | {SLEEVES[cell.sleeve]} "
            f"| {_pct(cell.precision['groen'])} | {_pct(cell.precision['+10%'])} "
            f"| {_pct(cell.precision['+20%'])} | {_pct(cell.precision['+50%'])} "
            f"| {_pct(cell.gross)} |"
        )
    lines += ["", _closing(cells), ""]
    path.write_text("\n".join(lines))


def _name(cell) -> str:
    return f"{NAMES[cell.score]}, {SLEEVES[cell.sleeve]}, {cell.slice}, n={cell.n}"


def _closing(cells: list) -> str:
    titles = {
        "groen": "een groene week",
        "+10%": "+10%",
        "+20%": "+20%",
        "+50%": "+50%",
    }
    parts = []
    for name, _level in LABELS:
        stable, absolute = headline(cells, name)
        title = titles[name]
        if stable is None:
            parts.append(f"Op {title} is er geen cel met 100 keuzes.")
            continue
        text = (
            f"Op {title} is het hoogste percentage met minstens 100 keuzes "
            f"{_pct(stable.precision[name])} ({_name(stable)}), "
            f"tegen een basis van {_pct(stable.base[name])}. "
            f"Recall daarbij is {_pct(stable.recall[name])}."
        )
        if absolute is not None and absolute is not stable:
            text += (
                f" De hoogste cel zonder die drempel is {_pct(absolute.precision[name])} "
                f"({_name(absolute)})."
            )
        parts.append(text)
    reached = [
        name
        for name, _level in LABELS
        if (stable := headline(cells, name)[0]) is not None and stable.precision[name] >= 0.60
    ]
    if reached:
        parts.append("Minimaal 60% met minstens 100 keuzes geldt voor: " + ", ".join(reached) + ".")
    else:
        parts.append("Geen stap haalt 60% in een cel met minstens 100 keuzes.")
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def main() -> None:
    coins = []
    listed = 0
    for path in sorted(DAILY.glob("*.json")):
        listed += 1
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    print(f"listed {listed} loaded {len(coins)}", flush=True)
    cells = run_tape(coins, tape_calendar(coins))
    _write(OUT, listed, cells)
    print(OUT.read_text())


if __name__ == "__main__":
    main()
