"""Bear-market sleeve lab: what makes money when the RS sleeve is in cash?

    .venv/bin/python -m bot.research.bear_sleeve            # cached candles + fresh tail
    .venv/bin/python -m bot.research.bear_sleeve --no-fresh # cache only

Three families on the desk universe, EUR 20k book, fees and perp funding modelled:
outright shorts (BTC or weakest alts, perps), the dollar-neutral long-BTC /
short-weakest pair, and long-spot capitulation bounces. Every family is shown on a
single clock and, for the candidates, phase-averaged (staggered tranches) so that
start-day luck cannot carry the verdict. Writes artifacts/bear_sleeve_lab.json;
see RESULTS.md for the reading.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.bear_sleeve.engine import (
    PHASE_PROBE,
    Frame,
    Result,
    phase_average,
    run,
    single_clock_specs,
    staggered_specs,
)
from bot.research.factor_scoreboard.__main__ import load_candles
from bot.research.factor_scoreboard.external import funding_daily_mean, funding_history

YEARS = ("2024", "2025", "2026")


def _row(r: Result) -> dict[str, Any]:
    return {
        "name": r.name,
        "pnl_eur": r.pnl_eur,
        "max_dd_eur": r.max_dd_eur,
        "max_dd_pct": r.max_dd_pct,
        "n_trades": r.n_trades,
        "days_active": r.days_active,
        "days_total": r.days_total,
        "funding_eur": r.funding_eur,
        "fees_eur": r.fees_eur,
        "by_year": r.by_year,
    }


def _line(r: Result) -> str:
    cells = " ".join(
        f"{y_.get('pnl_eur', 0):8.0f} {y_.get('max_dd_eur', 0):6.0f}"
        for y_ in (r.by_year.get(y, {}) for y in YEARS)
    )
    return (
        f"{r.name:34s} {cells} | {r.pnl_eur:8.0f} {r.max_dd_pct * 100:5.1f}% "
        f"{r.n_trades:5d} {r.days_active:4d}/{r.days_total} {r.funding_eur:5.0f} {r.fees_eur:5.0f}"
    )


def regime_table(ohlc: Any, start: str, end: str, universe: list[str]) -> dict[str, Any]:
    """Per calendar year: BTC return, median desk-alt return, days BTC < SMA50."""
    frame = Frame.build(ohlc)
    out: dict[str, Any] = {}
    for y in YEARS:
        idx = [i for i, d in enumerate(frame.dates) if d[:4] == y and start <= d <= end]
        if len(idx) < 2:
            continue
        a, b = idx[0], idx[-1]
        rets = {
            base: c1 / c0 - 1.0
            for base in ("BTC", *universe)
            if (c0 := frame.close[base][a]) and (c1 := frame.close[base][b])
        }
        alts = sorted(r for base, r in rets.items() if base != "BTC")
        below = sum(
            1
            for i in idx
            if (s := frame.sma("BTC", i, 50)) is not None
            and frame.close["BTC"][i]
            and frame.close["BTC"][i] < s
        )
        out[y] = {
            "btc_ret": round(rets.get("BTC", 0.0), 4),
            "alt_median_ret": round(alts[len(alts) // 2], 4) if alts else None,
            "btc_below_sma50_days": below,
            "days": len(idx),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-03-16")
    ap.add_argument("--end", default="2026-10-14")
    ap.add_argument("--book", type=float, default=20_000.0)
    ap.add_argument("--no-fresh", action="store_true", help="do not merge fresh Bitvavo candles")
    ap.add_argument("--out", default="artifacts/bear_sleeve_lab.json")
    a = ap.parse_args()
    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    ohlc = load_candles(Path("data/residual_wet_candles"), merge_fresh=not a.no_fresh)
    universe = [b for b in DEFAULT_UNIVERSE if b in ohlc]
    fund = funding_daily_mean(funding_history(("BTC", *DEFAULT_UNIVERSE)))
    log(f"{len(ohlc)} names, desk universe {len(universe)}, funding for {len(fund)} names")

    regimes = regime_table(ohlc, a.start, a.end, universe)
    for y, r in regimes.items():
        log(
            f"{y}: BTC {r['btc_ret'] * 100:+.1f}%  alt median {r['alt_median_ret'] * 100:+.1f}%  "
            f"BTC<SMA50 {r['btc_below_sma50_days']}/{r['days']} days"
        )

    kw = {"start": a.start, "end": a.end, "book_eur": a.book, "funding": fund, "universe": universe}
    header = (
        f"{'name':34s} {'2024':>8s} {'dd':>6s} {'2025':>8s} {'dd':>6s} {'2026':>8s} {'dd':>6s} "
        f"| {'total':>8s} {'maxDD':>6s} trades active  fund  fees"
    )
    log("single clock")
    print(header)
    single = []
    for spec in single_clock_specs():
        r = run(ohlc, spec, **kw)
        single.append(_row(r))
        print(_line(r))

    log(f"phase sweep for {PHASE_PROBE.name}")
    frame = Frame.build(ohlc)
    first = next(i for i, d in enumerate(frame.dates) if d >= a.start)
    phases = []
    for k in range(PHASE_PROBE.rebalance_days):
        r = run(ohlc, PHASE_PROBE, **{**kw, "start": frame.dates[first + k]})
        phases.append({"start": frame.dates[first + k], **_row(r)})
        print(f"start {frame.dates[first + k]} " + _line(r))

    log("staggered tranches (phase-averaged)")
    print(header)
    staggered = []
    for spec in staggered_specs():
        r = phase_average(ohlc, spec, **kw)
        staggered.append(_row(r))
        print(_line(r))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "start": a.start,
                "end": a.end,
                "book_eur": a.book,
                "universe": universe,
                "regimes": regimes,
                "single_clock": single,
                "phase_sweep": {"spec": PHASE_PROBE.name, "rows": phases},
                "staggered": staggered,
            },
            indent=1,
        )
    )
    log(f"wrote {out}")


if __name__ == "__main__":
    main()
