"""Replay the locked cells against the live residual book.

Reproduce with ``python -m bot.research.beat_live``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from bot.research.beat_live import BAR_MULT
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS, LIVE_POLICY

START = "2024-03-16"
END = "2026-10-03"
BOOK = 20_000.0
CANDLES = Path("data/residual_wet_candles")
OUT = Path(__file__).with_name("RESULTS.md")
SHIFTS = 7


def _policy(name: str, *, arm: float = 0.0, trail: float = 0.0, hold: bool = False) -> ExitPolicy:
    return replace(
        LIVE_POLICY,
        name=name,
        alt_runner_arm_pct=arm,
        alt_runner_trail_pct=trail,
        alt_runner_hold=hold,
    )


CELLS: list[tuple[str, dict]] = [
    ("live", {}),
    ("runner_20_trail20", {"policy": _policy("runner_20_trail20", arm=0.20, trail=0.20)}),
    ("runner_40_trail20", {"policy": _policy("runner_40_trail20", arm=0.40, trail=0.20)}),
    ("runner_40_hold", {"policy": _policy("runner_40_hold", arm=0.40, hold=True)}),
    ("flatten_regime", {"flatten": "regime"}),
    ("equity_ma20", {"equity_ma_block": 20}),
]


def _starts() -> list[str]:
    origin = datetime.strptime(START, "%Y-%m-%d")
    return [(origin + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(SHIFTS)]


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _run(ohlc, start: str, extra: dict) -> dict:
    knobs = {**LIVE_KNOBS, **extra}
    return run_btc_residual(ohlc, start=start, end=END, book_eur=BOOK, **knobs)


def _beats(row: dict, live: dict) -> bool:
    if float(row["pnl_eur"]) < BAR_MULT * float(live["pnl_eur"]):
        return False
    if float(row["max_dd_pct"]) > float(live["max_dd_pct"]) + 1e-9:
        return False
    for year, pnl in live["year_pnl"].items():
        if float(row["year_pnl"].get(year, -1e18)) < float(pnl):
            return False
    return True


def _line(name: str, row: dict) -> str:
    years = row["year_pnl"]
    return (
        f"| {name} | {_eur(row['pnl_eur'])} | {100 * row['max_dd_pct']:.1f}% | "
        f"{row['n_trades']} | {_eur(years.get('2024', 0))} | "
        f"{_eur(years.get('2025', 0))} | {_eur(years.get('2026', 0))} |"
    )


def main() -> None:
    ohlc = load_candles(CANDLES, merge_fresh=False)
    starts = _starts()
    table: dict[str, dict[str, dict]] = {}
    for start in starts:
        table[start] = {}
        for name, extra in CELLS:
            row = _run(ohlc, start, extra)
            table[start][name] = row
            print(
                f"{start} {name} {_eur(row['pnl_eur'])} dd {100 * row['max_dd_pct']:.1f}%",
                flush=True,
            )
    text = _render(table, starts)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT}", flush=True)


def _render(table: dict[str, dict[str, dict]], starts: list[str]) -> str:
    live0 = table[START]["live"]
    passed: list[str] = []
    lines = [
        "# Beat-live search",
        "",
        f"Window ending {END}, book €{BOOK:,.0f}, wet Bitvavo next-open.",
        "AlphaI, the intraday books, the factor overlays, the bear sleeve",
        "and the volume-spike books were already replayed. They are not",
        "in this table. The live row is the armed residual pack. A runner",
        "cell keeps that pack and, once the peak gain reaches the arm,",
        "either widens the 10% trail to 20% or turns the trail and the",
        "lock off. Below the",
        "arm the exit is unchanged. `flatten_regime` keeps a qualifying alt",
        "when BTC is under its SMA50, where the live book goes to cash.",
        "`equity_ma20` starts no new name while the book is under its own",
        "20-day equity average. A name already held is kept.",
        "",
        f"Considerably better means at least {BAR_MULT:.1f} times the live pnl",
        "on 2024-03-16, each year at least the live year, drawdown not deeper,",
        "and a pnl at least as high as the live book on every start from",
        "2024-03-16 through 2024-03-22. Nothing is armed.",
        "",
        f"## {START}",
        "",
        "| cell | pnl | max DD | trades | 2024 | 2025 | 2026 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_line(name, table[START][name]) for name, _extra in CELLS)
    lines.extend(["", "## Other start days", ""])
    lines.append("| cell | " + " | ".join(starts) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in starts) + " |")
    for name, _extra in CELLS:
        cells = " | ".join(_eur(table[start][name]["pnl_eur"]) for start in starts)
        lines.append(f"| {name} | {cells} |")
        if name == "live":
            continue
        if not _beats(table[START][name], live0):
            continue
        if all(
            table[start][name]["pnl_eur"] + 1e-6 >= table[start]["live"]["pnl_eur"]
            for start in starts
        ):
            passed.append(name)
    lines.extend(
        [
            "",
            "Considerably better: " + (", ".join(passed) if passed else "none") + ".",
            "",
            "Reproduce: `python -m bot.research.beat_live`",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
