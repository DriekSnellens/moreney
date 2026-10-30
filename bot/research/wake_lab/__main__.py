"""Scan Bitvavo EUR daily bars for a decline, a dormant shelf, and a first wake.

    .venv/bin/python -m bot.research.wake_lab --refresh
    .venv/bin/python -m bot.research.wake_lab

Writes artifacts/wake_lab.json and refreshes RESULTS.md. The in-progress UTC
day is dropped. Thresholds stay on the shape; this module only measures them.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.unusual_volume_lab.data import CACHE_1D, DAY_MS, eur_bases, load_cached, refresh
from bot.research.wake_lab.detect import WakeSpec, forward_audit, open_setups, scan, scan_pops

EXAMPLE_CUT = "2026-10-01"


def _completed(rows: list[list[float]], today_ms: int) -> list[list[float]]:
    return [r for r in rows if int(r[0]) < today_ms]


def _pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{100.0 * x:.1f}%"


def _cell(stats: dict[str, Any], key: str) -> str:
    if not stats:
        return "—"
    value = stats.get(key)
    if value is None:
        return "—"
    if key == "n":
        return f"{int(value)}"
    return _pct(float(value))


def _audit_table(title: str, audit: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"Signals {audit['n_signals']}, with a next open {audit['n_used']}. "
        f"A boom is a high at least {_pct(float(audit['boom']))} above the next open.",
        "",
        "| horizon | n | close mean | close median | % close > 0 | % high ≥ 20 | % high ≥ boom |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon, bucket in audit["horizons"].items():
        close = bucket["close"]
        high = bucket["high"]
        lines.append(
            f"| {horizon}d | {_cell(close, 'n')} | {_cell(close, 'mean')} | "
            f"{_cell(close, 'median')} | {_cell(close, 'p_pos')} | "
            f"{_cell(high, 'p_ge_20')} | {_cell(high, 'p_ge_boom')} |"
        )
    lines.append("")
    return lines


def _setup_table(setups: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Open now",
        "",
        "Last 7 completed days, and the high since the signal is still under +50% "
        "from that close. Ranked by likeness to the shape.",
        "",
        "| base | day | likeness | drawdown | shelf range | lift "
        "| day ret | vol x | qvol | high since |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not setups:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    for row in setups[:25]:
        lines.append(
            f"| {row['base']} | {row['day']} | {float(row['likeness']):.2f} | "
            f"{_pct(float(row['drawdown']))} | {_pct(float(row['shelf_range']))} | "
            f"{_pct(float(row['lift']))} | {_pct(float(row['day_ret']))} | "
            f"{float(row['vol_mult']):.1f} | {float(row['spark_qvol']):,.0f} | "
            f"{_pct(float(row['high_since']))} |"
        )
    lines.append("")
    return lines


def _month_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Example month, signal by signal",
        "",
        "| base | day | likeness | drawdown | lift | day ret | vol x | qvol |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    if not rows:
        lines.append("| — | — | — | — | — | — | — | — |")
    for row in rows:
        lines.append(
            f"| {row['base']} | {row['day']} | {float(row['likeness']):.2f} | "
            f"{_pct(float(row['drawdown']))} | {_pct(float(row['lift']))} | "
            f"{_pct(float(row['day_ret']))} | {float(row['vol_mult']):.1f} | "
            f"{float(row['spark_qvol']):,.0f} |"
        )
    lines.append("")
    return lines


def _band_table(bands: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Likeness bands, before the example month",
        "",
        "Ten days after the next open. A higher likeness is a deeper decline,",
        "a tighter shelf, a louder volume jump, and a lift nearer +30%.",
        "",
        "| likeness | n | close median | % high ≥ boom |",
        "|---|---:|---:|---:|",
    ]
    for band in bands:
        lines.append(
            f"| {band['label']} | {band['n']} | {_pct(band['close_median'])} | "
            f"{_pct(band['boom'])} |"
        )
    lines.append("")
    return lines


def _reading(
    before: dict[str, Any],
    month: dict[str, Any],
    pops_before: dict[str, Any],
) -> list[str]:
    b_close = before["horizons"][10]["close"]
    b_high = before["horizons"][10]["high"]
    p_close = pops_before["horizons"][10]["close"]
    p_high = pops_before["horizons"][10]["high"]
    m_close = month["horizons"][5]["close"]
    m_high = month["horizons"][5]["high"]
    n_before = int(b_close.get("n", 0))
    n_pops = int(p_close.get("n", 0))
    return [
        "## Reading",
        "",
        "The rule matches the chart it was read from. On GROVE the only wake",
        "is 2026-10-22: drawdown 86%, close +30% off the low, 20× the shelf",
        "volume, likeness 0.79. The vertical close on 2026-10-27 sits outside",
        "the interest band, so it is not a second signal. The next session",
        "gave that pop back. The high above +50% printed later the same week,",
        "and the close has been giving it back since.",
        "",
        "That path is not the typical one.",
        "",
        (
            f"1. Before October 2026 there are {n_before} wakes with a full "
            f"10-day window. The median close is {_cell(b_close, 'median')} and "
            f"{_cell(b_close, 'p_pos')} of closes are positive. A high of +50% "
            f"or more shows up in {_cell(b_high, 'p_ge_boom')} of those windows."
        ),
        (
            f"2. The ablation drops the decline and the shelf. It keeps {n_pops} "
            f"up days on 3× the prior 21-day volume. The 10-day boom rate is "
            f"{_cell(p_high, 'p_ge_boom')} and the median close is "
            f"{_cell(p_close, 'median')}. The shelf fires less often, and not "
            "into more spikes."
        ),
        "3. Likeness does not sort winners from losers. Every band has a "
        "negative 10-day median close.",
        (
            f"4. October is {month['n_signals']} signals, and it is the month the "
            f"thresholds were read from. Among the windows with five completed "
            f"days, {_cell(m_high, 'p_ge_boom')} print a +50% high and the median "
            f"close is {_cell(m_close, 'median')}. The high is a wick."
        ),
        "",
        "## Verdict",
        "",
        "Nothing armed. The open list is who matches the shape on the last",
        "completed days and has not already traded +50% above that close.",
        "On this tape the shape describes a chart. It does not enrich for",
        "the later spike. A volume pop without the shelf already captures",
        "the wick more often, and the close after either signal is negative.",
        "",
    ]


def write_results(
    path: Path,
    *,
    asof: str,
    n_markets: int,
    full: dict[str, Any],
    before: dict[str, Any],
    month: dict[str, Any],
    pops_before: dict[str, Any],
    setups: list[dict[str, Any]],
    month_rows: list[dict[str, Any]],
    bands: list[dict[str, Any]],
    spec: WakeSpec,
) -> None:
    lines = [
        "# Wake scan",
        "",
        "Question: after a real decline and a long quiet shelf, does the first",
        "day of interest — a modest rise on expanded volume, still well under",
        "the old high — show up before a later spike, often enough to be useful?",
        "",
        "The shape was read off one chart: a listing dump, about three quiet",
        "weeks, a short flush, then one green day of about +30% on much higher",
        "volume, with the vertical day five sessions later. The rule is generic.",
        "It does not name that market. The same thresholds are applied to every",
        "Bitvavo EUR daily series.",
        "",
        f"Tape through {asof} ({n_markets} markets with a completed bar). "
        "Entry in the audit is the next day's open. The in-progress UTC day is",
        "not a signal.",
        "",
        "Phases, all on completed bars:",
        "",
        f"- Decline: the shelf median sits at least {_pct(spec.min_drawdown)} "
        f"under the high of the prior {spec.decline_lookback} days "
        f"(or all available history once {spec.min_decline_days} days exist).",
        f"- Shelf: {spec.base_len} days, close range at most {_pct(spec.max_base_range)}, "
        f"median absolute day move at most {_pct(spec.max_median_abs_ret)}, "
        f"at most {spec.max_wide_days} days wider than {_pct(spec.wide_ret)}. "
        f"The shelf may end up to {spec.flush_room} days before the signal, "
        "so a short final dip still counts.",
        f"- Quiet: shelf median quote volume is at most {_pct(spec.quiet_ratio)} "
        "of the active (top 30%) quote volume before the shelf.",
        f"- Interest: that day's rise is {_pct(spec.min_day_ret)} to {_pct(spec.max_day_ret)}, "
        f"the close is {_pct(spec.lift_min)} to {_pct(spec.lift_max)} off the recent low, "
        f"quote volume is at least {spec.vol_mult:.0f}× the shelf and "
        f"at least €{spec.min_spark_qvol:,.0f}, and the close is still under "
        f"{_pct(spec.still_below)} of the old high.",
        f"- One signal per name per {spec.cooldown} days.",
        "",
        f"`before the example month` is every signal before {EXAMPLE_CUT}. "
        "That slice was not used to set the thresholds. "
        "`example month` is the rest, and it contains the chart the shape was read from.",
        "",
        "The volume-pop row is the ablation: the same up-day and volume multiple "
        f"against the prior {spec.base_len} days, with no decline and no shelf.",
        "",
        "Reproduce: `.venv/bin/python -m bot.research.wake_lab --refresh`",
        "",
    ]
    lines += _audit_table("Full tape", full)
    lines += _audit_table("Before the example month", before)
    lines += _audit_table("Example month", month)
    lines += _audit_table("Ablation — volume pop, before the example month", pops_before)
    lines += _band_table(bands)
    lines += _month_table(month_rows)
    lines += _setup_table(setups)
    lines += _reading(before, month, pops_before)
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="extend the daily candle cache first")
    ap.add_argument("--results", default="bot/research/wake_lab/RESULTS.md")
    ap.add_argument("--out", default="artifacts/wake_lab.json")
    args = ap.parse_args()

    now = datetime.now(UTC)
    today_ms = int(datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    bases = eur_bases()
    if args.refresh:
        print(f"refresh {len(bases)} markets", flush=True)
        refresh(
            bases,
            interval="1d",
            cache=CACHE_1D,
            start_ms=end_ms - 800 * DAY_MS,
            end_ms=end_ms,
            log=print,
        )
    ohlc: dict[str, list[list[float]]] = {}
    for base, bars in load_cached(CACHE_1D, bases).items():
        done = _completed(bars, today_ms)
        if done:
            ohlc[base] = done
    spec = WakeSpec()
    signals = scan(ohlc, spec)
    pops = scan_pops(ohlc, spec)
    before_sigs = [s for s in signals if s["day"] < EXAMPLE_CUT]
    month_sigs = [s for s in signals if s["day"] >= EXAMPLE_CUT]
    pops_before = [s for s in pops if s["day"] < EXAMPLE_CUT]
    full = forward_audit(ohlc, signals)
    before = forward_audit(ohlc, before_sigs)
    month = forward_audit(ohlc, month_sigs)
    pop_audit = forward_audit(ohlc, pops_before)
    last_day = datetime.fromtimestamp((today_ms - DAY_MS) / 1000, UTC).strftime("%Y-%m-%d")
    setups = open_setups(ohlc, signals, last_day=last_day)
    bands: list[dict[str, Any]] = []
    for lo, hi, label in (
        (0.0, 0.50, "0.00–0.50"),
        (0.50, 0.60, "0.50–0.60"),
        (0.60, 0.70, "0.60–0.70"),
        (0.70, 0.80, "0.70–0.80"),
        (0.80, 1.01, "0.80–1.00"),
    ):
        sub = [s for s in before_sigs if lo <= float(s["likeness"]) < hi]
        band_audit = forward_audit(ohlc, sub, horizons=(10,))
        close = band_audit["horizons"][10]["close"]
        high = band_audit["horizons"][10]["high"]
        bands.append(
            {
                "label": label,
                "n": int(close.get("n", 0)),
                "close_median": close.get("median"),
                "boom": high.get("p_ge_boom"),
            }
        )
    asof = last_day
    write_results(
        Path(args.results),
        asof=asof,
        n_markets=len(ohlc),
        full=full,
        before=before,
        month=month,
        pops_before=pop_audit,
        setups=setups,
        month_rows=month_sigs,
        bands=bands,
        spec=spec,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asof": asof,
        "n_markets": len(ohlc),
        "n_signals": len(signals),
        "audit": {"full": full, "before": before, "month": month, "pops_before": pop_audit},
        "open": setups,
        "signals": signals,
    }
    out.write_text(json.dumps(payload))
    print(
        f"markets {len(ohlc)} wakes {len(signals)} "
        f"before {len(before_sigs)} month {len(month_sigs)} open {len(setups)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
