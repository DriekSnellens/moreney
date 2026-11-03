"""Locked bid and offer grid for the dip. Nothing here is armed.

Bids are 0%, 1%, 2%, and 4% above the printed low. Offers are 3%, 5%,
8%, and 12% above the fill. The 1% / 5% pair is the control. The grid
was locked before this replay. Reproduce with
``python -m bot.research.sentiment_dip.percents``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.sentiment_dip.gate import (
    TONE_DAYS,
    Run,
    Tone,
    _shift,
    load_headlines,
    simulate,
    tones_by_day,
)
from bot.research.swing_grid.bars import load_cache
from bot.research.swing_grid.grid import BOOK_EUR, BOUNCE_5, CUSHION, sessions_from_rows
from bot.research.swing_grid.grid import simulate as swing_simulate

OUT = Path(__file__).with_name("PERCENTS.md")
ARCHIVE = Path("data/research/alphai_crypto_news.json")
CACHE = Path("data/research/swing_grid/15m")
BIDS = (0.00, 0.01, 0.02, 0.04)
OFFERS = (0.03, 0.05, 0.08, 0.12)
OVERLAP_MODES = ("tone", "block", "plain")
TAPE_START = "2024-01-01"
PRE_END = "2025-12-31"
YEAR = "2026-01-01"
PUBLISHED_BOUNCE = {
    "full": -19_468,
    "before 2026": -17_551,
    "2026": -16_378,
}
PUBLISHED_OVERLAP = {"tone": -2_568, "block": 485, "plain": -1_649}


def _eur(value: float) -> str:
    rounded = int(round(value))
    if rounded > 0:
        return f"+€{rounded:,.0f}"
    if rounded < 0:
        return f"-€{abs(rounded):,.0f}"
    return "€0"


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _dist(value: float) -> str:
    return f"{100.0 * value:.0f}%"


def _last_complete_day() -> str:
    return (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


def _windows(last: str) -> tuple[tuple[str, str, str], ...]:
    return (
        ("full", TAPE_START, last),
        ("before 2026", TAPE_START, PRE_END),
        ("2026", YEAR, last),
    )


def _clears(full: Run, pre: Run, year: Run) -> bool:
    """Same bar as the swing lab. Both periods are required."""
    one_day = full.largest_day <= 0 or full.largest_day < 0.5 * full.pnl
    drawdown = full.max_dd < (full.pnl / BOOK_EUR) if full.pnl > 0 else False
    return (
        full.pnl > 0
        and pre.pnl > 0
        and year.pnl > 0
        and full.maker_exits >= 40
        and one_day
        and drawdown
    )


def _check_control(
    books: dict[str, list],
    grouped: dict[str, list],
    tones: dict[str, Tone],
    last: str,
    overlap_start: str,
) -> None:
    """The 1% / 5% path has to match the replays already published."""
    for label, start, end in _windows(last):
        priced = simulate(
            books,
            {},
            universe=DEFAULT_UNIVERSE,
            mode="plain",
            start=start,
            end=end,
            cushion=CUSHION,
            target=BOUNCE_5,
        )
        locked = swing_simulate(
            books,
            universe=DEFAULT_UNIVERSE,
            start=start,
            end=end,
            mode="bounce5",
        )
        if priced.pnl != locked.pnl or priced.maker_exits != locked.maker_exits:
            raise RuntimeError(f"1%/5% drifted from bounce5 on {label}")
        if int(round(priced.pnl)) != PUBLISHED_BOUNCE[label]:
            raise RuntimeError(f"1%/5% no longer matches the published {label} bounce")
    for mode in OVERLAP_MODES:
        run = simulate(
            books,
            grouped,
            universe=DEFAULT_UNIVERSE,
            mode=mode,
            start=overlap_start,
            end=last,
            tones=None if mode == "plain" else tones,
        )
        if int(round(run.pnl)) != PUBLISHED_OVERLAP[mode]:
            raise RuntimeError(f"1%/5% no longer matches the published {mode} overlap")


def render(
    tape: dict[tuple[float, float, str], Run],
    overlap: dict[tuple[float, float, str], Run],
    *,
    first_story: str,
    last_story: str,
    overlap_start: str,
    last: str,
) -> str:
    passed = [
        (bid, offer)
        for bid in BIDS
        for offer in OFFERS
        if _clears(
            tape[(bid, offer, "full")],
            tape[(bid, offer, "before 2026")],
            tape[(bid, offer, "2026")],
        )
    ]
    if passed:
        names = ", ".join(f"{_dist(bid)} / {_dist(offer)}" for bid, offer in passed)
        verdict = f"Pairs that clear the locked bar: {names}."
    else:
        verdict = "No pair clears the locked bar for a convincing profit."
    full_runs = [tape[(bid, offer, "full")] for bid in BIDS for offer in OFFERS]
    makers_up = all(run.maker_pnl > 0 for run in full_runs)
    flattens_down = all(run.flatten_pnl < 0 for run in full_runs)
    if makers_up and flattens_down:
        split = (
            "On every full-path pair the completed maker sells make money "
            "and the close flattens lose more."
        )
    else:
        split = "The maker-sell and flatten split is not the same sign on every pair."
    tape_header = (
        "| bid | offer | full | max DD | before 2026 | 2026 | fills | "
        "maker exits | flattens | largest day | bar |"
    )
    tape_rule = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    tape_lines = []
    for bid in BIDS:
        for offer in OFFERS:
            full = tape[(bid, offer, "full")]
            pre = tape[(bid, offer, "before 2026")]
            year = tape[(bid, offer, "2026")]
            flag = "pass" if (bid, offer) in passed else "—"
            tape_lines.append(
                f"| {_dist(bid)} | {_dist(offer)} | {_eur(full.pnl)} | {_pct(full.max_dd)} | "
                f"{_eur(pre.pnl)} | {_eur(year.pnl)} | {full.filled} | {full.maker_exits} | "
                f"{full.flattens} | {_eur(full.largest_day)} | {flag} |"
            )
    overlap_header = (
        "| bid | offer | tone | tone DD | tone exits | block | block DD | "
        "block exits | plain | plain DD | plain exits |"
    )
    overlap_rule = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    overlap_lines = []
    for bid in BIDS:
        for offer in OFFERS:
            cells = []
            for mode in OVERLAP_MODES:
                run = overlap[(bid, offer, mode)]
                cells.append(f"{_eur(run.pnl)} | {_pct(run.max_dd)} | {run.maker_exits}")
            overlap_lines.append(
                f"| {_dist(bid)} | {_dist(offer)} | {' | '.join(cells)} |"
            )
    return "\n".join(
        (
            "# Dip distances",
            "",
            "Rules are the module docstring in `__init__.py`. The sixteen pairs",
            "were locked before this replay. The order is still one bid above the",
            "low already printed today, one offer above the fill, and a taker",
            "flatten at the session close. Same-bar round trips do not count.",
            f"Book €{BOOK_EUR:,.0f}. Tape {TAPE_START} → {last}.",
            f"News archive {first_story} → {last_story}. Overlap {overlap_start} → {last}.",
            "The archive does not reach before 2026, so an overlap cell cannot",
            "clear the two-period bar. `rank` is not in this grid.",
            "",
            "## Full tape, no sentiment",
            "",
            tape_header,
            tape_rule,
            "\n".join(tape_lines),
            "",
            verdict,
            split,
            "The 1% / 5% row is the published bounce and matches that replay.",
            "",
            "## News overlap",
            "",
            overlap_header,
            overlap_rule,
            "\n".join(overlap_lines),
            "",
            "`tone` buys only a name the score marks as a buy. `block` skips a",
            "name the score marks as not a buy. `plain` ignores the score.",
            "The 1% / 5% row matches the published sentiment replay.",
            "Nothing is armed.",
            "",
            "Reproduce: `python -m bot.research.sentiment_dip.percents`",
            "",
        )
    )


def main() -> None:
    last = _last_complete_day()
    grouped = load_headlines(ARCHIVE)
    first_story = min(grouped)
    overlap_start = _shift(first_story, TONE_DAYS)
    raw = load_cache(CACHE, DEFAULT_UNIVERSE)
    books = {base: sessions_from_rows(rows) for base, rows in raw.items()}
    overlap_days = sorted(
        {
            session.day
            for base in DEFAULT_UNIVERSE
            for session in books.get(base, ())
            if overlap_start <= session.day <= last
        }
    )
    tones = tones_by_day(grouped, overlap_days, DEFAULT_UNIVERSE)
    print("checking the 1%/5% control", flush=True)
    _check_control(books, grouped, tones, last, overlap_start)
    tape: dict[tuple[float, float, str], Run] = {}
    for bid in BIDS:
        for offer in OFFERS:
            for label, start, end in _windows(last):
                run = simulate(
                    books,
                    {},
                    universe=DEFAULT_UNIVERSE,
                    mode="plain",
                    start=start,
                    end=end,
                    cushion=bid,
                    target=offer,
                )
                tape[(bid, offer, label)] = run
                print(
                    f"tape {_dist(bid):>3} {_dist(offer):>3} {label:12} {_eur(run.pnl):>12}",
                    flush=True,
                )
    overlap: dict[tuple[float, float, str], Run] = {}
    for bid in BIDS:
        for offer in OFFERS:
            for mode in OVERLAP_MODES:
                run = simulate(
                    books,
                    grouped,
                    universe=DEFAULT_UNIVERSE,
                    mode=mode,
                    start=overlap_start,
                    end=last,
                    cushion=bid,
                    target=offer,
                    tones=None if mode == "plain" else tones,
                )
                overlap[(bid, offer, mode)] = run
                print(
                    f"news {_dist(bid):>3} {_dist(offer):>3} {mode:6} {_eur(run.pnl):>12}",
                    flush=True,
                )
    text = render(
        tape,
        overlap,
        first_story=first_story,
        last_story=max(grouped),
        overlap_start=overlap_start,
        last=last,
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
