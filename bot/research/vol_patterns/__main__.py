"""Pattern search on quiet and obscure alts.

Usage::

    .venv/bin/python -m bot.research.vol_patterns
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.cmc_regime.engine import Market, Point
from bot.research.vol_patterns.engine import (
    DAY_MS,
    UNIVERSES,
    YEARS,
    Context,
    Row,
    _rate,
    _universe_mask,
    build_table,
    clears,
    evaluate,
    grid,
)
from bot.research.week_book.engine import Coin, make_coin, master_dates

DAILY = Path("data/research/bitvavo_1d_all")
GLOBAL = Path("data/research/cmc/global.json")
QUOTES = Path("data/research/cmc/quotes.json")
NEWS = Path("data/research/alphai_crypto_news.json")
OUT = Path("bot/research/vol_patterns/PATROON.md")
START_MS = int(datetime(2024, 3, 16, tzinfo=UTC).timestamp() * 1000)
NEWS_FROM = int(datetime(2026, 8, 12, tzinfo=UTC).timestamp() * 1000)
QUIET_WORLD = 0.7
FAMILIES = (
    ("patroon", "Het patroon en de buren"),
    ("vorm", "Vormen eromheen"),
    ("cmc", "CoinMarketCap, alleen waar de print er is"),
    ("alphai", "AlphaI, alleen vanaf 12 augustus 2026"),
)


def _load(path: Path) -> Coin | None:
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    quote = cols[4] * cols[5]
    return make_coin(path.stem, cols[0].astype(np.int64), cols[1], cols[2], cols[3], cols[4], quote)


def _published(text: str) -> int:
    stamp = np.datetime64(text.replace("Z", ""), "ms")
    return int(stamp.astype(np.int64))


def _context(dates: np.ndarray) -> Context:
    ctx = Context(news_from=NEWS_FROM)
    if GLOBAL.exists():
        points = [
            Point(int(row["ts"]), float(row["mcap"]), float(row["volume"]), float(row["btc_dom"]))
            for row in json.loads(GLOBAL.read_text())["points"]
        ]
        market = Market(points)
        for stamp in dates:
            view = market.view(int(stamp))
            if view is None:
                continue
            if view.mcap < 0.0:
                ctx.mcap_down.add(int(stamp))
            elif view.mcap > 0.0:
                ctx.mcap_up.add(int(stamp))
            if view.vol_x <= QUIET_WORLD:
                ctx.world_quiet.add(int(stamp))
    if QUOTES.exists():
        names = json.loads(QUOTES.read_text())["names"]
        by_ts: dict[int, list[tuple[str, float]]] = defaultdict(list)
        for base, node in names.items():
            rows = sorted(node["points"], key=lambda row: int(row[0]))
            for index, row in enumerate(rows):
                ts = int(row[0])
                mcap = float(row[2])
                volume = float(row[3])
                if mcap > 0.0:
                    by_ts[ts].append((str(base), mcap))
                if index < 7:
                    continue
                prev = rows[index - 7 : index]
                gaps = range(7)
                broken = any(
                    int(rows[index - age][0]) - int(rows[index - age - 1][0]) != DAY_MS
                    for age in gaps
                )
                if broken:
                    continue
                baseline = float(np.mean([float(item[3]) for item in prev]))
                if baseline > 0.0 and volume <= 0.5 * baseline:
                    ctx.vol_quiet.add((str(base), ts))
        for ts, items in by_ts.items():
            ordered = sorted(items, key=lambda item: item[1], reverse=True)
            for rank, (base, _mcap) in enumerate(ordered, start=1):
                ctx.ranks[(base, ts)] = rank
    if NEWS.exists():
        payload = json.loads(NEWS.read_text())
        for row in payload["rows"]:
            published = _published(str(row["published"]))
            sentiments = row.get("sentiments") or {}
            if not isinstance(sentiments, dict):
                continue
            for symbol, label in sentiments.items():
                if label not in {"positive", "negative"}:
                    continue
                base = str(symbol).split("-")[0].upper()
                ctx.news.setdefault(base, []).append((published, str(label)))
        for items in ctx.news.values():
            items.sort()
    return ctx


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}{abs(100.0 * value):.1f}%".replace(".", ",")


def _cell(row: Row) -> str:
    groen = "—" if row.n == 0 else _pct(row.groen)
    plus = "—" if row.n == 0 else _pct(row.plus10)
    low = "—" if row.phase_min is None else _pct(row.phase_min)
    years = " | ".join(
        f"{_eur(row.year_pnl.get(year, 0.0))} ({row.year_weeks.get(year, 0)})" for year in YEARS
    )
    flag = "ja" if row.clears else "nee"
    return (
        f"| {row.name} | {row.n} | {groen} | {_pct(row.base)} | {low} | {plus} "
        f"| {_pct(row.mean_gross)} | {_pct(row.mean_wet)} | {_eur(row.end)} "
        f"| {_pct(row.dd)} | {row.weeks} | {years} | {flag} |"
    )


def _sentence(row: Row) -> str:
    low = "geen tweede fase met 100 namen" if row.phase_min is None else _pct(row.phase_min)
    return (
        f"{row.name}: groen {_pct(row.groen)} tegen basis {_pct(row.base)}, "
        f"n={row.n}, laagste fase {low}, boek {_eur(row.end)}"
    )


def _closing(rows: list[Row]) -> str:
    asked = next(row for row in rows if row.name == "dun, een maand geleden 0 tot 10%")
    ranked = [
        row for row in rows if row.family in {"patroon", "vorm"} and row.n >= 100
    ]
    ranked.sort(key=lambda row: row.groen, reverse=True)
    winners = sorted((row for row in rows if row.clears), key=lambda row: row.end, reverse=True)
    parts = [
        "Het gevraagde patroon, een stille of obscure naam die een maand geleden "
        f"0 tot 10% steeg: {_sentence(asked)}."
    ]
    if ranked:
        top = ranked[0]
        parts.append(f"De hoogste kans met minstens 100 namen: {_sentence(top)}.")
        if top.phase_min is None or top.phase_min <= top.base:
            parts.append("Die hogere kans zakt op een andere fase onder de basis.")
    if winners:
        parts.append(f"De hoogste boek die de lat haalt: {_sentence(winners[0])}.")
    else:
        parts.append("Geen boek haalt de lat van drie groene jaren.")
    parts.append(
        "De kans is het aandeel namen dat zeven dagen later hoger opent. "
        "Het boek koopt daarvan de vijf met de kleinste omzet. Er is niets aangezet."
    )
    return " ".join(parts)


def _write(path: Path, rows: list[Row], bases: list[tuple[str, int, float]], end: str) -> None:
    lines = [
        "# Patronen in stille en obscure alts",
        "",
        "De kans is: van de namen die het patroon aanwijst, hoeveel openen",
        "zeven dagen later hoger. Het signaal is het slot. De koop is de",
        "volgende open. Elke cel staat vast. De klok is één fase, elke zeven",
        f"dagen vanaf 16 maart 2024 tot {end}. Een tweede tot en met zevende",
        "fase telt mee zodra die zelf minstens 100 namen heeft. De laagste",
        "van die fases staat in de tabel.",
        "",
        "Een stille naam heeft over zeven dagen hoogstens de helft van de",
        "omzet van de 21 dagen ervoor. Obscure is €10.000 tot €1.000.000 omzet.",
        "Dun is stil of obscure. Het boek neemt de vijf kleinste omzetten,",
        "elk een vijfde van €20.000 en hoogstens 2% van de omzet van die dag.",
        "Taker 0,25% per kant plus impact. Pegs en de benchmark blijven erbuiten.",
        "",
        "Een boek haalt de lat met minstens 30 weken, een eindstand boven",
        "€20.000, en in 2024, 2025 en 2026 elk een plus met minstens acht weken.",
        "CoinMarketCap-koersen dekken ongeveer twaalf maanden. AlphaI-nieuws",
        "loopt van 5 augustus 2026 tot 2 november 2026, en telt vanaf 12 augustus.",
        "Die cellen kunnen de drie-jaren-lat niet halen. Niets wordt aangezet.",
        "",
        "## Basis, zonder patroon",
        "",
        "| universum | n | groen |",
        "|---|---:|---:|",
    ]
    for name, n, rate in bases:
        lines.append(f"| {name} | {n} | {_pct(rate)} |")
    lines += ["", "## Uitkomst", "", _closing(rows), ""]
    header = (
        "| cel | n | groen | basis | laagste fase | +10% | bruto | nat "
        "| eind | terugval | weken | 2024 | 2025 | 2026 | lat |"
    )
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    by_family = {family: [] for family, _title in FAMILIES}
    for row in rows:
        by_family[row.family].append(row)
    for family, title in FAMILIES:
        lines += [f"## {title}", "", header, rule]
        for row in by_family[family]:
            lines.append(_cell(row))
        lines.append("")
    lines.append("Er is niets aangezet.")
    lines.append("")
    path.write_text("\n".join(lines))


def _bases(table, clock_phase: np.ndarray) -> list[tuple[str, int, float]]:
    found = []
    for name in UNIVERSES:
        gross = table.gross[_universe_mask(table, name) & clock_phase]
        n, rate, _ten, _mean = _rate(gross)
        found.append((name, n, rate))
    return found


def main() -> None:
    coins = []
    for path in sorted(DAILY.glob("*.json")):
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    dates = master_dates(coins)
    start_at = int(np.searchsorted(dates, START_MS, side="left"))
    clock = dates[start_at::7]
    ctx = _context(dates[start_at:])
    print(
        f"coins {len(coins)} clock {len(clock)} cmc {len(ctx.ranks)} news {len(ctx.news)}",
        flush=True,
    )
    table = build_table(coins, dates, START_MS, ctx)
    print(f"rows {table.ms.size}", flush=True)
    rows: list[Row] = []
    specs = grid()
    for index, spec in enumerate(specs, start=1):
        row = evaluate(table, spec, clock)
        row.clears = clears(row) and spec.family in {"patroon", "vorm"}
        rows.append(row)
        print(f"{index}/{len(specs)} {spec.name} n {row.n} groen {row.groen:.1%}", flush=True)
    end = datetime.fromtimestamp(int(dates[-1]) / 1000, UTC).strftime("%Y-%m-%d")
    _write(OUT, rows, _bases(table, table.phase == 0), end)
    print(_closing(rows))


if __name__ == "__main__":
    main()
