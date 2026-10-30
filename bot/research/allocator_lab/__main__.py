"""Scan the Bitvavo daily tape for the footprint of return-seeking size.

    .venv/bin/python -m bot.research.allocator_lab

Uses the daily cache and, when present, the Binance trade-count cache.
The in-progress UTC day is dropped. Writes RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.research.allocator_lab.profile import (
    crowd_days,
    day_of,
    euro_shares,
    scan,
    summarize_cells,
    summarize_crowds,
    summarize_magnets,
    summarize_tickets,
    ticket_events,
)
from bot.research.allocator_lab.report import render
from bot.research.allocator_lab.sim import run_books
from bot.research.unusual_volume_lab.data import CACHE_1D, eur_bases, load_cached

FLOW_DIR = Path("data/entry_quality_flow")


def _load_flow(path: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    if not path.exists():
        return out
    for file in sorted(path.glob("*.json")):
        try:
            rows = json.loads(file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if rows:
            out[file.stem] = rows
    return out


def _flow_asof(flow: dict[str, list[list[float]]]) -> str:
    last = ""
    for rows in flow.values():
        for row in rows:
            day = day_of(int(row[0]))
            if day > last:
                last = day
    return last


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="bot/research/allocator_lab/RESULTS.md")
    args = ap.parse_args()
    now = datetime.now(UTC)
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000)
    bases = eur_bases()
    if "BTC" not in bases:
        bases.append("BTC")
    ohlc: dict[str, list[list[float]]] = {}
    for base, rows in load_cached(CACHE_1D, bases).items():
        done = [row for row in rows if int(row[0]) < today_ms]
        if done:
            ohlc[base] = done
    print(f"markets {len(ohlc)}", flush=True)
    prints, by_day = scan(ohlc)
    print(f"liquid days {len(prints)}", flush=True)
    flow = _load_flow(FLOW_DIR)
    indexed = {(row.base, row.day): row for row in prints}
    tickets = summarize_tickets(ticket_events(flow, indexed)) if flow else None
    print(f"flow bases {len(flow)}", flush=True)
    days = crowd_days(by_day)
    full = run_books(ohlc, by_day, start="2024-01-01", end="2026-12-31")
    fresh = run_books(ohlc, by_day, start="2026-01-01", end="2026-12-31")
    for row, year in zip(full, fresh, strict=True):
        print(
            f"{row['strategy']:10} full {row['pnl_eur']:10,.0f} "
            f"trades {row['n_trades']:4d}  2026 {year['pnl_eur']:10,.0f}",
            flush=True,
        )
    asof = max((row.day for row in prints), default="")
    text = render(
        asof=asof,
        n_markets=len(ohlc),
        n_prints=len(prints),
        flow_bases=len(flow),
        flow_asof=_flow_asof(flow),
        shares=euro_shares(prints),
        cells=summarize_cells(prints),
        stride=summarize_cells([row for row in prints if row.on_stride]),
        crowds=summarize_crowds(days),
        magnets=summarize_magnets(days),
        tickets=tickets,
        books_full=full,
        books_fresh=fresh,
    )
    Path(args.results).write_text(text)
    print(f"wrote {args.results}", flush=True)


if __name__ == "__main__":
    main()
