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
from bot.research.intraday_eight.book import BookResult, Edge, measure, run_book
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


def _pct(value: float) -> str:
    return f"{100 * value:+.2f}%"


def _win(edge: Edge) -> str:
    if edge.trades == 0:
        return "—"
    return f"{100 * edge.wins / edge.trades:.0f}%"


def _hole(value: float) -> str:
    return f"€{int(round(value)):,.0f}"


def _reasons(edge: Edge) -> str:
    if not edge.reasons:
        return "none"
    return ", ".join(f"{name} {count}" for name, count in sorted(edge.reasons.items()))


def _line(name: str, edge: Edge, account: BookResult) -> str:
    years = edge.year_pnl
    return (
        f"| {name} | {edge.trades} | {_win(edge)} | {_pct(edge.avg_net)} | "
        f"{edge.profit_factor:.2f} | {_eur(edge.pnl)} | {_hole(edge.hole)} | "
        f"{_eur(years.get('2024', 0))} | {_eur(years.get('2025', 0))} | "
        f"{_eur(years.get('2026', 0))} | {_eur(account.pnl)} | "
        f"{100 * account.max_dd:.1f}% |"
    )


def _beats(result: BookResult, live: dict) -> bool:
    if result.pnl <= float(live["pnl_eur"]) or result.max_dd > float(live["max_dd_pct"]):
        return False
    for year, pnl in live["year_pnl"].items():
        if result.year_pnl.get(year, -1e18) < float(pnl):
            return False
    return True


def _measure_row(
    label: str,
    trades: list[Trade],
    mode: str,
    start_ts: int,
    end_ts: int,
) -> tuple[str, Edge, BookResult]:
    edge = measure(trades, mode=mode, start_ts=start_ts, end_ts=end_ts)
    account = run_book(
        trades, mode=mode, book=BOOK, start_ts=start_ts, end_ts=end_ts, compound=False
    )
    print(
        f"{label} avg {_pct(edge.avg_net)} signals {_eur(edge.pnl)} "
        f"account {_eur(account.pnl)} {_reasons(edge)}",
        flush=True,
    )
    return label, edge, account


def main() -> None:
    start_ts, end_ts = _ts(START), _ts(END) + 86_400_000
    ohlc = load_candles(CANDLES, merge_fresh=False)
    live = run_btc_residual(ohlc, start=START, end=END, book_eur=BOOK, **LIVE_KNOBS)
    print(f"live {_eur(live['pnl_eur'])}", flush=True)
    books, used = _scan()
    rows: list[tuple[str, Edge, BookResult]] = []
    for name, trades in books.items():
        longs = [trade for trade in trades if trade.side > 0]
        shorts = [trade for trade in trades if trade.side < 0]
        rows.append(_measure_row(f"{name} long", longs, "spot_long", start_ts, end_ts))
        if name == "funding":
            rows.append(_measure_row("funding perp", longs, "perp", start_ts, end_ts))
        if shorts:
            rows.append(_measure_row(f"{name} short", shorts, "perp", start_ts, end_ts))
    text = _render(live, used, rows)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT}", flush=True)


def _render(live: dict, used: int, rows: list[tuple[str, Edge, BookResult]]) -> str:
    better = [name for name, _edge, account in rows if _beats(account, live)]
    live_years = live["year_pnl"]
    lines = [
        "# Eight intraday books",
        "",
        "The earlier table showed the same 99.9% drawdown because each book",
        "spent the €20,000. A drawdown cannot pass 100%, and a later year",
        "prints €0 once the stake is gone. That number is the ruined account,",
        "not the tactic.",
        "",
        f"Window {START} through {END}. {used} alts with a median daily quote",
        f"of at least €{MIN_DAILY_QUOTE:,.0f}. The live residual on this window",
        f"is {_eur(live['pnl_eur'])} with a {100 * live['max_dd_pct']:.1f}% drawdown",
        (
            f"({_eur(live_years.get('2024', 0))} / {_eur(live_years.get('2025', 0))} / "
            f"{_eur(live_years.get('2026', 0))}, {live['n_trades']} trades)."
        ),
        "",
        "VWAP bids the lower band known at the prior close, for the next bar",
        "only, and offers that session VWAP. The EMA book bids EMA 9 the bar",
        "after the close confirms the trend. Both rest a limit: a fill at the",
        "limit pays the Bitvavo maker (0.15%) and a stop or a cross pays the",
        "taker plus impact. The sweep uses the prior UTC day's high and low,",
        "enters on the next open, and aims at the other side of that range.",
        "Opening-range, squeeze, RSI/MACD and Supertrend still enter on the",
        "next open, because the rule is a close breaking a level. Funding is",
        "priced on the perp, where the funding cashflow is real; the spot row",
        "is the price leg only. Shorts are not on the Bitvavo spot book.",
        "",
        "Each signal below is a fixed €5,000. The sum does not stop at zero",
        "and it is not one account: overlapping names each get €5,000. The",
        "hole is the deepest peak-to-trough of that euro sum. The account is",
        "one €20,000 book with four fixed €5,000 slots, and it stops taking",
        "trades when the cash cannot fund a slot. A tactic that loses on",
        "the average therefore spends the book down to just under €5,000,",
        "so the account loss sits near €15,000. That is the slot floor,",
        "not a shared drawdown. Read the average and the sum.",
        "Parameters were locked before this replay. Nothing is armed.",
        "",
        "| book | trades | win | avg net | pf | sum | hole | 2024 | 2025 | 2026 | account | DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_line(name, edge, account) for name, edge, account in rows)
    lines.append("")
    lines.append("Exits:")
    lines.append("")
    for name, edge, _account in rows:
        lines.append(f"- {name}: {_reasons(edge)}")
    lines.extend(
        [
            "",
            "Better than the live residual: " + (", ".join(better) if better else "none") + ".",
            "",
            "Reproduce: `python -m bot.research.intraday_eight`",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
