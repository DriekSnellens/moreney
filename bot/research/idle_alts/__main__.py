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
    combined = simulate(holds, books, _btc_closes(ohlc), book_eur=BOOK)
    live_years = {year: float(pnl) for year, pnl in live["year_pnl"].items()}
    better = beats_live(combined, float(live["pnl_eur"]), float(live["max_dd_pct"]), live_years)
    text = _render(live, control, combined, better)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT}", flush=True)


def _book_line(name: str, pnl: float, dd: float, years: dict, buys: int) -> str:
    return (
        f"| {name} | {_eur(pnl)} | {100 * dd:.1f}% | "
        f"{_eur(years.get('2024', 0))} | {_eur(years.get('2025', 0))} | "
        f"{_eur(years.get('2026', 0))} | {buys} |"
    )


def _render(live: dict, control, combined, better: bool) -> str:
    return "\n".join(
        [
            "# Idle alts",
            "",
            "The live book is unchanged. On days it is flat at both closes,",
            "idle euros can buy up to four non-desk alts after an 8% drop",
            "that BTC did not share. Rules are the package docstring.",
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
            _book_line(
                "live + idle alts",
                combined.pnl,
                combined.max_dd,
                combined.year_pnl,
                combined.buys,
            ),
            "",
            (
                f"Idle exits: {combined.target_exits} at +6%, "
                f"{combined.time_exits} on the fourth session, "
                f"{combined.forced_exits} because the live book took the cash back."
            ),
            "",
            "Better than live." if better else "Not better than live.",
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.idle_alts`",
            "",
        ]
    )


if __name__ == "__main__":
    main()
