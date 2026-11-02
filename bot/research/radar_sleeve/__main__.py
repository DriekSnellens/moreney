"""Replay the locked radar sleeve on the archived news window.

Usage::

    .venv/bin/python -m bot.research.radar_sleeve
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.alphai_crypto_feeds.archive import ARCHIVE_PATH, load_archive
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, bar_date
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS
from bot.research.radar_sleeve.score import (
    BASELINE_DAYS,
    GATES,
    HOT_Z,
    MAX_STORY_TICKERS,
    SPIKE_TAIL,
    index_stories,
    picks_for,
)
from bot.research.radar_sleeve.sleeve import run_sleeve

BOOK_EUR = 20_000.0
SLEEVE_FRAC = 0.15


def _tape_end(ohlc: dict[str, list[list[float]]]) -> str:
    last = bar_date(ohlc["BTC"][-1])
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if last == today and len(ohlc["BTC"]) > 1:
        return bar_date(ohlc["BTC"][-2])
    return last


def _dd(equity: list[float], start: float) -> float:
    peak = start
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _combine(
    residual: dict[str, Any],
    sleeve: dict[str, Any],
    *,
    book: float,
    residual_book: float,
    sleeve_book: float,
) -> dict[str, float]:
    left = {str(day): float(value) for day, value in residual.get("curve") or []}
    right = {str(day): float(value) for day, value in sleeve.get("curve") or []}
    days = sorted(set(left) | set(right))
    last_left = residual_book
    last_right = sleeve_book
    equity: list[float] = []
    for day in days:
        if day in left:
            last_left = left[day]
        if day in right:
            last_right = right[day]
        equity.append(last_left + last_right)
    end = equity[-1] if equity else book
    return {
        "pnl_eur": end - book,
        "end_eur": end,
        "max_dd_pct": _dd(equity, book),
    }


def _line(pnl: float, dd: float, trades: int) -> str:
    return f"pnl {pnl:+.0f}  dd {dd * 100:5.1f}%  trades {trades:3d}"


def run(ohlc: dict[str, list[list[float]]], archive: dict[str, Any]) -> dict[str, Any]:
    start = str(archive.get("from_date"))
    end = min(str(archive.get("to_date")), _tape_end(ohlc))
    grouped = index_stories(archive.get("rows") or [])
    universe = {base for base in DEFAULT_UNIVERSE if base in ohlc}
    tape = [bar_date(row) for row in ohlc["BTC"] if start <= bar_date(row) <= end]
    # The last session has no next open, so it cannot start a sleeve trade.
    signal_dates = tape[:-1]
    knobs = dict(LIVE_KNOBS)
    full = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=BOOK_EUR,
        model=WET,
        strategy="residual_full",
        keep_curve=True,
        **knobs,
    )
    residual_book = BOOK_EUR * (1.0 - SLEEVE_FRAC)
    sleeve_book = BOOK_EUR * SLEEVE_FRAC
    core = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=residual_book,
        model=WET,
        strategy="residual_core",
        keep_curve=True,
        **knobs,
    )
    cells: list[dict[str, Any]] = []
    for gate in GATES:
        picks = picks_for(grouped, signal_dates, universe, gate)
        fired = sum(1 for name in picks.values() if name)
        sleeve = run_sleeve(
            ohlc,
            picks,
            start=start,
            end=end,
            book_eur=sleeve_book,
        )
        combined = _combine(
            core,
            sleeve,
            book=BOOK_EUR,
            residual_book=residual_book,
            sleeve_book=sleeve_book,
        )
        names = Counter(name for name in picks.values() if name)
        largest = sleeve.get("largest_trip")
        sleeve_pnl = float(sleeve["pnl_eur"])
        trip_pnl = float(largest["pnl_eur"]) if largest else 0.0
        cells.append(
            {
                "gate": gate,
                "fired_days": fired,
                "days_in_market": sleeve["days_in_market"],
                "sleeve_pnl": sleeve_pnl,
                "sleeve_dd": float(sleeve["max_dd_pct"]),
                "sleeve_trades": int(sleeve["n_trades"]),
                "combined_pnl": combined["pnl_eur"],
                "combined_dd": combined["max_dd_pct"],
                "names": names.most_common(5),
                "largest_base": None if largest is None else largest["base"],
                "largest_pnl": trip_pnl,
                "largest_share": (trip_pnl / sleeve_pnl) if sleeve_pnl > 0 else None,
            }
        )
    return {
        "start": start,
        "end": end,
        "stories": len(archive.get("rows") or []),
        "kept_days": len(grouped),
        "full": full,
        "core": core,
        "cells": cells,
    }


def _markdown(result: dict[str, Any]) -> str:
    full = result["full"]
    full_pnl = float(full["pnl_eur"])
    lines = [
        "# Radar sleeve — archived news proxy",
        "",
        (
            "AlphaI's radar is a live snapshot (`point_in_time` false). "
            "This replay does not call it. The signal is a Poisson tail on "
            f"archived crypto stories, relevance ≥ 6, prior {BASELINE_DAYS} calendar days, "
            f"stories with more than {MAX_STORY_TICKERS} tickers dropped."
        ),
        "",
        (
            f"Window `{result['start']}` → `{result['end']}`, "
            f"{result['stories']} stories, {result['kept_days']} publication days. "
            f"Book €{BOOK_EUR:,.0f}. Sleeve {SLEEVE_FRAC:.0%} (€{BOOK_EUR * SLEEVE_FRAC:,.0f}), "
            "one name, held while it stays selected, wet next open. "
            "The other 85% is the live residual book. Idle sleeve cash earns nothing. "
            "The sleeve does not read the residual rank or the BTC average."
        ),
        "",
        (
            f"Locked cells: `spike` tail < {SPIKE_TAIL} and sentiment delta ≥ 0; "
            f"`hot` z ≥ {HOT_Z:g} and sentiment delta ≥ 0; "
            f"`hot_any` z ≥ {HOT_Z:g}. Nothing is armed. The archive does not reach before 2026."
        ),
        "",
        "## Residual on the same window",
        "",
        (
            f"- full €{BOOK_EUR:,.0f}: "
            f"{_line(full_pnl, float(full['max_dd_pct']), int(full['n_trades']))} "
            f"hold {full.get('end_hold')}"
        ),
        "",
        "## Sleeve plus residual core",
        "",
        (
            "| Cell | Sleeve PnL | Sleeve DD | Trips | Days picked | "
            "Combined PnL | Combined DD | vs full |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in result["cells"]:
        delta = float(cell["combined_pnl"]) - full_pnl
        lines.append(
            "| {gate} | {sleeve:+.0f} | {sdd:.1f}% | {trips} | {fired} | "
            "{combined:+.0f} | {cdd:.1f}% | {delta:+.0f} |".format(
                gate=cell["gate"],
                sleeve=float(cell["sleeve_pnl"]),
                sdd=float(cell["sleeve_dd"]) * 100.0,
                trips=int(cell["sleeve_trades"]),
                fired=int(cell["fired_days"]),
                combined=float(cell["combined_pnl"]),
                cdd=float(cell["combined_dd"]) * 100.0,
                delta=delta,
            )
        )
    lines.extend(["", "## What each cell traded", ""])
    for cell in result["cells"]:
        names = ", ".join(f"{base} {count}" for base, count in cell["names"]) or "none"
        share = cell["largest_share"]
        if share is None:
            share_text = "sleeve finished down"
        else:
            share_text = f"{share * 100:.0f}% of sleeve pnl"
        lines.append(
            f"- `{cell['gate']}`: {names}. Largest trip {cell['largest_base']} "
            f"€{float(cell['largest_pnl']):+.0f} ({share_text})."
        )
    best = max(result["cells"], key=lambda cell: float(cell["combined_pnl"]))
    beat = float(best["combined_pnl"]) > full_pnl
    if beat:
        reading = (
            f"`{best['gate']}` is the highest combined cell and finishes ahead of the full book "
            f"by €{float(best['combined_pnl']) - full_pnl:+.0f}. One window inside 2026 cannot "
            "clear the two-period bar. Nothing is armed."
        )
    else:
        reading = (
            f"The highest combined cell is `{best['gate']}` "
            f"at €{float(best['combined_pnl']):+.0f}, "
            f"against the full book at €{full_pnl:+.0f}. No locked cell beats the residual book "
            "on this overlap. Nothing is armed."
        )
    lines.extend(["", "## Reading", "", reading, ""])
    lines.append("```bash")
    lines.append(".venv/bin/python -m bot.research.radar_sleeve")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wet radar-sleeve proxy")
    parser.add_argument("--cache-dir", default="data/residual_wet_candles")
    parser.add_argument("--archive", default=str(ARCHIVE_PATH))
    parser.add_argument("--out", default="bot/research/radar_sleeve/RESULTS.md")
    parser.add_argument("--no-fresh", action="store_true")
    args = parser.parse_args()
    archive = load_archive(Path(args.archive))
    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    result = run(ohlc, archive)
    text = _markdown(result)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
