"""Neighbor times and percentages around the armed residual pack.

Usage::

    .venv/bin/python -m bot.research.residual_neighbors
"""

from __future__ import annotations

from pathlib import Path

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import bar_date
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.residual_neighbors.engine import YEARS, Row, Spec, beats, grid, knobs

DAILY = Path("data/residual_wet_candles")
OUT = Path("bot/research/residual_neighbors/SWEEP.md")
START = "2024-03-16"
FAMILIES = (
    ("live", "Live"),
    ("terugblik", "Terugblik"),
    ("overslaan", "Dagen overslaan"),
    ("klok", "Klok"),
    ("gemiddelde", "Gemiddelde"),
    ("drempel", "Drempel"),
    ("trail", "Trail"),
    ("lock", "Lock"),
    ("near", "Near-high"),
    ("kruis_tijd", "Terugblik tegen klok"),
    ("kruis_uitstap", "Trail tegen lock"),
    ("weekdag", "Weekdag van de klok"),
)


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(100.0 * value):.1f}".replace(".", ",")
    return f"{sign}{text}%"


def _run(ohlc: dict, spec: Spec, end: str) -> Row:
    raw = run_btc_residual(
        ohlc,
        start=START,
        end=end,
        book_eur=20_000.0,
        strategy=spec.name,
        **knobs(spec),
    )
    year = {str(key): float(value) for key, value in (raw.get("year_pnl") or {}).items()}
    return Row(
        spec.name,
        spec.family,
        float(raw["end_eur"]),
        float(raw["pnl_eur"]),
        float(raw["max_dd_pct"]),
        int(raw["n_trades"]),
        year,
    )


def _sentence(row: Row) -> str:
    years = ", ".join(f"{year} {_eur(row.year.get(year, 0.0))}" for year in YEARS)
    return (
        f"{row.name} eindigt op {_eur(row.end)} "
        f"(PnL {_eur(row.pnl)}, terugval {_pct(row.dd)}, {row.trades} rondes, {years})"
    )


def _closing(rows: list[Row], control: Row) -> str:
    winners = sorted(
        (row for row in rows if row.beats),
        key=lambda row: row.end,
        reverse=True,
    )
    ranked = sorted(
        (row for row in rows if row.name != control.name),
        key=lambda row: row.end,
        reverse=True,
    )
    parts = []
    if winners:
        lead = _sentence(winners[0])
        parts.append(f"De hoogste cel die het pakket op deze lat verslaat: {lead}.")
        if len(winners) > 1:
            parts.append(f"In totaal halen {len(winners)} cellen die lat.")
        rest = [row for row in winners if row.end != winners[0].end]
        if rest:
            shared = [row.name for row in winners if row.end == rest[0].end]
            if len(shared) > 1:
                names = " en ".join(shared)
                gap = rest[0].end - control.end
                parts.append(
                    f"{names} drukken hetzelfde boek af, {_eur(gap)} boven het pakket."
                )
    else:
        parts.append("Geen cel haalt de lat tegen het live pakket.")
    top_is_winner = bool(winners) and ranked and ranked[0].name == winners[0].name
    if ranked and not top_is_winner:
        parts.append(f"De hoogste cel van het rooster: {_sentence(ranked[0])}.")
    parts.append(
        f"Het live pakket eindigt op {_eur(control.end)}, PnL {_eur(control.pnl)}, "
        f"terugval {_pct(control.dd)}, {control.trades} rondes."
    )
    parts.append(
        "Het rooster staat op dezelfde tape als het pakket. Een hogere cel is "
        "een kandidaat, geen nieuwe live-stand. Er is niets aangezet."
    )
    return " ".join(parts)


def _table(lines: list[str], rows: list[Row]) -> None:
    lines.append("| cel | eind | PnL | terugval | rondes | 2024 | 2025 | 2026 | hoger |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in rows:
        years = " | ".join(_eur(row.year.get(year, 0.0)) for year in YEARS)
        flag = "ja" if row.beats else "nee"
        lines.append(
            f"| {row.name} | {_eur(row.end)} | {_eur(row.pnl)} | {_pct(row.dd)} "
            f"| {row.trades} | {years} | {flag} |"
        )


def _write(path: Path, end: str, rows: list[Row]) -> None:
    control = rows[0]
    lines = [
        "# Buren van het residual-pakket",
        "",
        "Het live pakket blijft de controle: terugblik 10 dagen, één dag",
        "overslaan, drempel 3,5%, gemiddelde 50, klok 7 dagen, near-high 0,25,",
        "trail 10% en lock +12%/+6%. Elke andere cel verandert één tijd of",
        f"één percentage, of een van de twee kruisingen. Venster {START} t/m {end}.",
        "Start €20.000. Natte Bitvavo-vulling. De tape is dagelijks, dus een",
        "tijd is een aantal dagen of de weekdag van de klok.",
        "",
        "Een cel verslaat het pakket alleen met een hogere eindstand, een plus",
        "in 2024, 2025 en 2026, minstens 30 rondes, en een terugval die",
        "hoogstens 15 procentpunten slechter is. Niets wordt aangezet.",
        "",
        "## Uitkomst",
        "",
        _closing(rows, control),
        "",
    ]
    by_family = {family: [] for family, _title in FAMILIES}
    for row in rows:
        by_family[row.family].append(row)
    for family, title in FAMILIES:
        lines += [f"## {title}", ""]
        _table(lines, by_family[family])
        lines.append("")
    lines.append("Er is niets aangezet.")
    lines.append("")
    path.write_text("\n".join(lines))


def main() -> None:
    ohlc = load_candles(DAILY, merge_fresh=False)
    end = bar_date(ohlc["BTC"][-1])
    specs = grid()
    rows: list[Row] = []
    control: Row | None = None
    for index, spec in enumerate(specs, start=1):
        row = _run(ohlc, spec, end)
        if control is None:
            control = row
        else:
            row.beats = beats(row, control)
        rows.append(row)
        print(
            f"{index}/{len(specs)} {spec.name} end {row.end:.0f} dd {row.dd:.1%}",
            flush=True,
        )
    assert control is not None
    _write(OUT, end, rows)
    print(_closing(rows, control))


if __name__ == "__main__":
    main()
