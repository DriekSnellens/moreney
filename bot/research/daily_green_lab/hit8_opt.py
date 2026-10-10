"""Neighborhood search around the accurate 3-day sleeve.

The hit rate stays the share of taken trades whose high reaches +8% within
three days of the next open. Entry and exit may move. A candidate is eligible
only when that hit rate is at least the baseline on both 2024 and 2025, and
both of those windows make money. The winner maximizes 2024+2025 PnL.
2026 is scored once.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from bot.research.daily_green_lab.hit8_precision import (
    BOOK_EUR,
    build_frame,
    split_masks,
)
from bot.research.daily_green_lab.tp8 import FEE, SLIP, resolve_from_entry
from bot.research.moonshot_preimage.day50 import load_series

R3_GRID = (0.0, 0.03, 0.05, 0.08, 0.15)
UP_GRID = (0.06, 0.08, 0.10, 0.12)
XS_GRID = (0.15, 0.20, 0.25, 0.30, 0.40)
TP_GRID = (0.06, 0.08, 0.10, 0.12, 0.15, 0.20)
HARD_GRID = (0.0, 0.03, 0.05, 0.08, 0.10)
HOLD_GRID = (2, 3, 4, 5, 8)
MIN_FIT = 20
MIN_SEL = 40
BASELINE = {
    "r3_max": 0.05,
    "up_min": 0.08,
    "xs_min": 0.25,
    "tp": 0.08,
    "hard": 0.05,
    "hold": 3,
}


@dataclass(frozen=True)
class Spec:
    r3_max: float
    up_min: float
    xs_min: float
    tp: float
    hard: float
    hold: int

    def label(self) -> str:
        return (
            f"r3<={self.r3_max:.2f} up>={self.up_min:.2f} xs>={self.xs_min:.2f} "
            f"tp={self.tp:.0%} stop={self.hard:.0%} {self.hold}d"
        )


@dataclass(frozen=True)
class Row:
    spec: Spec
    fit_n: int
    fit_p: float
    fit_pnl: float
    sel_n: int
    sel_p: float
    sel_pnl: float


def choose(rows: list[Row], *, fit_floor: float, sel_floor: float) -> Row | None:
    """Highest pre-2026 PnL among sleeves that stay at least as accurate."""
    eligible = [
        row
        for row in rows
        if row.fit_n >= MIN_FIT
        and row.sel_n >= MIN_SEL
        and row.fit_p + 1e-12 >= fit_floor
        and row.sel_p + 1e-12 >= sel_floor
        and row.fit_pnl > 0
        and row.sel_pnl > 0
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row.fit_pnl + row.sel_pnl, row.sel_p, row.fit_p))


def _exit(ser_o, ser_h, ser_l, ser_c, ser_d, i: int, hold: int, tp: float, hard: float):
    if i + 1 >= len(ser_c) or i + hold >= len(ser_c):
        return None
    raw = ser_o[i + 1]
    if raw <= 0:
        return None
    fill = raw * (1.0 + SLIP)
    last = i + hold
    for k in range(i + 1, last + 1):
        got = resolve_from_entry(
            entry=fill,
            o=ser_o[k],
            h=ser_h[k],
            l=ser_l[k],
            c=ser_c[k],
            hard=hard,
            tp=tp,
            slip=SLIP,
        )
        if got is not None:
            return got[1] / fill - 1.0 - 2.0 * FEE, ser_d[k]
    px = ser_c[last] * (1.0 - SLIP)
    return px / fill - 1.0 - 2.0 * FEE, ser_d[last]


def _sleeve(
    date: np.ndarray,
    base: np.ndarray,
    rank: np.ndarray,
    ret: np.ndarray,
    exit_d: np.ndarray,
    hit: np.ndarray,
    mask: np.ndarray,
) -> dict[str, float]:
    idx = np.flatnonzero(mask)
    best: dict[str, int] = {}
    for i in idx.tolist():
        day = str(date[i])
        prev = best.get(day)
        if prev is None:
            best[day] = i
            continue
        ri, rp = float(rank[i]), float(rank[prev])
        if ri > rp or (ri == rp and str(base[i]) < str(base[prev])):
            best[day] = i
    taken: list[int] = []
    lock = ""
    for day in sorted(best):
        if lock and day < lock:
            continue
        i = best[day]
        if not exit_d[i]:
            continue
        taken.append(i)
        lock = str(exit_d[i])
    n = len(taken)
    if n == 0:
        return {"n": 0, "p": 0.0, "pnl": 0.0}
    hits = hit[taken]
    rets = ret[taken]
    return {"n": n, "p": float(hits.mean()), "pnl": float(rets.sum()) * BOOK_EUR}


def _prepare(candle_dir: str):
    series = load_series(candle_dir)
    frame = build_frame(series)
    r3 = frame.col("r3")
    up = frame.col("up_vol")
    xs = frame.col("xs10")
    loose = (r3 <= max(R3_GRID)) & (up >= min(UP_GRID)) & (xs >= min(XS_GRID))
    loc = {(b, d): i for b, ser in series.items() for i, d in enumerate(ser.dates)}
    by_base = {b: (ser.o, ser.h, ser.l, ser.c, ser.dates) for b, ser in series.items()}
    rows = np.flatnonzero(loose)
    n = len(rows)
    date = frame.date[rows]
    base = frame.base[rows]
    rank = xs[rows]
    r3v, upv, xsv = r3[rows], up[rows], xs[rows]
    hit = frame.y_up[3][rows]
    series_i = np.array([loc[(str(base[j]), str(date[j]))] for j in range(n)], dtype=np.int32)
    fit, sel, test = split_masks(date)
    return {
        "date": date,
        "base": base,
        "rank": rank,
        "r3": r3v,
        "up": upv,
        "xs": xsv,
        "hit": hit,
        "series_i": series_i,
        "by_base": by_base,
        "fit": fit,
        "sel": sel,
        "test": test,
    }


def _exit_cache(prep: dict[str, Any], tp: float, hard: float, hold: int):
    n = len(prep["date"])
    ret = np.zeros(n, dtype=np.float64)
    exit_d = np.empty(n, dtype=object)
    ok = np.zeros(n, dtype=bool)
    by_base = prep["by_base"]
    series_i = prep["series_i"]
    base = prep["base"]
    for j in range(n):
        o, h, l, c, d = by_base[str(base[j])]
        got = _exit(o, h, l, c, d, int(series_i[j]), hold, tp, hard)
        if got is None:
            exit_d[j] = ""
            continue
        ret[j], exit_d[j] = got
        ok[j] = True
    return ret, exit_d, ok


def run(*, candle_dir: str = "data/ignition_expand_candles") -> dict[str, Any]:
    prep = _prepare(candle_dir)
    exits = [(tp, hard, hold) for tp in TP_GRID for hard in HARD_GRID for hold in HOLD_GRID]
    cache: dict[tuple[float, float, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for tp, hard, hold in exits:
        cache[(tp, hard, hold)] = _exit_cache(prep, tp, hard, hold)

    def stats_for(spec: Spec) -> dict[str, dict[str, float]]:
        ret, exit_d, ok = cache[(spec.tp, spec.hard, spec.hold)]
        mask = ok & (prep["r3"] <= spec.r3_max) & (prep["up"] >= spec.up_min) & (prep["xs"] >= spec.xs_min)
        out = {}
        for name, window in ("fit", prep["fit"]), ("select", prep["sel"]):
            out[name] = _sleeve(
                prep["date"], prep["base"], prep["rank"], ret, exit_d, prep["hit"], mask & window
            )
        return out

    baseline = Spec(**BASELINE)
    base_stats = stats_for(baseline)
    rows: list[Row] = []
    specs = [
        Spec(r3, up, xs, tp, hard, hold)
        for r3 in R3_GRID
        for up in UP_GRID
        for xs in XS_GRID
        for tp in TP_GRID
        for hard in HARD_GRID
        for hold in HOLD_GRID
    ]
    for spec in specs:
        st = stats_for(spec)
        rows.append(
            Row(
                spec=spec,
                fit_n=int(st["fit"]["n"]),
                fit_p=float(st["fit"]["p"]),
                fit_pnl=float(st["fit"]["pnl"]),
                sel_n=int(st["select"]["n"]),
                sel_p=float(st["select"]["p"]),
                sel_pnl=float(st["select"]["pnl"]),
            )
        )
    winner = choose(rows, fit_floor=base_stats["fit"]["p"], sel_floor=base_stats["select"]["p"])
    chosen = winner.spec if winner is not None else baseline

    def with_test(spec: Spec, known: dict[str, dict[str, float]]) -> dict[str, Any]:
        ret, exit_d, ok = cache[(spec.tp, spec.hard, spec.hold)]
        mask = (
            ok
            & (prep["r3"] <= spec.r3_max)
            & (prep["up"] >= spec.up_min)
            & (prep["xs"] >= spec.xs_min)
        )
        test_stats = _sleeve(
            prep["date"], prep["base"], prep["rank"], ret, exit_d, prep["hit"], mask & prep["test"]
        )
        return {"spec": spec.label(), "fit": known["fit"], "select": known["select"], "test": test_stats}

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "n_specs": len(specs),
        "baseline": with_test(baseline, base_stats),
        "winner": with_test(chosen, stats_for(chosen)),
        "kept_accuracy": winner is not None,
    }
    path = Path(__file__).resolve().parent / "HIT8_OPT.md"
    path.write_text(_markdown(payload), encoding="utf-8")
    return payload


def _fmt_pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def _line(stats: dict[str, float]) -> str:
    return f"{_fmt_pct(float(stats['p']))} · n={int(stats['n'])} · €{float(stats['pnl']):+,.0f}"


def _markdown(payload: dict[str, Any]) -> str:
    b = payload["baseline"]
    w = payload["winner"]
    lines = [
        "# Entry/exit rond de trefzekere sleeve",
        "",
        f"asof `{payload['asof']}`  specs `{payload['n_specs']}`",
        "",
        "Trefzekerheid = aandeel trades waarvan de high binnen 3 dagen +8% boven de opening komt.",
        "Een kandidaat telt alleen mee als die lat op 2024 én op 2025 minstens zo hoog blijft als de basis, en beide vensters groen zijn.",
        "Gekozen op de som van de 2024- en 2025-PnL. 2026 is één meting.",
        "",
        f"Basis: `{b['spec']}`",
        "",
        "| Venster | Basis |",
        "|---|---|",
        f"| 2024 | {_line(b['fit'])} |",
        f"| 2025 | {_line(b['select'])} |",
        f"| 2026 | {_line(b['test'])} |",
        "",
        f"Hoogste PnL met minstens die trefzekerheid: `{w['spec']}`",
        "",
        "| Venster | Gekozen |",
        "|---|---|",
        f"| 2024 | {_line(w['fit'])} |",
        f"| 2025 | {_line(w['select'])} |",
        f"| 2026 | {_line(w['test'])} |",
        "",
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab.hit8_opt",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Optimize entry and exit around the accurate sleeve")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    args = p.parse_args()
    payload = run(candle_dir=args.candles)
    w = payload["winner"]
    print(w["spec"])
    for key in ("fit", "select", "test"):
        st = w[key]
        print(f"  {key} p={st['p']:.3f} n={st['n']} pnl={st['pnl']:+.0f}")


if __name__ == "__main__":
    main()
