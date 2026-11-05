"""CoinMarketCap gates on the live residual book.

Usage::

    .venv/bin/python -m bot.research.cmc_live
"""

from __future__ import annotations

import json
from pathlib import Path

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import bar_date
from bot.research.cmc_live.engine import (
    PUBLISHED_DD,
    PUBLISHED_END,
    PUBLISHED_PNL,
    PUBLISHED_TRADES,
    PUBLISHED_YEAR,
    YEARS,
    Market,
    Quote,
    Row,
    beats,
    gate_table,
)
from bot.research.cmc_regime.engine import Point
from bot.research.cmc_regime.fetch import GLOBAL, QUOTES
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS

DAILY = Path("data/residual_wet_candles")
OUT = Path("bot/research/cmc_live/LIVE.md")
START = "2024-03-16"
NAMES = {
    "live": "live residual",
    "mcap+": "marktkapitaal stijgt",
    "mcap-": "marktkapitaal daalt",
    "dom+": "dominantie stijgt",
    "dom-": "dominantie daalt",
    "seizoen": "kapitaal stijgt, dominantie daalt",
    "btc_sterk": "kapitaal en dominantie stijgen",
    "luid": "wereldvolume minstens 1,5×",
    "stil": "wereldvolume hoogstens 0,7×",
    "cmc7": "CMC-stijging positief",
    "excess": "CMC-stijging boven de grootste",
    "top10": "binnen de 10 grootste",
    "top20": "binnen de 20 grootste",
    "top50": "binnen de 50 grootste",
}


def _load_market() -> Market:
    raw = json.loads(GLOBAL.read_text())
    return Market(
        [
            Point(int(row["ts"]), float(row["mcap"]), float(row["volume"]), float(row["btc_dom"]))
            for row in raw["points"]
        ]
    )


def _load_quotes() -> dict[str, dict[int, Quote]]:
    raw = json.loads(QUOTES.read_text())
    series: dict[str, dict[int, Quote]] = {}
    for base, meta in raw["names"].items():
        rows: dict[int, Quote] = {}
        for point in meta["points"]:
            ts, _price, mcap, _vol, pct = point
            pct_f = float("nan") if pct is None else float(pct)
            rows[int(ts)] = Quote(int(ts), float(mcap), pct_f)
        series[base] = rows
    return series


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(100.0 * value):.1f}".replace(".", ",")
    return f"{sign}{text}%"


def _run(ohlc: dict, name: str, gate, start: str, end: str) -> Row:
    raw = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=20_000.0,
        entry_gate=gate,
        strategy=name,
        **LIVE_KNOBS,
    )
    year = {str(key): float(value) for key, value in (raw.get("year_pnl") or {}).items()}
    return Row(
        name,
        float(raw["end_eur"]),
        float(raw["pnl_eur"]),
        float(raw["max_dd_pct"]),
        int(raw["n_trades"]),
        year,
    )


def _sentence(row: Row) -> str:
    years = ", ".join(f"{year} {_eur(row.year.get(year, 0.0))}" for year in YEARS)
    return (
        f"{NAMES[row.name]} eindigt op {_eur(row.end)} "
        f"(PnL {_eur(row.pnl)}, terugval {_pct(row.dd)}, {row.trades} rondes, {years})"
    )


def _closing(rows: list[Row], control: Row) -> str:
    winners = sorted((row for row in rows if row.beats), key=lambda row: row.end, reverse=True)
    ranked = sorted(
        (row for row in rows if row.name != "live"),
        key=lambda row: row.end,
        reverse=True,
    )
    same = [
        row
        for row in ranked
        if row.end == control.end and row.trades == control.trades and row.dd == control.dd
    ]
    changed = [row for row in ranked if row not in same]
    parts = []
    if winners:
        parts.append(f"De poort die het live boek op deze tape verslaat: {_sentence(winners[0])}.")
    else:
        parts.append("Geen poort verslaat het live boek op deze tape.")
    if same:
        names = ", ".join(NAMES[row.name] for row in same)
        parts.append(f"Deze poorten laten elke ronde van het live boek staan: {names}.")
    if changed:
        parts.append(f"De hoogste poort die het pad wel verandert: {_sentence(changed[0])}.")
    parts.append(
        f"Hetzelfde residual-boek zonder CoinMarketCap eindigt hier op {_eur(control.end)}, "
        f"PnL {_eur(control.pnl)}, terugval {_pct(control.dd)}, {control.trades} rondes."
    )
    parts.append("Er is niets aangezet.")
    return " ".join(parts)


def _write(path: Path, end: str, rows: list[Row]) -> None:
    control = next(row for row in rows if row.name == "live")
    lines = [
        "# CoinMarketCap op het live residual-boek",
        "",
        "Het boek is het pakket dat aan staat: één munt, 10-daags excess",
        "met één dag overslaan, klok van 7 dagen, trail 10% en lock +12%/+6%.",
        f"Start €20.000, venster {START} t/m {end}, natte Bitvavo-vulling.",
        "CoinMarketCap is de enige toevoeging. Elke poort stond vast.",
        "",
        "Een wereldpoort leest de stempel die bij de slot van die sessie",
        "hoort. Een muntpoort doet niets tot er die dag twintig namen een",
        "stempel hebben. Daarna eist hij een positieve CMC-stijging over",
        "7 dagen, een stijging boven de naam met het grootste CMC-kapitaal,",
        "of een plek in de 10, 20 of 50 grootste.",
        "",
        "Hij verslaat het live boek alleen met een hogere eindstand, een plus",
        "in 2024, 2025 en 2026, minstens 30 rondes, en een terugval die",
        "hoogstens 15 procentpunten slechter is. Niets wordt aangezet.",
        "",
        "## Gepubliceerde live-stand",
        "",
        "Tactic lab, venster tot 2026-10-03. De rij daaronder is dit cache",
        "opnieuw gedraaid, op hetzelfde venster als de poorten.",
        "",
        "| boek | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| gepubliceerd | {_eur(PUBLISHED_END)} | {_eur(PUBLISHED_PNL)} | {_pct(PUBLISHED_DD)} "
        f"| {PUBLISHED_TRADES} | {_eur(PUBLISHED_YEAR[2024])} | {_eur(PUBLISHED_YEAR[2025])} "
        f"| {_eur(PUBLISHED_YEAR[2026])} |",
        "",
        "## Deze tape",
        "",
        _closing(rows, control),
        "",
        "| poort | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | verslaat |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        years = " | ".join(_eur(row.year.get(year, 0.0)) for year in YEARS)
        flag = "ja" if row.beats else "nee"
        lines.append(
            f"| {NAMES[row.name]} | {_eur(row.end)} | {_eur(row.pnl)} | {_pct(row.dd)} "
            f"| {row.trades} | {years} | {flag} |"
        )
    lines += ["", "Er is niets aangezet.", ""]
    path.write_text("\n".join(lines))


def main() -> None:
    ohlc = load_candles(DAILY, merge_fresh=False)
    end = bar_date(ohlc["BTC"][-1])
    market = _load_market()
    series = _load_quotes()
    rows: list[Row] = []
    control: Row | None = None
    for name, gate in gate_table(market, series):
        row = _run(ohlc, name, gate, START, end)
        if name == "live":
            control = row
        elif control is not None:
            row.beats = beats(row, control)
        rows.append(row)
        print(
            f"{name} end {row.end:.0f} pnl {row.pnl:.0f} dd {row.dd:.1%} trades {row.trades}",
            flush=True,
        )
    assert control is not None
    _write(OUT, end, rows)
    print(_closing(rows, control))


if __name__ == "__main__":
    main()
