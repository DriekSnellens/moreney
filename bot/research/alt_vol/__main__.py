"""Dip-and-bounce sleeve on liquid Bitvavo alts.

Usage::

    .venv/bin/python -m bot.research.alt_vol
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.alt_vol.engine import (
    BENCHMARK,
    START,
    YEARS,
    Coin,
    Result,
    clears,
    grid,
    make_coin,
    master_dates,
    run,
)

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/alt_vol/VOL.md")
START_MS = int(datetime(2024, 3, 16, tzinfo=UTC).timestamp() * 1000)


def _load(path: Path) -> Coin | None:
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    quote = cols[4] * cols[5]
    return make_coin(path.stem, cols[0].astype(np.int64), cols[1], cols[2], cols[3], cols[4], quote)


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}{abs(100.0 * value):.1f}%".replace(".", ",")


def _sentence(row: Result) -> str:
    years = ", ".join(
        f"{year} {_eur(row.year_pnl.get(year, 0.0))} ({row.year_trades.get(year, 0)})"
        for year in YEARS
    )
    return (
        f"{row.name} eindigt op {_eur(row.end)} "
        f"(PnL {_eur(row.pnl)}, terugval {_pct(row.dd)}, {row.trades} rondes, "
        f"gemiddeld {_pct(row.invested)} in de markt, {years})"
    )


def _closing(rows: list[Result]) -> str:
    winners = sorted((row for row in rows if row.clears), key=lambda row: row.end, reverse=True)
    ranked = sorted(rows, key=lambda row: row.end, reverse=True)
    parts = []
    if winners:
        parts.append(f"De hoogste cel die de lat haalt: {_sentence(winners[0])}.")
        if len(winners) > 1:
            parts.append(f"In totaal halen {len(winners)} cellen die lat.")
    else:
        parts.append("Geen cel haalt de lat.")
    if ranked and (not winners or ranked[0].name != winners[0].name):
        parts.append(f"De hoogste cel van het rooster: {_sentence(ranked[0])}.")
    primary = rows[0]
    parts.append(
        f"Van de {primary.trades} rondes van de primaire band eindigen "
        f"{primary.stops} op de stop en {primary.targets} op het doel. "
        "De middelste ronde is de volle stop. Het boek is daarna op."
    )
    parts.append(
        f"Op de primaire band valt {_eur(primary.btc_up)} op dagen waarop bitcoin "
        f"over 30 dagen hoger stond, en {_eur(primary.btc_down)} op dagen waarop "
        "die 30 dagen lager stonden."
    )
    parts.append(
        "Dit is een long die de terugkaatsing pakt. Een daling die doorzet "
        "raakt de stop. Er is niets aangezet."
    )
    return " ".join(parts)


def _write(path: Path, rows: list[Result]) -> None:
    lines = [
        "# Volatiliteit van alts, long op de dip",
        "",
        "Bitvavo-spot kan niet shorten. De sleeve koopt een alt nadat het slot",
        "minstens 8% onder de hoogste slot van 10 dagen staat, en alleen als de",
        "band van 20 dagen minstens 15% wijd is. De koop is de volgende open.",
        "De verkoop is +8%, de stop −8%, of de open na 10 dagen. Een balk die",
        "stop en doel allebei raakt, vult de stop. Een gat vult op de open.",
        "Hoogstens drie namen, elk een derde van het boek en hoogstens 2% van",
        "de omzet van de signaaldag. Pegs en de benchmark blijven erbuiten.",
        "Start €20.000 op 16 maart 2024. Taker 0,25% per kant plus impact,",
        "behalve de maker-cel: 0,15% per kant, zonder impact.",
        "",
        "Een cel haalt de lat met minstens 30 rondes, een eindstand boven",
        "€20.000, en in 2024, 2025 en 2026 elk een plus met minstens acht",
        "rondes. Niets wordt aangezet.",
        "",
        "## Uitkomst",
        "",
        _closing(rows),
        "",
        "| cel | eind | PnL | terugval | rondes | in de markt | 2024 | 2025 | 2026 | lat |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        years = " | ".join(
            f"{_eur(row.year_pnl.get(year, 0.0))} ({row.year_trades.get(year, 0)})"
            for year in YEARS
        )
        flag = "ja" if row.clears else "nee"
        lines.append(
            f"| {row.name} | {_eur(row.end)} | {_eur(row.pnl)} | {_pct(row.dd)} "
            f"| {row.trades} | {_pct(row.invested)} | {years} | {flag} |"
        )
    lines.append("")
    lines.append("Er is niets aangezet.")
    lines.append("")
    path.write_text("\n".join(lines))


def main() -> None:
    coins = []
    for path in sorted(DAILY.glob("*.json")):
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    btc = next((coin for coin in coins if coin.base == BENCHMARK), None)
    tradable = [coin for coin in coins if coin.base != BENCHMARK]
    dates = master_dates(coins)
    rows: list[Result] = []
    specs = grid()
    for index, spec in enumerate(specs, start=1):
        row = run(tradable, spec, dates, btc, start_ms=START_MS, book=START)
        row.clears = clears(row)
        rows.append(row)
        print(
            f"{index}/{len(specs)} {spec.name} end {row.end:.0f} trades {row.trades}",
            flush=True,
        )
    _write(OUT, rows)
    print(_closing(rows))


if __name__ == "__main__":
    main()
