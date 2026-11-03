"""Replay the eight intraday books. Nothing is armed.

Reproduce with ``python -m bot.research.intraday_eight``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS
from bot.research.intraday_eight.book import BookResult, run_book
from bot.research.intraday_eight.indicators import median_daily_quote
from bot.research.intraday_eight.strategies import (
    SCANNERS,
    Trade,
    funding_momentum,
)

START = "2024-03-16"
END = "2026-10-03"
BOOK = 20_000.0
MIN_DAILY_QUOTE = 100_000.0
BARS = Path("data/research/alphai_sleeve/15m")
FUNDING = Path("data/research/external/binance_funding.json")
CANDLES = Path("data/residual_wet_candles")
OUT = Path(__file__).with_name("RESULTS.md")
NOT_ALT = frozenset(
    {
        "BTC", "EUR", "USD", "EURC", "EURCV", "EUROP", "USDC", "USDCV", "USDT",
        "DAI", "FRAX", "TUSD", "USDE", "FDUSD", "PYUSD",
    }
)
BOTH = ("ema_pullback", "sweep", "orb", "squeeze", "supertrend")


def _ts(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _load_bars(path: Path) -> tuple[np.ndarray, ...] | None:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list) or len(rows) < 96 * 30:
        return None
    parsed = []
    for row in rows:
        if len(row) < 6:
            continue
        parsed.append(
            (
                int(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
        )
    parsed.sort()
    cols = list(zip(*parsed, strict=False))
    if len(cols) < 6:
        return None
    return tuple(np.asarray(col, dtype=np.float64) for col in cols)


def _funding() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    if not FUNDING.exists():
        return {}
    raw = json.loads(FUNDING.read_text(encoding="utf-8"))
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for base, rows in raw.items():
        if base in NOT_ALT or not isinstance(rows, list):
            continue
        rows = sorted(rows, key=lambda item: int(item[0]))
        out[base] = (
            np.asarray([int(item[0]) for item in rows], dtype=np.int64),
            np.asarray([float(item[1]) for item in rows], dtype=np.float64),
        )
    return out


def _scan() -> tuple[dict[str, list[Trade]], int]:
    books: dict[str, list[Trade]] = {name: [] for name in (*SCANNERS, "funding")}
    funding = _funding()
    used = 0
    paths = sorted(BARS.glob("*.json"))
    for index, path in enumerate(paths, start=1):
        base = path.stem
        if base in NOT_ALT:
            continue
        loaded = _load_bars(path)
        if loaded is None:
            continue
        ts, open_, high, low, close, volume = loaded
        ts_i = ts.astype(np.int64)
        if median_daily_quote(ts_i, close, volume) < MIN_DAILY_QUOTE:
            continue
        used += 1
        arrays = (base, ts_i, open_, high, low, close, volume)
        for name, scanner in SCANNERS.items():
            books[name].extend(scanner(*arrays))
        series = funding.get(base)
        if series is not None:
            books["funding"].extend(funding_momentum(*arrays, series[0], series[1]))
        if index % 50 == 0:
            print(f"scanned {index}/{len(paths)} kept {used}", flush=True)
    print(f"liquid alts {used}", flush=True)
    return books, used


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _line(name: str, mode: str, result: BookResult) -> str:
    years = result.year_pnl
    win = 100.0 * result.wins / result.trades if result.trades else 0.0
    return (
        f"| {name} | {mode} | {_eur(result.pnl)} | {100 * result.max_dd:.1f}% | "
        f"{_eur(years.get('2024', 0))} | {_eur(years.get('2025', 0))} | "
        f"{_eur(years.get('2026', 0))} | {result.trades} | {win:.0f}% |"
    )


def _beats(result: BookResult, live: dict) -> bool:
    if result.pnl <= float(live["pnl_eur"]) or result.max_dd > float(live["max_dd_pct"]):
        return False
    for year, pnl in live["year_pnl"].items():
        if result.year_pnl.get(year, -1e18) < float(pnl):
            return False
    return True


def main() -> None:
    start_ts, end_ts = _ts(START), _ts(END) + 86_400_000
    ohlc = load_candles(CANDLES, merge_fresh=False)
    live = run_btc_residual(
        ohlc, start=START, end=END, book_eur=BOOK, **LIVE_KNOBS
    )
    print(f"live {_eur(live['pnl_eur'])}", flush=True)
    books, used = _scan()
    rows: list[tuple[str, str, BookResult]] = []
    for name, trades in books.items():
        spot = run_book(trades, mode="spot_long", book=BOOK, start_ts=start_ts, end_ts=end_ts)
        rows.append((name, "spot long", spot))
        print(f"{name} spot {_eur(spot.pnl)} n={spot.trades}", flush=True)
        if name in BOTH or name == "funding":
            perp = run_book(trades, mode="perp", book=BOOK, start_ts=start_ts, end_ts=end_ts)
            label = "perp both" if name in BOTH else "perp long"
            rows.append((name, label, perp))
            print(f"{name} {label} {_eur(perp.pnl)} n={perp.trades}", flush=True)
    better = [name for name, _mode, result in rows if _beats(result, live)]
    text = _render(live, used, rows, better)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT}", flush=True)


def _render(
    live: dict,
    used: int,
    rows: list[tuple[str, str, BookResult]],
    better: list[str],
) -> str:
    lines = [
        "# Eight intraday books",
        "",
        f"15-minute bars, {START} through {END}. {used} alts with a median",
        f"daily quote of at least €{MIN_DAILY_QUOTE:,.0f}. Four slots of a €20,000 book.",
        "Longs on the spot book pay the Bitvavo taker and square-root impact.",
        "Shorts are on the perp book only (0.05% fee and 0.05% slip per side).",
        "There is no open-interest series. Funding is the Binance cache for the",
        "names that have it. Rules are the package docstring. Nothing is armed.",
        "",
        "| book | venue | pnl | max DD | 2024 | 2025 | 2026 | trades | win |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| live residual | spot | {_eur(live['pnl_eur'])} | {100 * live['max_dd_pct']:.1f}% | "
            f"{_eur(live['year_pnl'].get('2024', 0))} | {_eur(live['year_pnl'].get('2025', 0))} | "
            f"{_eur(live['year_pnl'].get('2026', 0))} | {live['n_trades']} |  |"
        ),
    ]
    lines.extend(_line(name, mode, result) for name, mode, result in rows)
    lines.extend(
        [
            "",
            "Better than live: " + (", ".join(better) if better else "none") + ".",
            "",
            "Reproduce: `python -m bot.research.intraday_eight`",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
