"""English write-up. Numbers come from the scan; the screen does not move."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.allocator_lab.profile import (
    CONCENTRATED,
    DISPERSED,
    FORWARD,
    GAP,
    LIGHT_VOL,
    LOOK,
    MIN_ADV,
    MIN_N,
    MIN_NAMES,
    PAIRS,
    PRE_END,
    SHARE_KEYS,
    TICKET_LOOK,
    TICKET_PAIRS,
    VOL_MULT,
    WIN_RET,
    magnet_passes,
    passes,
)

CELL_ORDER = (
    "winner_up_heavy",
    "winner_up_light",
    "winner_down_heavy",
    "winner_down_light",
    "loser_down_heavy",
    "loser_down_light",
    "loser_up_heavy",
    "loser_up_light",
    "flat_up_heavy",
    "flat_up_light",
    "flat_down_heavy",
    "flat_down_light",
)
TICKET_ORDER = (
    "large_buy",
    "small_buy",
    "large_buy_winner",
    "small_buy_winner",
    "large_sell",
    "small_sell",
    "large_sell_loser",
    "small_sell_loser",
)
PAIR_LABELS = {
    "winner_up_heavy": "up day in a winner, heavy versus light volume",
    "loser_down_heavy": "down day in a loser, heavy versus light volume",
    "winner_down_heavy": "down day in a winner, heavy versus light volume",
    "loser_up_heavy": "up day in a loser, heavy versus light volume",
    "large_buy": "large versus small ticket, aggressor buying an up day",
    "large_buy_winner": "large versus small ticket, buying a winner",
    "large_sell": "large versus small ticket, aggressor selling a down day",
    "large_sell_loser": "large versus small ticket, selling a loser",
}


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.1f}%"


def _n(stats: Mapping[str, float | None] | None) -> str:
    if not stats:
        return "0"
    return f"{int(stats.get('n') or 0):,}"


def _cell(stats: Mapping[str, Any] | None, key: str) -> str:
    if not stats or stats.get(key) is None:
        return "—"
    if key == "n":
        return _n(stats)
    return _pct(float(stats[key]))


def _pair_names(cells: Mapping[str, Any], pairs: Sequence[tuple[str, str, str]]) -> list[str]:
    kept: list[str] = []
    for heavy, light, direction in pairs:
        block = cells.get(heavy) or {}
        other = cells.get(light) or {}
        if passes(block.get("pre") or {}, other.get("pre") or {}, direction) and passes(
            block.get("2026") or {}, other.get("2026") or {}, direction
        ):
            kept.append(PAIR_LABELS[heavy])
    return kept


def _book_row(result: Mapping[str, Any]) -> str:
    years = result.get("year_pnl") or {}
    return (
        f"| {result['strategy']} | {result['pnl_eur']:,.0f} | "
        f"{_pct(result.get('max_dd_pct'))} | {_pct(result.get('green_weeks_pct'))} | "
        f"{result['n_trades']} | {_pct(result.get('win_pct'))} | "
        f"{result.get('avg_trade', 0):,.0f} | "
        f"{years.get('2024', 0):,.0f} | {years.get('2025', 0):,.0f} | "
        f"{years.get('2026', 0):,.0f} |"
    )


def _book_table(title: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| book | PnL | DD | green W | trades | win | avg | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in rows:
        lines.append(_book_row(result))
    lines.append("")
    return lines


def _fwd_table(
    title: str,
    cells: Mapping[str, Any],
    order: Sequence[str],
    *,
    level: int = 2,
) -> list[str]:
    lines = [
        f"{'#' * level} {title}",
        "",
        "| cell | n pre | median | euros | %>0 | high | n 2026 | median | euros | %>0 | high |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in order:
        block = cells.get(name) or {}
        pre = block.get("pre") or {}
        year = block.get("2026") or {}
        lines.append(
            f"| {name} | {_cell(pre, 'n')} | {_cell(pre, 'median')} | {_cell(pre, 'euro')} | "
            f"{_cell(pre, 'pos')} | {_cell(pre, 'high')} | {_cell(year, 'n')} | "
            f"{_cell(year, 'median')} | {_cell(year, 'euro')} | {_cell(year, 'pos')} | "
            f"{_cell(year, 'high')} |"
        )
    lines.append("")
    return lines


def _share_table(shares: Mapping[str, Mapping[str, float]]) -> list[str]:
    lines = [
        "## 1. Where the euros print",
        "",
        "Share of quote-volume euros on liquid alt days. A winner is already",
        f"up at least {WIN_RET:.0%} over {LOOK} sessions; a loser is already",
        "down at least that much. The day is up or down against the prior close.",
        "",
        "| bucket | before 2026 | 2026 |",
        "|---|---:|---:|",
    ]
    for key in SHARE_KEYS:
        lines.append(
            f"| {key} | {_pct(shares['pre'].get(key))} | {_pct(shares['2026'].get(key))} |"
        )
    lines.append("")
    return lines


def _crowd_table(crowds: Mapping[str, Any]) -> list[str]:
    lines = [
        "## 4. Euros inside the slow-return leaders",
        "",
        f"Each day with at least {MIN_NAMES} liquid names, the leaders are the",
        "top fifth by 20-session return. Concentration is their euro share",
        "divided by their count share. Concentrated means at least",
        f"{CONCENTRATED:.1f}; dispersed means at most {DISPERSED:.1f}. The",
        "forward column is the median, across days, of that day's median",
        "leader return from the next open to the close ten sessions later.",
        "",
        "| slice | n pre | leader median | leader euros | n 2026 | leader median | leader euros |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("concentrated", "dispersed"):
        block = crowds.get(name) or {}
        pre = block.get("pre") or {}
        year = block.get("2026") or {}
        lines.append(
            f"| {name} | {_cell(pre, 'n')} | {_cell(pre, 'median')} | {_cell(pre, 'euro')} | "
            f"{_cell(year, 'n')} | {_cell(year, 'median')} | {_cell(year, 'euro')} |"
        )
    lines.append("")
    return lines


def _magnet_table(magnets: Mapping[str, Any]) -> list[str]:
    lines = [
        "## 5. The name that took the most euros that day",
        "",
        "`gap` is the median of (that name's 10-session close minus the",
        "median name that day). `euros` weights the same close by the name's",
        "quote volume.",
        "",
        "| magnet was | n pre | median | euros | gap | n 2026 | median | euros | gap |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("any", "winner", "loser", "flat"):
        block = magnets.get(name) or {}
        pre = block.get("pre") or {}
        year = block.get("2026") or {}
        lines.append(
            f"| {name} | {_cell(pre, 'n')} | {_cell(pre, 'median')} | {_cell(pre, 'euro')} | "
            f"{_cell(pre, 'high')} | {_cell(year, 'n')} | {_cell(year, 'median')} | "
            f"{_cell(year, 'euro')} | {_cell(year, 'high')} |"
        )
    lines.append("")
    return lines


def _heavy_note(cells: Mapping[str, Any]) -> str:
    block = cells.get("winner_up_heavy") or {}
    pre = block.get("pre") or {}
    year = block.get("2026") or {}
    return (
        "On an up day in a winner with heavy volume, the median 10-session"
        f" close is {_pct(pre.get('median'))} before 2026 and"
        f" {_pct(year.get('median'))} in 2026. The euro-weighted close is"
        f" {_pct(pre.get('euro'))} and {_pct(year.get('euro'))}."
    )


def _btc_note(fresh: Mapping[str, Any], full: Mapping[str, Any]) -> str:
    share = fresh.get("best_share") or 0.0
    who = str(fresh.get("best_base") or "")
    name = f" in {who}" if who else ""
    if fresh["pnl_eur"] > 0 and full["pnl_eur"] > 0:
        tail = " Both windows are positive."
    else:
        tail = " Both windows have to be positive, so this cell does not pass."
    best = fresh.get("best_trade", 0)
    median = fresh.get("median_trade", 0)
    if share > 1:
        weight = "more than the book's whole profit, so the other trades together lost"
    elif share > 0:
        weight = f"{_pct(share)} of summed trade PnL"
    else:
        weight = "no single profitable trade"
    return (
        f"Fresh 2026 `size_btc` finishes at €{fresh['pnl_eur']:,.0f}"
        f" (drawdown {_pct(fresh.get('max_dd_pct'))}, {fresh['n_trades']} trades,"
        f" median €{median:,.0f}, best €{best:,.0f}{name}, {weight})."
        f" The full path is €{full['pnl_eur']:,.0f}.{tail}"
    )


def _listed(names: Sequence[str]) -> str:
    if not names:
        return "none of them"
    return "; ".join(names)


def _sign_note(cells: Mapping[str, Any], stride: Mapping[str, Any]) -> str:
    agree = 0
    seen = 0
    for heavy, _light, _direction in PAIRS:
        for period in ("pre", "2026"):
            full = ((cells.get(heavy) or {}).get(period) or {}).get("median")
            thin = ((stride.get(heavy) or {}).get(period) or {}).get("median")
            if full is None or thin is None:
                continue
            seen += 1
            if (full > 0) == (thin > 0):
                agree += 1
    if seen == 0:
        return "The every-10th-bar sample had no heavy cell to compare."
    return (
        f"On the heavy cells, the every-10th-bar median has the same sign as "
        f"the full sample in {agree} of {seen} period-cells."
    )


def render(
    *,
    asof: str,
    n_markets: int,
    n_prints: int,
    flow_bases: int,
    flow_asof: str,
    shares: Mapping[str, Mapping[str, float]],
    cells: Mapping[str, Any],
    stride: Mapping[str, Any],
    crowds: Mapping[str, Any],
    magnets: Mapping[str, Any],
    tickets: Mapping[str, Any] | None,
    books_full: Sequence[Mapping[str, Any]],
    books_fresh: Sequence[Mapping[str, Any]],
) -> str:
    volume_hits = _pair_names(cells, PAIRS)
    ticket_hits = _pair_names(tickets or {}, TICKET_PAIRS)
    crowd_hit = passes(
        (crowds.get("concentrated") or {}).get("pre") or {},
        (crowds.get("dispersed") or {}).get("pre") or {},
        "up",
    ) and passes(
        (crowds.get("concentrated") or {}).get("2026") or {},
        (crowds.get("dispersed") or {}).get("2026") or {},
        "up",
    )
    magnet_hit = magnet_passes((magnets.get("winner") or {}).get("pre") or {}) and magnet_passes(
        (magnets.get("winner") or {}).get("2026") or {}
    )
    by_name = {row["strategy"]: row for row in books_full}
    fresh = {row["strategy"]: row for row in books_fresh}
    size_beats = (
        by_name["size"]["pnl_eur"] > by_name["price"]["pnl_eur"]
        and by_name["size"]["pnl_eur"] > 0
        and fresh["size"]["pnl_eur"] > fresh["price"]["pnl_eur"]
        and fresh["size"]["pnl_eur"] > 0
    )
    size_btc_beats = (
        by_name["size_btc"]["pnl_eur"] > by_name["price_btc"]["pnl_eur"]
        and by_name["size_btc"]["pnl_eur"] > 0
        and fresh["size_btc"]["pnl_eur"] > fresh["price_btc"]["pnl_eur"]
        and fresh["size_btc"]["pnl_eur"] > 0
    )
    lines = [
        "# Allocator footprint",
        "",
        "Question: the people who put in the most capital set the price, and",
        "they want return. Does their footprint — euros in names that are",
        "already working, and a larger average print — have a pattern the",
        "next ten sessions keep, after a Bitvavo fill?",
        "",
        "This does not re-run the crowd-bot presets, the taker-ratio gates,",
        "or the 3-day high-rise chase. Those are already measured. The new",
        "axes are where the euros go, and how large the average print is.",
        "",
        f"Tape: Bitvavo EUR daily bars, {n_markets} markets, through {asof}.",
        f"Liquid means the prior {LOOK} sessions average at least €{MIN_ADV:,.0f}",
        "of quote volume, so the day being judged cannot qualify the name.",
        f"{n_prints:,} liquid alt days. BTC is the regime, not a candidate.",
        f"Forward return buys the next open and sells the close {FORWARD}",
        "sessions later. `euros` weights that return by the signal day's",
        f"quote volume. Periods split at {PRE_END}.",
        "",
        "Screen, fixed before the run. A volume or ticket pair passes only",
        "when both periods have at least "
        f"{MIN_N} events, the heavy side's median and its euro-weighted",
        "return have the predicted sign, and the heavy side beats the light",
        f"side by at least {GAP:.0%} on the median (and is at least as good",
        "on the euros). Predicted sign is positive when the footprint is",
        "buying, negative when it is selling. Overlapping ten-session",
        "windows are not independent; the one-slot book below is the costed",
        "check. Nothing in that screen is refit after the table.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.allocator_lab`",
        "",
    ]
    lines += _share_table(shares)
    lines += _fwd_table(
        "2. Ten sessions after the volume footprint",
        cells,
        CELL_ORDER,
    )
    lines += _fwd_table(
        "3. Same cells, every 10th bar of each name",
        stride,
        [name for name in CELL_ORDER if "heavy" in name or name.endswith("_light")],
    )
    lines += _crowd_table(crowds)
    lines += _magnet_table(magnets)
    if tickets is None:
        lines += [
            "## 6. Average print size",
            "",
            "No Binance trade-count cache was on disk, so this section is empty.",
            "",
        ]
    else:
        lines += [
            "## 6. Average print size",
            "",
            f"Binance `<BASE>USDT` daily klines for {flow_bases} bases already",
            f"cached by the entry-quality lab, through {flow_asof or '—'}.",
            "Bitvavo does not publish trade counts. Average print is quote",
            f"volume divided by trade count. Large is at or above the {80:.0f}th",
            f"percentile of the prior {TICKET_LOOK} days of that same name;",
            "small is at or below the 20th. A buy also needs the Binance",
            "taker-buy share at least 0.52 and a Bitvavo up close; a sell",
            "needs 0.48 or less and a Bitvavo down close. The outcome is the",
            "Bitvavo forward return, not the Binance close. The cache is the",
            "liquid desk, which is where a large print can exist. It is not",
            "a hand-picked list of winners.",
            "",
        ]
        lines += _fwd_table("Ticket cells", tickets, TICKET_ORDER, level=3)
    lines += [
        "## 7. Wet one-slot books",
        "",
        "€20,000 compounded, one name. Both rules require a liquid alt that",
        f"is already up at least {WIN_RET:.0%} over {LOOK} sessions and that",
        "closed up on the day. `price` buys the strongest of those.",
        f"`size` keeps only names whose quote volume is at least {VOL_MULT:.0f}×",
        f"the prior {LOOK}-day median (today excluded from that median) and",
        "then buys the one with the most euros. `*_btc` adds a gate that",
        "BTC's close is above its 50-session average. Fill is the next open,",
        "Bitvavo taker 0.25% a side plus impact `0.02 × sqrt(notional / 20-day",
        "average quote volume)`, capped at 3%. Exit is a 10% trail from the",
        "prior peak, or the next open after 10 bars in the trade, whichever",
        "comes first. A gap through the trail fills at the open. `Fresh 2026`",
        "restarts at €20,000 on 1 January 2026.",
        "",
        "A size book passes only when its euro PnL is positive and higher",
        "than the matching price book, on the full path and on the fresh",
        "2026 book. Light volume is a quote-volume ratio under "
        f"{LIGHT_VOL:.0f}; the book itself uses the heavy gate only.",
        "",
    ]
    lines += _book_table("Full path", books_full)
    lines += _book_table("Fresh 2026", books_fresh)
    pre_up = shares["pre"].get("winner_up", 0.0)
    y_up = shares["2026"].get("winner_up", 0.0)
    pre_dn = shares["pre"].get("loser_down", 0.0)
    y_dn = shares["2026"].get("loser_down", 0.0)
    sits = pre_up > pre_dn and y_up > y_dn
    profile = (
        f"Before 2026, {_pct(pre_up)} of liquid-alt euros printed on an up day"
        f" in a name already up at least 10% over 20 sessions, against"
        f" {_pct(pre_dn)} on a down day in a name already down 10%. In 2026"
        f" those shares are {_pct(y_up)} and {_pct(y_dn)}."
    )
    size = fresh["size"]
    price = fresh["price"]
    if sits:
        lead = "The euros sit in names that are already ahead. "
    else:
        lead = "The euro share does not favor winners on both sides of 2026. "
    lines += [
        "## Reading",
        "",
        "1. " + lead + profile,
        (
            f"2. Volume screen, both periods: {_listed(volume_hits)}. "
            f"{_sign_note(cells, stride)} {_heavy_note(cells)}"
        ),
        (
            "3. Leader-concentration screen, both periods: "
            + ("passes." if crowd_hit else "does not pass.")
            + " Magnet screen (most euros went to a winner, both periods): "
            + ("passes." if magnet_hit else "does not pass.")
        ),
        (
            "4. Ticket-size screen, both periods: "
            f"{_listed(ticket_hits)}. The average Binance print is the"
            " public proxy for ticket size. It is not a wallet, and a"
            " 'large' print is large only versus that name's own last"
            f" {TICKET_LOOK} days."
        ),
        (
            f"5. Fresh 2026, `size` PnL €{size['pnl_eur']:,.0f}"
            f" ({size['n_trades']} trades, average €{size.get('avg_trade', 0):,.0f},"
            f" drawdown {_pct(size.get('max_dd_pct'))}) against `price`"
            f" €{price['pnl_eur']:,.0f} ({price['n_trades']} trades, average"
            f" €{price.get('avg_trade', 0):,.0f}). Full path `size`"
            f" €{by_name['size']['pnl_eur']:,.0f} against `price`"
            f" €{by_name['price']['pnl_eur']:,.0f}. Size beats price with a"
            f" positive book on both windows: {'yes' if size_beats else 'no'}."
            f" The BTC-gated pair: {'yes' if size_btc_beats else 'no'}."
            f" {_btc_note(fresh['size_btc'], by_name['size_btc'])}"
        ),
        "",
        "## Verdict",
        "",
    ]
    if size_beats or size_btc_beats:
        lines.append(
            "The pre-specified size book beat the price-only book after"
            " Bitvavo costs on both windows. This commit does not wire it"
            " into the live desk; the live book stays the residual pack."
            " The screen results above say which footprints earned that,"
            " and which did not."
        )
    else:
        lines.append(
            "Nothing armed. "
            + profile
            + " That allocation is the profile of the capital that prints"
            " the volume. It is not a fill. Requiring abnormal volume, or"
            " buying the single name that absorbed the most euros, does not"
            " beat simply buying the slow winner, once the next open, the"
            " taker, and impact are paid, on the full path and on a fresh"
            " 2026 book. A larger average print, where the trade-count cache"
            " exists, is held to the same screen and does not open a"
            " separate book. The slow residual pack remains the book that"
            " compounds; this footprint does not replace it. "
            + _btc_note(fresh["size_btc"], by_name["size_btc"])
        )
    lines.append("")
    return "\n".join(lines) + "\n"
