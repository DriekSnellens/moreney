"""Scan large-name taker selling against the next day in smaller names.

Usage::

    .venv/bin/python -m bot.research.capital_flow
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bot.research.capital_flow.engine import (
    BREADTH,
    BREADTH_NEIGHBOURS,
    BUY_BOOK,
    DAY_MS,
    SELL_BOOK,
    SELL_BOOK_NEIGHBOURS,
    BarRow,
    DayResult,
    FlowRow,
    basket_stats,
    keep,
    run_days,
)

FLOW = Path("data/entry_quality_flow")
BARS = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/capital_flow/ROTATION.md")


def _load_flow(path: Path) -> list[FlowRow]:
    raw = json.loads(path.read_text())
    return [(int(row[0]), float(row[1]), float(row[2]), float(row[4])) for row in raw]


def _load_bars(path: Path) -> list[BarRow]:
    raw = json.loads(path.read_text())
    return [
        (int(row[0]), float(row[1]), float(row[2]), float(row[4]) * float(row[5])) for row in raw
    ]


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _year(signal_ms: int) -> int:
    return datetime.fromtimestamp((signal_ms + DAY_MS) / 1000, UTC).year


def _outcome(name: str, days: list[DayResult], basket: str) -> str:
    fee = basket_stats(days, f"{basket}_net")
    wet = basket_stats(days, f"{basket}_wet")
    rise = basket_stats(days, f"{basket}_rise")
    if fee.get("n", 0.0) <= 0.0:
        return f"| {name} | 0 | — | — | — | — |"
    return (
        f"| {name} | {int(fee['n'])} | {_pct(fee['mean'])} | {_pct(fee['median'])} "
        f"| {_pct(wet['mean'])} | {_pct(rise['mean'])} |"
    )


def _regimes(days: list[DayResult]) -> list[tuple[str, list[DayResult]]]:
    rows: list[tuple[str, list[DayResult]]] = [("alle dagen", keep(days))]
    for cut in (SELL_BOOK, *SELL_BOOK_NEIGHBOURS):
        label = f"geld verkocht, mand vanaf {int(round(100 * cut))}%"
        rows.append((label, keep(days, sell_at_least=cut)))
    rows.append(
        (f"geld gekocht, mand tot {int(round(100 * BUY_BOOK))}%", keep(days, sell_at_most=BUY_BOOK))
    )
    for cut in (BREADTH, *BREADTH_NEIGHBOURS):
        label = f"veel coins verkocht, minstens {int(round(100 * cut))}%"
        rows.append((label, keep(days, breadth_at_least=cut)))
    rows.append(
        (
            "veel coins én geld verkocht",
            keep(days, sell_at_least=SELL_BOOK, breadth_at_least=BREADTH),
        )
    )
    return rows


def _write(path: Path, days: list[DayResult]) -> None:
    head = [
        "| meting | dagen | na taker | mediaan | na taker en impact | stijgt +3% |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lines = [
        "# Geldstroom van groot naar klein",
        "",
        "Bitvavo bewaart geen historische koop- of verkoopkant. Of een coin",
        "verkocht werd, staat op de Binance-USDT-dagkaars van diezelfde naam:",
        "taker-sell gedeeld door de hele omzet. De long, als het signaal",
        "gebruikt wordt, koopt de volgende Bitvavo-open in euro en verkoopt",
        "de open daarna.",
        "",
        "Groot en klein is elke dag opnieuw het kwartiel van de Binance-omzet",
        "over de voorgaande 7 dagen. Een naam waarvan de gemiddelde prijs in",
        "die week binnen 0,5% blijft, is een peg en doet niet mee. Een coin",
        "is verkocht als zijn eigen taker-sell minstens 55% is. Veel coins",
        "betekent dat minstens de helft van de grote namen zo verkocht is.",
        "De buren zijn 40% en 60% van de namen. Veel geld betekent dat de",
        "omzetgewogen verkoop van de grote mand minstens 55% is, met buren",
        "52% en 58%. De spiegel is een mand die gekocht wordt, taker-sell",
        "hoogstens 45%.",
        "",
        "Een dag telt met minstens 32 gerangschikte namen, een mand met",
        "minstens 8. Onder €10.000 Bitvavo-omzet op de signaaldag wordt een",
        "coin niet gekocht. De natte fill rekent een order van €5.000, taker",
        "0,25% per kant, en impact 0,02 maal de wortel van order gedeeld door",
        "de omzet van de signaaldag, met een plafond van 3% per kant. Elke rij",
        "stond vast voor de run.",
        "",
        "## Kleine namen, de volgende dag",
        "",
        *head,
    ]
    regimes = _regimes(days)
    lines += [_outcome(name, picked, "small") for name, picked in regimes]
    lines += ["", "## Middelste namen, de volgende dag", "", *head]
    lines += [_outcome(name, picked, "mid") for name, picked in regimes]
    lines += ["", "## Grote namen, de volgende dag", "", *head]
    lines += [_outcome(name, picked, "large") for name, picked in regimes]
    lines += ["", "## Jaren, kleine namen", "", *head]
    for year in (2024, 2025, 2026):
        year_days = [day for day in days if _year(day.signal_ms) == year]
        lines.append(_outcome(f"{year} alle dagen", keep(year_days), "small"))
        lines.append(
            _outcome(
                f"{year} veel coins verkocht",
                keep(year_days, breadth_at_least=BREADTH),
                "small",
            )
        )
        lines.append(
            _outcome(
                f"{year} geld gekocht",
                keep(year_days, sell_at_most=BUY_BOOK),
                "small",
            )
        )
    lines += [
        "",
        "## Wordt er dezelfde dag in de kleine namen gekocht?",
        "",
        "Dit is de omzetgewogen taker-koop van de kleine namen op de",
        "signaaldag zelf. De long hierboven gebruikt die dag niet als fill.",
        "",
        "| meting | dagen | taker-koop klein | mediaan |",
        "|---|---:|---:|---:|",
    ]
    for name, picked in (
        ("alle dagen", keep(days)),
        ("veel coins verkocht", keep(days, breadth_at_least=BREADTH)),
        ("geld verkocht, mand vanaf 55%", keep(days, sell_at_least=SELL_BOOK)),
        ("geld gekocht, mand tot 45%", keep(days, sell_at_most=BUY_BOOK)),
    ):
        stats = basket_stats(picked, "small_buy")
        if stats.get("n", 0.0) <= 0.0:
            lines.append(f"| {name} | 0 | — | — |")
        else:
            lines.append(
                f"| {name} | {int(stats['n'])} | {_pct(stats['mean'])} | {_pct(stats['median'])} |"
            )
    lines += ["", _closing(days), "", "Niets hiervan wordt live gezet.", ""]
    path.write_text("\n".join(lines))


def _closing(days: list[DayResult]) -> str:
    breadth = basket_stats(keep(days, breadth_at_least=BREADTH), "small_wet")
    base = basket_stats(keep(days), "small_wet")
    soft_cut = min(SELL_BOOK_NEIGHBOURS)
    soft = basket_stats(keep(days, sell_at_least=soft_cut), "small_wet")
    if breadth.get("n", 0.0) <= 0.0:
        head = (
            "De rij waarin minstens de helft van de grote namen verkocht is, heeft geen enkele dag."
        )
    else:
        head = (
            "Als minstens de helft van de grote namen verkocht is, levert de kleine mand "
            f"daarna {_pct(breadth['mean'])} na taker en impact over {int(breadth['n'])} dagen."
        )
    if soft.get("n", 0.0) <= 0.0 or base.get("n", 0.0) <= 0.0:
        return head
    tail = (
        f" Op dagen met minstens {int(round(100 * soft_cut))}% omzet verkocht in de grote mand "
        f"levert de kleine mand daarna {_pct(soft['mean'])} na taker en impact "
        f"over {int(soft['n'])} dagen, tegen {_pct(base['mean'])} over alle dagen."
    )
    return head + tail


def main() -> None:
    flow = {path.stem: _load_flow(path) for path in sorted(FLOW.glob("*.json"))}
    bars = {path.stem: _load_bars(path) for path in sorted(BARS.glob("*.json"))}
    print(f"flow {len(flow)} bitvavo {len(bars)}", flush=True)
    days = run_days(flow, bars)
    print(f"days {len(days)}", flush=True)
    _write(OUT, days)
    print(OUT.read_text())


if __name__ == "__main__":
    main()
