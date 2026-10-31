"""Render the search. The verdict follows ``bar.judge_family`` only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.better_book.bar import (
    DD_SLACK,
    FRESH_MULT,
    FULL_MULT,
    ONE_TRADE_SHARE,
    FamilyVerdict,
)


def _eur(n: float) -> str:
    sign = "+" if n > 0 else ""
    return f"{sign}{n:,.0f}"


def _row(cell: Mapping[str, Any]) -> str:
    full = cell["full"]
    is_ = cell["is"]
    oos = cell["oos"]
    return (
        f"| {cell['name']} | {_eur(full['pnl_eur'])} | {full['max_dd_pct']:.1%} | "
        f"{_eur(is_['pnl_eur'])} | {_eur(oos['pnl_eur'])} | {oos['max_dd_pct']:.1%} | "
        f"{full['n_trades']} |"
    )


def render(
    payload: Mapping[str, Any],
    verdicts: Sequence[FamilyVerdict],
    *,
    asof: str,
    start: str,
) -> str:
    base = payload["baseline"]
    lines = [
        "# Better than the live residual pack",
        "",
        f"asof `{asof}`. Wet Bitvavo next-open, book €20,000, desk 16, {start} → {asof}.",
        "Baseline is the armed pack (10d skip-1, floor 3.5%, own SMA50, cash when none",
        "qualify, 7-day clock, near-high 0.25, trail 10%, lock +12%/+6%). IS runs that",
        "book through 2025-12-31. OOS is a fresh €20k book from 2026-01-01.",
        "",
        "The bar was locked before this run:",
        "",
        f"- fresh 2026 PnL at least {FRESH_MULT:.2f}× the baseline, and positive",
        f"- full-path PnL at least {FULL_MULT:.2f}× the baseline",
        "- IS PnL at least the baseline",
        f"- full-path max drawdown at most {DD_SLACK:.0%} worse",
        "- at least half the cells in that family, and at least two, beat the",
        "  baseline on both IS and fresh 2026",
        f"- less than {ONE_TRADE_SHARE:.0%} of the full-path euro gain comes from one",
        "  round trip the baseline did not also book",
        "",
        "A family that misses any line is not a find. Nothing here is wired into the live clip.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.better_book`",
        "(`--no-fresh` skips the Bitvavo merge and replays the local cache only.)",
        "",
        "## Baseline",
        "",
        "| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _row(base),
        "",
    ]
    titles = {
        "scale_in": "Scale in after the starter proves itself",
        "runner": "Widen the trail only after a large peak",
        "weekday_trail": "Price trail on one weekday, lock every day",
        "trail_confirm": "Same 10% trail, but one close is not enough",
        "trail_touch": "Sell at the level when the low trades it",
    }
    by_name = {v.name: v for v in verdicts}
    for family, cells in payload["families"].items():
        verdict = by_name[family]
        lines.append(f"## {titles.get(family, family)}")
        lines.append("")
        lines.append("| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for cell in cells:
            lines.append(_row(cell))
        lines.append("")
        if verdict.cleared:
            names = ", ".join(f"`{n}`" for n in verdict.cleared)
            lines.append(f"Clears the bar: {names}.")
        else:
            why = []
            if not verdict.region:
                why.append("the cells do not form a region above the baseline on both windows")
            if not verdict.margin_names:
                why.append("no cell reaches the size bar")
            elif not verdict.cleared:
                why.append("the cells that reach the size bar fail the one-trip test or the region")
            lines.append("Does not clear the bar: " + "; ".join(why) + ".")
        lines.append("")
    lines.append("## Reading")
    lines.append("")
    lines.append(
        "Full size on the leader is the book. A starter of 40–60% that adds only "
        "after the close is through an arm keeps about a quarter to three-fifths "
        "of the full path, with a lower drawdown that does not pay for the lost euros."
    )
    lines.append("")
    lines.append(
        "The 10% trail has to fire on the first close through the level, and it "
        "has to sell the whole lot. Widening it after a large peak never reaches "
        "the size bar; the closest cells are the ones that almost never widen. "
        "Checking that trail on only one weekday, waiting for a second or third "
        "close, or banking half on the first pierce, all give the compounded path "
        "back. Selling when the low trades the prior-peak level is earlier than "
        "the close and does the same: the wick is not a fill you want."
    )
    lines.append("")
    lines.append(
        "The bar was not loosened after these numbers. A later family has to "
        "leave the entry size and this exit alone."
    )
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    found = [v for v in verdicts if v.cleared]
    if not found:
        lines.append(
            "Nothing cleared the pre-registered bar. The live residual pack stays the book. "
            "Nothing is armed."
        )
    else:
        lines.append(
            "These cells cleared the bar on this tape: "
            + ", ".join(f"`{n}`" for v in found for n in v.cleared)
            + ". They are not switched on. A find here is a research result, not a live change."
        )
    lines.append("")
    return "\n".join(lines)
