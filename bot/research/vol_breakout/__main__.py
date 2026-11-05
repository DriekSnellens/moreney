"""Breakout books on volatile Bitvavo alts.

Usage::

    .venv/bin/python -m bot.research.vol_breakout
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.cmc_regime.engine import Market, Point
from bot.research.vol_breakout.engine import (
    BENCHMARK,
    DAY_MS,
    START,
    YEARS,
    Row,
    Spec,
    clears,
    grid,
    run,
    tape_of,
)
from bot.research.week_book.engine import make_coin, master_dates

DAILY = Path("data/research/bitvavo_1d_all")
GLOBAL = Path("data/research/cmc/global.json")
NEWS = Path("data/research/alphai_crypto_news.json")
OUT = Path("bot/research/vol_breakout/BREAKOUT.md")
START_MS = int(datetime(2024, 3, 16, tzinfo=UTC).timestamp() * 1000)
NEWS_FROM = int(datetime(2026, 8, 12, tzinfo=UTC).timestamp() * 1000)
QUIET_NOTE = (
    "Een 4-uurs Donchian 20/10 verdiende op Upbit bij 0,05% kosten. "
    "De turtle op bitcoin-4u, 2024 tot juli 2026, eindigde op −13% bij 0,06% kosten, "
    "met 2025 op −25%. Hier is de dagtape van Bitvavo, taker 0,25% per kant plus impact."
)


def _load(path: Path):
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    quote = cols[4] * cols[5]
    coin = make_coin(path.stem, cols[0].astype(np.int64), cols[1], cols[2], cols[3], cols[4], quote)
    return tape_of(coin)


def _published(text: str) -> int:
    stamp = np.datetime64(text.replace("Z", ""), "ms")
    return int(stamp.astype(np.int64))


def _regimes(dates: np.ndarray) -> tuple[set[int], set[int]]:
    up: set[int] = set()
    down: set[int] = set()
    if not GLOBAL.exists():
        return up, down
    points = [
        Point(int(row["ts"]), float(row["mcap"]), float(row["volume"]), float(row["btc_dom"]))
        for row in json.loads(GLOBAL.read_text())["points"]
    ]
    market = Market(points)
    for stamp in dates:
        view = market.view(int(stamp))
        if view is None:
            continue
        if view.mcap > 0.0:
            up.add(int(stamp))
        elif view.mcap < 0.0:
            down.add(int(stamp))
    return up, down


def _news(dates: np.ndarray) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    if not NEWS.exists():
        return found
    by_base: dict[str, list[int]] = defaultdict(list)
    for row in json.loads(NEWS.read_text())["rows"]:
        published = _published(str(row["published"]))
        sentiments = row.get("sentiments") or {}
        if not isinstance(sentiments, dict):
            continue
        for symbol, label in sentiments.items():
            if label != "positive":
                continue
            by_base[str(symbol).split("-")[0].upper()].append(published)
    clock = [int(stamp) for stamp in dates if int(stamp) >= NEWS_FROM]
    for base, times in by_base.items():
        ordered = sorted(times)
        for stamp in clock:
            hi = stamp + DAY_MS
            lo = hi - 7 * DAY_MS
            if any(lo < published <= hi for published in ordered):
                found.add((base, stamp))
    return found


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


def _closing(rows: list[Row]) -> str:
    winners = sorted((row for row in rows if row.clears), key=lambda row: row.end, reverse=True)
    full = [row for row in rows if row.family != "alphai"]
    best = max(full, key=lambda row: row.end)
    news = sorted(
        (row for row in rows if row.family == "alphai" and row.end > START),
        key=lambda row: row.end,
        reverse=True,
    )
    parts = [
        "Het live residual-pakket is het boek dat op deze tape al winstgevend is: "
        "eind €488.677, terugval 31,2%, 96 rondes, 2024 €132.986, 2025 €18.812, 2026 €316.879. "
        "Twee buren op dezelfde tape halen dezelfde lat en zijn niet aangezet: "
        "near-high 0,50 eindigt op €493.342, en geen lock of lock 16/8 op €488.949."
    ]
    if winners:
        parts.append(f"In dit nieuwe rooster haalt de lat: {_sentence(winners[0])}.")
        if len(winners) > 1:
            parts.append(f"In totaal halen {len(winners)} cellen die lat.")
    else:
        parts.append("Geen uitbraak in dit rooster haalt die lat.")
    if best.end <= START:
        parts.append(
            f"Over de hele tape blijft de hoogste uitbraak onder de inleg: {_sentence(best)}."
        )
    else:
        lead = f"Over de hele tape is de hoogste uitbraak {_sentence(best)}."
        parts.append(f"{lead} Die haalt de lat niet.")
    if news:
        parts.append(
            f"{len(news)} nieuwscellen eindigen boven de inleg, alleen in 2026. "
            f"De hoogste is {_sentence(news[0])}. Dat haalt de lat niet."
        )
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(path: Path, rows: list[Row], end: str) -> None:
    lines = [
        "# Uitbraak op volatiele alts",
        "",
        "Vers zoeken, los van de dip en van de stijging van een maand geleden.",
        "Een naam wordt gekocht als het slot door het hoogste punt van het",
        "kanaal gaat. De verkoop is de doorbraak van het lagere kanaal, of",
        "een spoor van 15% onder de hoogste slot sinds de koop. Drie plaatsen.",
        f"Venster 16 maart 2024 tot {end}. Start €20.000.",
        "",
        QUIET_NOTE,
        "",
        "Volatiel betekent een band van 20 dagen van minstens 20%. Obscure is",
        "€10.000 tot €1.000.000 omzet, liquide is daarboven. CoinMarketCap",
        "filtert op de 7-daagse marktwaarde, over de hele tape. AlphaI telt",
        "positief nieuws vanaf 12 augustus 2026 en kan de drie-jaren-lat niet halen.",
        "",
        "Een cel is winstgevend als ze minstens 30 rondes heeft, boven €20.000",
        "eindigt, en in 2024, 2025 en 2026 elk een plus heeft met minstens acht",
        "rondes. Niets wordt aangezet.",
        "",
        "## Overzicht",
        "",
        _closing(rows),
        "",
    ]
    header = "| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | lat |"
    rule = "|---|---:|---:|---:|---:|---:|---:|---:|---|"
    for family, title in (
        ("uitbraak", "Uitbraak"),
        ("cmc", "CoinMarketCap"),
        ("alphai", "AlphaI"),
    ):
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
    tapes = [tape for tape in loaded if tape.base != BENCHMARK]
    dates = master_dates(coins)
    clock = dates[int(np.searchsorted(dates, START_MS, side="left")) :]
    mcap_up, mcap_down = _regimes(clock)
    news_pos = _news(clock)
    print(
        f"tapes {len(tapes)} days {len(clock)} mcap+ {len(mcap_up)} news {len(news_pos)}",
        flush=True,
    )
    rows: list[Row] = []
    specs: list[Spec] = grid()
    for index, spec in enumerate(specs, start=1):
        row = run(
            tapes,
            spec,
            dates,
            start_ms=START_MS,
            mcap_up=mcap_up,
            mcap_down=mcap_down,
            news_pos=news_pos,
            news_from=NEWS_FROM,
        )
        row.clears = clears(row)
        rows.append(row)
        print(f"{index}/{len(specs)} {spec.name} end {row.end:.0f} trades {row.trades}", flush=True)
    end = datetime.fromtimestamp(int(dates[-1]) / 1000, UTC).strftime("%Y-%m-%d")
    _write(OUT, rows, end)
    print(_closing(rows))


if __name__ == "__main__":
    main()
