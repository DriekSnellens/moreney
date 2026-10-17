"""Minute volume-velocity replay inside the daily €200k–€300k section.

    .venv/bin/python -m bot.research.volume_velocity --fetch
    .venv/bin/python -m bot.research.volume_velocity

Fetch caches 1m bars under data/ (gitignored). The replay writes
artifacts/volume_velocity.json and refreshes RESULTS.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from bot.research.volume_velocity.engine import DAY_MS, collect_compact, run_compact
from bot.research.volume_velocity.fetch import fetch_members
from bot.research.volume_velocity.membership import load_daily, member_days

START = "2024-06-01"
END = "2026-10-16"
IS_END = "2026-01-01"
SECTION_LO = 200_000.0
SECTION_HI = 300_000.0
BANDS = (("200-300k", 200_000.0, 300_000.0),)
TRIGGERS = (
    ("1m x10", 1, 10.0),
    ("1m x20", 1, 20.0),
    ("1m x40", 1, 40.0),
    ("1m x80", 1, 80.0),
    ("5m x8", 5, 8.0),
    ("5m x15", 5, 15.0),
    ("5m x30", 5, 30.0),
)
EXITS = (
    ("hold 15m", 15 * 60_000, 0.0, 0.0),
    ("hold 60m", 60 * 60_000, 0.0, 0.0),
    ("hold 4h", 4 * 60 * 60_000, 0.0, 0.0),
    ("hold 24h", 24 * 60 * 60_000, 0.0, 0.0),
    ("trail 10% / 24h", 24 * 60 * 60_000, 0.10, 0.0),
    ("trail 10% + stop 8%", 24 * 60 * 60_000, 0.10, 0.08),
)
BOOKS = (
    ("€2k x1", 2_000.0, 2_000.0, 1),
    ("€20k x1", 20_000.0, 20_000.0, 1),
    ("€5k x4", 20_000.0, 5_000.0, 4),
)


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def build_members() -> list[dict]:
    daily = load_daily()
    rows = member_days(
        daily, start_ms=_ms(START), end_ms=_ms(END), lo=SECTION_LO, hi=SECTION_HI, window=30
    )
    n_bases = len({r["base"] for r in rows})
    print(
        f"section days {SECTION_LO:.0f}-{SECTION_HI:.0f}: {len(rows)} across {n_bases} bases",
        flush=True,
    )
    return rows


def _with_next_day(members: list[dict]) -> list[dict]:
    """Also pull the following day so a late burst has an exit path."""
    have = {(r["base"], int(r["day_ms"])) for r in members}
    extra: list[dict] = []
    for row in members:
        nxt = int(row["day_ms"]) + DAY_MS
        key = (row["base"], nxt)
        if key in have:
            continue
        have.add(key)
        extra.append({"base": row["base"], "day_ms": nxt, "baseline_eur": row["baseline_eur"]})
    return members + extra


def run_grid(members: list[dict]) -> dict:
    is_end = _ms(IS_END)
    oos_end = _ms(END) + DAY_MS
    out: dict = {
        "start": START,
        "end": END,
        "is_end": IS_END,
        "section": [SECTION_LO, SECTION_HI],
        "cost": "taker 0.25% + impact 0.02*notional/baseline per side, cap 3%",
        "rows": [],
    }
    jobs = [("clock open", 0, 0.0), *TRIGGERS]
    for band_name, lo, hi in BANDS:
        print(f"band {band_name}", flush=True)
        compact = collect_compact(members, jobs, EXITS, lo=lo, hi=hi)
        for trig_name, _, _ in jobs:
            for exit_spec in EXITS:
                rows = compact[(trig_name, exit_spec[0])]
                print(f"  {trig_name} {exit_spec[0]}: {len(rows)} signals", flush=True)
                for up_only in (False, True):
                    if trig_name == "clock open" and up_only:
                        continue
                    for book in BOOKS:
                        _label, slot_book, slot, slots = book
                        is_cell = run_compact(
                            rows,
                            book_eur=slot_book,
                            slot_eur=slot,
                            max_positions=slots,
                            up_only=up_only,
                            start_ms=0,
                            end_ms=is_end,
                        )
                        oos_cell = run_compact(
                            rows,
                            book_eur=slot_book,
                            slot_eur=slot,
                            max_positions=slots,
                            up_only=up_only,
                            start_ms=is_end,
                            end_ms=oos_end,
                        )
                        out["rows"].append(
                            {
                                "band": band_name,
                                "trigger": trig_name,
                                "up_only": up_only,
                                "exit": exit_spec[0],
                                "book": book[0],
                                "signals": len(rows),
                                "is": is_cell,
                                "oos": oos_cell,
                            }
                        )
    return out


def _fmt_pnl(cell: dict) -> str:
    return (
        f"{cell['pnl']:+,.0f} (n={cell['n']}, win {cell['win'] * 100:.0f}%, "
        f"med {cell['median'] * 100:+.2f}%, dd {cell['max_dd']:,.0f})"
    )


def write_results(grid: dict, path: Path) -> None:
    rows = grid["rows"]
    # Primary reading: the €200-300k section, up-minute bursts, €2k slot.
    primary = [
        r
        for r in rows
        if r["band"] == "200-300k"
        and r["up_only"]
        and r["book"] == "€2k x1"
        and r["trigger"] != "clock open"
    ]
    both = [
        r
        for r in primary
        if r["is"]["pnl"] > 0 and r["oos"]["pnl"] > 0 and r["is"]["n"] >= 30 and r["oos"]["n"] >= 15
    ]
    lines = [
        "# Minute volume velocity inside the €200k–€300k section",
        "",
        "Question: names whose prior-30-day median daily quote volume sits in the",
        "€200k–€300k section, and that then print a sudden burst of volume per minute —",
        "does buying that burst make money after Bitvavo taker fees and impact?",
        "",
        f"Window {grid['start']} → {grid['end']}. IS through {grid['is_end']}, OOS after.",
        "Membership uses only the prior 30 completed days, so the burst itself cannot",
        "qualify the name. Fill is the next traded minute's open within 5 minutes.",
        "One hour cooldown per name. Costs: " + grid["cost"] + ".",
        "Neighbours €150–250k and €250–400k are the same rules, to see whether a result",
        "is the section or a knife-edge. Nothing here is armed.",
        "",
        "## €200k–€300k, up-minute only, one €2k slot",
        "",
        "| trigger | exit | IS | OOS |",
        "| --- | --- | --- | --- |",
    ]
    for r in primary:
        lines.append(
            f"| {r['trigger']} | {r['exit']} | {_fmt_pnl(r['is'])} | {_fmt_pnl(r['oos'])} |"
        )
    lines += ["", "## Same triggers, both up and down minutes, one €2k slot", ""]
    lines.append("| trigger | exit | IS | OOS |")
    lines.append("| --- | --- | --- | --- |")
    for r in rows:
        if r["band"] == "200-300k" and not r["up_only"] and r["book"] == "€2k x1":
            lines.append(
                f"| {r['trigger']} | {r['exit']} | {_fmt_pnl(r['is'])} | {_fmt_pnl(r['oos'])} |"
            )
    lines += ["", "## Full €20k in one name (impact binds harder)", ""]
    lines.append("| trigger | exit | direction | IS | OOS |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in rows:
        if (
            r["band"] == "200-300k"
            and r["book"] == "€20k x1"
            and r["trigger"] in {"1m x20", "1m x40", "5m x15", "clock open"}
            and r["exit"] in {"hold 60m", "hold 4h", "trail 10% / 24h", "trail 10% + stop 8%"}
        ):
            direction = "up" if r["up_only"] else "any"
            lines.append(
                f"| {r['trigger']} | {r['exit']} | {direction} | "
                f"{_fmt_pnl(r['is'])} | {_fmt_pnl(r['oos'])} |"
            )
    lines += ["", "## Cells positive in both IS and OOS (€2k, up-only, n large enough)", ""]
    if not both:
        lines.append("None on the €200k–€300k section.")
    else:
        for r in both:
            lines.append(
                f"- {r['trigger']} / {r['exit']}: IS {_fmt_pnl(r['is'])}; OOS {_fmt_pnl(r['oos'])}"
            )
    lines += [
        "",
        "Neighbour bands (€150–250k, €250–400k) are not in this replay. On daily bars",
        "those bands already had no close-to-close edge (unusual-volume lab). This run",
        "spends the minute data on the section that was actually asked.",
        "",
        "",
        "Gross PnL (before costs) is in the JSON under `gross_pnl`. A cell that is only",
        "positive after a tiny sample, or only in one neighbour band, is not an edge.",
        "",
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.volume_velocity --fetch",
        ".venv/bin/python -m bot.research.volume_velocity",
        "```",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    members = build_members()
    if args.fetch:
        fetch_members(
            _with_next_day(members),
            workers=args.workers,
            log=lambda msg: print(msg, flush=True),
        )
        return
    grid = run_grid(members)
    art = Path("artifacts")
    art.mkdir(exist_ok=True)
    (art / "volume_velocity.json").write_text(json.dumps(grid))
    write_results(grid, Path("bot/research/volume_velocity/RESULTS.md"))
    print(f"rows {len(grid['rows'])} -> artifacts/volume_velocity.json", flush=True)


if __name__ == "__main__":
    main()
