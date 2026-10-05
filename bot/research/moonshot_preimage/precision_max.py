"""Push moonshot precision above the frozen ``r3≥15% + xs≥25% + trend`` rule.

The 7-day study topped out at 15.8% test precision. This module keeps that
label (forward high / next open − 1 ≥ +50%) and only adds filters that
improve the fit window and still hold on a later select window. 2026 is
scored once, after the rule and the score threshold are frozen.

Splits:
  fit    ≤ 2024-12-31   — where atoms and the logistic score are learned
  select 2025          — where the operating point is chosen
  test   ≥ 2026-01-01  — reported, not used to pick
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from bot.research.moonshot_preimage.day50 import Series, _ret_at, _sma_at, load_series
from bot.research.moonshot_preimage.engine import _wilson

FIT_END = "2024-12-31"
SEL_END = "2025-12-31"
# Close-time inputs for the score. Lag twins (r5/xs3/…) stay out: they
# flip sign against r3/xs10 and do not add a separate claim.
SCORE_FEATURES = ("r1", "r3", "xs10", "volx", "rngx", "loc20", "dayloc", "brk20", "trend", "btc_on")
MIN_QVOL = 50_000.0

FEATURE_NAMES = (
    "r1",
    "r3",
    "r5",
    "r10",
    "xs3",
    "xs5",
    "xs10",
    "xs20",
    "volx",
    "rngx",
    "dayloc",
    "loc20",
    "brk20",
    "trend",
    "btc_on",
    "gap",
)


@dataclass
class Panel:
    date: np.ndarray
    base: np.ndarray
    x: np.ndarray
    hit1: np.ndarray
    hit7: np.ndarray
    names: tuple[str, ...]


def _clip(v: float, lo: float, hi: float) -> float:
    return float(min(hi, max(lo, v)))


def build_panel(series: dict[str, Series], *, min_qvol: float = MIN_QVOL) -> Panel:
    if "BTC" not in series:
        raise ValueError("BTC candles required")
    btc = series["BTC"]
    btc_r: dict[int, dict[str, float]] = {3: {}, 5: {}, 10: {}, 20: {}}
    btc_on: set[str] = set()
    for i, day in enumerate(btc.dates):
        for n in btc_r:
            r = _ret_at(btc.c, i, n)
            if r is not None:
                btc_r[n][day] = float(r)
        sma = _sma_at(btc.c, i, 50)
        if sma is not None and btc.c[i] > sma:
            btc_on.add(day)

    dates: list[str] = []
    bases: list[str] = []
    rows: list[list[float]] = []
    hit1: list[bool] = []
    hit7: list[bool] = []
    for base, ser in series.items():
        if base == "BTC":
            continue
        n = len(ser.dates)
        for i in range(55, n - 7):
            day = ser.dates[i]
            if day not in btc_r[20] or day not in btc_r[3]:
                continue
            c = ser.c[i]
            if c <= 0 or ser.o[i + 1] <= 0:
                continue
            rets = {k: _ret_at(ser.c, i, k) for k in (1, 3, 5, 10, 20)}
            if any(v is None for v in rets.values()):
                continue
            vol_ma = _sma_at(ser.v, i, 20) or 0.0
            volx = (ser.v[i] / vol_ma) if vol_ma > 0 else 0.0
            if ser.v[i] * c < min_qvol:
                continue
            s20 = _sma_at(ser.c, i, 20)
            s50 = _sma_at(ser.c, i, 50)
            if s20 is None or s50 is None or s20 <= 0:
                continue
            hi, lo = ser.h[i], ser.l[i]
            dayloc = ((c - lo) / (hi - lo)) if hi > lo else 0.5
            rng = (hi - lo) / c if c > 0 else 0.0
            rngs = [
                (ser.h[k] - ser.l[k]) / ser.c[k]
                for k in range(i - 19, i + 1)
                if ser.c[k] > 0
            ]
            rng_ma = sum(rngs) / len(rngs) if rngs else 0.0
            rngx = (rng / rng_ma) if rng_ma > 0 else 0.0
            lo20 = min(ser.l[i - 19 : i + 1])
            hi20 = max(ser.h[i - 19 : i + 1])
            loc20 = ((c - lo20) / (hi20 - lo20)) if hi20 > lo20 else 0.5
            prior_hi = max(ser.h[i - 20 : i])
            brk20 = 1.0 if c >= prior_hi else 0.0
            trend = 1.0 if c > s20 > s50 else 0.0
            entry_o = ser.o[i + 1]
            gap = entry_o / c - 1.0
            fwd_h1 = ser.h[i + 1] / entry_o - 1.0
            fwd_h7 = max(ser.h[i + 1 : i + 8]) / entry_o - 1.0
            dates.append(day)
            bases.append(base)
            rows.append(
                [
                    _clip(float(rets[1]), -0.8, 2.0),
                    _clip(float(rets[3]), -0.9, 3.0),
                    _clip(float(rets[5]), -0.9, 4.0),
                    _clip(float(rets[10]), -0.9, 5.0),
                    _clip(float(rets[3]) - btc_r[3][day], -1.0, 3.0),
                    _clip(float(rets[5]) - btc_r[5][day], -1.0, 4.0),
                    _clip(float(rets[10]) - btc_r[10][day], -1.5, 5.0),
                    _clip(float(rets[20]) - btc_r[20][day], -1.5, 6.0),
                    _clip(volx, 0.0, 20.0),
                    _clip(rngx, 0.0, 10.0),
                    _clip(dayloc, 0.0, 1.0),
                    _clip(loc20, 0.0, 1.0),
                    brk20,
                    trend,
                    1.0 if day in btc_on else 0.0,
                    _clip(gap, -0.5, 1.0),
                ]
            )
            hit1.append(fwd_h1 >= 0.50)
            hit7.append(fwd_h7 >= 0.50)
    idx = {name: i for i, name in enumerate(FEATURE_NAMES)}
    x = np.asarray(rows, dtype=np.float64)
    date_a = np.asarray(dates)
    # Cross-section at the signal close: who is the strongest name that day.
    top_r3 = np.zeros(len(dates), dtype=np.float64)
    top_xs = np.zeros(len(dates), dtype=np.float64)
    by: dict[str, list[int]] = defaultdict(list)
    for i, day in enumerate(dates):
        by[day].append(i)
    r3_i, xs_i = idx["r3"], idx["xs10"]
    for idxs in by.values():
        top_r3[max(idxs, key=lambda i: x[i, r3_i])] = 1.0
        top_xs[max(idxs, key=lambda i: x[i, xs_i])] = 1.0
    # Keep ranks out of the logistic matrix; they are boolean atoms only.
    return Panel(
        date=date_a,
        base=np.asarray(bases),
        x=x,
        hit1=np.asarray(hit1, dtype=bool),
        hit7=np.asarray(hit7, dtype=bool),
        names=FEATURE_NAMES,
    ), top_r3, top_xs


def _col(panel: Panel, name: str) -> np.ndarray:
    return panel.x[:, panel.names.index(name)]


def _metrics(y: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    m = np.asarray(mask, dtype=bool)
    n = int(m.sum())
    k = int(y[m].sum()) if n else 0
    p = (k / n) if n else 0.0
    return {
        "n": n,
        "k": k,
        "p": round(p, 4),
        "wilson": round(_wilson(k, n), 4) if n else 0.0,
    }


def _persist(dates: np.ndarray, bases: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """True when the same name passed ``mask`` on its previous session, within 4 days."""
    out = np.zeros(len(dates), dtype=bool)
    last: dict[str, tuple[int, int]] = {}
    for i, (day, base) in enumerate(zip(dates, bases)):
        ordinal = datetime.strptime(str(day), "%Y-%m-%d").toordinal()
        prev = last.get(str(base))
        if prev is not None and mask[prev[0]] and 0 < ordinal - prev[1] <= 4:
            out[i] = True
        last[str(base)] = (i, ordinal)
    return out


def _atoms(panel: Panel, top_r3: np.ndarray, top_xs: np.ndarray, persist: np.ndarray) -> dict[str, np.ndarray]:
    r1, r3, r5 = _col(panel, "r1"), _col(panel, "r3"), _col(panel, "r5")
    xs3, xs10 = _col(panel, "xs3"), _col(panel, "xs10")
    volx, rngx = _col(panel, "volx"), _col(panel, "rngx")
    dayloc, loc20 = _col(panel, "dayloc"), _col(panel, "loc20")
    brk, trend, btc = _col(panel, "brk20"), _col(panel, "trend"), _col(panel, "btc_on")
    gap = _col(panel, "gap")
    return {
        "r3_25": r3 >= 0.25,
        "r3_40": r3 >= 0.40,
        "r3_60": r3 >= 0.60,
        "r5_30": r5 >= 0.30,
        "xs40": xs10 >= 0.40,
        "xs60": xs10 >= 0.60,
        "xs3_15": xs3 >= 0.15,
        "r1_08": r1 >= 0.08,
        "r1_15": r1 >= 0.15,
        "r1_25": r1 >= 0.25,
        "vol2": volx >= 2.0,
        "vol4": volx >= 4.0,
        "brk20": brk >= 1.0,
        "loc80": loc20 >= 0.80,
        "dayloc75": dayloc >= 0.75,
        "btc_on": btc >= 1.0,
        "rng2": rngx >= 2.0,
        "top1_r3": top_r3 >= 1.0,
        "top1_xs": top_xs >= 1.0,
        "gap_le_12": gap <= 0.12,
        "gap_up": gap >= 0.0,
        "persist": persist,
    }


def baseline_mask(panel: Panel) -> np.ndarray:
    """The published accuracy rule: r3≥15%, xs10≥25%, trend."""
    return (_col(panel, "r3") >= 0.15) & (_col(panel, "xs10") >= 0.25) & (_col(panel, "trend") >= 1.0)


def refine(
    y_fit: np.ndarray,
    atoms_fit: dict[str, np.ndarray],
    y_sel: np.ndarray,
    atoms_sel: dict[str, np.ndarray],
    base_fit: np.ndarray,
    base_sel: np.ndarray,
    *,
    min_n_fit: int = 40,
    min_n_sel: int = 20,
    max_adds: int = 4,
) -> list[str]:
    """Add atoms while fit-Wilson rises and the select window still has mass.

    Select labels are a veto (sample size and a collapse in precision), not
    the objective. Test labels are not an argument.
    """
    chosen: list[str] = []
    mf = np.asarray(base_fit, dtype=bool).copy()
    ms = np.asarray(base_sel, dtype=bool).copy()
    if int(mf.sum()) < min_n_fit:
        return chosen
    floor = _wilson(int(y_fit[mf].sum()), int(mf.sum()))
    for _ in range(max_adds):
        best_name: str | None = None
        best_w = floor
        for name, col_f in atoms_fit.items():
            if name in chosen:
                continue
            nf = mf & col_f
            ns = ms & atoms_sel[name]
            n_f, n_s = int(nf.sum()), int(ns.sum())
            if n_f < min_n_fit or n_s < min_n_sel:
                continue
            p_fit = float(y_fit[nf].mean())
            p_sel = float(y_sel[ns].mean())
            if p_sel + 0.02 < p_fit * 0.45:
                continue
            w = _wilson(int(y_fit[nf].sum()), n_f)
            if w > best_w + 0.005:
                best_w = w
                best_name = name
        if best_name is None:
            break
        chosen.append(best_name)
        mf = mf & atoms_fit[best_name]
        ms = ms & atoms_sel[best_name]
        floor = best_w
    return chosen


def _apply(base: np.ndarray, atoms: dict[str, np.ndarray], names: list[str]) -> np.ndarray:
    m = np.asarray(base, dtype=bool).copy()
    for name in names:
        m = m & atoms[name]
    return m


def _fit_score(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_other: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    scaler = StandardScaler()
    z_fit = scaler.fit_transform(x_fit)
    z_other = scaler.transform(x_other)
    clf = LogisticRegression(
        C=0.5,
        class_weight="balanced",
        max_iter=400,
        solver="lbfgs",
    )
    clf.fit(z_fit, y_fit.astype(int))
    return clf.predict_proba(z_fit)[:, 1], clf.predict_proba(z_other)[:, 1]


def _best_threshold(
    y: np.ndarray,
    score: np.ndarray,
    *,
    min_n: int = 25,
) -> tuple[float, dict[str, Any]]:
    if len(score) == 0 or int(y.sum()) == 0:
        return 1.0, _metrics(y, np.zeros(len(y), dtype=bool))
    qs = np.quantile(score, np.linspace(0.50, 0.995, 40))
    best_t = float(qs[0])
    best = _metrics(y, score >= best_t)
    best_w = -1.0
    for t in qs:
        m = _metrics(y, score >= float(t))
        if m["n"] < min_n:
            continue
        if m["wilson"] > best_w:
            best_w = m["wilson"]
            best_t = float(t)
            best = m
    return best_t, best


def _top1(dates: np.ndarray, score: np.ndarray, mask: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    best_i: dict[str, int] = {}
    for i in np.flatnonzero(mask):
        day = str(dates[i])
        prev = best_i.get(day)
        if prev is None or score[i] > score[prev]:
            best_i[day] = int(i)
    if not best_i:
        return {"n": 0, "k": 0, "p": 0.0, "wilson": 0.0}
    idx = np.fromiter(best_i.values(), dtype=int)
    return _metrics(y, np.isin(np.arange(len(y)), idx))


def _split_masks(dates: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fit = dates <= FIT_END
    sel = (dates > FIT_END) & (dates <= SEL_END)
    test = dates > SEL_END
    return fit, sel, test


def run_precision_max(
    *,
    candle_dir: Path | str = "data/ignition_expand_candles",
) -> dict[str, Any]:
    series = load_series(candle_dir)
    panel, top_r3, top_xs = build_panel(series)
    base = baseline_mask(panel)
    persist = _persist(panel.date, panel.base, base)
    atoms = _atoms(panel, top_r3, top_xs, persist)
    fit_m, sel_m, te_m = _split_masks(panel.date)
    # Gap is known at the next open, so it is an entry filter, not a score input.
    model_idx = [panel.names.index(n) for n in SCORE_FEATURES]
    x_model = panel.x[:, model_idx]
    model_names = list(SCORE_FEATURES)

    horizons: dict[str, Any] = {}
    for label, y in (("hit7", panel.hit7), ("hit1", panel.hit1)):
        y_fit, y_sel, y_te = y[fit_m], y[sel_m], y[te_m]
        atoms_fit = {k: v[fit_m] for k, v in atoms.items()}
        atoms_sel = {k: v[sel_m] for k, v in atoms.items()}
        atoms_te = {k: v[te_m] for k, v in atoms.items()}
        added = refine(
            y_fit,
            atoms_fit,
            y_sel,
            atoms_sel,
            base[fit_m],
            base[sel_m],
        )
        rule_fit = _apply(base[fit_m], atoms_fit, added)
        rule_sel = _apply(base[sel_m], atoms_sel, added)
        rule_te = _apply(base[te_m], atoms_te, added)

        score_fit, score_rest = _fit_score(x_model[fit_m], y_fit, x_model[~fit_m])
        score = np.empty(len(y), dtype=np.float64)
        score[fit_m] = score_fit
        score[~fit_m] = score_rest
        # Threshold inside the baseline, so the score can only tighten the known rule.
        gate_sel = base[sel_m]
        t, sel_at = _best_threshold(y_sel[gate_sel], score[sel_m][gate_sel], min_n=25)
        score_te_m = base[te_m] & (score[te_m] >= t)
        score_sel_m = gate_sel & (score[sel_m] >= t)
        score_fit_m = base[fit_m] & (score[fit_m] >= t)

        # Operating point = whichever frozen candidate has the higher select Wilson.
        cand = {
            "baseline": {
                "fit": _metrics(y_fit, base[fit_m]),
                "select": _metrics(y_sel, base[sel_m]),
                "test": _metrics(y_te, base[te_m]),
                "mask_te": base[te_m],
                "mask_sel": base[sel_m],
            },
            "refined": {
                "fit": _metrics(y_fit, rule_fit),
                "select": _metrics(y_sel, rule_sel),
                "test": _metrics(y_te, rule_te),
                "mask_te": rule_te,
                "mask_sel": rule_sel,
                "atoms": added,
            },
            "score": {
                "fit": _metrics(y_fit, score_fit_m),
                "select": sel_at,
                "test": _metrics(y_te, score_te_m),
                "mask_te": score_te_m,
                "mask_sel": score_sel_m,
                "threshold": round(t, 4),
            },
        }
        op_name = max(
            ("baseline", "refined", "score"),
            key=lambda k: (float(cand[k]["select"]["wilson"]), float(cand[k]["select"]["p"])),
        )
        op = cand[op_name]
        # Rank inside the chosen mask by r3 (causal at the close).
        r3 = _col(panel, "r3")
        top = {
            "select": _top1(panel.date[sel_m], r3[sel_m], op["mask_sel"], y_sel),
            "test": _top1(panel.date[te_m], r3[te_m], op["mask_te"], y_te),
        }
        coef = {}
        if op_name == "score":
            # Refit is already done; expose coefficients from a fresh fit on the same fit rows.
            scaler = StandardScaler().fit(x_model[fit_m])
            clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=400, solver="lbfgs")
            clf.fit(scaler.transform(x_model[fit_m]), y_fit.astype(int))
            coef = {
                name: round(float(w), 3)
                for name, w in sorted(zip(model_names, clf.coef_[0]), key=lambda kv: -abs(kv[1]))
            }
        horizons[label] = {
            "base_rate": {
                "fit": _metrics(y_fit, np.ones(len(y_fit), dtype=bool)),
                "select": _metrics(y_sel, np.ones(len(y_sel), dtype=bool)),
                "test": _metrics(y_te, np.ones(len(y_te), dtype=bool)),
            },
            "baseline": {k: cand["baseline"][k] for k in ("fit", "select", "test")},
            "refined": {
                "atoms": added,
                "fit": cand["refined"]["fit"],
                "select": cand["refined"]["select"],
                "test": cand["refined"]["test"],
            },
            "score": {
                "threshold": cand["score"]["threshold"],
                "fit": cand["score"]["fit"],
                "select": cand["score"]["select"],
                "test": cand["score"]["test"],
                "coefficients": coef,
            },
            "operating_point": op_name,
            "operating": {k: op[k] for k in ("fit", "select", "test")},
            "top1_inside_operating": top,
        }
        print(
            f"{label} op={op_name} select p={op['select']['p']:.3f} n={op['select']['n']} "
            f"test p={op['test']['p']:.3f} n={op['test']['n']} "
            f"baseline test p={cand['baseline']['test']['p']:.3f}",
            flush=True,
        )

    return {
        "asof": datetime.now(UTC).isoformat(),
        "candle_dir": str(candle_dir),
        "n_rows": int(len(panel.date)),
        "n_bases": len({b for b in panel.base}),
        "fit_end": FIT_END,
        "select_end": SEL_END,
        "label": "forward high / next open - 1 >= +50%",
        "published_baseline": "r3>=15% + xs10>=25% + trend",
        "horizons": horizons,
        "note": (
            "Operating point is the candidate with the highest Wilson lower bound "
            "on 2025. Fit is 2024. Test (2026) is not used to choose."
        ),
    }


def _fmt(block: dict[str, Any]) -> str:
    return f"{100 * float(block['p']):.1f}% (n={block['n']}, wilson≥{100 * float(block['wilson']):.1f}%)"


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Nauwkeurigheid omhoog — boven op r3≥15% + xs≥25% + trend",
        "",
        f"asof `{payload['asof']}`  rijen `{payload['n_rows']}`  bases **{payload['n_bases']}**",
        "",
        payload["note"],
        "",
        f"Label: {payload['label']}. Baseline: `{payload['published_baseline']}`.",
        f"Fit ≤ `{payload['fit_end']}` · select = 2025 · test 2026.",
        "Select is het hele jaar 2025. Een half jaar (2025-H2) had te weinig +50%-dagen om een strakkere regel van de baseline te onderscheiden.",
        "De scoredrempel is een rangorde uit een gebalanceerd model, geen gekalibreerde kans.",
        "",
        "## Kort",
        "",
    ]
    h7 = payload["horizons"]["hit7"]
    h1 = payload["horizons"]["hit1"]
    lines += [
        f"+50% binnen 7 dagen: baseline test **{_fmt(h7['baseline']['test'])}**, "
        f"operating **{_fmt(h7['operating']['test'])}** "
        f"(2025: baseline {_fmt(h7['baseline']['select'])} → operating {_fmt(h7['operating']['select'])}).",
        f"+50% binnen 1 dag: baseline test **{_fmt(h1['baseline']['test'])}**, "
        f"operating **{_fmt(h1['operating']['test'])}** "
        f"(2025: baseline {_fmt(h1['baseline']['select'])} → operating {_fmt(h1['operating']['select'])}).",
        "Zelfde startregel (`r3≥15% + xs≥25% + trend`). De score houdt alleen de hoogste rangen daarvan over. "
        "Het zwaarste gewicht op 7 dagen is BTC boven zijn SMA50.",
        "",
    ]
    titles = {
        "hit7": "+50% binnen 7 dagen",
        "hit1": "+50% binnen 1 dag",
    }
    for key, title in titles.items():
        h = payload["horizons"][key]
        op = h["operating_point"]
        lines += [
            f"## {title}",
            "",
            f"Gekozen op select: **`{op}`**.",
            "",
            "| Punt | Fit | Select | Test 2026 |",
            "|---|---|---|---|",
            f"| basiskans | {_fmt(h['base_rate']['fit'])} | {_fmt(h['base_rate']['select'])} | {_fmt(h['base_rate']['test'])} |",
            f"| baseline | {_fmt(h['baseline']['fit'])} | {_fmt(h['baseline']['select'])} | {_fmt(h['baseline']['test'])} |",
            f"| extra filters | {_fmt(h['refined']['fit'])} | {_fmt(h['refined']['select'])} | {_fmt(h['refined']['test'])} |",
            f"| score binnen baseline | {_fmt(h['score']['fit'])} | {_fmt(h['score']['select'])} | {_fmt(h['score']['test'])} |",
            f"| **operating `{op}`** | {_fmt(h['operating']['fit'])} | {_fmt(h['operating']['select'])} | {_fmt(h['operating']['test'])} |",
            "",
        ]
        atoms = h["refined"]["atoms"]
        lines.append(
            "Extra filters op de baseline: "
            + (", ".join(f"`{a}`" for a in atoms) if atoms else "geen — niets hield de select-steekproef overeind.")
        )
        lines.append(
            f"Score-drempel ≥ `{h['score']['threshold']}` alleen binnen de baseline."
        )
        top = h["top1_inside_operating"]
        lines += [
            "",
            "Eén naam per dag (hoogste 3-daagse return binnen het operating masker):",
            "",
            f"- select {_fmt(top['select'])}",
            f"- test {_fmt(top['test'])}",
            "",
        ]
        if h["score"].get("coefficients"):
            coef = ", ".join(f"`{k}` {v:+.2f}" for k, v in list(h["score"]["coefficients"].items())[:8])
            lines.append(f"Logistische gewichten (fit, gestandaardiseerd): {coef}.")
            lines.append("")
    lines += [
        "## Hoe te lezen",
        "",
        "1. De baseline is het eerdere onderzoek (`RESULTS.md`): ongeveer 16% op +50%/7d in 2026.",
        "2. Een extra filter telt alleen als de fit-Wilson stijgt én de select-periode nog minstens 20 signalen houdt zonder dat de precisie daar instort.",
        "3. Het operating point is de kandidaat met de hoogste select-Wilson. De testkolom is de meting, niet de keuze.",
        "4. Eén naam per dag is de precisie van een sleeve die uit de gefilterde namen de sterkste 3-daagse return pakt.",
        "",
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.moonshot_preimage.precision_max",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Raise moonshot precision above the published rule")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    args = p.parse_args()
    payload = run_precision_max(candle_dir=args.candles)
    md = to_markdown(payload)
    pkg = Path(__file__).resolve().parent
    (pkg / "PRECISION.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "PRECISION.md").write_text(md, encoding="utf-8")
    print(f"wrote {pkg / 'PRECISION.md'}", flush=True)


if __name__ == "__main__":
    main()
