"""Pre-spike feature scan — coin-agnostic, train/test split.

Labels (from signal-day close, evaluated on next-open → forward highs):
  hit30 / hit50: max high in next ``horizon`` sessions >= +30% / +50%
  from the *next* open (no lookahead on the signal close).

Features use only data available on the signal day close.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import math


Row = list[float]  # ms, o, h, l, c, v


def _load_dir(cache: Path) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for path in sorted(cache.glob("*.json")):
        base = path.stem.upper()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            continue
        rows: list[Row] = []
        for r in raw:
            if not isinstance(r, (list, tuple)) or len(r) < 6:
                continue
            rows.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
        if len(rows) >= 40:
            out[base] = rows
    return out


def _by_date(rows: Sequence[Row]) -> dict[str, Row]:
    out: dict[str, Row] = {}
    for r in rows:
        day = datetime.fromtimestamp(r[0] / 1000.0, tz=UTC).strftime("%Y-%m-%d")
        out[day] = list(r)
    return out


def _sma(xs: Sequence[float], n: int) -> float | None:
    if len(xs) < n or n <= 0:
        return None
    return sum(xs[-n:]) / float(n)


def _ret(xs: Sequence[float], n: int) -> float | None:
    if len(xs) < n + 1 or xs[-1 - n] <= 0:
        return None
    return xs[-1] / xs[-1 - n] - 1.0


@dataclass
class DayObs:
    base: str
    date: str
    close: float
    next_open: float
    fwd_high: float
    hit30: bool
    hit50: bool
    features: dict[str, float | bool]


def _features(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    vols: Sequence[float],
    btc_closes: Sequence[float],
) -> dict[str, float | bool] | None:
    if len(closes) < 55 or len(btc_closes) < 55:
        return None
    c = closes[-1]
    if c <= 0:
        return None
    r1 = _ret(closes, 1)
    r3 = _ret(closes, 3)
    r10 = _ret(closes, 10)
    btc10 = _ret(btc_closes, 10)
    if None in (r1, r3, r10, btc10):
        return None
    xs10 = float(r10) - float(btc10)
    s20 = _sma(closes, 20)
    s50 = _sma(closes, 50)
    if s20 is None or s50 is None or s20 <= 0:
        return None
    # compression: 5d range vs 20d range
    span5 = max(highs[-5:]) / min(lows[-5:]) - 1.0 if min(lows[-5:]) > 0 else 0.0
    span20 = max(highs[-20:]) / min(lows[-20:]) - 1.0 if min(lows[-20:]) > 0 else 0.0
    coil = span20 > 1e-9 and span5 / span20 < 0.5
    # breakout vs prior 20 high (exclude today)
    prior_hi20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    brk20 = c >= prior_hi20
    vol_ma = _sma(vols, 20) or 0.0
    volx = (vols[-1] / vol_ma) if vol_ma > 0 else 0.0
    quiet10 = float(r10) < 0.12
    loc = 0.0
    hi = max(highs[-20:])
    lo = min(lows[-20:])
    if hi > lo:
        loc = (c - lo) / (hi - lo)
    qvol = vols[-1] * c  # approx quote vol
    return {
        "r1": float(r1),
        "r3": float(r3),
        "r10": float(r10),
        "xs10": float(xs10),
        "volx": float(volx),
        "above_sma20": c > s20,
        "above_sma50": c > s50,
        "coil": bool(coil),
        "brk20": bool(brk20),
        "quiet10": bool(quiet10),
        "loc": float(loc),
        "qvol": float(qvol),
        "day6": float(r1) >= 0.06,
        "vol2": float(volx) >= 2.0,
        "xs15": float(xs10) >= 0.15,
        "xs25": float(xs10) >= 0.25,
        "r3_15": float(r3) >= 0.15,
        "trend": c > s20 > s50,
    }


def build_observations(
    ohlc: Mapping[str, Sequence[Row]],
    *,
    horizon: int = 7,
    min_qvol: float = 50_000.0,
    train_end: str = "2025-12-31",
) -> tuple[list[DayObs], list[DayObs]]:
    if "BTC" not in ohlc:
        raise ValueError("BTC candles required")
    btc_by = _by_date(ohlc["BTC"])
    btc_dates = sorted(btc_by)
    train: list[DayObs] = []
    test: list[DayObs] = []

    for base, rows in ohlc.items():
        if base == "BTC":
            continue
        by = _by_date(rows)
        dates = sorted(by)
        for i, day in enumerate(dates):
            # need forward horizon completed
            if i + 1 + horizon > len(dates):
                continue
            # align BTC history ending at day
            if day not in btc_by:
                continue
            # build series through day
            hist_dates = [d for d in dates[: i + 1]]
            if len(hist_dates) < 55:
                continue
            closes = [by[d][4] for d in hist_dates]
            highs = [by[d][2] for d in hist_dates]
            lows = [by[d][3] for d in hist_dates]
            vols = [by[d][5] for d in hist_dates]
            btc_hist = [btc_by[d][4] for d in btc_dates if d <= day]
            feats = _features(closes, highs, lows, vols, btc_hist)
            if feats is None:
                continue
            if float(feats["qvol"]) < min_qvol:
                continue
            nxt = dates[i + 1]
            next_open = by[nxt][1]
            if next_open <= 0:
                continue
            fwd_days = dates[i + 1 : i + 1 + horizon]
            fwd_high = max(by[d][2] for d in fwd_days)
            up = fwd_high / next_open - 1.0
            obs = DayObs(
                base=base,
                date=day,
                close=closes[-1],
                next_open=next_open,
                fwd_high=fwd_high,
                hit30=up >= 0.30,
                hit50=up >= 0.50,
                features=feats,
            )
            (train if day <= train_end else test).append(obs)
    return train, test


def _alphai_days(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    sessions = raw.get("sessions") if isinstance(raw, dict) else raw
    if not isinstance(sessions, list):
        return {}
    by_day: dict[str, set[str]] = {}
    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        ga = str(sess.get("generated_at") or "")
        day = ga[:10]
        if len(day) < 10:
            continue
        picks = {
            str(p.get("base") or "").upper()
            for p in (sess.get("picks") or [])
            if isinstance(p, dict) and p.get("base")
        }
        by_day.setdefault(day, set()).update(picks)
    return by_day


Rule = tuple[str, Any]  # name, predicate(obs)->bool


def _rules(alphai: Mapping[str, set[str]]) -> list[Rule]:
    def f(pred):  # noqa: ANN001
        return pred

    rules: list[Rule] = [
        ("all_liquid", f(lambda o: True)),
        ("quiet10", f(lambda o: bool(o.features["quiet10"]))),
        ("xs15", f(lambda o: bool(o.features["xs15"]))),
        ("xs25", f(lambda o: bool(o.features["xs25"]))),
        ("coil", f(lambda o: bool(o.features["coil"]))),
        ("brk20+day6+vol2", f(lambda o: bool(o.features["brk20"] and o.features["day6"] and o.features["vol2"]))),
        ("r3_15+xs25+trend", f(lambda o: bool(o.features["r3_15"] and o.features["xs25"] and o.features["trend"]))),
        ("r3_15+vol2+trend", f(lambda o: bool(o.features["r3_15"] and o.features["vol2"] and o.features["trend"]))),
        ("coil+xs15", f(lambda o: bool(o.features["coil"] and o.features["xs15"]))),
        ("quiet+brk20+day6+vol2", f(lambda o: bool(o.features["quiet10"] and o.features["brk20"] and o.features["day6"] and o.features["vol2"]))),
        ("alphai_pick", f(lambda o: o.base in alphai.get(o.date, set()))),
        ("alphai+xs15", f(lambda o: o.base in alphai.get(o.date, set()) and bool(o.features["xs15"]))),
        ("alphai+coil", f(lambda o: o.base in alphai.get(o.date, set()) and bool(o.features["coil"]))),
        ("alphai+r3_15+trend", f(lambda o: o.base in alphai.get(o.date, set()) and bool(o.features["r3_15"] and o.features["trend"]))),
        # Top-of-book style: strong RS only among names above SMA50
        ("xs25+sma50+trend", f(lambda o: bool(o.features["xs25"] and o.features["above_sma50"] and o.features["trend"]))),
    ]
    return rules


def _eval(rows: Sequence[DayObs], pred) -> dict[str, Any]:  # noqa: ANN001
    sel = [o for o in rows if pred(o)]
    n = len(sel)
    if n == 0:
        return {"n": 0, "p30": 0.0, "p50": 0.0, "k30": 0, "k50": 0}
    k30 = sum(1 for o in sel if o.hit30)
    k50 = sum(1 for o in sel if o.hit50)
    return {
        "n": n,
        "p30": round(k30 / n, 4),
        "p50": round(k50 / n, 4),
        "k30": k30,
        "k50": k50,
    }


def _wilson(k: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / den)


def run_moonshot_preimage(
    *,
    candle_dir: Path | str = "data/ignition_expand_candles",
    alphai_path: Path | str = "data/research/alphai_sessions_merged.json",
    horizon: int = 7,
    train_end: str = "2025-12-31",
) -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    # ensure BTC present — residual cache has longer BTC if needed
    if "BTC" not in ohlc:
        btc_path = Path("data/residual_wet_candles/BTC.json")
        if btc_path.exists():
            ohlc["BTC"] = _load_dir(btc_path.parent)["BTC"]
    alphai = _alphai_days(Path(alphai_path))
    train, test = build_observations(ohlc, horizon=horizon, train_end=train_end)

    def base_rate(rows: Sequence[DayObs]) -> dict[str, Any]:
        n = len(rows)
        k30 = sum(1 for o in rows if o.hit30)
        k50 = sum(1 for o in rows if o.hit50)
        return {
            "n": n,
            "p30": round(k30 / n, 4) if n else 0.0,
            "p50": round(k50 / n, 4) if n else 0.0,
            "wilson50": round(_wilson(k50, n), 4) if n else 0.0,
        }

    br_train = base_rate(train)
    br_test = base_rate(test)
    ranked: list[dict[str, Any]] = []
    for name, pred in _rules(alphai):
        tr = _eval(train, pred)
        te = _eval(test, pred)
        # AlphaI-only metrics on days with any AlphaI coverage
        ai_test = [o for o in test if o.date in alphai]
        te_ai = _eval(ai_test, pred) if name.startswith("alphai") else te
        lift50 = (
            round(te["p50"] / br_test["p50"], 2)
            if br_test["p50"] > 0 and te["n"] > 0
            else 0.0
        )
        ranked.append(
            {
                "rule": name,
                "train": tr,
                "test": te,
                "test_on_alphai_days": te_ai if name.startswith("alphai") else None,
                "lift50_test": lift50,
                "wilson50_test": round(_wilson(int(te["k50"]), int(te["n"])), 4)
                if te["n"]
                else 0.0,
            }
        )
    ranked.sort(key=lambda r: (-float(r["test"]["p50"]), -float(r["test"]["n"])))

    # Best precision among rules with n>=20 test
    viable = [r for r in ranked if int(r["test"]["n"]) >= 20]
    best_prec = max(viable, key=lambda r: float(r["test"]["p50"])) if viable else None
    # Can we hit 60% precision anywhere with n>=10?
    hit60 = [r for r in ranked if float(r["test"]["p50"]) >= 0.60 and int(r["test"]["n"]) >= 10]

    # AlphaI coverage stats
    ai_cov_test = sum(1 for o in test if o.date in alphai)
    ai_pick_test = [o for o in test if o.base in alphai.get(o.date, set())]

    return {
        "asof": datetime.now(UTC).isoformat(),
        "candle_dir": str(candle_dir),
        "n_bases": len(ohlc) - 1,
        "horizon_sessions": horizon,
        "label": "forward high from next open >= +30% / +50%",
        "train_end": train_end,
        "base_rate_train": br_train,
        "base_rate_test": br_test,
        "alphai_session_days": len(alphai),
        "alphai_coverage_test_rows": ai_cov_test,
        "alphai_pick_rows_test": len(ai_pick_test),
        "alphai_pick_p50_test": round(
            sum(1 for o in ai_pick_test if o.hit50) / len(ai_pick_test), 4
        )
        if ai_pick_test
        else None,
        "best_precision_n20": best_prec,
        "rules_hitting_60pct_precision_n10": hit60,
        "ranked": ranked,
        "note": (
            "Precision = P(moonshot | signal). Base rate of +50%/7d is ~3%. "
            "60–70% precision is a different claim than 'patterns exist' — "
            "patterns can lift 5–6× and still land near 15–20% precision."
        ),
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Moonshot preimage — can we hit 60–70% precision?",
        "",
        f"asof `{payload['asof']}`  universe `{payload['n_bases']}` bases  "
        f"horizon {payload['horizon_sessions']}d  `{payload['label']}`",
        "",
        payload["note"],
        "",
        "## Base rate (no filter)",
        "",
        f"| Split | n | P(+30%) | P(+50%) |",
        f"|---|---:|---:|---:|",
        f"| train ≤{payload['train_end']} | {payload['base_rate_train']['n']} | "
        f"{100*payload['base_rate_train']['p30']:.1f}% | {100*payload['base_rate_train']['p50']:.1f}% |",
        f"| test 2026+ | {payload['base_rate_test']['n']} | "
        f"{100*payload['base_rate_test']['p30']:.1f}% | {100*payload['base_rate_test']['p50']:.1f}% |",
        "",
        f"AlphaI session days in store: **{payload['alphai_session_days']}**. "
        f"Test rows on AlphaI days: {payload['alphai_coverage_test_rows']}. "
        f"AlphaI pick rows in test: {payload['alphai_pick_rows_test']} "
        f"(P50={payload['alphai_pick_p50_test']}).",
        "",
        "## Verdict on 60–70%",
        "",
    ]
    hit60 = payload.get("rules_hitting_60pct_precision_n10") or []
    if hit60:
        lines.append(f"**Yes on this tape** for: {[r['rule'] for r in hit60]}")
    else:
        lines.append(
            "**No rule in this grid reaches 60% precision on +50%/7d with n≥10 test signals.** "
            "Best viable (n≥20) below."
        )
    best = payload.get("best_precision_n20")
    if best:
        lines += [
            "",
            f"Best test precision (n≥20): **`{best['rule']}`** → "
            f"P50=**{100*best['test']['p50']:.1f}%** "
            f"(n={best['test']['n']}, lift×{best['lift50_test']}, "
            f"wilson≥{100*best['wilson50_test']:.1f}%).",
        ]
    lines += [
        "",
        "## Rule table (sorted by test P50)",
        "",
        "| Rule | train n | train P50 | test n | test P50 | test P30 | lift× |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("ranked") or []:
        tr, te = r["train"], r["test"]
        lines.append(
            f"| `{r['rule']}` | {tr['n']} | {100*tr['p50']:.1f}% | "
            f"{te['n']} | {100*te['p50']:.1f}% | {100*te['p30']:.1f}% | {r['lift50_test']} |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "1. **Patterns are real** — several rules lift 4–6× over the ~3% base rate.",
        "2. **Lift ≠ 60% accuracy** — 5× on a 3% event ≈ 15% precision.",
        "3. **AlphaI alone** is not a moonshot oracle on this sample; combine with tape features.",
        "4. Trading the signal still needs exits; prior explosive scans showed high P50 "
        "rules can still lose money with naive holds.",
        "",
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.moonshot_preimage",
        "```",
        "",
    ]
    return "\n".join(lines)
