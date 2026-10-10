"""Search for a causal +8% call with at least 70% precision.

Label, from the signal-day close: buy the next open. A hit is the forward
high within the horizon, divided by that open, at or above +8%. Features on
the signal close are known then. The next-open gap is known at the fill.

Splits follow the moonshot precision lab:
  fit    ≤ 2024-12-31   — where rules and the score are learned
  select 2025          — where the operating point is chosen
  test   ≥ 2026-01-01  — scored once, after the choice

A 70% claim needs the hit rate on fit, on select, and on test. Select does
not get to pick a different rule after seeing 2026.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

from bot.research.daily_green_lab.tp8 import FEE, SLIP, resolve_from_entry
from bot.research.moonshot_preimage.day50 import Series, _ret_at, _sma_at, load_series
from bot.research.moonshot_preimage.engine import _wilson

FIT_END = "2024-12-31"
SEL_END = "2025-12-31"
HORIZONS = (1, 3, 5)
TARGET = 0.08
MIN_N = 80
MIN_QVOL = 50_000.0
HARD = 0.05
BOOK_EUR = 2_000.0
MIN_FIT_TRADES = 12
MIN_SEL_TRADES = 20

FEATURES = (
    "r1",
    "r3",
    "r5",
    "r10",
    "xs3",
    "xs10",
    "volx",
    "rngx",
    "dayloc",
    "loc20",
    "brk20",
    "trend",
    "btc_on",
    "gap",
    "coil",
    "dist_hi",
    "up_vol",
    "day_up",
    "streak",
    "btc_r1",
    "sma_dist20",
)


@dataclass
class Frame:
    date: np.ndarray
    base: np.ndarray
    x: np.ndarray
    names: tuple[str, ...]
    y_up: dict[int, np.ndarray]
    y_close: dict[int, np.ndarray]
    y_tp: dict[int, np.ndarray]
    ret: dict[int, np.ndarray]
    exit_date: dict[int, np.ndarray]

    def col(self, name: str) -> np.ndarray:
        return self.x[:, self.names.index(name)]


@dataclass
class Cand:
    name: str
    horizon: int
    label: str
    mask: np.ndarray
    fit_n: int
    fit_p: float
    fit_w: float
    sel_n: int
    sel_p: float
    sel_w: float
    extra: dict[str, Any] = field(default_factory=dict)


def _clip(v: float, lo: float, hi: float) -> float:
    return float(min(hi, max(lo, v)))


def _trade_ret(ser: Series, i: int, horizon: int) -> tuple[bool, float, int]:
    """Take-profit at +8% before a 5% stop, else the horizon close.

    Entry is the next open plus slippage, matching the TP8 sleeve.
    The third value is the exit bar index inside ``ser``.
    """
    raw = ser.o[i + 1]
    fill = raw * (1.0 + SLIP)
    last = i + horizon
    for k in range(i + 1, last + 1):
        got = resolve_from_entry(
            entry=fill,
            o=ser.o[k],
            h=ser.h[k],
            l=ser.l[k],
            c=ser.c[k],
            hard=HARD,
            tp=TARGET,
            slip=SLIP,
        )
        if got is not None:
            reason, px = got
            return reason == "take_profit", px / fill - 1.0 - 2.0 * FEE, k
    exit_px = ser.c[last] * (1.0 - SLIP)
    return False, exit_px / fill - 1.0 - 2.0 * FEE, last


def build_frame(series: dict[str, Series], *, min_qvol: float = MIN_QVOL) -> Frame:
    if "BTC" not in series:
        raise ValueError("BTC candles required")
    btc = series["BTC"]
    btc_r3: dict[str, float] = {}
    btc_r10: dict[str, float] = {}
    btc_r1: dict[str, float] = {}
    btc_on: set[str] = set()
    for i, day in enumerate(btc.dates):
        r1 = _ret_at(btc.c, i, 1)
        r3 = _ret_at(btc.c, i, 3)
        r10 = _ret_at(btc.c, i, 10)
        if r1 is not None:
            btc_r1[day] = float(r1)
        if r3 is not None:
            btc_r3[day] = float(r3)
        if r10 is not None:
            btc_r10[day] = float(r10)
        sma = _sma_at(btc.c, i, 50)
        if sma is not None and btc.c[i] > sma:
            btc_on.add(day)

    max_h = max(HORIZONS)
    dates: list[str] = []
    bases: list[str] = []
    rows: list[list[float]] = []
    y_up = {h: [] for h in HORIZONS}
    y_close = {h: [] for h in HORIZONS}
    y_tp = {h: [] for h in HORIZONS}
    ret = {h: [] for h in HORIZONS}
    exit_date = {h: [] for h in HORIZONS}
    for base, ser in series.items():
        if base == "BTC":
            continue
        n = len(ser.dates)
        for i in range(55, n - max_h):
            day = ser.dates[i]
            if day not in btc_r10 or day not in btc_r1:
                continue
            raw_entry = ser.o[i + 1]
            close = ser.c[i]
            if close <= 0 or raw_entry <= 0 or ser.o[i] <= 0:
                continue
            rets = {k: _ret_at(ser.c, i, k) for k in (1, 3, 5, 10)}
            if any(v is None for v in rets.values()):
                continue
            vol_ma = _sma_at(ser.v, i, 20) or 0.0
            volx = (ser.v[i] / vol_ma) if vol_ma > 0 else 0.0
            if ser.v[i] * close < min_qvol:
                continue
            s20 = _sma_at(ser.c, i, 20)
            s50 = _sma_at(ser.c, i, 50)
            if s20 is None or s50 is None or s20 <= 0:
                continue
            hi, lo = ser.h[i], ser.l[i]
            dayloc = ((close - lo) / (hi - lo)) if hi > lo else 0.5
            rng = (hi - lo) / close if close > 0 else 0.0
            rngs = [
                (ser.h[k] - ser.l[k]) / ser.c[k]
                for k in range(i - 19, i + 1)
                if ser.c[k] > 0
            ]
            rng_ma = sum(rngs) / len(rngs) if rngs else 0.0
            rngx = (rng / rng_ma) if rng_ma > 0 else 0.0
            lo20 = min(ser.l[i - 19 : i + 1])
            hi20 = max(ser.h[i - 19 : i + 1])
            loc20 = ((close - lo20) / (hi20 - lo20)) if hi20 > lo20 else 0.5
            prior_hi = max(ser.h[i - 20 : i])
            brk20 = 1.0 if prior_hi > 0 and close >= prior_hi else 0.0
            trend = 1.0 if close > s20 > s50 else 0.0
            gap = raw_entry / close - 1.0
            span5_hi, span5_lo = max(ser.h[i - 4 : i + 1]), min(ser.l[i - 4 : i + 1])
            span5 = span5_hi / span5_lo - 1.0 if span5_lo > 0 else 0.0
            span20 = hi20 / lo20 - 1.0 if lo20 > 0 else 0.0
            coil = span5 / span20 if span20 > 1e-9 else 1.0
            dist_hi = close / prior_hi - 1.0 if prior_hi > 0 else 0.0
            ups = [
                ser.h[k] / ser.o[k] - 1.0
                for k in range(i - 19, i + 1)
                if ser.o[k] > 0
            ]
            up_vol = sum(ups) / len(ups) if ups else 0.0
            day_up = hi / ser.o[i] - 1.0
            streak = 0
            for k in range(i, i - 6, -1):
                if k <= 0 or ser.c[k] <= ser.c[k - 1]:
                    break
                streak += 1
            dates.append(day)
            bases.append(base)
            rows.append(
                [
                    _clip(float(rets[1]), -0.8, 2.0),
                    _clip(float(rets[3]), -0.9, 3.0),
                    _clip(float(rets[5]), -0.9, 4.0),
                    _clip(float(rets[10]), -0.9, 5.0),
                    _clip(float(rets[3]) - btc_r3[day], -1.0, 3.0),
                    _clip(float(rets[10]) - btc_r10[day], -1.5, 5.0),
                    _clip(volx, 0.0, 20.0),
                    _clip(rngx, 0.0, 10.0),
                    _clip(dayloc, 0.0, 1.0),
                    _clip(loc20, 0.0, 1.0),
                    brk20,
                    trend,
                    1.0 if day in btc_on else 0.0,
                    _clip(gap, -0.5, 1.0),
                    _clip(coil, 0.0, 2.0),
                    _clip(dist_hi, -0.9, 1.0),
                    _clip(up_vol, 0.0, 1.0),
                    _clip(day_up, 0.0, 1.5),
                    float(streak),
                    _clip(btc_r1[day], -0.4, 0.4),
                    _clip(close / s20 - 1.0, -0.8, 1.5),
                ]
            )
            for h in HORIZONS:
                fwd_h = max(ser.h[i + 1 : i + 1 + h]) / raw_entry - 1.0
                fwd_c = max(ser.c[i + 1 : i + 1 + h]) / raw_entry - 1.0
                tp, pnl, exit_i = _trade_ret(ser, i, h)
                y_up[h].append(fwd_h >= TARGET)
                y_close[h].append(fwd_c >= TARGET)
                y_tp[h].append(tp)
                ret[h].append(pnl)
                exit_date[h].append(ser.dates[exit_i])
    return Frame(
        date=np.asarray(dates),
        base=np.asarray(bases),
        x=np.asarray(rows, dtype=np.float64),
        names=FEATURES,
        y_up={h: np.asarray(v, dtype=bool) for h, v in y_up.items()},
        y_close={h: np.asarray(v, dtype=bool) for h, v in y_close.items()},
        y_tp={h: np.asarray(v, dtype=bool) for h, v in y_tp.items()},
        ret={h: np.asarray(v, dtype=np.float64) for h, v in ret.items()},
        exit_date={h: np.asarray(v) for h, v in exit_date.items()},
    )


def split_masks(date: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fit = date <= FIT_END
    sel = (date > FIT_END) & (date <= SEL_END)
    test = date > SEL_END
    return fit, sel, test


def _rate(y: np.ndarray, mask: np.ndarray) -> tuple[int, float, float]:
    m = np.asarray(mask, dtype=bool)
    n = int(m.sum())
    if n == 0:
        return 0, 0.0, 0.0
    k = int(np.asarray(y, dtype=bool)[m].sum())
    p = k / n
    return n, p, _wilson(k, n)


def make_atoms(frame: Frame) -> list[tuple[str, np.ndarray]]:
    c = frame.col
    r1, r3 = c("r1"), c("r3")
    xs10, volx, rngx = c("xs10"), c("volx"), c("rngx")
    dayloc, loc20 = c("dayloc"), c("loc20")
    brk, trend, btc = c("brk20"), c("trend"), c("btc_on")
    gap, coil = c("gap"), c("coil")
    dist, up_vol, day_up = c("dist_hi"), c("up_vol"), c("day_up")
    streak, btc_r1, sma = c("streak"), c("btc_r1"), c("sma_dist20")
    out: list[tuple[str, np.ndarray]] = []

    def add(name: str, mask: np.ndarray) -> None:
        out.append((name, np.asarray(mask, dtype=bool)))

    for t in (0.0, 0.03, 0.05, 0.08, 0.12):
        add(f"r1>={t:.2f}", r1 >= t)
    for t in (0.02, 0.08):
        add(f"r1<={t:.2f}", r1 <= t)
    for t in (0.08, 0.15, 0.25):
        add(f"r3>={t:.2f}", r3 >= t)
    add("r3<=0.05", r3 <= 0.05)
    for t in (0.05, 0.15, 0.25):
        add(f"xs10>={t:.2f}", xs10 >= t)
    for t in (1.5, 2.0, 3.0):
        add(f"volx>={t:.1f}", volx >= t)
    add("rngx<=0.80", rngx <= 0.80)
    add("rngx>=1.50", rngx >= 1.5)
    add("dayloc>=0.80", dayloc >= 0.80)
    add("loc20>=0.85", loc20 >= 0.85)
    add("loc20<=0.50", loc20 <= 0.50)
    add("brk20", brk >= 1.0)
    add("trend", trend >= 1.0)
    add("btc_on", btc >= 1.0)
    add("btc_off", btc < 1.0)
    add("gap>=0", gap >= 0.0)
    add("gap<=0.03", gap <= 0.03)
    add("gap_flat", (gap >= -0.01) & (gap <= 0.02))
    add("coil<=0.50", coil <= 0.50)
    add("near_high", dist >= -0.03)
    add("under_high", dist <= -0.10)
    for t in (0.04, 0.06, 0.08, 0.10, 0.12, 0.15):
        add(f"up_vol>={t:.2f}", up_vol >= t)
    for t in (0.05, 0.08, 0.12):
        add(f"day_up>={t:.2f}", day_up >= t)
    add("streak>=2", streak >= 2.0)
    add("above_sma20", sma >= 0.0)
    add("sma20_near", (sma >= 0.0) & (sma <= 0.08))
    add("btc_day_up", btc_r1 >= 0.0)
    add("btc_day_down", btc_r1 <= 0.0)
    return out


def climb_atoms(
    y: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    atoms: list[tuple[str, np.ndarray]],
    *,
    max_adds: int = 4,
    width: int = 12,
) -> list[tuple[str, np.ndarray]]:
    """Grow AND-rules by fit Wilson. Select size is only a veto.

    Test labels are not an argument. Each returned mask already includes
    every row, so a later test score is just applying the frozen thresholds.
    """
    start = np.ones(len(y), dtype=bool)
    beam: list[tuple[np.ndarray, tuple[str, ...]]] = [(start, ())]
    found: list[tuple[str, np.ndarray]] = []
    seen: set[tuple[str, ...]] = {()}
    for _ in range(max_adds):
        nxt: list[tuple[float, np.ndarray, tuple[str, ...]]] = []
        for mask, names in beam:
            parent_n, _, parent_w = _rate(y, mask & fit)
            if parent_n < MIN_N:
                continue
            for name, atom in atoms:
                if name in names:
                    continue
                child_names = tuple(sorted((*names, name)))
                if child_names in seen:
                    continue
                seen.add(child_names)
                child = mask & atom
                fit_n, fit_p, fit_w = _rate(y, child & fit)
                sel_n, _, _ = _rate(y, child & sel)
                if fit_n < MIN_N or sel_n < MIN_N:
                    continue
                if fit_w <= parent_w + 1e-4:
                    continue
                nxt.append((fit_w, child, child_names))
                if fit_p >= 0.70:
                    found.append((" + ".join(child_names), child))
        nxt.sort(key=lambda row: row[0], reverse=True)
        beam = [(m, n) for _, m, n in nxt[:width]]
        if not beam:
            break
    # Unique by name, keep the first (highest climb order).
    uniq: dict[str, np.ndarray] = {}
    for name, mask in found:
        uniq.setdefault(name, mask)
    return list(uniq.items())


def _leaf_rules(clf: DecisionTreeClassifier, names: tuple[str, ...]) -> dict[int, str]:
    tree = clf.tree_
    out: dict[int, str] = {}

    def walk(node: int, rules: list[str]) -> None:
        left = int(tree.children_left[node])
        right = int(tree.children_right[node])
        if left == -1:
            out[node] = " en ".join(rules) if rules else "alle"
            return
        feat = names[int(tree.feature[node])]
        thr = float(tree.threshold[node])
        walk(left, [*rules, f"{feat}<={thr:.3f}"])
        walk(right, [*rules, f"{feat}>{thr:.3f}"])

    walk(0, [])
    return out


def _candidates_for_label(
    frame: Frame,
    y: np.ndarray,
    horizon: int,
    label: str,
    fit: np.ndarray,
    sel: np.ndarray,
) -> list[Cand]:
    atoms = make_atoms(frame)
    cands: list[Cand] = []

    def add(name: str, mask: np.ndarray, **extra: Any) -> None:
        fit_n, fit_p, fit_w = _rate(y, mask & fit)
        sel_n, sel_p, sel_w = _rate(y, mask & sel)
        if fit_n < MIN_N or sel_n < MIN_N:
            return
        cands.append(
            Cand(
                name=name,
                horizon=horizon,
                label=label,
                mask=np.asarray(mask, dtype=bool),
                fit_n=fit_n,
                fit_p=fit_p,
                fit_w=fit_w,
                sel_n=sel_n,
                sel_p=sel_p,
                sel_w=sel_w,
                extra=extra,
            )
        )

    strength = frame.col("xs10")
    add("alle liquide", np.ones(len(y), dtype=bool), rank=strength)
    for name, mask in climb_atoms(y, fit, sel, atoms):
        add(name, mask, kind="rule", rank=strength)

    x_fit = frame.x[fit]
    y_fit = y[fit]
    if int(y_fit.sum()) < 30 or int((~y_fit).sum()) < 30:
        return cands
    scaler = StandardScaler()
    x_fit_s = scaler.fit_transform(x_fit)
    x_all = scaler.transform(frame.x)
    logit = LogisticRegression(C=0.2, max_iter=400, random_state=0)
    logit.fit(x_fit_s, y_fit)
    proba = logit.predict_proba(x_all)[:, 1]
    fit_scores = proba[fit]
    grid = np.unique(np.quantile(fit_scores, np.linspace(0.50, 0.99, 30)))
    for thr in grid:
        add(f"score>={float(thr):.3f}", proba >= float(thr), kind="score", rank=proba)

    tree = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=MIN_N, random_state=0
    )
    tree.fit(x_fit, y_fit)
    leaves = tree.apply(frame.x)
    rules = _leaf_rules(tree, frame.names)
    for leaf, rule in rules.items():
        add(f"boom: {rule}", leaves == leaf, kind="tree", rank=strength)
    return cands


def sleeve_on(
    frame: Frame,
    cand: Cand,
    window: np.ndarray,
    *,
    book: float = BOOK_EUR,
) -> dict[str, Any]:
    """One slot. Each day takes the strongest name, then waits until that exit.

    Hit rate is the share of taken trades whose high reached +8% from the open.
    PnL refills a fixed book on every trade, with the TP8 costs already in ``ret``.
    """
    horizon = cand.horizon
    rank = cand.extra.get("rank")
    if rank is None:
        rank = frame.col("xs10")
    rank = np.asarray(rank, dtype=np.float64)
    idx = np.flatnonzero(cand.mask & window)
    best: dict[str, int] = {}
    for i in idx:
        day = str(frame.date[i])
        prev = best.get(day)
        if prev is None:
            best[day] = int(i)
            continue
        ri, rp = float(rank[i]), float(rank[prev])
        if ri > rp or (ri == rp and str(frame.base[i]) < str(frame.base[prev])):
            best[day] = int(i)
    taken: list[int] = []
    exit_on = ""
    for day in sorted(best):
        if exit_on and day < exit_on:
            continue
        i = best[day]
        taken.append(i)
        exit_on = str(frame.exit_date[horizon][i])
    n = len(taken)
    if n == 0:
        return {
            "n": 0,
            "k": 0,
            "p": 0.0,
            "wilson": 0.0,
            "p_tp": 0.0,
            "median": 0.0,
            "pnl": 0.0,
            "avg": 0.0,
        }
    hits = frame.y_up[horizon][taken]
    tps = frame.y_tp[horizon][taken]
    rets = frame.ret[horizon][taken]
    k = int(hits.sum())
    return {
        "n": n,
        "k": k,
        "p": k / n,
        "wilson": _wilson(k, n),
        "p_tp": float(tps.mean()),
        "median": float(np.median(rets)),
        "pnl": float(rets.sum()) * book,
        "avg": float(rets.mean()),
    }


def pick_green(
    frame: Frame,
    cands: list[Cand],
    fit: np.ndarray,
    sel: np.ndarray,
    *,
    min_fit: int = MIN_FIT_TRADES,
    min_sel: int = MIN_SEL_TRADES,
) -> tuple[Cand, dict[str, Any], dict[str, Any]] | None:
    """Highest select hit rate among sleeves that are green on fit and on select.

    Test trades are not an argument.
    """
    best: tuple[Cand, dict[str, Any], dict[str, Any]] | None = None
    best_key: tuple[float, float, float] | None = None
    for cand in cands:
        if cand.name == "alle liquide":
            continue
        fit_stats = sleeve_on(frame, cand, fit)
        sel_stats = sleeve_on(frame, cand, sel)
        if fit_stats["n"] < min_fit or sel_stats["n"] < min_sel:
            continue
        if fit_stats["pnl"] <= 0 or sel_stats["pnl"] <= 0:
            continue
        key = (float(sel_stats["p"]), float(sel_stats["wilson"]), float(sel_stats["pnl"]))
        if best_key is None or key > best_key:
            best_key = key
            best = (cand, fit_stats, sel_stats)
    return best


def pick_winner(cands: list[Cand]) -> tuple[Cand | None, bool]:
    """Choose on select Wilson. A 70% claim also needs fit precision ≥ 70%.

    ``claimed`` is the fit-and-select gate only. The test window is not here.
    """
    pool = [c for c in cands if c.fit_n >= MIN_N and c.sel_n >= MIN_N and c.name != "alle liquide"]
    if not pool:
        return None, False
    claim = [c for c in pool if c.fit_p >= 0.70 and c.sel_p >= 0.70]
    if claim:
        return max(claim, key=lambda c: (c.sel_w, c.fit_w)), True
    return max(pool, key=lambda c: (c.sel_w, c.fit_w)), False


def _window_detail(frame: Frame, cand: Cand, mask: np.ndarray) -> dict[str, Any]:
    """Primary rate is the label the rule was chosen on."""
    primary = frame.y_tp[cand.horizon] if cand.label == "tp" else frame.y_up[cand.horizon]
    y_close = frame.y_close[cand.horizon]
    y_tp = frame.y_tp[cand.horizon]
    ret = frame.ret[cand.horizon]
    n, p_up, w_up = _rate(primary, mask)
    _, p_close, _ = _rate(y_close, mask)
    _, p_tp, _ = _rate(y_tp, mask)
    avg = float(ret[mask].mean()) if n else 0.0
    return {
        "n": n,
        "p_up": p_up,
        "wilson_up": w_up,
        "p_close": p_close,
        "p_tp": p_tp,
        "avg_ret": avg,
    }


def _fmt_pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def _fmt_detail(d: dict[str, Any]) -> str:
    return (
        f"{_fmt_pct(float(d['p_up']))} (n={d['n']}, wilson≥{_fmt_pct(float(d['wilson_up']))})"
    )


def _question(
    frame: Frame,
    *,
    label: str,
    fit: np.ndarray,
    sel: np.ndarray,
    test: np.ndarray,
) -> tuple[dict[str, Any], list[Cand]]:
    """One precommitted question. Horizon is chosen on select, then test is scored once."""
    cands: list[Cand] = []
    bases: dict[int, dict[str, tuple[int, float, float]]] = {}
    frontiers: dict[int, list[Cand]] = {}
    for horizon in HORIZONS:
        y = frame.y_tp[horizon] if label == "tp" else frame.y_up[horizon]
        found = _candidates_for_label(frame, y, horizon, label, fit, sel)
        cands.extend(found)
        bases[horizon] = {
            "fit": _rate(y, fit),
            "select": _rate(y, sel),
            "test": _rate(y, test),
        }
        frontiers[horizon] = sorted(
            [c for c in found if c.name != "alle liquide"],
            key=lambda c: (c.sel_p, c.sel_w),
            reverse=True,
        )[:5]
    winner, claimed = pick_winner(cands)
    row: dict[str, Any] = {
        "label": label,
        "claimed_on_select": claimed,
        "base": bases,
        "frontier": [
            {
                "horizon": c.horizon,
                "name": c.name,
                "fit_n": c.fit_n,
                "fit_p": c.fit_p,
                "sel_n": c.sel_n,
                "sel_p": c.sel_p,
                "sel_w": c.sel_w,
            }
            for h in HORIZONS
            for c in frontiers[h]
        ],
    }
    if winner is not None:
        row["winner"] = {
            "name": winner.name,
            "horizon": winner.horizon,
            "fit": _window_detail(frame, winner, winner.mask & fit),
            "select": _window_detail(frame, winner, winner.mask & sel),
            "test": _window_detail(frame, winner, winner.mask & test),
        }
    return row, cands


def _green_report(
    frame: Frame,
    cands: list[Cand],
    fit: np.ndarray,
    sel: np.ndarray,
    test: np.ndarray,
) -> dict[str, Any]:
    """Max select hit rate among sleeves green on fit and select. Test PnL is one look."""
    picked = pick_green(frame, cands, fit, sel)
    if picked is None:
        return {"shown": False, "reason": "geen sleeve die op fit én 2025 geld verdient"}
    cand, fit_stats, sel_stats = picked
    test_stats = sleeve_on(frame, cand, test)
    shown = float(test_stats["pnl"]) > 0
    return {
        "shown": shown,
        "reason": "" if shown else "2026-PnL is niet positief",
        "name": cand.name,
        "horizon": cand.horizon,
        "label": cand.label,
        "book": BOOK_EUR,
        "fit": fit_stats,
        "select": sel_stats,
        "test": test_stats,
    }


def search(frame: Frame) -> dict[str, Any]:
    fit, sel, test = split_masks(frame.date)
    # Two precision questions, plus one sleeve question. Each scores 2026 once.
    high, high_cands = _question(frame, label="high", fit=fit, sel=sel, test=test)
    tp, tp_cands = _question(frame, label="tp", fit=fit, sel=sel, test=test)
    return {
        "asof": datetime.now(UTC).isoformat(),
        "rows": int(len(frame.date)),
        "bases": int(len(set(frame.base.tolist()))),
        "questions": [high, tp],
        "green": _green_report(frame, [*high_cands, *tp_cands], fit, sel, test),
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# +8% precisie — zoektocht naar 70%",
        "",
        f"asof `{payload['asof']}`  rijen `{payload['rows']}`  bases **{payload['bases']}**",
        "",
        "Label: hoogste koers binnen de horizon / volgende opening − 1 ≥ +8%.",
        "Koop op de volgende opening. Features van de slotdag, plus de gap die bij de opening bekend is.",
        "Fit ≤ 2024-12-31 · select = 2025 · test ≥ 2026-01-01.",
        "De regel wordt gekozen op select (Wilson), en alleen als de fit óók ≥ 70% haalt.",
        "2026 wordt daarna één keer gemeten.",
        "Een tweede vraag is of +8% vóór een −5% stop wordt geraakt (`tp`), met dezelfde kosten als de TP8-sleeve.",
        "",
        f"Minimale steekproef per venster: **{MIN_N}**.",
        "",
    ]
    for q in payload["questions"]:
        label = "high ≥ +8% vanaf de opening" if q["label"] == "high" else "+8% vóór een −5% stop"
        lines.append(f"## {label}")
        lines.append("")
        lines.append("| Horizon | Fit basiskans | Select basiskans | Test basiskans |")
        lines.append("|---|---|---|---|")
        for h in HORIZONS:
            bf, bs, bt = q["base"][h]["fit"], q["base"][h]["select"], q["base"][h]["test"]
            lines.append(
                f"| {h}d | {_fmt_pct(bf[1])} (n={bf[0]}) | {_fmt_pct(bs[1])} (n={bs[0]}) | "
                f"{_fmt_pct(bt[1])} (n={bt[0]}) |"
            )
        lines.append("")
        winner = q.get("winner")
        if winner is None:
            lines.append("Geen regel met genoeg signalen.")
            lines.append("")
            continue
        held = (
            q["claimed_on_select"]
            and float(winner["test"]["p_up"]) >= 0.70
            and int(winner["test"]["n"]) >= 40
        )
        verdict = "70% gehouden op fit, select en test." if held else "70% niet gehouden."
        lines.append(
            f"**{verdict}** horizon {winner['horizon']}d · `{winner['name']}`"
        )
        lines.append("")
        head = "High ≥ +8%" if q["label"] == "high" else "+8% vóór stop"
        lines.append(f"| Venster | {head} | Slot ≥ +8% | +8% vóór stop | Gem. trade |")
        lines.append("|---|---|---|---|---|")
        for key, title in (("fit", "Fit"), ("select", "Select"), ("test", "Test 2026")):
            d = winner[key]
            lines.append(
                f"| {title} | {_fmt_detail(d)} | {_fmt_pct(float(d['p_close']))} | "
                f"{_fmt_pct(float(d['p_tp']))} | {float(d['avg_ret']) * 100:+.2f}% |"
            )
        lines.append("")
        lines.append("Hoogste select-precisie per horizon (test niet gebruikt om te kiezen):")
        lines.append("")
        lines.append("| Horizon | Regel | Fit | Select |")
        lines.append("|---|---|---|---|")
        for row in q["frontier"]:
            lines.append(
                f"| {row['horizon']}d | `{row['name']}` | {_fmt_pct(row['fit_p'])} (n={row['fit_n']}) | "
                f"{_fmt_pct(row['sel_p'])} (n={row['sel_n']}, wilson≥{_fmt_pct(row['sel_w'])}) |"
            )
        lines.append("")
    green = payload.get("green") or {}
    lines.append("## Hoogste trefzekerheid met positieve PnL")
    lines.append("")
    lines.append(
        f"Eén slot, vast boek €{BOOK_EUR:,.0f}, +8% take-profit vóór een −5% stop, "
        "anders de horizonsluit. Kosten zoals de TP8-sleeve. "
        "Gekozen op de hoogste trefzekerheid in 2025 onder de sleeves die op fit én 2025 groen zijn. "
        "2026 wordt daarna één keer gemeten en alleen getoond als die PnL ook positief is."
    )
    lines.append("")
    if not green.get("shown"):
        lines.append(f"Niet getoond. {green.get('reason', '')}")
        if green.get("test"):
            lines.append("")
            lines.append(
                f"De gekozen sleeve ({green['horizon']}d, `{green['name']}`) "
                f"heeft in 2026 PnL €{float(green['test']['pnl']):+,.0f} "
                f"bij trefzekerheid {_fmt_pct(float(green['test']['p']))} "
                f"(n={green['test']['n']})."
            )
        lines.append("")
    else:
        lines.append(
            f"**Getoond.** horizon {green['horizon']}d · label `{green['label']}` · `{green['name']}`"
        )
        lines.append("")
        lines.append("| Venster | High ≥ +8% | +8% vóór stop | Mediaan trade | Trades | PnL |")
        lines.append("|---|---|---|---|---:|---:|")
        for key, title in (("fit", "Fit"), ("select", "Select 2025"), ("test", "Test 2026")):
            d = green[key]
            lines.append(
                f"| {title} | {_fmt_pct(float(d['p']))} (wilson≥{_fmt_pct(float(d['wilson']))}) | "
                f"{_fmt_pct(float(d['p_tp']))} | {float(d['median']) * 100:+.2f}% | "
                f"{d['n']} | €{float(d['pnl']):+,.0f} |"
            )
        lines.append("")
    lines.extend(
        [
            "Reproduce:",
            "",
            "```bash",
            ".venv/bin/python -m bot.research.daily_green_lab.hit8_precision",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run(*, candle_dir: str = "data/ignition_expand_candles") -> dict[str, Any]:
    series = load_series(candle_dir)
    frame = build_frame(series)
    payload = search(frame)
    path = Path(__file__).resolve().parent / "HIT8.md"
    path.write_text(to_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Search for a 70% precise +8% call")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    args = p.parse_args()
    payload = run(candle_dir=args.candles)
    for q in payload["questions"]:
        w = q.get("winner") or {}
        test = (w.get("test") or {}) if w else {}
        print(
            f"{q['label']} claimed={q['claimed_on_select']} h={w.get('horizon', '-')} "
            f"rule={w.get('name', '-')} test_p={test.get('p_up', 0):.3f} n={test.get('n', 0)}"
        )
    green = payload.get("green") or {}
    print(
        f"green shown={green.get('shown')} reason={green.get('reason', '')} "
        f"h={green.get('horizon', '-')} rule={green.get('name', '-')}"
    )


if __name__ == "__main__":
    main()
