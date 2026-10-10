"""Broader search for a +8% call.

The first precision pass used a shallow score on daily bars. This pass adds
candle shape, cross-sectional ranks, a gradient-boosted score, and a 4h tape.
Fit learns the score. Select picks the threshold. 2026 is scored once per tape.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance

from bot.research.daily_green_lab.hit8_precision import (
    BOOK_EUR,
    HARD,
    TARGET,
    build_frame,
    split_masks,
)
from bot.research.daily_green_lab.tp8 import FEE, SLIP, resolve_from_entry
from bot.research.moonshot_preimage.day50 import Series, load_series
from bot.research.moonshot_preimage.engine import _wilson

MIN_N = 80
H4_DIR = Path("/tmp/h4_cache")
# Taken from the fit-window score drivers. Quantiles stay on the fit window.
DAILY_PAIR = (
    "btc_r1", "up_vol", "loc20", "r10", "dayloc", "volx", "coil", "lower_wick", "xs10", "r3", "xs3",
)
H4_PAIR = ("r30", "btc_r6", "dist120", "xs6", "r18", "xs18")


@dataclass
class Cut:
    name: str
    fit_n: int
    fit_p: float
    fit_w: float
    sel_n: int
    sel_p: float
    sel_w: float
    mask: np.ndarray


def choose_cut(cuts: list[Cut]) -> Cut | None:
    """Highest select hit rate with enough trades. Test labels are not here."""
    pool = [c for c in cuts if c.fit_n >= MIN_N and c.sel_n >= MIN_N and c.fit_p >= 0.45]
    if not pool:
        return None
    return max(pool, key=lambda c: (c.sel_p, c.sel_w, c.fit_p))


def one_slot_dates(
    date: np.ndarray,
    score: np.ndarray,
    mask: np.ndarray,
    exit_date: np.ndarray,
    base: np.ndarray | None = None,
) -> np.ndarray:
    """One name per day, then free again on the exit day. ISO dates sort in time."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx

    def _key(i: int) -> tuple[str, float, str]:
        tie = str(base[i]) if base is not None else ""
        return (str(date[i]), -float(score[i]), tie)

    taken: list[int] = []
    busy = ""
    open_day: str | None = None
    took = False
    for i in sorted(idx.tolist(), key=_key):
        day = str(date[i])
        if day != open_day:
            open_day = day
            took = False
        if took:
            continue
        if busy and day < busy:
            took = True
            continue
        taken.append(i)
        took = True
        busy = str(exit_date[i])
    return np.asarray(taken, dtype=np.int64)


def one_slot_indices(
    stamp: np.ndarray,
    score: np.ndarray,
    mask: np.ndarray,
    exit_stamp: np.ndarray,
    base: np.ndarray | None = None,
) -> np.ndarray:
    """One name per bar. A new signal is allowed on the exit bar itself."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx

    def _key(i: int) -> tuple[int, float, str]:
        tie = str(base[i]) if base is not None else ""
        return (int(stamp[i]), -float(score[i]), tie)

    taken: list[int] = []
    busy: int | None = None
    open_stamp: int | None = None
    took = False
    for i in sorted(idx.tolist(), key=_key):
        ts = int(stamp[i])
        if ts != open_stamp:
            open_stamp = ts
            took = False
        if took:
            continue
        if busy is not None and ts < busy:
            took = True
            continue
        taken.append(i)
        took = True
        busy = int(exit_stamp[i])
    return np.asarray(taken, dtype=np.int64)


def episode_first(
    base: np.ndarray,
    stamp: np.ndarray,
    mask: np.ndarray,
    *,
    gap_ms: int,
) -> np.ndarray:
    """First signal of each same-coin run. A new run starts after ``gap_ms``."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx
    order = sorted(idx.tolist(), key=lambda i: (str(base[i]), int(stamp[i])))
    out: list[int] = []
    prev_b: str | None = None
    prev_ts: int | None = None
    for i in order:
        b = str(base[i])
        ts = int(stamp[i])
        if prev_b != b or prev_ts is None or ts - prev_ts >= gap_ms:
            out.append(i)
        prev_b = b
        prev_ts = ts
    return np.asarray(out, dtype=np.int64)


def same_bar_counts(stamp: np.ndarray, mask: np.ndarray) -> tuple[int, int, int]:
    ts = np.asarray(stamp)[np.asarray(mask, dtype=bool)]
    n = int(len(ts))
    if n == 0:
        return 0, 0, 0
    _, counts = np.unique(ts, return_counts=True)
    return n, int(len(counts)), int(counts.max())


def _book_row(
    y: np.ndarray,
    ret: np.ndarray,
    tp: np.ndarray,
    taken: np.ndarray,
    *,
    book: float = BOOK_EUR,
) -> dict[str, float | int]:
    n = int(len(taken))
    if n == 0:
        return {"n": 0, "p": 0.0, "w": 0.0, "p_tp": 0.0, "median": 0.0, "pnl": 0.0}
    yy = np.asarray(y)[taken]
    k = int(yy.sum())
    rr = np.asarray(ret, dtype=np.float64)[taken]
    return {
        "n": n,
        "p": k / n,
        "w": _wilson(k, n),
        "p_tp": float(np.asarray(tp)[taken].mean()),
        "median": float(np.median(rr)),
        "pnl": float(rr.sum()) * book,
    }


def _windows_book(
    y: np.ndarray,
    ret: np.ndarray,
    tp: np.ndarray,
    taken_for: dict[str, np.ndarray],
) -> dict[str, dict[str, float | int]]:
    return {name: _book_row(y, ret, tp, taken) for name, taken in taken_for.items()}


def _tail_masks(col: np.ndarray, fit: np.ndarray, name: str) -> list[tuple[str, np.ndarray]]:
    """High and low tails. Thresholds are quantiles of the fit window only."""
    out: list[tuple[str, np.ndarray]] = []
    held = col[fit]
    for q in (0.80, 0.90):
        hi = float(np.quantile(held, q))
        lo = float(np.quantile(held, 1.0 - q))
        out.append((f"{name}>={hi:.3f}", col >= hi))
        out.append((f"{name}<={lo:.3f}", col <= lo))
    return out


def _atom_feature(label: str) -> str:
    for sep in (">=", "<="):
        if sep in label:
            return label.split(sep, 1)[0]
    return label


def _pair_cuts(
    x: np.ndarray,
    names: list[str],
    feats: tuple[str, ...],
    y: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
) -> list[Cut]:
    atoms: list[tuple[str, np.ndarray]] = []
    for name in feats:
        if name not in names:
            continue
        atoms.extend(_tail_masks(x[:, names.index(name)], fit, name))
    cuts: list[Cut] = []
    for a in range(len(atoms)):
        fa = _atom_feature(atoms[a][0])
        for b in range(a + 1, len(atoms)):
            if _atom_feature(atoms[b][0]) == fa:
                continue
            cut = _as_cut(
                f"{atoms[a][0]} + {atoms[b][0]}",
                y,
                atoms[a][1] & atoms[b][1],
                fit,
                sel,
            )
            if cut is not None:
                cuts.append(cut)
    return cuts


def _exit_path(rows: list[list[float]], i: int, horizon_bars: int) -> tuple[float, int, bool]:
    """Buy the next open. Fixed +8% target and 5% stop, else the horizon close."""
    entry = float(rows[i + 1][1])
    fill = entry * (1.0 + SLIP)
    last = i + horizon_bars
    for k in range(i + 1, last + 1):
        got = resolve_from_entry(
            entry=fill,
            o=float(rows[k][1]),
            h=float(rows[k][2]),
            l=float(rows[k][3]),
            c=float(rows[k][4]),
            hard=HARD,
            tp=TARGET,
            slip=SLIP,
        )
        if got is not None:
            reason, px = got
            return px / fill - 1.0 - 2.0 * FEE, int(rows[k][0]), reason == "take_profit"
    px = float(rows[last][4]) * (1.0 - SLIP)
    return px / fill - 1.0 - 2.0 * FEE, int(rows[last][0]), False


def _rate(y: np.ndarray, mask: np.ndarray) -> tuple[int, float, float]:
    m = np.asarray(mask, dtype=bool)
    n = int(m.sum())
    if n == 0:
        return 0, 0.0, 0.0
    k = int(y[m].sum())
    return n, k / n, _wilson(k, n)


def _as_cut(name: str, y: np.ndarray, mask: np.ndarray, fit: np.ndarray, sel: np.ndarray) -> Cut | None:
    fit_n, fit_p, fit_w = _rate(y, mask & fit)
    sel_n, sel_p, sel_w = _rate(y, mask & sel)
    if fit_n < MIN_N or sel_n < MIN_N:
        return None
    return Cut(name, fit_n, fit_p, fit_w, sel_n, sel_p, sel_w, np.asarray(mask, dtype=bool))


def _pct_rank(values: np.ndarray, keys: np.ndarray) -> np.ndarray:
    out = np.full(len(values), 0.5, dtype=np.float64)
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, key in enumerate(keys.tolist()):
        groups[key].append(i)
    for idxs in groups.values():
        vals = values[np.asarray(idxs)]
        order = np.argsort(vals, kind="mergesort")
        n = len(idxs)
        if n == 1:
            out[idxs[0]] = 0.5
            continue
        ranks = np.empty(n, dtype=np.float64)
        ranks[order] = np.arange(n, dtype=np.float64) / (n - 1)
        for pos, i in enumerate(idxs):
            out[i] = ranks[pos]
    return out


def _shape_columns(series: dict[str, Series], date: np.ndarray, base: np.ndarray) -> np.ndarray:
    loc = {(b, d): i for b, ser in series.items() for i, d in enumerate(ser.dates)}
    body = np.zeros(len(date))
    upper = np.zeros(len(date))
    lower = np.zeros(len(date))
    since = np.zeros(len(date))
    for j, (b, d) in enumerate(zip(base.tolist(), date.tolist())):
        ser = series[str(b)]
        i = loc[(str(b), str(d))]
        o = ser.o[i]
        if o <= 0:
            continue
        h, l, c = ser.h[i], ser.l[i], ser.c[i]
        body[j] = (c - o) / o
        upper[j] = (h - max(o, c)) / o
        lower[j] = (min(o, c) - l) / o
        peak_at = i
        peak = ser.h[i]
        for k in range(i - 1, max(-1, i - 20), -1):
            if ser.h[k] > peak:
                peak = ser.h[k]
                peak_at = k
        since[j] = float(i - peak_at)
    return np.column_stack([body, upper, lower, since])


def _boost_cuts(
    x: np.ndarray,
    y: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    *,
    tag: str,
) -> tuple[list[Cut], np.ndarray | None, HistGradientBoostingClassifier | None]:
    y_fit = y[fit]
    if int(y_fit.sum()) < 40 or int((~y_fit).sum()) < 40:
        return [], None, None
    model = HistGradientBoostingClassifier(
        max_depth=5,
        min_samples_leaf=50,
        learning_rate=0.06,
        max_iter=250,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=15,
        random_state=0,
    )
    model.fit(x[fit], y_fit)
    proba = model.predict_proba(x)[:, 1]
    cuts: list[Cut] = []
    grid = np.unique(np.quantile(proba[fit], np.linspace(0.40, 0.995, 40)))
    for thr in grid:
        cut = _as_cut(f"{tag} score>={float(thr):.3f}", y, proba >= float(thr), fit, sel)
        if cut is not None:
            cuts.append(cut)
    return cuts, proba, model


def _one_per_group(
    y: np.ndarray,
    proba: np.ndarray,
    groups: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    *,
    tag: str,
) -> list[Cut]:
    cuts: list[Cut] = []
    grid = np.unique(np.quantile(proba[fit], np.linspace(0.50, 0.995, 24)))
    order = np.argsort(groups, kind="mergesort")
    # Precompute the best row in each group.
    best: dict[Any, int] = {}
    for i in order.tolist():
        g = groups[i]
        prev = best.get(g)
        if prev is None or proba[i] > proba[prev]:
            best[g] = i
    best_rows = np.fromiter(best.values(), dtype=np.int64, count=len(best))
    for thr in grid:
        mask = np.zeros(len(y), dtype=bool)
        keep = best_rows[proba[best_rows] >= float(thr)]
        mask[keep] = True
        cut = _as_cut(f"{tag} top1>={float(thr):.3f}", y, mask, fit, sel)
        if cut is not None:
            cuts.append(cut)
    return cuts


def _quantile_cuts(
    x: np.ndarray,
    names: list[str],
    y: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
) -> list[Cut]:
    cuts: list[Cut] = []
    for i, name in enumerate(names):
        col = x[:, i]
        for q in (0.80, 0.90):
            thr = float(np.quantile(col[fit], q))
            lo = float(np.quantile(col[fit], 1.0 - q))
            high = _as_cut(f"{name}>={thr:.3f}", y, col >= thr, fit, sel)
            low = _as_cut(f"{name}<={lo:.3f}", y, col <= lo, fit, sel)
            if high is not None:
                cuts.append(high)
            if low is not None:
                cuts.append(low)
    return cuts


def _question(
    x: np.ndarray,
    y: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    groups: np.ndarray,
    simple_names: list[str],
    *,
    tag: str,
) -> tuple[dict[str, Any], Cut | None, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Select a cut. The returned mask is applied to the test window later, once."""
    cuts, proba, model = _boost_cuts(x, y, fit, sel, tag=tag)
    if proba is not None:
        cuts.extend(_one_per_group(y, proba, groups, fit, sel, tag=tag))
    use = [simple_names.index(n) for n in simple_names if n in {
        "r1", "r3", "xs10", "up_vol", "day_up", "dist_hi", "volx", "body", "upper_wick",
        "lower_wick", "days_since_high", "cs_xs", "cs_up", "r6", "r18", "xs6", "dist120",
        "day_ret",
    }]
    cuts.extend(_quantile_cuts(x[:, use], [simple_names[i] for i in use], y, fit, sel))
    winner = choose_cut(cuts)
    base_n, base_p, _ = _rate(y, np.ones(len(y), dtype=bool))
    summary: dict[str, Any] = {
        "tag": tag,
        "base_p": base_p,
        "base_n": base_n,
        "n_cuts": len(cuts),
        "winner": None,
    }
    if winner is not None:
        summary["winner"] = {
            "name": winner.name,
            "fit_n": winner.fit_n,
            "fit_p": winner.fit_p,
            "fit_w": winner.fit_w,
            "sel_n": winner.sel_n,
            "sel_p": winner.sel_p,
            "sel_w": winner.sel_w,
        }
        if model is not None and winner.name.startswith(f"{tag} score"):
            sample = np.flatnonzero(fit)
            if len(sample) > 4000:
                rng = np.random.default_rng(0)
                sample = rng.choice(sample, 4000, replace=False)
            imp = permutation_importance(
                model, x[sample], y[sample], n_repeats=4, random_state=0, scoring="roc_auc"
            )
            order = np.argsort(imp.importances_mean)[::-1][:6]
            summary["winner"]["drivers"] = [
                f"{simple_names[i]} {imp.importances_mean[i]:+.3f}" for i in order if i < len(simple_names)
            ]
    return summary, winner, y, None if winner is None else winner.mask, proba


def _date_books(
    date: np.ndarray,
    base: np.ndarray,
    score: np.ndarray,
    mask: np.ndarray,
    y: np.ndarray,
    ret: np.ndarray,
    tp: np.ndarray,
    exit_date: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    test: np.ndarray,
) -> dict[str, dict[str, float | int]]:
    taken = {
        name: one_slot_dates(date, score, mask & window, exit_date, base)
        for name, window in (("fit", fit), ("sel", sel), ("test", test))
    }
    return _windows_book(y, ret, tp, taken)


def _stamp_books(
    stamp: np.ndarray,
    base: np.ndarray,
    score: np.ndarray,
    mask: np.ndarray,
    y: np.ndarray,
    ret: np.ndarray,
    tp: np.ndarray,
    exit_stamp: np.ndarray,
    fit: np.ndarray,
    sel: np.ndarray,
    test: np.ndarray,
) -> dict[str, dict[str, float | int]]:
    taken = {
        name: one_slot_indices(stamp, score, mask & window, exit_stamp, base)
        for name, window in (("fit", fit), ("sel", sel), ("test", test))
    }
    return _windows_book(y, ret, tp, taken)


def _fill_paths(
    raw: dict[str, list[list[float]]],
    base: np.ndarray,
    bar: np.ndarray,
    mask: np.ndarray,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(mask)
    ret = np.zeros(n, dtype=np.float64)
    exit_stamp = np.zeros(n, dtype=np.int64)
    tp = np.zeros(n, dtype=bool)
    for i in np.flatnonzero(mask).tolist():
        r, ts, hit = _exit_path(raw[str(base[i])], int(bar[i]), horizon)
        ret[i] = r
        exit_stamp[i] = ts
        tp[i] = hit
    return ret, exit_stamp, tp


def _readable(
    x: np.ndarray,
    names: list[str],
    feats: tuple[str, ...],
    labeled: list[tuple[str, np.ndarray]],
    fit: np.ndarray,
    sel: np.ndarray,
    test: np.ndarray,
) -> dict[str, Any]:
    """Pair rules on the fit drivers. Select picks the pair. 2026 is scored once."""
    rows: list[dict[str, Any]] = []
    held: dict[str, tuple[Cut, np.ndarray]] = {}
    for tag, y in labeled:
        cuts = _pair_cuts(x, names, feats, y, fit, sel)
        winner = choose_cut(cuts)
        row: dict[str, Any] = {"tag": tag, "n_cuts": len(cuts), "winner": None, "tested": False}
        if winner is not None:
            row["winner"] = {
                "name": winner.name,
                "fit_n": winner.fit_n,
                "fit_p": winner.fit_p,
                "fit_w": winner.fit_w,
                "sel_n": winner.sel_n,
                "sel_p": winner.sel_p,
                "sel_w": winner.sel_w,
            }
            held[tag] = (winner, y)
        rows.append(row)
    ranked = [r for r in rows if r["winner"] is not None]
    out: dict[str, Any] = {"rows": rows}
    if not ranked:
        return out
    best = max(ranked, key=lambda r: (r["winner"]["sel_p"], r["winner"]["sel_w"]))
    winner, y = held[best["tag"]]
    tn, tp, tw = _rate(y, winner.mask & test)
    best["tested"] = True
    best["winner"]["test_n"] = tn
    best["winner"]["test_p"] = tp
    best["winner"]["test_w"] = tw
    out["tag"] = best["tag"]
    out["mask"] = winner.mask
    out["y"] = y
    return out


def _daily(series: dict[str, Series]) -> dict[str, Any]:
    frame = build_frame(series)
    shape = _shape_columns(series, frame.date, frame.base)
    cs = np.column_stack([
        _pct_rank(frame.col(name), frame.date)
        for name in ("xs10", "r1", "up_vol", "volx", "day_up")
    ])
    names = list(frame.names) + ["body", "upper_wick", "lower_wick", "days_since_high", "cs_xs", "cs_r1", "cs_up", "cs_vol", "cs_day"]
    x = np.column_stack([frame.x, shape, cs])
    fit, sel, test = split_masks(frame.date)
    found = []
    held: dict[str, dict[str, Any]] = {}
    for horizon in (1, 3, 5):
        summary, winner, y, mask, proba = _question(
            x, frame.y_up[horizon], fit, sel, frame.date, names, tag=f"dag {horizon}d"
        )
        found.append((summary, winner, y, mask, proba))
        held[summary["tag"]] = {"horizon": horizon, "mask": mask, "proba": proba}
    block = _finish("dag", found, test)
    tested = next((q for q in block["questions"] if q.get("tested")), None)
    if tested is not None:
        h = held[tested["tag"]]
        if h["proba"] is not None and h["mask"] is not None:
            horizon = int(h["horizon"])
            block["book"] = _date_books(
                frame.date,
                frame.base,
                h["proba"],
                h["mask"],
                frame.y_up[horizon],
                frame.ret[horizon],
                frame.y_tp[horizon],
                frame.exit_date[horizon],
                fit,
                sel,
                test,
            )
    readable = _readable(
        x,
        names,
        DAILY_PAIR,
        [(f"dag {horizon}d", frame.y_up[horizon]) for horizon in (1, 3, 5)],
        fit,
        sel,
        test,
    )
    if readable.get("mask") is not None:
        horizon = int(str(readable["tag"]).split()[1][:-1])
        readable["book"] = _date_books(
            frame.date,
            frame.base,
            frame.col("xs10"),
            readable["mask"],
            frame.y_up[horizon],
            frame.ret[horizon],
            frame.y_tp[horizon],
            frame.exit_date[horizon],
            fit,
            sel,
            test,
        )
        readable.pop("mask", None)
        readable.pop("y", None)
    block["readable"] = readable
    return block


def _load_h4(cache: Path) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for path in sorted(cache.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        if len(rows) < 200:
            continue
        out[path.stem.upper()] = rows
    return out


def _h4_panel(cache: Path) -> dict[str, Any] | None:
    raw = _load_h4(cache)
    if "BTC" not in raw:
        return None
    btc = {int(r[0]): float(r[4]) for r in raw["BTC"]}
    dates: list[str] = []
    stamps: list[int] = []
    bases: list[str] = []
    bars: list[int] = []
    xs: list[list[float]] = []
    hit6: list[bool] = []
    hit18: list[bool] = []
    names = ["r1", "r6", "r18", "r30", "xs6", "xs18", "volx", "rngx", "dist120", "day_ret", "btc_r6"]
    for base, rows in raw.items():
        if base == "BTC":
            continue
        n = len(rows)
        o = [float(r[1]) for r in rows]
        h = [float(r[2]) for r in rows]
        l = [float(r[3]) for r in rows]
        c = [float(r[4]) for r in rows]
        v = [float(r[5]) for r in rows]
        ts = [int(r[0]) for r in rows]
        for i in range(120, n - 18):
            entry = o[i + 1]
            if entry <= 0 or c[i - 30] <= 0 or c[i - 6] <= 0:
                continue
            qvol = sum(v[k] * c[k] for k in range(i - 5, i + 1)) / 6.0
            if qvol < 20_000:
                continue
            btc_now = btc.get(ts[i])
            btc_6 = btc.get(ts[i - 6])
            btc_18 = btc.get(ts[i - 18])
            if not btc_now or not btc_6 or not btc_18 or btc_6 <= 0 or btc_18 <= 0:
                continue
            r1 = c[i] / c[i - 1] - 1.0
            r6 = c[i] / c[i - 6] - 1.0
            r18 = c[i] / c[i - 18] - 1.0
            r30 = c[i] / c[i - 30] - 1.0
            vol_ma = sum(v[i - 30 : i]) / 30.0
            rng = (h[i] - l[i]) / c[i] if c[i] else 0.0
            rng_ma = sum((h[k] - l[k]) / c[k] for k in range(i - 30, i) if c[k] > 0) / 30.0
            prior_hi = max(h[i - 120 : i])
            midnight = ts[i] - (ts[i] % 86_400_000)
            day_open = o[i]
            for k in range(i, max(-1, i - 6), -1):
                if ts[k] == midnight:
                    day_open = o[k]
                    break
            day_ret = c[i] / day_open - 1.0 if day_open > 0 else 0.0
            fwd6 = max(h[i + 1 : i + 7]) / entry - 1.0
            fwd18 = max(h[i + 1 : i + 19]) / entry - 1.0
            day = datetime.fromtimestamp(ts[i] / 1000, UTC).strftime("%Y-%m-%d")
            dates.append(day)
            stamps.append(ts[i])
            bases.append(base)
            bars.append(i)
            xs.append([
                float(np.clip(r1, -0.5, 1.5)),
                float(np.clip(r6, -0.8, 2.0)),
                float(np.clip(r18, -0.9, 3.0)),
                float(np.clip(r30, -0.9, 4.0)),
                float(np.clip(r6 - (btc_now / btc_6 - 1.0), -1.0, 2.0)),
                float(np.clip(r18 - (btc_now / btc_18 - 1.0), -1.5, 3.0)),
                float(np.clip(v[i] / vol_ma if vol_ma else 0.0, 0.0, 20.0)),
                float(np.clip(rng / rng_ma if rng_ma else 0.0, 0.0, 10.0)),
                float(np.clip(c[i] / prior_hi - 1.0 if prior_hi else 0.0, -0.9, 0.5)),
                float(np.clip(day_ret, -0.5, 1.5)),
                float(np.clip(btc_now / btc_6 - 1.0, -0.3, 0.3)),
            ])
            hit6.append(fwd6 >= 0.08)
            hit18.append(fwd18 >= 0.08)
    x = np.asarray(xs, dtype=np.float64)
    date = np.asarray(dates)
    stamp = np.asarray(stamps)
    # Rank the 24h return inside each timestamp.
    cs = _pct_rank(x[:, names.index("r6")], stamp)
    x = np.column_stack([x, cs])
    names = names + ["cs_r6"]
    fit, sel, test = split_masks(date)
    y6 = np.asarray(hit6)
    y18 = np.asarray(hit18)
    found = []
    held: dict[str, dict[str, Any]] = {}
    for label, y, horizon in (("24u", y6, 6), ("3d", y18, 18)):
        summary, winner, yy, mask, proba = _question(
            x, y, fit, sel, stamp, names, tag=f"4u {label}"
        )
        found.append((summary, winner, yy, mask, proba))
        held[summary["tag"]] = {"horizon": horizon, "mask": mask, "proba": proba, "y": yy}
    block = _finish("4u", found, test)
    block["rows"] = int(len(date))
    base_arr = np.asarray(bases)
    bar_arr = np.asarray(bars)
    tested = next((q for q in block["questions"] if q.get("tested")), None)
    if tested is not None:
        h = held[tested["tag"]]
        if h["proba"] is not None and h["mask"] is not None:
            ret, exit_stamp, tp = _fill_paths(raw, base_arr, bar_arr, h["mask"], int(h["horizon"]))
            block["book"] = _stamp_books(
                stamp, base_arr, h["proba"], h["mask"], h["y"], ret, tp, exit_stamp, fit, sel, test
            )
            gap = int(h["horizon"]) * 4 * 60 * 60 * 1000
            cluster: dict[str, dict[str, float | int]] = {}
            for name, window in (("fit", fit), ("sel", sel), ("test", test)):
                m = h["mask"] & window
                signals, bars_n, max_names = same_bar_counts(stamp, m)
                epi = episode_first(base_arr, stamp, m, gap_ms=gap)
                cluster[name] = {
                    "signals": signals,
                    "bars": bars_n,
                    "max_names": max_names,
                    "episodes": int(len(epi)),
                    "episode_p": float(h["y"][epi].mean()) if len(epi) else 0.0,
                }
            block["cluster"] = cluster
            sample = sorted(
                np.flatnonzero(h["mask"] & test).tolist(),
                key=lambda i: (int(stamp[i]), str(base_arr[i])),
            )[:8]
            block["examples"] = [
                f"{date[i]} {base_arr[i]} {'raak' if h['y'][i] else 'mis'}" for i in sample
            ]
    readable = _readable(
        x,
        names,
        H4_PAIR,
        [("4u 24u", y6), ("4u 3d", y18)],
        fit,
        sel,
        test,
    )
    if readable.get("mask") is not None:
        horizon = 6 if readable["tag"] == "4u 24u" else 18
        ret, exit_stamp, tp = _fill_paths(raw, base_arr, bar_arr, readable["mask"], horizon)
        rank = x[:, names.index("xs6")]
        readable["book"] = _stamp_books(
            stamp,
            base_arr,
            rank,
            readable["mask"],
            readable["y"],
            ret,
            tp,
            exit_stamp,
            fit,
            sel,
            test,
        )
        readable.pop("mask", None)
        readable.pop("y", None)
    block["readable"] = readable
    return block


def _finish(
    tape: str,
    found: list[tuple[dict[str, Any], Cut | None, np.ndarray, np.ndarray | None, np.ndarray | None]],
    test: np.ndarray,
) -> dict[str, Any]:
    """Pick the horizon on select hit rate, then score 2026 once."""
    ranked = [item for item in found if item[1] is not None]
    chosen = max(ranked, key=lambda item: (item[1].sel_p, item[1].sel_w)) if ranked else None
    questions = []
    for summary, winner, y, mask, _proba in found:
        row = dict(summary)
        row["tested"] = False
        if chosen is not None and winner is chosen[1]:
            tn, tp, tw = _rate(y, mask & test)
            row["winner"]["test_n"] = tn
            row["winner"]["test_p"] = tp
            row["winner"]["test_w"] = tw
            row["tested"] = True
        questions.append(row)
    return {"tape": tape, "questions": questions}


def _fmt(p: float) -> str:
    return f"{p * 100:.1f}%"


def _eur(v: float) -> str:
    sign = "+" if v >= 0 else "−"
    return f"{sign}€{abs(v):,.0f}"


def _book_table(book: dict[str, dict[str, float | int]]) -> list[str]:
    lines = [
        "| Venster | Trades | Trefzekerheid | TP-fill | Mediaan | PnL |",
        "|---|---|---|---|---|---|",
    ]
    labels = (("fit", "Fit"), ("sel", "Select 2025"), ("test", "Test 2026"))
    for key, label in labels:
        row = book[key]
        lines.append(
            f"| {label} | {int(row['n'])} | {_fmt(float(row['p']))} (wilson≥{_fmt(float(row['w']))}) "
            f"| {_fmt(float(row['p_tp']))} | {float(row['median']) * 100:+.2f}% | {_eur(float(row['pnl']))} |"
        )
    return lines


def _winner_lines(row: dict[str, Any]) -> list[str]:
    w = row.get("winner")
    if not w:
        return ["", "Geen paar met genoeg signalen.", ""]
    lines = [
        "",
        f"`{w['name']}`",
        "",
        "| Venster | Trefzekerheid |",
        "|---|---|",
        f"| Fit | {_fmt(w['fit_p'])} (n={w['fit_n']}, wilson≥{_fmt(w['fit_w'])}) |",
        f"| Select 2025 | {_fmt(w['sel_p'])} (n={w['sel_n']}, wilson≥{_fmt(w['sel_w'])}) |",
    ]
    if row.get("tested"):
        lines.append(f"| Test 2026 | {_fmt(w['test_p'])} (n={w['test_n']}, wilson≥{_fmt(w['test_w'])}) |")
    else:
        lines.append("| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |")
    lines.append("")
    return lines


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Bredere zoektocht naar +8%",
        "",
        f"asof `{payload['asof']}`",
        "",
        "De score leert op 2024. De drempel kiest 2025, op de hoogste trefzekerheid met minstens 80 signalen in fit en in select, en een fit-trefzekerheid van minstens 45%.",
        "2026 wordt per vraag één keer gemeten, op de gekozen drempel.",
        "Dagfeatures zijn de eerdere set plus kaarslichaam, lonten, dagen sinds de 20d-high, en de rang binnen de dag.",
        "De 4u-tape is elke 4 uur, entry op de volgende 4u-opening.",
        "Paren gebruiken de fit-drivers. Hun drempels zijn fit-kwantielen. 2025 kiest het paar, los van de score.",
        "Het ene slot vult een boek van €2000 per trade. De exit stond vast: take-profit +8%, harde stop 5%, anders de slotkoers aan het eind van de horizon. Kosten 0,15% en 0,1% slippage per kant.",
        "",
    ]
    for block in payload["blocks"]:
        lines.append(f"## {block['tape']}")
        lines.append("")
        if block.get("rows"):
            lines.append(f"Rijen `{block['rows']}`.")
            lines.append("")
        for q in block["questions"]:
            lines.append(f"### {q['tag']}")
            lines.append("")
            lines.append(f"Basiskans {_fmt(q['base_p'])} (n={q['base_n']}). Kandidaten `{q['n_cuts']}`.")
            w = q.get("winner")
            if not w:
                lines.append("")
                lines.append("Geen drempel met genoeg signalen.")
                lines.append("")
                continue
            lines.append("")
            lines.append(f"`{w['name']}`")
            lines.append("")
            lines.append("| Venster | Trefzekerheid |")
            lines.append("|---|---|")
            lines.append(f"| Fit | {_fmt(w['fit_p'])} (n={w['fit_n']}, wilson≥{_fmt(w['fit_w'])}) |")
            lines.append(f"| Select 2025 | {_fmt(w['sel_p'])} (n={w['sel_n']}, wilson≥{_fmt(w['sel_w'])}) |")
            if q.get("tested"):
                lines.append(f"| Test 2026 | {_fmt(w['test_p'])} (n={w['test_n']}, wilson≥{_fmt(w['test_w'])}) |")
            else:
                lines.append("| Test 2026 | niet gemeten; deze horizon haalde niet de hoogste select-trefzekerheid |")
            if w.get("drivers"):
                lines.append("")
                lines.append("Sterkste score-drivers op de fit-steekproef: " + ", ".join(w["drivers"]) + ".")
            lines.append("")
        if block.get("book"):
            lines.append("### Eén slot op de gekozen score")
            lines.append("")
            lines.extend(_book_table(block["book"]))
            lines.append("")
        if block.get("cluster"):
            lines.append("### Samenloop")
            lines.append("")
            lines.append("Een episode is het eerste signaal van dezelfde munt. Een nieuwe episode begint pas na de horizon.")
            lines.append("")
            lines.append("| Venster | Signalen | Unieke bars | Max op één bar | Episodes | Trefzekerheid eerste |")
            lines.append("|---|---|---|---|---|---|")
            for key, label in (("fit", "Fit"), ("sel", "Select 2025"), ("test", "Test 2026")):
                c = block["cluster"][key]
                lines.append(
                    f"| {label} | {c['signals']} | {c['bars']} | {c['max_names']} | {c['episodes']} | {_fmt(float(c['episode_p']))} |"
                )
            lines.append("")
        if block.get("examples"):
            lines.append("Eerste 2026-signalen van de score: " + ", ".join(block["examples"]) + ".")
            lines.append("")
        readable = block.get("readable") or {}
        if readable.get("rows"):
            lines.append("### Leesbare paren")
            lines.append("")
            for row in readable["rows"]:
                lines.append(f"#### {row['tag']}")
                lines.append("")
                lines.append(f"Kandidaten `{row['n_cuts']}`.")
                lines.extend(_winner_lines(row))
            if readable.get("book"):
                lines.append("Eén slot op het gekozen paar, gerangschikt op relatieve kracht.")
                lines.append("")
                lines.extend(_book_table(readable["book"]))
                lines.append("")
    lines.extend([
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab.hit8_broad",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(*, candle_dir: str = "data/ignition_expand_candles", h4_dir: Path = H4_DIR) -> dict[str, Any]:
    series = load_series(candle_dir)
    blocks = [_daily(series)]
    h4 = _h4_panel(h4_dir) if h4_dir.exists() else None
    if h4 is not None:
        blocks.append(h4)
    payload = {"asof": datetime.now(UTC).isoformat(), "blocks": blocks}
    path = Path(__file__).resolve().parent / "HIT8_BROAD.md"
    path.write_text(_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Broader +8% precision search")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    p.add_argument("--h4", default=str(H4_DIR))
    args = p.parse_args()
    payload = run(candle_dir=args.candles, h4_dir=Path(args.h4))
    for block in payload["blocks"]:
        for q in block["questions"]:
            w = q.get("winner") or {}
            print(
                f"{q['tag']} tested={q.get('tested')} cuts={q['n_cuts']} "
                f"sel={w.get('sel_p', 0):.3f} test={w.get('test_p', 0):.3f} n={w.get('test_n', 0)} "
                f"{w.get('name', '-')}"
            )
        book = block.get("book") or {}
        if book.get("test"):
            row = book["test"]
            print(
                f"  slot test n={row['n']} p={row['p']:.3f} tp={row['p_tp']:.3f} "
                f"median={row['median']:.4f} pnl={row['pnl']:.0f}"
            )
        for row in (block.get("readable") or {}).get("rows") or []:
            w = row.get("winner") or {}
            if not row.get("tested"):
                continue
            print(
                f"  pair {row['tag']} cuts={row['n_cuts']} sel={w.get('sel_p', 0):.3f} "
                f"test={w.get('test_p', 0):.3f} n={w.get('test_n', 0)} {w.get('name', '-')}"
            )


if __name__ == "__main__":
    main()
