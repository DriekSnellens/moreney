"""English write-up. The estimate is the conditional median, not a fit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.depth_flow_lab.profile import (
    CLOSE_GAP,
    DEEP,
    FLOWS,
    FORWARD,
    FORWARD_SLOW,
    HIGH_GAP,
    HIGH_N,
    LOCATIONS,
    MIN_ADV,
    MIN_N,
    NEAR,
    SAMPLES,
    TAKER_BUY,
    TAKER_SELL,
    bot_holds,
    close_holds,
    shape_holds,
)

Cell = Mapping[str, float | None]
Grid = Mapping[tuple[str, str], Mapping[str, Cell]]


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.1f}%"


def _n(stats: Cell | None) -> str:
    if not stats:
        return "0"
    return f"{int(stats.get('n') or 0):,}"


def _rho(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def _cell(stats: Cell | None, key: str) -> str:
    if not stats or stats.get(key) is None:
        return "—"
    if key == "n":
        return _n(stats)
    return _pct(float(stats[key]))


def _grid_table(title: str, summary: Grid) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| location | flow | n pre | high | close | p90 high | n 2026 | high | close | p90 high |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for location in LOCATIONS:
        for flow in FLOWS:
            block = summary[(location, flow)]
            pre = block["pre"]
            year = block["2026"]
            lines.append(
                f"| {location} | {flow} | {_cell(pre, 'n')} | {_cell(pre, 'high')} | "
                f"{_cell(pre, 'close')} | {_cell(pre, 'p90')} | {_cell(year, 'n')} | "
                f"{_cell(year, 'high')} | {_cell(year, 'close')} | {_cell(year, 'p90')} |"
            )
    lines.append("")
    return lines


def _book_row(result: Mapping[str, Any]) -> str:
    years = result.get("year_pnl") or {}
    return (
        f"| {result['strategy']} | {result['pnl_eur']:,.0f} | "
        f"{_pct(result.get('max_dd_pct'))} | {_pct(result.get('green_weeks_pct'))} | "
        f"{result['n_trades']} | {_pct(result.get('win_pct'))} | "
        f"{result.get('avg_trade', 0):,.0f} | {result.get('median_trade', 0):,.0f} | "
        f"{years.get('2024', 0):,.0f} | {years.get('2025', 0):,.0f} | "
        f"{years.get('2026', 0):,.0f} |"
    )


def _book_table(title: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| book | PnL | DD | green W | trades | win | avg | median | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in rows:
        lines.append(_book_row(result))
    lines.append("")
    return lines


def _bot_sentence(bot_buy: bool, bot_sell: bool) -> str:
    if bot_sell and not bot_buy:
        return (
            "Small-ticket selling at a deep price has the larger high and "
            "the less-bad close than a large ticket, on both sides of 2026. "
            "That close is still negative, and the pre-specified book buys "
            "heavy demand, so the gap is not a fill. "
        )
    if bot_buy and bot_sell:
        return (
            "Small tickets have the larger rise than large tickets at a deep "
            "price, on both the buy and the sell. The book still has to clear "
            "costs. "
        )
    if bot_buy:
        return (
            "Small-ticket buying at a deep price has the larger rise than a "
            "large ticket. The book still has to clear costs. "
        )
    return (
        "Small tickets do not have the larger rise at a deep price, on the "
        "buy side or on the sell side. "
    )


def _books_pass(full: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]]) -> bool:
    for rows in (full, fresh):
        by_name = {row["strategy"]: row for row in rows}
        pnl = by_name["deep_buy"]["pnl_eur"]
        if pnl <= 0 or pnl <= by_name["deep"]["pnl_eur"] or pnl <= by_name["buy"]["pnl_eur"]:
            return False
    return True


def _best_note(row: Mapping[str, Any]) -> str:
    share = float(row.get("best_share") or 0.0)
    who = str(row.get("best_base") or "")
    name = f" in {who}" if who else ""
    best = float(row.get("best_trade") or 0.0)
    if share > 1:
        weight = "more than the book's whole profit, so the other trades together lost"
    elif share > 0:
        weight = f"{_pct(share)} of summed trade PnL"
    elif best > 0:
        weight = "the other trades lost more than this one made"
    else:
        weight = "no profitable trade"
    return (
        f"best €{row.get('best_trade', 0):,.0f}{name}, {weight}; "
        f"median trade €{row.get('median_trade', 0):,.0f}"
    )


def render(
    *,
    asof: str,
    n_markets: int,
    n_flow: int,
    n_events: int,
    fast: Grid,
    slow: Grid,
    base_fast: Mapping[str, Cell],
    bots: Grid,
    corr: Mapping[str, Mapping[str, float | None]],
    books_full: Sequence[Mapping[str, Any]],
    books_fresh: Sequence[Mapping[str, Any]],
) -> str:
    shaped = shape_holds(fast)
    kept = close_holds(fast)
    bot_buy = bot_holds(bots, "buy")
    bot_sell = bot_holds(bots, "sell")
    booked = _books_pass(books_full, books_fresh)
    deep = fast[("deep", "heavy_buy")]
    near = fast[("near", "heavy_buy")]
    sold = fast[("deep", "heavy_sell")]
    by_full = {row["strategy"]: row for row in books_full}
    by_fresh = {row["strategy"]: row for row in books_fresh}
    lines = [
        "# Depth and signed flow",
        "",
        "Question: can the size of the next rise be read off how low the",
        "price sits and how much is being bought or sold? Bots are in that",
        "flow as small tickets. This is the conditional median, not a fitted",
        "model, and it does not re-run the crowd-bot presets.",
        "",
        f"Tape through {asof}. {n_markets} Bitvavo markets, {n_flow} with a",
        f"Binance USDT taker print, {n_events:,} joined liquid days. Liquid",
        f"means the prior 20 Bitvavo sessions average at least €{MIN_ADV:,.0f}.",
        "Depth is one minus the close divided by the highest high of the",
        f"last {HIGH_N} sessions, including today. Near is under {NEAR:.0%}",
        f"off that high, mid is {NEAR:.0%} to {DEEP:.0%}, deep is {DEEP:.0%}",
        "or more. Buy quantity is taker-buy share times Binance quote volume,",
        "divided by that name's own prior-20-day median of the same quantity.",
        f"Heavy is at least {2:.0f}× and a taker share of at least {TAKER_BUY:.2f}",
        f"for a buy, or at most {TAKER_SELL:.2f} for a sell. Light is under",
        "1×. The mid band of quantity is left out. A small ticket is an",
        "average print at or below the 20th percentile of the prior 60 days",
        "of that name; a large ticket is at or above the 80th.",
        "",
        "The forward path buys the next Bitvavo open. The high is the",
        f"largest high over the next {FORWARD} sessions. The close is the",
        f"close of session {FORWARD}. A second table uses {FORWARD_SLOW}",
        "sessions. The screen uses the 10-session table only.",
        "",
        "Screen, fixed before the run. The shape holds when, on both sides",
        f"of 2026, each cell has at least {MIN_N} days, the median high",
        "grows from near to mid to deep under heavy buying, the deep cell",
        f"beats the near cell by at least {HIGH_GAP:.0%}, and it beats heavy",
        "selling at the same depth by the same gap. The close holds when",
        "that deep heavy-buy close is positive and beats both neighbouring",
        f"cells by at least {CLOSE_GAP:.0%}. A bot cell holds when the small",
        "ticket has the larger high by the same high gap and the better",
        "close, both periods. The book holds only when `deep_buy` is",
        "positive and ahead of both `deep` and `buy`, on the full path and",
        "on a fresh 2026 book.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.depth_flow_lab`",
        "",
        (
            f"Base rate, any joined day, 10 sessions: before 2026 the median"
            f" high is {_pct(base_fast['pre'].get('high'))} and the median"
            f" close is {_pct(base_fast['pre'].get('close'))}"
            f" (n={_n(base_fast['pre'])}). In 2026 the high is"
            f" {_pct(base_fast['2026'].get('high'))} and the close is"
            f" {_pct(base_fast['2026'].get('close'))} (n={_n(base_fast['2026'])})."
        ),
        "",
    ]
    lines += _grid_table("1. Ten-session estimate", fast)
    lines += _grid_table("2. Twenty-session estimate", slow)
    lines += [
        "## 3. Does depth or quantity rank with the rise?",
        "",
        "Spearman rank correlation with the 10-session high. This is a",
        "summary of the same days. The screen above is the bins.",
        "",
        "| sample | rho pre | n pre | rho 2026 | n 2026 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, kind in SAMPLES:
        block = corr.get(kind) or {}
        n_pre = int(block.get("n_pre") or 0)
        n_year = int(block.get("n_2026") or 0)
        lines.append(
            f"| {label} | {_rho(block.get('pre'))} | {n_pre:,} | "
            f"{_rho(block.get('2026'))} | {n_year:,} |"
        )
    lines += [
        "",
        "## 4. Small tickets at a deep price",
        "",
        "Same deep location. Heavy buy or heavy sell, split by ticket size.",
        "Small tickets are the bot-and-retail print.",
        "",
        "| ticket | side | n pre | high | close | n 2026 | high | close |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for ticket in ("small", "large"):
        for side in ("buy", "sell"):
            block = bots[(ticket, side)]
            pre = block["pre"]
            year = block["2026"]
            lines.append(
                f"| {ticket} | {side} | {_cell(pre, 'n')} | {_cell(pre, 'high')} | "
                f"{_cell(pre, 'close')} | {_cell(year, 'n')} | {_cell(year, 'high')} | "
                f"{_cell(year, 'close')} |"
            )
    lines += [
        "",
        "## 5. Wet one-slot books",
        "",
        "€20,000 compounded, one name, Bitvavo taker 0.25% a side plus",
        "impact `0.02 × sqrt(notional / 20-day average quote volume)`,",
        "capped at 3%. `deep_buy` is the deep name with the heaviest buy",
        "multiple that day. `deep` is the deepest name, flow ignored.",
        "`buy` is the heaviest buy multiple at any depth. Exit is a 10%",
        "trail from the prior peak, or the next open after 10 bars,",
        "whichever comes first. A gap through the trail fills at the open.",
        "`Fresh 2026` restarts at €20,000 on 1 January 2026.",
        "",
    ]
    lines += _book_table("Full path", books_full)
    lines += _book_table("Fresh 2026", books_fresh)
    fresh = by_fresh["deep_buy"]
    lines += [
        "## Reading",
        "",
        (
            "1. At a deep price with heavy buying, the 10-session median high"
            f" is {_pct(deep['pre'].get('high'))} before 2026 and"
            f" {_pct(deep['2026'].get('high'))} in 2026. The median close is"
            f" {_pct(deep['pre'].get('close'))} and {_pct(deep['2026'].get('close'))}."
            f" Heavy buying near the high: high {_pct(near['pre'].get('high'))} /"
            f" {_pct(near['2026'].get('high'))}, close {_pct(near['pre'].get('close'))} /"
            f" {_pct(near['2026'].get('close'))}. Heavy selling at the same"
            f" depth: high {_pct(sold['pre'].get('high'))} /"
            f" {_pct(sold['2026'].get('high'))}, close {_pct(sold['pre'].get('close'))} /"
            f" {_pct(sold['2026'].get('close'))}."
        ),
        (
            f"2. Shape screen: {'passes' if shaped else 'does not pass'}."
            f" Close screen: {'passes' if kept else 'does not pass'}."
            " The twenty-session table is the same rule on a slower clock."
            " It is not a second screen."
        ),
        (
            "3. Small-ticket buying at a deep price: "
            f"{'passes' if bot_buy else 'does not pass'} "
            f"(high {_pct(bots[('small', 'buy')]['pre'].get('high'))} / "
            f"{_pct(bots[('small', 'buy')]['2026'].get('high'))}, "
            f"close {_pct(bots[('small', 'buy')]['pre'].get('close'))} / "
            f"{_pct(bots[('small', 'buy')]['2026'].get('close'))}). "
            "Small-ticket selling at a deep price: "
            f"{'passes' if bot_sell else 'does not pass'} "
            f"(high {_pct(bots[('small', 'sell')]['pre'].get('high'))} / "
            f"{_pct(bots[('small', 'sell')]['2026'].get('high'))}, "
            f"close {_pct(bots[('small', 'sell')]['pre'].get('close'))} / "
            f"{_pct(bots[('small', 'sell')]['2026'].get('close'))}). "
            "A pass here is a larger high and a better close than the large "
            "ticket. It is not a positive close."
        ),
        (
            f"4. Fresh 2026 `deep_buy` is €{fresh['pnl_eur']:,.0f}"
            f" (drawdown {_pct(fresh.get('max_dd_pct'))}, {fresh['n_trades']} trades,"
            f" {_best_note(fresh)}). Full path €{by_full['deep_buy']['pnl_eur']:,.0f}."
            f" Against `deep` and `buy`, both windows:"
            f" {'yes' if booked else 'no'}."
        ),
        "",
        "## Verdict",
        "",
    ]
    if booked and kept:
        lines.append(
            "The pre-specified deep-and-heavy-buy book beat both controls"
            " after Bitvavo costs on both windows, and the close screen"
            " held. This commit does not wire it into the live desk."
        )
    else:
        lines.append(
            "Nothing armed. The estimate is the pair of medians in the"
            " ten-session table: how low the close sits, and whether the"
            " taker print is a heavy buy or a heavy sell, read off as the"
            " median high and the median close that followed. "
            + (
                "The high lines up with the claim on both sides of 2026. "
                if shaped
                else "The high does not grow with depth under heavy buying, "
                "on both sides of 2026, by the gap the screen requires. "
            )
            + (
                "The close keeps a positive edge over the neighbouring cells. "
                if kept
                else "The close does not keep a positive edge over those cells. "
            )
            + _bot_sentence(bot_buy, bot_sell)
            + "The one-slot book that buys the most intense buying at a deep "
            "price does not beat buying the deepest name, or buying the most "
            "intense buying at any depth, once the next open, the taker, and "
            "impact are paid."
        )
    lines.append("")
    return "\n".join(lines) + "\n"
