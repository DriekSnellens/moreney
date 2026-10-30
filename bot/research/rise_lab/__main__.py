"""Rank prior states by the rise that follows.

    .venv/bin/python -m bot.research.rise_lab

Uses the daily cache. The in-progress UTC day is dropped. Writes RESULTS.md.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from bot.research.rise_lab.measure import PRE_END, RiseSpec, collect, split_summary
from bot.research.unusual_volume_lab.data import CACHE_1D, eur_bases, load_cached

ORDER = (
    "r3_neg",
    "r3_mild",
    "r3_mid",
    "r3_hot",
    "liquid",
    "quiet",
    "wake",
    "breakout",
    "live_xs",
    "r3_15",
    "xs25",
    "continuation",
    "day_leader",
)

LABELS = {
    "r3_neg": "3d return negative",
    "r3_mild": "3d return 0 to 15%",
    "r3_mid": "3d return 15 to 40%",
    "r3_hot": "3d return ≥ 40%",
    "liquid": "any liquid day",
    "quiet": "10d return under 12%",
    "wake": "decline, quiet shelf, first interest day",
    "breakout": "20d high, day ≥ 6%, volume ≥ 2×",
    "live_xs": "10d up and ≥ 3.5% ahead of BTC",
    "r3_15": "3d return ≥ 15%",
    "xs25": "10d excess vs BTC ≥ 25%",
    "continuation": "3d ≥ 15%, excess ≥ 25%, above SMA20 > SMA50",
    "day_leader": "strongest 3d return that day",
}


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{100.0 * value:.1f}%"


def _cell(stats: dict[str, float], key: str) -> str:
    if not stats or key not in stats:
        return "—"
    if key == "n":
        return f"{int(stats['n'])}"
    return _pct(float(stats[key]))


def _table(title: str, period: str, summary: dict[str, dict[str, dict[str, float]]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| state | n | high median | high p90 | close median | % high ≥ 20 | % high ≥ 50 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ORDER:
        stats = summary[name][period]
        lines.append(
            f"| {LABELS[name]} | {_cell(stats, 'n')} | {_cell(stats, 'high_median')} | "
            f"{_cell(stats, 'high_p90')} | {_cell(stats, 'close_median')} | "
            f"{_cell(stats, 'p_high_20')} | {_cell(stats, 'p_high_boom')} |"
        )
    lines.append("")
    return lines


def _reading(summary: dict[str, dict[str, dict[str, float]]]) -> list[str]:
    hot_b = summary["r3_hot"]["before"]
    hot_a = summary["r3_hot"]["after"]
    lead_b = summary["day_leader"]["before"]
    lead_a = summary["day_leader"]["after"]
    cont_b = summary["continuation"]["before"]
    cont_a = summary["continuation"]["after"]
    base_b = summary["liquid"]["before"]
    base_a = summary["liquid"]["after"]
    wake_a = summary["wake"]["after"]
    return [
        "## Reading",
        "",
        "The next week's high scales with how hard the name is already rising.",
        "A negative or mild 3-day return is followed by the same high as any",
        "liquid day. From there the median high, the p90, and the share of",
        "+50% highs all step up together, in the years before 2026 and again",
        "in 2026.",
        "",
        (
            f"1. The strongest 3-day return on the board is the top of that "
            f"scale. Before 2026 the median high is {_cell(lead_b, 'high_median')} "
            f"and {_cell(lead_b, 'p_high_boom')} of the next 7 days trade +50% "
            f"above the next open (n={_cell(lead_b, 'n')}). In 2026 those are "
            f"{_cell(lead_a, 'high_median')} and {_cell(lead_a, 'p_high_boom')} "
            f"(n={_cell(lead_a, 'n')}). A 3-day return already at +40% or more "
            f"is the same family: median high {_cell(hot_b, 'high_median')} then "
            f"{_cell(hot_a, 'high_median')}, +50% high "
            f"{_cell(hot_b, 'p_high_boom')} then {_cell(hot_a, 'p_high_boom')}."
        ),
        (
            f"2. The rule measured earlier on an 80-name universe is the broad "
            f"version: 3-day return at least +15%, 10-day return at least 25 "
            f"points ahead of BTC, close above the 20-day average and that "
            f"average above the 50-day. Median high {_cell(cont_b, 'high_median')} "
            f"before 2026 and {_cell(cont_a, 'high_median')} in 2026. A +50% high "
            f"lands {_cell(cont_b, 'p_high_boom')} then {_cell(cont_a, 'p_high_boom')} "
            f"of the time, against {_cell(base_b, 'p_high_boom')} and "
            f"{_cell(base_a, 'p_high_boom')} on any liquid day."
        ),
        (
            f"3. A quiet 10-day tape, and the decline-shelf-interest wake, sit "
            f"next to the base rate. In 2026 the wake's median high is "
            f"{_cell(wake_a, 'high_median')} and its +50% rate is "
            f"{_cell(wake_a, 'p_high_boom')}, on a base of "
            f"{_cell(base_a, 'high_median')} and {_cell(base_a, 'p_high_boom')}."
        ),
        (
            "4. The close does not keep the high. Every state above has a "
            "negative 7-day median close. The states with the largest highs "
            f"give the most back: the day's leader closes at "
            f"{_cell(lead_a, 'close_median')} in 2026, and a name already up "
            f"40% in three days closes at {_cell(hot_a, 'close_median')}. "
            "The high is real. A hold to the 7-day close is not where it is paid."
        ),
        "",
        "## Verdict",
        "",
        "The structural high is continuation of a name that is already",
        "running, not a quiet base waking up. The fattest 7-day highs follow",
        "the strongest 3-day return on the board. The same ordering holds",
        "before 2026 and in 2026. Nothing here is armed: the close after",
        "those highs is negative, and the live book is a milder leader held",
        "for weeks, which is a different payoff than this 7-day high.",
        "",
    ]


def _completed(rows: list[list[float]], today_ms: int) -> list[list[float]]:
    return [row for row in rows if int(row[0]) < today_ms]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="bot/research/rise_lab/RESULTS.md")
    args = ap.parse_args()
    now = datetime.now(UTC)
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000)
    bases = eur_bases()
    if "BTC" not in bases:
        bases.append("BTC")
    ohlc: dict[str, list[list[float]]] = {}
    for base, bars in load_cached(CACHE_1D, bases).items():
        done = _completed(bars, today_ms)
        if done:
            ohlc[base] = done
    spec = RiseSpec()
    events = collect(ohlc, spec)
    summary = split_summary(events, boom=spec.boom, cut=PRE_END)
    last = max(str(event["day"]) for event in events)
    lines = [
        "# What is followed by the largest rises",
        "",
        "Question: after the quiet-shelf wake failed to enrich for spikes,",
        "which prior state on this tape is followed by the largest rises,",
        "in both the earlier years and in 2026?",
        "",
        f"Every Bitvavo EUR name, daily bars, quote volume at least "
        f"€{spec.min_qvol:,.0f} on the signal day. Entry is the next open. "
        f"The high and the close are measured over the next {spec.horizon} bars. "
        f"The in-progress UTC day is dropped. Rows run through {last}.",
        "",
        "States overlap: a hot day is also liquid. The 3-day return buckets",
        "do not overlap. `before 2026` was not used to pick the continuation",
        "rule; that rule is the one the moonshot preimage already measured",
        "as the highest precision on a +50% high within 7 days.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.rise_lab`",
        "",
    ]
    lines += _table("Before 2026", "before", summary)
    lines += _table("2026", "after", summary)
    lines += _reading(summary)
    Path(args.results).write_text("\n".join(lines) + "\n")
    print(f"events {len(events)} through {last} markets {len(ohlc)}", flush=True)
    for name in ORDER:
        after = summary[name]["after"]
        before = summary[name]["before"]
        print(
            f"{name:14} before n={_cell(before, 'n'):>7} "
            f"hi={_cell(before, 'high_median'):>6} "
            f"p50={_cell(before, 'p_high_boom'):>6}  "
            f"2026 n={_cell(after, 'n'):>6} hi={_cell(after, 'high_median'):>6} "
            f"p50={_cell(after, 'p_high_boom'):>6} "
            f"close={_cell(after, 'close_median'):>6}",
            flush=True,
        )


if __name__ == "__main__":
    main()
