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
        "beta_rank": "Rank by excess after the alt's own BTC beta",
        "ir_rank": "Rank that excess per unit of residual volatility",
        "rearm": "After a green exit, open the next leader before the week is up",
        "btc_tide": "Open a new ride only while BTC's own window is up",
        "green_pause": "After a green exit, wait longer than a week",
        "btc_veto": "Cash on a BTC SMA break only after a deeper gap",
        "rank_slot": "Buy a cooler qualifier than the residual leader",
        "red_cut": "Sell a position that is still red after a few days",
        "red_leader": "Sell a red position only after it loses the lead",
        "gap_cap": "Cancel a buy when the next open gaps up",
        "lead_exit": "Sell when the held name loses the residual lead",
        "keep_ride": "Keep a held name when BTC closes under its average",
        "agree": "Require the longer residual to be positive too",
        "loss_ban": "Skip a name after a sell that closed under the entry",
        "vol_expand": "Buy only a name whose own volume has expanded",
        "fresh_cross": "Buy only a residual that just cleared the floor",
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
    lines.append(
        "Changing only who is ranked first does not clear it either. The live "
        "excess treats every alt as if its BTC beta were 1. Estimating that beta "
        "on 20, 40 or 60 daily log returns leaves the fresh 2026 book on the same "
        "euros and cuts the compounded path. Dividing the live score by the "
        "volatility of the alt-minus-BTC residual cuts the compounded path as well. "
        "The 40-day window is a few hundred euros ahead on a fresh 2026 book and "
        "about €210k behind over the full path."
    )
    lines.append("")
    lines.append(
        "Opening the next leader sooner after a green exit loses. Waiting 14, "
        "21 or 28 days after that same green exit also loses. A losing exit "
        "already keeps the week; lengthening the wait only after a winner gives "
        "the next ride away. Refusing a new ride unless BTC's own 10-day return "
        "is positive takes the fresh 2026 book to about zero."
    )
    lines.append("")
    lines.append(
        "Re-picking the sleeve each day from the most-traded names on the whole "
        "venue, 12, 16 or 24 of them, finishes between a small gain and a loss. "
        "The fixed desk on that same cache is the book above."
    )
    lines.append("")
    lines.append(
        "Letting a qualifying alt stay on when BTC is 5% or 10% under its SMA50, "
        "or ignoring that average, raises the full-path drawdown to 56–75% and "
        "gives the euros back. Buying the second score, the third, or the "
        "qualifier closest to the floor loses the stake or finishes a few "
        "thousand euros down. The leader, and the cash rule on a BTC close under "
        "its average, stay in the book."
    )
    lines.append("")
    lines.append(
        "Selling any position that is still under its entry after two days is "
        "ahead of the fresh-2026 size bar and misses the rest of it. The "
        "compounded path and the book through 2025 both give way. Four days is "
        "a little ahead through 2025 and a little behind on the full path, and "
        "it gives back fresh 2026. Six days leaves fresh 2026 unchanged. The "
        "day count stays where it was locked."
    )
    lines.append("")
    lines.append(
        "Keeping that two-day cut only when the name has also lost the lead "
        "is the closest cell: full path +€417,544 versus +€342,074, fresh 2026 "
        "+€40,807 versus +€30,791, and the book through 2025 a few thousand "
        "euros behind. That is short of 1.25× and of 1.5×, it is one cell, and "
        "four and six days do not come with it. Cancelling a buy when the next "
        "open gaps 3, 5 or 8 percent does not change a fill on this tape."
    )
    lines.append("")
    lines.append(
        "Selling as soon as the held name is no longer the top qualifier, "
        "with or without a red close, or as soon as its excess falls through "
        "the floor, gives the compounded path back. The two-day wait is what "
        "kept that closer cell. Dropping the wait is not a find."
    )
    lines.append("")
    lines.append(
        "Keeping a name already held when BTC closes under its average does "
        "not change a fill on this tape. Requiring the 40-day excess to be "
        "positive as well is a little ahead on both windows, about €8k on the "
        "full path and a few hundred euros on a fresh 2026 book, and it misses "
        "the size bar. Twenty and sixty days do not come with it. Skipping a "
        "name for 14 days after a losing sell changes nothing. Twenty-eight "
        "and fifty-six days cost about €1,200."
    )
    lines.append("")
    lines.append(
        "Requiring the name's own quote volume to have expanded cuts the path. "
        "At one times the prior twenty days the full path is +€186,857 and a "
        "fresh 2026 book is +€28,182. At 1.5× and 2× the book is a small gain "
        "with a much deeper drawdown. Buying only a residual that cleared the "
        "floor in the last one, two or three days loses the stake. The name "
        "that has already been above the floor is the book."
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
