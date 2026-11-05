"""Rank every liquid name and measure the next week.

Usage::

    .venv/bin/python -m bot.research.cross_section
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bot.research.cross_section.engine import (
    FLOOR_BROAD,
    FLOOR_LIQUID,
    Study,
    calendar,
    study,
)
from bot.research.week_book.engine import Coin, make_coin

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/cross_section/CROSS.md")
LABELS = {
    "ret1": "rendement 1 dag",
    "ret7": "rendement 7 dagen",
    "ret28": "rendement 28 dagen",
    "volx": "omzet tegen de eigen week",
    "near": "slot tegen de high van 20 dagen",
    "size": "omzet die dag",
}
SIDES = {"top": "bovenste vijfde", "bottom": "onderste vijfde"}


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


def _num(value: float, digits: int) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.2f}%".replace(".", ",")


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _write(path: Path, listed: int, broad: Study, liquid: Study) -> None:
    lines = [
        "# Rang over veel munten",
        "",
        f"Bitvavo heeft hier {listed} dagelijkse namen, niet duizenden.",
        "Een coin-week is geen onafhankelijke trekking: de munten bewegen",
        "samen. Daarom is één week één waarneming. De klok stapt zeven dagen.",
        "Het signaal is het slot. Het rendement is de open zeven dagen later",
        "gedeeld door de open van de volgende dag.",
        "",
        "Zes scores staan vast. Een hogere score is de kant waarvan de",
        "rang verwacht dat hij meer stijgt: het rendement over 1, 7 en 28",
        "dagen, de omzet tegen de eigen week, het slot tegen de high van",
        "20 dagen, en de omzet zelf. De correlatie is Spearman, per week,",
        "tussen die score en het rendement erna. De t gebruikt die weken.",
        "",
        "Een score is zichtbaar als de correlatie positief is, de t minstens",
        "2 is, en 2024, 2025 en 2026 elk positief zijn. De drempel is de",
        "correlatie die hier een t van 2 zou geven. Het boek van €20.000",
        "verdeelt de inleg gelijk over het vijfde en loopt mee. Het haalt de",
        "lat als het boven €20.000 eindigt, minstens dertig weken draait,",
        "elk jaar positief is, en boven het boek eindigt dat alle geschikte",
        "namen aanhoudt. De netto-kolom is een vaste order van €1.000.",
        "Kosten zijn 0,25% taker per kant plus impact. Twaalf rankings per",
        "mand. Niets wordt aangezet.",
        "",
    ]
    for result, title in (
        (broad, "Minstens €10.000 omzet"),
        (liquid, "Minstens €1.000.000 omzet"),
    ):
        lines += [
            f"## {title}",
            "",
            f"{result.coins} munten, {result.weeks} weken, {result.coin_weeks} coin-weken.",
            f"Alle namen samen eindigen op {_eur(result.universe_equity)}.",
            "",
            "| score | weken | in de week | correlatie | t | drempel | 2024 | 2025 | 2026 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        tops = [row for row in result.rows if row.side == "top"]
        for row in tops:
            lines.append(
                f"| {LABELS[row.feature]} | {row.weeks} | {_num(row.pool, 0)} "
                f"| {_num(row.corr, 3)} | {_num(row.t, 1)} | {_num(row.detectable, 3)} "
                f"| {_num(row.year_corr.get(2024, np.nan), 3)} "
                f"| {_num(row.year_corr.get(2025, np.nan), 3)} "
                f"| {_num(row.year_corr.get(2026, np.nan), 3)} |"
            )
        lines += [
            "",
            "| score | kant | bruto | netto €1.000 | boek | terugval | 2024 | 2025 | 2026 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in result.rows:
            lines.append(
                f"| {LABELS[row.feature]} | {SIDES[row.side]} | {_pct(row.gross)} "
                f"| {_pct(row.net)} | {_eur(row.equity)} | {_pct(row.dd)} "
                f"| {_eur(row.year_pnl.get(2024, 0.0))} "
                f"| {_eur(row.year_pnl.get(2025, 0.0))} "
                f"| {_eur(row.year_pnl.get(2026, 0.0))} |"
            )
        lines.append("")
    lines.append(_closing(broad, liquid))
    lines.append("")
    path.write_text("\n".join(lines))


def _closing(broad: Study, liquid: Study) -> str:
    cleared = [
        f"{LABELS[row.feature]}, {SIDES[row.side]}"
        f" ({'breed' if result.floor == FLOOR_BROAD else 'liquide'})"
        for result in (broad, liquid)
        for row in result.rows
        if row.clears
    ]
    ret7 = next(row for row in broad.rows if row.feature == "ret7" and row.side == "top")
    text = (
        f"In de brede mand is de rangcorrelatie van het 7-daagse rendement "
        f"{_num(ret7.corr, 3)}, met t {_num(ret7.t, 1)}. "
        f"Een correlatie vanaf ongeveer {_num(ret7.detectable, 3)} is op deze "
        "tape zichtbaar."
    )
    bits = [
        f"{LABELS[row.feature]} ({'breed' if result.floor == FLOOR_BROAD else 'liquide'}) "
        f"bruto {_pct(row.gross)}, netto {_pct(row.net)}"
        for result in (broad, liquid)
        for row in result.rows
        if row.side == "top" and row.visible
    ]
    if bits:
        text += " Zichtbaar onder de lat: " + "; ".join(bits) + "."
    else:
        text += " Geen score is zichtbaar onder de lat."
    if cleared:
        text += " Het boek haalt de lat: " + ", ".join(cleared) + "."
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
    dates = calendar(coins)
    print(f"listed {listed} loaded {len(coins)} days {len(dates)}", flush=True)
    broad = study(coins, dates, FLOOR_BROAD)
    print(f"broad weeks {broad.weeks} coins {broad.coins}", flush=True)
    liquid = study(coins, dates, FLOOR_LIQUID)
    print(f"liquid weeks {liquid.weeks} coins {liquid.coins}", flush=True)
    _write(OUT, listed, broad, liquid)
    print(OUT.read_text())


if __name__ == "__main__":
    main()
