"""Run the locked weekly book.

Usage::

    .venv/bin/python -m bot.research.week_book
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bot.research.rise_50.engine import market_ratios
from bot.research.week_book.engine import (
    MIN_YEAR_TRADES,
    Coin,
    clears,
    locked_rules,
    make_coin,
    master_dates,
    run_rule,
)

DAILY = Path("data/research/bitvavo_1d_all")
OUT = Path("bot/research/week_book/WEEK.md")
LABELS = {
    "mom7": "hoogste stijging over 7 dagen",
    "mom28": "hoogste stijging over 28 dagen",
    "mom7_vol": "hoogste stijging, omzet vanaf 2×",
    "luid_mom7": "hoogste stijging, alleen op een luide markt",
    "luid_diep": "hoogste omzet, alleen op een luide markt",
    "rev7": "grootste daling over 7 dagen",
    "mom7_stop": "hoogste stijging, stop op −15%",
    "top5": "vijf hoogste stijgingen, gelijke weging",
    "dip": "daling binnen een stijging van 28 dagen",
    "mom7_markt": "hoogste stijging als de diepste markt omhoog is",
    "vasthouden": "eenmaal de diepste munt, tot het eind",
}


def _load(path: Path) -> Coin | None:
    raw = json.loads(path.read_text())
    if len(raw) < 40:
        return None
    cols = [np.asarray([float(row[i]) for row in raw], dtype=np.float64) for i in range(6)]
    ts = cols[0].astype(np.int64)
    quote = cols[4] * cols[5]
    return make_coin(path.stem, ts, cols[1], cols[2], cols[3], cols[4], quote)


def _eur(value: float) -> str:
    sign = "−" if value < 0 else ""
    text = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}€{text}"


def _pct(value: float) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{100.0 * value:.1f}%".replace(".", ",")


def _year(result, year: int) -> str:
    trades = result.year_trades.get(year, 0)
    if trades <= 0 and year not in result.year_pct:
        return "—"
    pct = result.year_pct.get(year, 0.0)
    return f"{_eur(result.year_pnl.get(year, 0.0))} ({_pct(pct)}, {trades})"


def _write(path: Path, rows: list) -> None:
    passed = [row for row in rows if clears(row)]
    lines = [
        "# Weekboek",
        "",
        "Eén boek van €20.000, elke zeven dagen opnieuw. Het signaal is het",
        "slot. De koop is de open van de volgende dag, de verkoop de open",
        "zeven dagen later. Een stop, waar die er staat, vult op het niveau",
        "of op de open als de dag erdoor opent. De boekwaarde loopt mee.",
        "Elke kant betaalt 0,25% taker plus impact op de omzet van de",
        "signaaldag. Een munt moet die dag minstens €1.000.000 omzetten en",
        "mag geen peg zijn.",
        "",
        "Een regel haalt de lat alleen als ze minstens 30 rondes draait,",
        "boven €20.000 eindigt, en in 2024, 2025 én 2026 minstens",
        f"{MIN_YEAR_TRADES} rondes en een positief euroresultaat heeft.",
        "Elke regel hieronder stond vast voor de run.",
        "",
        "| regel | eind | winst | terugval | rondes | win | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        win = "—" if row.trades == 0 else _pct(row.wins / row.trades)
        lines.append(
            f"| {LABELS[row.name]} | {_eur(row.equity)} | {_eur(row.pnl)} "
            f"| {_pct(row.dd)} | {row.trades} | {win} "
            f"| {_year(row, 2024)} | {_year(row, 2025)} | {_year(row, 2026)} |"
        )
    lines += [
        "",
        "De terugval is gemeten tussen de herwegingen. Bij de regel die",
        "eenmaal koopt is de terugval de koers tussendoor, en de kosten van",
        "de verkoop zitten in het laatste jaar.",
        "",
    ]
    if passed:
        names = ", ".join(LABELS[row.name] for row in passed)
        lines.append(f"{names} haalt de lat. Het boek wordt niet aangezet.")
    else:
        lines.append("Geen van deze regels haalt de lat. Er is niets aangezet.")
    lines.append("")
    path.write_text("\n".join(lines))


def main() -> None:
    coins = []
    for path in sorted(DAILY.glob("*.json")):
        coin = _load(path)
        if coin is not None:
            coins.append(coin)
    totals: dict[int, float] = {}
    for coin in coins:
        for stamp, quote in zip(coin.ts, coin.quote, strict=True):
            if quote > 0.0:
                totals[int(stamp)] = totals.get(int(stamp), 0.0) + float(quote)
    dates = master_dates(coins)
    market = market_ratios(totals)
    print(f"coins {len(coins)} days {len(dates)}", flush=True)
    rows = []
    for rule in locked_rules():
        result = run_rule(rule, coins, dates, market)
        rows.append(result)
        print(
            f"{rule.name} eind {result.equity:.0f} rondes {result.trades} dd {result.dd:.1%}",
            flush=True,
        )
    _write(OUT, rows)
    print(OUT.read_text())


if __name__ == "__main__":
    main()
