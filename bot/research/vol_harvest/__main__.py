"""Ladders and follow-days on volatile Bitvavo alts.

Usage::

    .venv/bin/python -m bot.research.vol_harvest
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.cmc_regime.engine import Market, Point
from bot.research.vol_harvest.engine import (
    BENCHMARK,
    START,
    YEARS,
    Row,
    Spec,
    clears,
    grid,
    run_follow,
    run_ladder,
    tape_of,
)
from bot.research.week_book.engine import make_coin, master_dates

DAILY = Path("data/research/bitvavo_1d_all")
GLOBAL = Path("data/research/cmc/global.json")
OUT = Path("bot/research/vol_harvest/OOGST.md")
START_MS = int(datetime(2024, 3, 16, tzinfo=UTC).timestamp() * 1000)


def _load(path: Path):
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    quote = cols[4] * cols[5]
    coin = make_coin(path.stem, cols[0].astype(np.int64), cols[1], cols[2], cols[3], cols[4], quote)
    return tape_of(coin)


def _mcap_up(dates: np.ndarray) -> set[int]:
    up: set[int] = set()
    if not GLOBAL.exists():
        return up
    points = [
        Point(int(row["ts"]), float(row["mcap"]), float(row["volume"]), float(row["btc_dom"]))
        for row in json.loads(GLOBAL.read_text())["points"]
    ]
    market = Market(points)
    for stamp in dates:
        view = market.view(int(stamp))
        if view is not None and view.mcap > 0.0:
            up.add(int(stamp))
    return up


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


def _neighbours(spec: Spec, specs: list[Spec]) -> list[Spec]:
    """Cells one knob away: another step or gate, another trigger or hold."""
    out = []
    for other in specs:
        if other is spec or other.family != spec.family or other.universe != spec.universe:
            continue
        differs = sum(
            1
            for left, right in (
                (other.step, spec.step),
                (other.gate, spec.gate),
                (other.trigger, spec.trigger),
                (other.hold, spec.hold),
            )
            if left != right
        )
        if differs == 1:
            out.append(other)
    return out


def _neighbour_text(row: Row, rows: list[Row], specs: list[Spec]) -> str:
    by_name = {spec.name: spec for spec in specs}
    ends = {item.name: item for item in rows}
    around = [ends[other.name] for other in _neighbours(by_name[row.name], specs)]
    if not around:
        return ""
    above = [item for item in around if item.end > START]
    passing = [item for item in around if item.clears]
    lead = f"Van haar {len(around)} buren eindigen {len(above)} boven de inleg"
    if passing:
        return f"{lead} en halen {len(passing)} de lat."
    if above:
        return f"{lead} en haalt geen enkele de lat. Dat is een smalle cel."
    return f"{lead}. Dat is een smalle cel: één stap of één poort verder verliest het boek."


def _closing(rows: list[Row], specs: list[Spec]) -> str:
    winners = sorted((row for row in rows if row.clears), key=lambda row: row.end, reverse=True)
    parts = [
        "Het live residual-pakket is het boek dat op deze tape al winstgevend is: "
        "eind €488.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879."
    ]
    if winners:
        parts.append(f"In dit rooster haalt de lat: {_sentence(winners[0])}.")
        if len(winners) > 1:
            parts.append(f"In totaal halen {len(winners)} cellen die lat.")
        note = _neighbour_text(winners[0], rows, specs)
        if note:
            parts.append(note)
        parts.append("Een cel op dezelfde tape is een kandidaat, geen nieuwe live-stand.")
    else:
        parts.append("Geen ladder en geen volgdag in dit rooster haalt die lat.")
    for family, label in (("ladder", "ladder"), ("volgdag", "volgdag")):
        best = max((row for row in rows if row.family == family), key=lambda row: row.end)
        if best.end <= START:
            parts.append(f"De hoogste {label} blijft onder de inleg: {_sentence(best)}.")
        elif best.clears:
            parts.append(f"De hoogste {label} is de cel hierboven.")
        else:
            parts.append(f"De hoogste {label} is {_sentence(best)}. Die haalt de lat niet.")
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(path: Path, rows: list[Row], specs: list[Spec], end: str) -> None:
    lines = [
        "# Ladder en volgdag op volatiele alts",
        "",
        "Tweede verse zoektocht. De ladder koopt een trede op een volatiele",
        "naam, koopt een trede bij als het slot een stap onder de laagste",
        "trede staat, en verkoopt een trede als het slot een stap boven haar",
        "kosten staat. Vier treden, drie namen, de meest liquide naam eerst.",
        "Een poort sluit de hele ladder: geen, het eigen 50-daags gemiddelde,",
        "het 50-daags gemiddelde van de benchmark, of een stijgende 7-daagse",
        "marktwaarde van CoinMarketCap. Elke ladder gaat na 90 dagen dicht.",
        "",
        "De volgdag koopt na een grote dag van de benchmark (3% of 5%) de drie",
        "namen met de hoogste 60-daagse bèta, en verkoopt na 1, 3 of 7 sessies.",
        "",
        "Signaal op het slot, vulling op de volgende open. Taker 0,25% per kant",
        "plus impact. Pegs en de benchmark doen niet mee. Volatiel is een band",
        "van 20 dagen van minstens 20%. Obscure is €10.000 tot €1.000.000 omzet,",
        "liquide is daarboven. AlphaI staat pas aan vanaf augustus 2026 en kan",
        "de drie-jaren-lat niet halen; het telt hier niet mee.",
        f"Venster 16 maart 2024 tot {end}. Start €20.000.",
        "",
        "Een cel is winstgevend als ze minstens 30 rondes heeft, boven €20.000",
        "eindigt, en in 2024, 2025 en 2026 elk een plus heeft met minstens acht",
        "rondes. Niets wordt aangezet.",
        "",
        "## Overzicht",
        "",
        _closing(rows, specs),
        "",
    ]
    header = "| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | lat |"
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|---|"
    for family, title in (("ladder", "Ladder"), ("volgdag", "Volgdag")):
        lines += [f"## {title}", "", header, rule]
        for row in rows:
            if row.family == family:
                lines.append(_line(row))
        lines.append("")
    lines.append("Er is niets aangezet.")
    lines.append("")
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
    mcap_up = _mcap_up(clock)
    print(f"tapes {len(tapes)} days {len(clock)} mcap+ {len(mcap_up)}", flush=True)
    rows: list[Row] = []
    specs: list[Spec] = grid()
    for index, spec in enumerate(specs, start=1):
        if spec.family == "ladder":
            row = run_ladder(tapes, spec, dates, start_ms=START_MS, bench=bench, mcap_up=mcap_up)
        else:
            row = run_follow(tapes, spec, dates, start_ms=START_MS, bench=bench)
        row.clears = clears(row)
        rows.append(row)
        print(f"{index}/{len(specs)} {spec.name} end {row.end:.0f} trades {row.trades}", flush=True)
    end = datetime.fromtimestamp(int(dates[-1]) / 1000, UTC).strftime("%Y-%m-%d")
    _write(OUT, rows, specs, end)
    print(_closing(rows, specs))


if __name__ == "__main__":
    main()
