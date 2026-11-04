"""Run the locked multi-tactic buy books on the cached daily tape.

Usage::

    .venv/bin/python -m bot.research.multi_tactic_buy
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import bar_date
from bot.research.multi_tactic_buy.engine import (
    N_NAMES,
    bullish_counts,
    choose,
    load_headline_sides,
    panel_on,
    run_desk,
    weekly_overlap,
)
from bot.research.tactic_lab.engine import run_residual_row

OUT_PATH = Path("bot/research/multi_tactic_buy/RESULTS.md")
BOOKS: tuple[tuple[str, str, int], ...] = (
    ("tsmom alleen", "tsmom", 1),
    ("ctrend alleen", "ctrend", 1),
    ("residual alleen", "residual", 1),
    ("twee van drie", "agree", 2),
    ("alle drie", "agree", 3),
)
NEWS_BOOKS: tuple[tuple[str, str, int], ...] = (
    *BOOKS,
    ("nieuws alleen", "news", 1),
    ("twee van drie én nieuws", "agree_news", 2),
)


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    return f"{sign}€{abs(value):,.0f}"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _load(cache_dir: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        path = cache_dir / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _run_window(
    ohlc: dict[str, list[list[float]]],
    books: tuple[tuple[str, str, int], ...],
    *,
    start: str,
    end: str,
    sides: dict[str, dict[str, list[int]]] | None,
    book_eur: float,
) -> list[dict[str, object]]:
    panels: dict[str, dict] = {}
    bullish: dict[str, dict[str, int]] = {}

    def picks_for(kind: str, min_votes: int):
        def _picks(date: str) -> list[str]:
            if date not in panels:
                panels[date] = panel_on(ohlc, date, DEFAULT_UNIVERSE)
            if date not in bullish:
                bullish[date] = bullish_counts(sides, date) if sides else {}
            return choose(
                kind,
                panels[date],
                bullish[date],
                min_votes=min_votes,
                n_names=N_NAMES,
            )

        return _picks

    rows = []
    for label, kind, min_votes in books:
        row = run_desk(
            ohlc,
            start=start,
            end=end,
            picks_on=picks_for(kind, min_votes),
            name=label,
            book_eur=book_eur,
        )
        row.pop("trades", None)
        rows.append(row)
    return rows


def _table(rows: list[dict[str, object]], years: tuple[str, ...]) -> list[str]:
    head = "| boek | eind | PnL | max DD | trades | " + " | ".join(years) + " |"
    rule = "|---|---:|---:|---:|---:|" + "|".join("---:" for _ in years) + "|"
    lines = [head, rule]
    for row in rows:
        booked = row.get("year_pnl") or {}
        assert isinstance(booked, dict)
        cells = " | ".join(_eur(float(booked.get(year) or 0.0)) for year in years)
        lines.append(
            "| {name} | {end} | {pnl} | {dd} | {n} | {cells} |".format(
                name=row["strategy"],
                end=_eur(float(row["end_eur"])),
                pnl=_eur(float(row["pnl_eur"])),
                dd=_pct(float(row["max_dd_pct"])),
                n=int(row["n_trades"]),
                cells=cells,
            )
        )
    return lines


def _write(
    path: Path,
    *,
    full: list[dict[str, object]],
    fresh_2026: list[dict[str, object]],
    news_rows: list[dict[str, object]],
    live: dict[str, object],
    overlap: dict[str, int],
    start: str,
    end: str,
    news_start: str,
    book_eur: float,
) -> None:
    weeks = max(1, int(overlap.get("weeks") or 0))
    two_share = 100.0 * int(overlap["any_two"]) / weeks
    three_share = 100.0 * int(overlap["any_three"]) / weeks
    lines = [
        "# Meerdere tactieken voor de aankoop",
        "",
        "De koopregel stond vast voordat deze tape gedraaid werd. Er is daarna",
        "geen drempel, horizon of munt bijgesteld.",
        "",
        f"Venster {start} t/m {end}. Startkapitaal {_eur(book_eur)}. Vier gelijke",
        "stukken, wekelijkse klok, signaal op de slotkoers, vulling op de volgende",
        "opening, 10% trail vanaf de piek, plat zodra BTC onder zijn 50-daags",
        "gemiddelde sluit. Kosten zijn de natte Bitvavo-vulling: 0,25% taker plus",
        "impact. Universum is de live desk, zonder uitzondering per munt.",
        "",
        "## De drie ja-stemmen",
        "",
        "- **tsmom** — eigen 28-daags rendement positief, excess versus BTC positief,",
        "  slot boven het eigen 50-daags gemiddelde, 20-daags volume minstens €80.000.",
        "- **ctrend** — gemiddelde van het eigen rendement over 7, 14, 28 en 56 dagen",
        "  positief, dezelfde gemiddelde- en volumepoort.",
        "- **residual** — 10-daags excess versus BTC, één dag overgeslagen, boven 3,5%,",
        "  slot boven het eigen 50-daags gemiddelde, dezelfde volumepoort. De rangorde",
        "  binnen de ja-stemmen gebruikt de live near-high-weging van 0,25.",
        "",
        "Een coin wordt gekocht bij minstens twee ja-stemmen. Het strenge boek eist",
        "alle drie. Elk boek alleen, met dezelfde klok en dezelfde uitstap, is de",
        "controle. Bij gelijk aantal stemmen wint het hogere 10-daags excess.",
        "",
        "## Hele tape, doorlopend boek",
        "",
        *_table(full, ("2024", "2025", "2026")),
        "",
        "De jaarbedragen zijn het verschil in equity binnen hetzelfde doorlopende",
        "boek. Ter vergelijking het live residual-boek van één munt op dezelfde",
        f"tape: eind {_eur(float(live['end_eur']))}, "
        f"PnL {_eur(float(live['pnl_eur']))}, "
        f"max DD {_pct(float(live['max_dd_pct']))}, {int(live['n_trades'])} rondes.",
        "Dat boek heeft een andere uitstap (trail én lock) en houdt één munt,",
        "dus het staat hier als context en niet als vijfde rij van dezelfde test.",
        "",
        "## 2026 opnieuw gestart op €20.000",
        "",
        *_table(fresh_2026, ("2026",)),
        "",
        "Een doorlopend boek komt 2026 binnen met het kapitaal dat 2024 en 2025",
        "overlieten. Deze tabel begint opnieuw op €20.000, daarom wijkt het",
        "2026-bedrag af van de jaarkolom hierboven.",
        "",
        "## Nieuws als vierde ja-stem",
        "",
        f"Alleen vanaf {news_start}, de eerste headline in de AlphaI-store, opnieuw",
        f"gestart op €20.000 en door tot {end}. Een coin telt als nieuws-ja wanneer",
        "de headlines van de laatste zeven UTC-dagen een bullish meerderheid hebben.",
        "Stilte en een gelijke stand zijn geen ja.",
        "",
        *_table(news_rows, ("2026",)),
        "",
        "Dit stuk tape is kort. Dezelfde tsmom verloor in 2024 en in 2025 op de",
        "hele tape, dus een plus in deze maanden bevestigt de koopregel niet.",
        "",
        "## Hoe vaak de tactieken samenvallen",
        "",
        (
            f"Op een vaste weekklok van {weeks} weken, los van de trail, "
            f"had tsmom minstens één coin in {overlap['tsmom']} weken, "
            f"ctrend in {overlap['ctrend']} en residual in {overlap['residual']}."
        ),
        (
            f"Minstens twee tactieken waren het eens in {overlap['any_two']} weken "
            f"({two_share:.0f}%). Alle drie in {overlap['any_three']} weken "
            f"({three_share:.0f}%)."
        ),
        "",
        "Niets hiervan wordt live gezet.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-tactic buy test")
    parser.add_argument("--cache-dir", default="data/residual_wet_candles")
    parser.add_argument("--news", default="data/research/alphai_crypto_news.json")
    parser.add_argument("--book", type=float, default=20_000.0)
    parser.add_argument("--start", default="2024-03-16")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    ohlc = _load(Path(args.cache_dir))
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    sides = load_headline_sides(Path(args.news)) if Path(args.news).exists() else {}
    news_days = [day for by_day in sides.values() for day in by_day]
    news_start = min(news_days) if news_days else end
    if news_start < args.start:
        news_start = args.start

    full = _run_window(ohlc, BOOKS, start=args.start, end=end, sides=None, book_eur=args.book)
    fresh = _run_window(ohlc, BOOKS, start="2026-01-01", end=end, sides=None, book_eur=args.book)
    news_rows = _run_window(
        ohlc,
        NEWS_BOOKS,
        start=news_start,
        end=end,
        sides=sides,
        book_eur=args.book,
    )
    live = run_residual_row(ohlc, start=args.start, end=end, book_eur=args.book)
    overlap = weekly_overlap(ohlc, start=args.start, end=end)
    _write(
        Path(args.out),
        full=full,
        fresh_2026=fresh,
        news_rows=news_rows,
        live=live,
        overlap=overlap,
        start=args.start,
        end=bar_date(ohlc["BTC"][-1]),
        news_start=news_start,
        book_eur=args.book,
    )
    print(Path(args.out).read_text())


if __name__ == "__main__":
    main()
