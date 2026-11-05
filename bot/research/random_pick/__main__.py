"""Random picks on the desk's clock and exits.

Usage::

    .venv/bin/python -m bot.research.random_pick
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.random_pick.engine import (
    SEEDS,
    Row,
    Spec,
    candidates_by_day,
    clears,
    grid,
    run,
    seed_grid,
)
from bot.research.vol_harvest.engine import BENCHMARK, START, YEARS, tape_of
from bot.research.week_book.engine import make_coin, master_dates

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/random_pick/TOEVAL.md")
START_MS = int(datetime(2024, 3, 16, tzinfo=UTC).timestamp() * 1000)
LIVE_END = 488_677.0


def _load(path: Path):
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    quote = cols[4] * cols[5]
    coin = make_coin(path.stem, cols[0].astype(np.int64), cols[1], cols[2], cols[3], cols[4], quote)
    return tape_of(coin)


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}{abs(100.0 * value):.1f}%".replace(".", ",")


def _years(row: Row) -> str:
    return " | ".join(
        f"{_eur(row.year_pnl.get(year, 0.0))} ({row.year_trades.get(year, 0)})" for year in YEARS
    )


def _line(row: Row) -> str:
    flag = "ja" if row.clears else "nee"
    return (
        f"| {row.name} | {_eur(row.end)} | {_eur(row.pnl)} | {_pct(row.dd)} "
        f"| {row.trades} | {_years(row)} | {flag} |"
    )


def _sentence(row: Row) -> str:
    return (
        f"{row.name} eindigt op {_eur(row.end)}, terugval {_pct(row.dd)}, "
        f"{row.trades} rondes, {_years(row).replace(' | ', ', ')}"
    )


def _seed_line(name: str, rows: list[Row]) -> str:
    ends = np.asarray([row.end for row in rows])
    above = int(np.sum(ends > START))
    green = sum(1 for row in rows if all(row.year_pnl.get(y, 0.0) > 0.0 for y in YEARS))
    passing = sum(1 for row in rows if row.clears)
    beats = int(np.sum(ends >= LIVE_END))
    total = len(rows)
    return (
        f"| {name} | {_eur(float(np.median(ends)))} | {_eur(float(np.percentile(ends, 5)))} "
        f"| {_eur(float(np.percentile(ends, 95)))} | {_eur(float(ends.max()))} "
        f"| {above}/{total} | {green}/{total} | {passing}/{total} | {beats}/{total} |"
    )


def _closing(rows: list[Row], seeds: dict[str, list[Row]]) -> str:
    parts = [
        "Het live residual-pakket is het boek dat op deze tape al winstgevend is: "
        "eind €488.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879."
    ]
    winners = sorted((row for row in rows if row.clears), key=lambda row: row.end, reverse=True)
    all_seeds = [row for group in seeds.values() for row in group]
    ends = np.asarray([row.end for row in all_seeds])
    passing = sum(1 for row in all_seeds if row.clears)
    beats = int(np.sum(ends >= LIVE_END))
    best = max(rows, key=lambda row: row.end)
    if winners:
        parts.append(f"Van de vaste willekeurige regels haalt de lat: {_sentence(winners[0])}.")
        if len(winners) > 1:
            parts.append(f"In totaal halen {len(winners)} van de {len(rows)} vaste regels die lat.")
        luck = int(np.sum(ends >= winners[0].end))
        parts.append(
            f"Van de {len(all_seeds)} toevalsboeken eindigen er {luck} op of boven die cel; "
            "een vaste regel die de lat haalt valt binnen wat toeval ook doet."
        )
    else:
        parts.append("Geen vaste willekeurige regel haalt die lat.")
    if not best.clears:
        parts.append(f"De hoogste vaste regel is {_sentence(best)}. Die haalt de lat niet.")
    parts.append(
        f"Over {len(all_seeds)} toevalsboeken ligt de mediaan op {_eur(float(np.median(ends)))}, "
        f"het hoogste op {_eur(float(ends.max()))}; {passing} halen de lat en {beats} halen "
        f"het live pakket."
    )
    top = max(seeds.items(), key=lambda item: float(np.median([row.end for row in item[1]])))
    parts.append(
        f"De beste omgeving voor toeval is {top[0]}: mediaan "
        f"{_eur(float(np.median([row.end for row in top[1]])))}."
    )
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(path: Path, rows: list[Row], seeds: dict[str, list[Row]], end: str) -> None:
    lines = [
        "# Willekeurige keuze op de klok van de desk",
        "",
        "Wat verdient de bot als de keuze niets weet? De weekklok, het spoor",
        "van 10% met lock +12%/+6% en de benchmarkpoort blijven staan; de",
        "pick komt van de stand van de maan, de tijdstempel, de dag van de",
        "maand, de letter van de weekdag, of een gezaaide toevalsgenerator.",
        f"Vijfhonderd zaden per omgeving. Venster 16 maart 2024 tot {end}. Start €20.000.",
        "",
        "Eén naam, hoogstens 2% van de dagomzet. Signaal op het slot, vulling",
        "op de volgende open, taker 0,25% per kant plus impact. Pegs en de",
        "benchmark doen niet mee. Alles is omzet vanaf €10.000, liquide is",
        "omzet vanaf €1.000.000. De weekklok verkoopt een naam die niet meer",
        "de pick is en koopt de nieuwe; de poort verkoopt zodra de benchmark",
        "onder zijn 50-daags gemiddelde sluit.",
        "",
        "Een cel is winstgevend als ze minstens 30 rondes heeft, boven €20.000",
        "eindigt, en in 2024, 2025 en 2026 elk een plus heeft met minstens acht",
        "rondes. Niets wordt aangezet.",
        "",
        "## Overzicht",
        "",
        _closing(rows, seeds),
        "",
        "## Toeval, vijfhonderd zaden per omgeving",
        "",
        "| omgeving | mediaan | 5% | 95% | hoogste | boven inleg | drie jaren groen "
        "| lat | boven live |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, group in seeds.items():
        lines.append(_seed_line(name, group))
    lines += ["", "## Vaste willekeurige regels", ""]
    header = "| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | lat |"
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|---|"
    lines += [header, rule]
    for row in rows:
        lines.append(_line(row))
    lines += ["", "Er is niets aangezet.", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    loaded = []
    for path in sorted(DAILY.glob("*.json")):
        tape = _load(path)
        if tape is not None:
            loaded.append(tape)
    coins = [
        make_coin(tape.base, tape.ts, tape.open, tape.high, tape.low, tape.close, tape.quote)
        for tape in loaded
    ]
    bench = next(tape for tape in loaded if tape.base == BENCHMARK)
    tapes = [tape for tape in loaded if tape.base != BENCHMARK]
    dates = master_dates(coins)
    clock = dates[int(np.searchsorted(dates, START_MS, side="left")) :]
    pools = {
        universe: candidates_by_day(tapes, clock, universe) for universe in ("alles", "liquide")
    }
    print(f"tapes {len(tapes)} days {len(clock)}", flush=True)
    rows: list[Row] = []
    specs: list[Spec] = grid()
    for index, spec in enumerate(specs, start=1):
        row = run(
            tapes, spec, dates, start_ms=START_MS, bench=bench, candidates=pools[spec.universe]
        )
        row.clears = clears(row)
        rows.append(row)
        print(f"{index}/{len(specs)} {spec.name} end {row.end:.0f} trades {row.trades}", flush=True)
    seeds: dict[str, list[Row]] = defaultdict(list)
    seed_specs = seed_grid()
    for index, spec in enumerate(seed_specs, start=1):
        row = run(
            tapes, spec, dates, start_ms=START_MS, bench=bench, candidates=pools[spec.universe]
        )
        row.clears = clears(row)
        seeds[spec.name].append(row)
        if index % SEEDS == 0:
            ends = [item.end for item in seeds[spec.name]]
            print(f"{spec.name}: median {np.median(ends):.0f} max {max(ends):.0f}", flush=True)
    end = datetime.fromtimestamp(int(dates[-1]) / 1000, UTC).strftime("%Y-%m-%d")
    _write(OUT, rows, dict(seeds), end)
    print(_closing(rows, dict(seeds)))


if __name__ == "__main__":
    main()
