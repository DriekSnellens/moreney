"""Run the locked search and write RESULTS.md.

Usage::

    .venv/bin/python -m bot.research.better_book [--no-fresh]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from bot.research.better_book.bar import judge_family
from bot.research.better_book.report import render
from bot.research.better_book.search import largest_new_trip, run_search
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS


def _with_trades(ohlc, spec, windows, book: float):
    from bot.research.better_book.search import _run

    return _run(ohlc, windows=windows, book_eur=book, keep_trades=True, **spec)


def main() -> None:
    p = argparse.ArgumentParser(description="Search for a book that beats residual_full")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--no-fresh", action="store_true")
    p.add_argument("--out", default="bot/research/better_book/RESULTS.md")
    args = p.parse_args()

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    # Drop the in-progress UTC day. completed bars only.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if end == today:
        last_ms = int(ohlc["BTC"][-2][0])
        end = datetime.fromtimestamp(last_ms / 1000, UTC).strftime("%Y-%m-%d")
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    print(f"tape {args.start} → {end} knobs trail={LIVE_KNOBS['policy'].name}")
    payload = run_search(ohlc, windows=windows, book_eur=args.book, log=print)

    from bot.research.better_book.search import families

    specs = {spec["name"]: spec for _, cells in families() for spec in cells}
    verdicts = []
    for family, cells in payload["families"].items():
        from bot.research.better_book.bar import hits_margin, region_ok

        margin = [c for c in cells if hits_margin(c, payload["baseline"])]
        new_trip: dict[str, float] = {}
        if margin and region_ok(cells, payload["baseline"]):
            base_full = _with_trades(
                ohlc, {"name": "baseline"}, (("full", args.start, end),), args.book
            )
            base_trades = base_full.get("full_trades") or []
            for cell in margin:
                got = _with_trades(
                    ohlc, specs[cell["name"]], (("full", args.start, end),), args.book
                )
                new_trip[cell["name"]] = largest_new_trip(
                    got.get("full_trades") or [], base_trades
                )
                print(f"one-trip {cell['name']} {new_trip[cell['name']]:+.0f}")
        verdicts.append(
            judge_family(family, cells, payload["baseline"], new_trip=new_trip or None)
        )
        print(
            f"family {family} region={verdicts[-1].region} "
            f"margin={verdicts[-1].margin_names} cleared={verdicts[-1].cleared}"
        )

    text = render(payload, verdicts, asof=end, start=args.start)
    wide = _wide_section(windows, args.book, end, args.start)
    if wide:
        text = text.rstrip() + "\n\n" + wide
        print("wide universe section included")
    Path(args.out).write_text(text + "\n")
    print(f"wrote {args.out}")


def _wide_section(
    windows: tuple[tuple[str, str, str], ...],
    book: float,
    end: str,
    start: str,
) -> str:
    """Same book on the liquid names of the whole venue, when that cache exists."""
    import json

    from bot.research.better_book.bar import VOLUME_TOPS, judge_family
    from bot.research.better_book.report import _row
    from bot.research.better_book.search import _run

    root = Path("data/research/bitvavo_1d_all")
    if not root.is_dir():
        return ""
    ohlc: dict[str, list] = {}
    for path in sorted(root.glob("*.json")):
        ohlc[path.stem] = json.loads(path.read_text())
    if "BTC" not in ohlc:
        return ""
    print(f"wide universe {len(ohlc) - 1} names, tape → {end}")
    base = _run(ohlc, name="baseline", windows=windows, book_eur=book)
    cells = []
    for n in VOLUME_TOPS:
        cell = _run(
            ohlc,
            name=f"vol_top_{n}",
            windows=windows,
            book_eur=book,
            universe_top_n=n,
        )
        cell["family"] = "volume_universe"
        cells.append(cell)
        print(
            f"vol_top_{n} full {cell['full']['pnl_eur']:+.0f} "
            f"oos {cell['oos']['pnl_eur']:+.0f}"
        )
    verdict = judge_family("volume_universe", cells, base)
    lines = [
        "## Liquid names, re-picked every day",
        "",
        f"Same rules, on every Bitvavo EUR name in `{root}` through {end}.",
        "Each day the book may only buy one of the most-traded names, and a",
        "name needs 60 completed days. The baseline row is the fixed desk on",
        "this same cache, not the desk cache used above.",
        "",
        "| variant | full PnL | full DD | IS PnL | fresh 2026 | 2026 DD | trades |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _row(base),
    ]
    for cell in cells:
        lines.append(_row(cell))
    lines.append("")
    if verdict.cleared:
        names = ", ".join(f"`{n}`" for n in verdict.cleared)
        lines.append(f"Clears the bar: {names}. Not switched on.")
    else:
        lines.append(
            "Does not clear the bar. "
            f"region={verdict.region} margin={list(verdict.margin_names)}."
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
