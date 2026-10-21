"""Published momentum tactics versus the armed residual pack.

Usage::

    .venv/bin/python -m bot.research.tactic_lab --no-fresh
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.alphai_pack_compare.alphai_map import (
    build_alt_allow,
    load_merged_sessions,
    picks_asof_hour,
)
from bot.research.clip_exit_lab.engine import bar_date
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.tactic_lab.engine import (
    default_tactic_specs,
    run_residual_row,
    run_tactic,
)


def _line(row: dict[str, Any]) -> str:
    y = row.get("year_pnl") or {}
    years = " ".join(f"{k}:{v:,.0f}" for k, v in y.items())
    return (
        f"{row['strategy']:24} {row['pnl_eur']:10,.0f} {row['max_dd_pct'] * 100:6.1f}% "
        f"{row.get('green_weeks_pct', 0) * 100:5.0f}% {row['n_trades']:5d}  {years}"
    )


def _write_md(path: Path, header: str, sections: list[tuple[str, list[dict[str, Any]]]]) -> None:
    lines = [header, ""]
    for title, rows in sections:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| variant | PnL | DD | green W | trades | 2024 | 2025 | 2026 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            y = row.get("year_pnl") or {}
            lines.append(
                f"| {row['strategy']} | {row['pnl_eur']:,.0f} | "
                f"{row['max_dd_pct'] * 100:.1f}% | {row.get('green_weeks_pct', 0) * 100:.0f}% | "
                f"{row['n_trades']} | {y.get('2024', 0):,.0f} | "
                f"{y.get('2025', 0):,.0f} | {y.get('2026', 0):,.0f} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n")


VERDICT = """
## Reading

The armed residual pack is the profitable book on this tape: +€468,677
over the full path, +€151,798 in-sample, +€46,533 on a fresh 2026 book,
drawdown 31.2%. Everything below is a published rule on the same wet fills.

1. **A weekly time-series momentum book nets after taker fees, and it is
   the smaller book.** `tsmom_7_reb7` is long only while the 7-day return
   is positive, ranked by excess versus BTC, trailed 10%. +€26,719
   in-sample, +€13,224 in 2026, full path +€50,449 at 61% drawdown.
   2025 is −€35,518. Green weeks are 24% against 32% for the residual pack.
2. **The multi-horizon trend, equal-weight 7/14/28/56 with no model fit,
   also nets and also lags the pack.** `ctrend_reb7` is +€10,868 in-sample
   and +€8,162 in 2026, full path +€21,587 at 65% drawdown. Adding a
   volume-expansion filter on the 28-day clock (`ctrend_reb28_vol`) is
   negative on the full path.
3. **The four-week hold the papers use to survive costs does not survive
   this tape.** `tsmom_28_reb28` and `ctrend_reb28` are negative in-sample
   and on the fresh 2026 book. Dropping the trail (`*_hold`) is positive
   in-sample (+€23k / +€31k) and a thin +€4,154 in 2026, and the full path
   is negative because 2026 on the compounded book gives the earlier gain
   back.
4. **AlphaI has 13 stored sessions, 2026-09-19 through 2026-10-03.** On
   those days the residual pack is +€6,948. The same window with the
   published signals is −€1,073. Restricting those signals to that day's
   AlphaI picks leaves one open name, marked +€1,900, with no closed round
   trip. The gate lab on this same store already measured the residual
   pack itself: a hard AlphaI gate went to −€1,004 or to cash, and the
   least costly intersect (as-of 16:00) cut +€6,948 to +€1,944.

## Verdict

Nothing new armed. The profitable way after Bitvavo taker fees is the
book already live: 10-day skip-1 excess, 7-day clock, 10% trail. The
online designs that claim to survive costs do net when the clock stays
at a week and the name's own return is still positive, and they make
roughly a tenth of that pack at about twice the drawdown. Stretching the
hold to four weeks, which is how those papers keep turnover down, gives
the gain back. AlphaI cannot be a two-year filter: the stored sessions
cover thirteen days, and on those days it does not improve the residual
pack.
"""


def main() -> None:
    p = argparse.ArgumentParser(description="Published momentum tactics")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--alphai", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--out", default="artifacts/tactic_lab.json")
    p.add_argument("--md", default="bot/research/tactic_lab/RESULTS.md")
    p.add_argument("--no-fresh", action="store_true")
    args = p.parse_args()

    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    specs = default_tactic_specs()
    payload: dict[str, Any] = {"end": end, "windows": {}}
    print(f"tactics {args.start} → {end}  book €{args.book:,.0f}")
    for label, start, stop in windows:
        print(f"\n== {label} {start} → {stop} ==")
        rows: list[dict[str, Any]] = []
        base = run_residual_row(ohlc, start=start, end=stop, book_eur=args.book)
        rows.append(base)
        print(_line(base))
        for spec in specs:
            row = run_tactic(ohlc, spec, start=start, end=stop, book_eur=args.book)
            rows.append(row)
            print(_line(row))
        payload["windows"][label] = rows

    alphai_rows: list[dict[str, Any]] = []
    path = Path(args.alphai)
    if path.exists():
        sessions = load_merged_sessions(path)
        dates = [bar_date(r) for r in ohlc["BTC"]]
        daily = picks_asof_hour(sessions, hour_utc=16, minute_grace=8)
        if daily:
            first = min(daily)
            last = min(end, max(daily))
            allow = build_alt_allow(start=first, end=last, ohlc_dates=dates, daily_picks=daily)
            payload["alphai_window"] = [first, last, len(daily)]
            print(f"\n== alphai {first} → {last}  days {len(daily)} ==")
            focus = [s for s in specs if s.name in {"tsmom_28_reb7", "ctrend_reb7"}]
            for spec in focus:
                plain = run_tactic(ohlc, spec, start=first, end=last, book_eur=args.book)
                plain["strategy"] = spec.name
                gated = run_tactic(
                    ohlc,
                    spec,
                    start=first,
                    end=last,
                    book_eur=args.book,
                    alphai_allow=allow,
                    alphai_from=first,
                )
                gated["strategy"] = f"{spec.name}_alphai"
                alphai_rows.extend([plain, gated])
                print(_line(plain))
                print(_line(gated))
            base = run_residual_row(ohlc, start=first, end=last, book_eur=args.book)
            alphai_rows.insert(0, base)
            print(_line(base))
            payload["alphai"] = alphai_rows

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str))
    sections = [(f"Daily {label}", rows) for label, rows in payload["windows"].items()]
    if alphai_rows:
        sections.append(("AlphaI sessions", alphai_rows))
    md = Path(args.md)
    _write_md(
        md,
        "\n".join(
            [
                "# Tactic lab",
                "",
                "Question: the published low-turnover momentum designs, and a real",
                "AlphaI allow-list, on wet Bitvavo daily fills.",
                "",
                "Time-series momentum is long only while the lookback return is",
                "positive, ranked by excess versus BTC. The multi-horizon trend is",
                "the equal-weight 7/14/28/56 day return, skip one day, long the",
                "highest score that is still positive. No model is fit on this tape.",
                "Clock 7 or 28 days. Trail 10% sells the next open; `hold` has no",
                "trail. BTC below its SMA50 blocks a new buy and flattens on the",
                "clock. €20k compounding, taker 0.25% plus impact.",
                "",
                "AlphaI rows use the stored sessions only (as-of 16:00 UTC, the",
                "hour the gate lab found least costly). There is no AlphaI history",
                "before the first stored session, so that gate is not applied to",
                "the long sample.",
                "",
                f"Daily tape through {end}.",
                "Reproduce: `.venv/bin/python -m bot.research.tactic_lab --no-fresh`",
                "",
            ]
        ),
        sections,
    )
    md.write_text(md.read_text() + VERDICT)
    print(f"wrote {md}")


if __name__ == "__main__":
    main()
