"""Estimate the next rise from depth and signed quantity.

    .venv/bin/python -m bot.research.depth_flow_lab

Joins the Bitvavo daily cache to Binance USDT taker prints. Missing
liquid bases are fetched once. The in-progress UTC day is dropped.
Writes RESULTS.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bot.research.depth_flow_lab.profile import (
    base_rate,
    bot_cells,
    correlations,
    scan,
    summarize,
)
from bot.research.depth_flow_lab.report import render
from bot.research.depth_flow_lab.sim import run_books
from bot.research.depth_flow_lab.tape import completed_ohlc, ensure_flow, liquid_bases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="bot/research/depth_flow_lab/RESULTS.md")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    ohlc = completed_ohlc()
    print(f"markets {len(ohlc)}", flush=True)
    names = liquid_bases(ohlc)
    print(f"liquid {len(names)}", flush=True)
    if args.no_fetch:
        from bot.research.depth_flow_lab.tape import load_flow

        flow = load_flow(names)
    else:
        flow = ensure_flow(names, log=print)
    print(f"flow {len(flow)}", flush=True)
    events = scan(ohlc, flow)
    print(f"events {len(events)}", flush=True)
    full = run_books(ohlc, events, start="2024-01-01", end="2026-12-31")
    fresh = run_books(ohlc, events, start="2026-01-01", end="2026-12-31")
    for row, year in zip(full, fresh, strict=True):
        print(
            f"{row['strategy']:10} full {row['pnl_eur']:10,.0f} "
            f"trades {row['n_trades']:4d}  2026 {year['pnl_eur']:10,.0f}",
            flush=True,
        )
    asof = max((row.day for row in events), default="")
    text = render(
        asof=asof,
        n_markets=len(ohlc),
        n_flow=len(flow),
        n_events=len(events),
        fast=summarize(events),
        slow=summarize(events, slow=True),
        base_fast=base_rate(events),
        bots=bot_cells(events),
        corr=correlations(events),
        books_full=full,
        books_fresh=fresh,
    )
    Path(args.results).write_text(text)
    print(f"wrote {args.results}", flush=True)


if __name__ == "__main__":
    main()
