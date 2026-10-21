"""Run the day/week horizon lab.

Usage::

    .venv/bin/python -m bot.research.horizon_lab
    .venv/bin/python -m bot.research.horizon_lab --only week_age_keep,day4h_utc_f35
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.consistency_lab.engine import consistency
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.engine import LIVE_KNOBS
from bot.research.horizon_lab.bank import default_bank_specs, run_momentum_bank
from bot.research.horizon_lab.engine import (
    DaySpec,
    default_day_specs,
    default_week_specs,
    run_day_4h,
    run_horizon,
)


def _load_4h(bases: tuple[str, ...], cache: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for base in ("BTC", *bases):
        path = cache / f"{base}.json"
        if path.exists():
            out[base] = json.loads(path.read_text())
    return out


def _line(row: dict[str, Any]) -> str:
    y = row.get("year_pnl") or {}
    years = " ".join(f"{k}:{v:,.0f}" for k, v in y.items())
    return (
        f"{row['strategy']:28} {row['pnl_eur']:10,.0f} {row['max_dd_pct'] * 100:6.1f}% "
        f"{row.get('green_weeks_pct', 0) * 100:5.0f}% {row.get('red_weeks_pct', 0) * 100:5.0f}% "
        f"{row.get('worst_week_pct', 0) * 100:7.1f}% {row['n_trades']:5d}  {years}"
    )


def _baseline(ohlc: dict, *, start: str, end: str, book: float) -> dict[str, Any]:
    row = run_btc_residual(
        ohlc, start=start, end=end, book_eur=book, keep_curve=True, **LIVE_KNOBS
    )
    curve = [(str(d), float(v)) for d, v in row["curve"]]
    row.update(consistency(curve, book))
    row["strategy"] = "baseline_residual"
    return row


def _write_md(path: Path, header: str, sections: list[tuple[str, list[dict[str, Any]]]]) -> None:
    lines = [header, ""]
    for title, rows in sections:
        exits = any("n_tp" in row for row in rows)
        lines.append(f"## {title}")
        lines.append("")
        head = "| variant | PnL | DD | green W | red W | worst W | trades |"
        rule = "|---|---:|---:|---:|---:|---:|---:|"
        if exits:
            head += " tp | stop | fail |"
            rule += "---:|---:|---:|"
        head += " 2024 | 2025 | 2026 |"
        rule += "---:|---:|---:|"
        lines.append(head)
        lines.append(rule)
        for row in rows:
            y = row.get("year_pnl") or {}
            green = row.get("green_weeks_pct", 0) * 100
            red = row.get("red_weeks_pct", 0) * 100
            worst = row.get("worst_week_pct", 0) * 100
            line = (
                f"| {row['strategy']} | {row['pnl_eur']:,.0f} | "
                f"{row['max_dd_pct'] * 100:.1f}% | {green:.0f}% | {red:.0f}% | "
                f"{worst:.1f}% | {row['n_trades']} |"
            )
            if exits:
                line += f" {row.get('n_tp', 0)} | {row.get('n_stop', 0)} | {row.get('n_fail', 0)} |"
            line += (
                f" {y.get('2024', 0):,.0f} | {y.get('2025', 0):,.0f} | {y.get('2026', 0):,.0f} |"
            )
            lines.append(line)
        lines.append("")
    path.write_text("\n".join(lines) + "\n")


VERDICT = """
## Reading

`week_age_cut` matches the armed residual pack to the euro (same PnL, same
drawdown, same 96 trades). The new engine is the live book when the runner
exemption is off. Everything below is a difference from that book, not a
different simulator.

1. **A day book gives the money back to fees.** On daily bars the one-session
   residual (`day_session`) is −€13.9k in-sample and −€1.0k out of sample.
   Letting a session that closed through +8% graduate onto the trail
   (`day_graduate`) is still negative on both sides. On 4h bars, buying the
   leader once per UTC day and selling the next midnight open loses at every
   excess floor (2%, 3.5%, 6%), with and without a 6% trail, for one name
   and for two. Round-trip taker cost is about 0.5% plus impact; the
   forward day does not clear it. Adding that sleeve on the weeks the
   residual book is in cash (`week_keep_plus_idle_day`) cuts the keep book
   from +€519k to +€87k.
2. **A shorter week is the same result the consistency lab already had.**
   Recycling every day (`week_daily_cut` / `week_daily_keep`) finishes
   in-sample flat to negative and carries a 74–80% drawdown. Pinning the
   clock to Sunday (`week_sun_*`) loses in 2024, 2025 and 2026.
3. **Not selling a runner on the 7-day clock is the only positive delta,
   and it is two episodes.** `week_age_keep` is +€153k IS versus +€152k
   for the live pack, and +€53k versus +€47k on a fresh 2026 book. The
   trade diff is a clock sell the keep book refused: SUI on 2025-05-12
   (held to the trail four days later) and LINK on 2026-08-25 (held one
   more day). The other sells in the diff are the path that followed from
   still being in that name. Arms at +8%, +12% and +18% are the same book
   through 2025; only 2026 moves, and by one further hold. Drawdown on the
   full path is worse (33.7% versus 31.2%). Green weeks do not improve
   (31% versus 32%).
4. **Taking the level while the bar is printing gives the rise back to
   fees.** A round trip is about 0.5% plus impact. A +3% target against a
   −3% stop needs about 58% of the decisive trades to hit the target
   before it breaks even. `bank_d_tp3_s3` prints 381 targets and 381
   stops and finishes at −€19,985. Every tight target on a 24h rise —
   partial, two names, or the BTC filter off — compounds €20k toward zero,
   in-sample and in 2026. A 24h hold with no target and no stop is still
   negative (`bank_d_f4_hold` −€14.4k IS / −€9.2k OOS, `bank_d_f8_hold`
   −€7.5k / −€9.8k).
5. **A 7-day hold until the rise dies is the only line above zero, and it
   is a 2024 windfall with a deep giveback.** No target, no stop, sell the
   next open once the lookback return is no longer positive. Floors 3%,
   4%, 6% and 8% are positive on the full path (+€15k to +€29k) and on a
   fresh 2026 book (+€5.3k, +€2.2k, +€1.2k, +€0.8k). The in-sample gain
   grows as the floor rises (+€11k to +€37k) while the fresh 2026 book
   shrinks, and 2025 loses €27k–€32k at every floor. Drawdown on the full
   path is 76–82%, worst week about −35%, green weeks about 30%. From the
   same 2024-06-01 start the armed residual pack is +€389,590 at 30.6%
   drawdown (2024 +€104k, 2025 +€17k, 2026 +€269k). A +4% target or an 8%
   stop on the 4% and 8% entries is negative in-sample and in 2026. The
   3% floor plus a 5% stop (`bank_w_follow`) is negative too (−€4.5k IS,
   −€1.2k OOS).

## Verdict

Nothing armed. The book already ranks what is rising. Banking that rise
at a fixed level inside the 4h bar loses after Bitvavo taker fees: the
win rate sits under the fee-adjusted breakeven and a €20k book goes to
zero. Holding a 7-day riser until the rise dies stays above zero only as
a 2024 profit that 2025 largely gives back, with a fresh 2026 result of
a few thousand euros and a drawdown past 70%. That is a smaller, rougher
book than the armed residual trail over the same dates. Day trading flat
by the next UTC open loses the same fees. A shorter residual clock gives
back the trends that pay the pack. Leaving a name that has closed through
the runner arm on the trail is two holds, and the share of green weeks
stays put. The live pack stays the 7-day residual with the 10% trail.
"""


def main() -> None:
    p = argparse.ArgumentParser(description="Day and week horizon lab")
    p.add_argument("--cache-dir", default="data/residual_wet_candles")
    p.add_argument("--cache-4h", default="data/research/bitvavo_4h")
    p.add_argument("--book", type=float, default=20_000.0)
    p.add_argument("--start", default="2024-03-16")
    p.add_argument("--start-4h", default="2024-06-01")
    p.add_argument("--is-end", default="2025-12-31")
    p.add_argument("--oos-start", default="2026-01-01")
    p.add_argument("--out", default="artifacts/horizon_lab.json")
    p.add_argument("--md", default="bot/research/horizon_lab/RESULTS.md")
    p.add_argument("--no-fresh", action="store_true")
    p.add_argument("--skip-4h", action="store_true")
    p.add_argument("--only", default="")
    args = p.parse_args()

    only = {s for s in args.only.split(",") if s}
    ohlc = load_candles(Path(args.cache_dir), merge_fresh=not args.no_fresh)
    if "BTC" not in ohlc:
        raise SystemExit("no BTC candles")
    end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    windows = (
        ("full", args.start, end),
        ("is", args.start, args.is_end),
        ("oos", args.oos_start, end),
    )
    week_specs = [s for s in default_week_specs() if not only or s.name in only]
    payload: dict[str, Any] = {"end": end, "windows": {}}

    print(f"daily {args.start} → {end}  book €{args.book:,.0f}")
    for label, start, stop in windows:
        print(f"\n== {label} {start} → {stop} ==")
        rows: list[dict[str, Any]] = []
        if not only or "baseline_residual" in only:
            base = _baseline(ohlc, start=start, end=stop, book=args.book)
            rows.append(base)
            print(_line(base))
        for spec in week_specs:
            row = run_horizon(ohlc, spec, start=start, end=stop, book_eur=args.book)
            rows.append(row)
            print(_line(row))
        payload["windows"][label] = rows

    h4_rows: dict[str, list[dict[str, Any]]] = {}
    if not args.skip_4h:
        h4 = _load_4h(DEFAULT_UNIVERSE, Path(args.cache_4h))
        missing = [b for b in ("BTC", *DEFAULT_UNIVERSE) if b not in h4]
        if missing:
            print(f"skip 4h, missing {missing}")
        else:
            h4_end = datetime.fromtimestamp(int(h4["BTC"][-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
            h4_windows = (
                ("full", args.start_4h, h4_end),
                ("is", args.start_4h, args.is_end),
                ("oos", args.oos_start, h4_end),
            )
            day_specs: list[DaySpec] = [
                s for s in default_day_specs() if not only or s.name in only
            ]
            print(f"\n4h day book {args.start_4h} → {h4_end}")
            for label, start, stop in h4_windows:
                print(f"\n== 4h {label} {start} → {stop} ==")
                got: list[dict[str, Any]] = []
                for spec in day_specs:
                    row = run_day_4h(h4, spec, start=start, end=stop, book_eur=args.book)
                    got.append(row)
                    print(_line(row))
                h4_rows[label] = got
            payload["h4_end"] = h4_end
            payload["h4"] = h4_rows

            bank_specs = [s for s in default_bank_specs() if not only or s.name in only]
            same = _baseline(ohlc, start=args.start_4h, end=end, book=args.book)
            same["strategy"] = "baseline_from_4h_start"
            payload["baseline_from_4h_start"] = {
                k: v for k, v in same.items() if k != "curve"
            }
            print(f"\nbank vs residual from {args.start_4h}")
            print(_line(same))
            if bank_specs:
                print(f"\n4h momentum bank {args.start_4h} → {h4_end}")
                bank_rows: dict[str, list[dict[str, Any]]] = {}
                for label, start, stop in h4_windows:
                    print(f"\n== bank {label} {start} → {stop} ==")
                    got = []
                    for spec in bank_specs:
                        row = run_momentum_bank(
                            h4, spec, start=start, end=stop, book_eur=args.book
                        )
                        got.append(row)
                        print(
                            f"{_line(row)}  tp={row.get('n_tp', 0)} "
                            f"stop={row.get('n_stop', 0)} fail={row.get('n_fail', 0)}"
                        )
                    bank_rows[label] = got
                payload["bank"] = bank_rows

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, default=str))
    print(f"wrote {out}")

    sections = [(f"Daily {label}", rows) for label, rows in payload["windows"].items()]
    sections += [(f"4h {label}", rows) for label, rows in h4_rows.items()]
    sections += [
        (f"Bank {label}", rows) for label, rows in payload.get("bank", {}).items()
    ]
    md = Path(args.md)
    _write_md(
        md,
        "\n".join(
            [
                "# Day and week horizon lab",
                "",
                "Question: the residual pack holds one name for weeks, and the minute",
                "desk loses to fees. Is there a week clock that recycles dead names",
                "without selling runners, and a day book that is flat by the next",
                "UTC open, that survive costs on this tape?",
                "",
                "Week rows use the live residual signal (10d skip-1, floor 3.5%,",
                "SMA50, near-high 0.25, trail 10%, lock +12%/+6%) on Bitvavo daily",
                "bars, wet next-open fills, €20k compounding. `keep` does not sell a",
                "lot whose close has reached the runner arm; `cut` sells it on the",
                "clock like a time box. The day session is a 3-day residual, one",
                "UTC session, round-tripped even when the leader does not change.",
                "`day_graduate` leaves that lot on the trail once the session closes",
                "through +8%. The 4h book enters once per UTC day and is sold on the",
                "first open of the next date.",
                "",
                "Bank rows are a 4h momentum book on the same desk universe.",
                "Entry is the next open after a close whose own lookback return",
                "is positive and whose excess versus BTC clears the floor. A",
                "target fills at the target when the high trades through it. A",
                "stop fills at the stop when the low trades through it; if both",
                "print in one bar the stop is booked. `hold` has no target and",
                "no stop and sells the next open once the lookback return is no",
                "longer positive. `bank_w_follow` is that 7-day hold plus a 5%",
                "stop. Fills are wet Bitvavo taker, €20k compounding.",
                "`baseline_from_4h_start` is the armed residual pack on daily",
                "bars from the same start date (printed in the run log).",
                "",
                f"Daily tape through {end}.",
                "Reproduce: `.venv/bin/python -m bot.research.horizon_lab --no-fresh`",
                "",
            ]
        ),
        sections,
    )
    md.write_text(md.read_text() + VERDICT)
    print(f"wrote {md}")


if __name__ == "__main__":
    main()
