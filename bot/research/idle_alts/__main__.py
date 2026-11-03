"""Replay the idle-cash book. Nothing is armed.

Reproduce with ``python -m bot.research.idle_alts``.
"""

from __future__ import annotations

from pathlib import Path

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import bar_date
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS
from bot.research.idle_alts.engine import (
    DESK,
    NOT_ALT,
    HoldDay,
    beats_live,
    load_alt_sessions,
    simulate,
)

START = "2024-03-16"
END = "2026-10-14"
BOOK = 20_000.0
CANDLES = Path("data/residual_wet_candles")
DAILY = Path("data/research/alphai_sleeve/1d")
OUT = Path(__file__).with_name("RESULTS.md")


def _btc_closes(ohlc: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in ohlc.get("BTC") or []:
        out[bar_date(row)] = float(row[4])
    return out


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def main() -> None:
    ohlc = load_candles(CANDLES, merge_fresh=False)
    live = run_btc_residual(
        ohlc,
        start=START,
        end=END,
        book_eur=BOOK,
        keep_holds=True,
        **LIVE_KNOBS,
    )
    holds = [
        HoldDay(row["date"], row["hold"], float(row["equity"]))
        for row in live["holds"]
        if row["date"] <= END
    ]
    control = simulate(holds, {}, _btc_closes(ohlc), book_eur=BOOK)
    print(
        f"live {_eur(live['pnl_eur'])} control {_eur(control.pnl)} "
        f"days {len(holds)}",
        flush=True,
    )
    listed = [
        path.stem
        for path in sorted(DAILY.glob("*.json"))
        if path.stem not in DESK and path.stem not in NOT_ALT
    ]
    print(f"alts {len(listed)}", flush=True)
    books = load_alt_sessions(DAILY, listed)
    print(f"loaded {len(books)}", flush=True)
    btc = _btc_closes(ohlc)
    capped = simulate(holds, books, btc, book_eur=BOOK, exit_mode="target")
    stopped = simulate(holds, books, btc, book_eur=BOOK, exit_mode="stop")
    live_years = {year: float(pnl) for year, pnl in live["year_pnl"].items()}
    better = beats_live(stopped, float(live["pnl_eur"]), float(live["max_dd_pct"]), live_years)
    text = _render(live, control, capped, stopped, better)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT}", flush=True)


def _book_line(name: str, pnl: float, dd: float, years: dict, buys: int) -> str:
    return (
        f"| {name} | {_eur(pnl)} | {100 * dd:.1f}% | "
        f"{_eur(years.get('2024', 0))} | {_eur(years.get('2025', 0))} | "
        f"{_eur(years.get('2026', 0))} | {buys} |"
    )


def _pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _exit_line(run) -> str:
    parts = []
    if run.target_exits:
        parts.append(f"{run.target_exits} at +6% ({_pct(run.avg_net['target'])})")
    if run.time_exits:
        parts.append(f"{run.time_exits} on the fourth session ({_pct(run.avg_net['time'])})")
    if run.stop_exits:
        parts.append(f"{run.stop_exits} at -6% ({_pct(run.avg_net['stop'])})")
    if run.gap_exits:
        parts.append(f"{run.gap_exits} gapped through the stop ({_pct(run.avg_net['gap'])})")
    if run.forced_exits:
        parts.append(f"{run.forced_exits} handed back ({_pct(run.avg_net['forced'])})")
    return ", ".join(part for part in parts if part)


def _render(live: dict, control, capped, stopped, better: bool) -> str:
    return "\n".join(
        [
            "# Idle alts",
            "",
            "The live book is unchanged. On days it is flat at both closes,",
            "idle euros can buy up to four non-desk alts after an 8% drop",
            "that BTC did not share. `capped` sells at +6% or on the fourth",
            "session. `stop` sells 6% under the entry and lets a winner run",
            "until the live book wants the cash. Rules were locked before the replay.",
            "",
            "| book | pnl | max DD | 2024 | 2025 | 2026 | buys |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            _book_line(
                "live",
                live["pnl_eur"],
                live["max_dd_pct"],
                live["year_pnl"],
                live["n_trades"],
            ),
            _book_line("control, no alts", control.pnl, control.max_dd, control.year_pnl, 0),
            _book_line("capped profit", capped.pnl, capped.max_dd, capped.year_pnl, capped.buys),
            _book_line("stopped loss", stopped.pnl, stopped.max_dd, stopped.year_pnl, stopped.buys),
            "",
            f"Capped exits: {_exit_line(capped)}.",
            f"Stopped exits: {_exit_line(stopped)}.",
            "",
            "The stopped book is better than live."
            if better
            else "The stopped book is not better than live.",
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.idle_alts`",
            "",
        ]
    )


if __name__ == "__main__":
    main()
