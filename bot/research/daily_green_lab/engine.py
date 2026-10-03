"""Daily-active sleeve grid for a fixed small book (€1.7k default).

Each pack tries to be **in market every risk-on day** (or every calendar day),
optionally steered by AlphaI picks. Ranked by green-day rate among packs that
stay active, then by avg day PnL / worst day.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


Row = list[float]


def _load_dir(cache: Path) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for path in sorted(cache.glob("*.json")):
        base = path.stem.upper()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            continue
        rows = [
            [float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
            for r in raw
            if isinstance(r, (list, tuple)) and len(r) >= 6
        ]
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
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def _ret(xs: Sequence[float], n: int) -> float | None:
    if len(xs) < n + 1 or xs[-1 - n] <= 0:
        return None
    return xs[-1] / xs[-1 - n] - 1.0


def _alphai_daily(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    sessions = raw.get("sessions") if isinstance(raw, dict) else raw
    if not isinstance(sessions, list):
        return {}
    # latest session per day → ordered picks
    latest: dict[str, tuple[str, list[str]]] = {}
    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        ga = str(sess.get("generated_at") or "")
        day = ga[:10]
        if len(day) < 10:
            continue
        picks = sorted(
            [p for p in (sess.get("picks") or []) if isinstance(p, dict) and p.get("base")],
            key=lambda p: (
                int(p["rank"]) if p.get("rank") is not None else 999,
                -float(p.get("score") or 0),
            ),
        )
        names = [str(p["base"]).upper() for p in picks]
        prev = latest.get(day)
        if prev is None or ga >= prev[0]:
            latest[day] = (ga, names)
    return {d: names for d, (_ga, names) in latest.items()}


@dataclass(frozen=True)
class Spec:
    name: str
    pick: str  # top_day | top_rs10 | alphai1 | alphai_or_rs | rs_in_alphai
    trail_pct: float
    time_max_days: int
    require_btc_sma: bool
    excess_floor: float
    lookback: int
    # Enter even if BTC below SMA when False risk filter off for alt-only day trades
    force_daily: bool


def specs() -> list[Spec]:
    out: list[Spec] = []
    for pick in ("top_day", "top_rs10", "alphai1", "alphai_or_rs", "rs_in_alphai"):
        for trail in (0.03, 0.05, 0.08, 0.10, 0.12):
            for tmax in (1, 2, 3, 5):
                for btc_sma in (True, False):
                    for floor in (0.0, 0.02, 0.04, 0.08):
                        if pick.startswith("alphai") and floor > 0.02:
                            continue  # AlphaI packs: lighter RS floors
                        name = (
                            f"{pick}_t{int(trail*100)}_h{tmax}"
                            f"_btc{int(btc_sma)}_fl{floor:.2f}"
                        )
                        out.append(
                            Spec(
                                name=name,
                                pick=pick,
                                trail_pct=trail,
                                time_max_days=tmax,
                                require_btc_sma=btc_sma,
                                excess_floor=floor,
                                lookback=10 if "rs" in pick or pick == "top_rs10" else 1,
                                force_daily=not btc_sma,
                            )
                        )
    # Dedup
    seen: set[str] = set()
    uniq: list[Spec] = []
    for s in out:
        if s.name in seen:
            continue
        seen.add(s.name)
        uniq.append(s)
    return uniq


def _rank_day(
    ohlc_by: Mapping[str, dict[str, Row]],
    date: str,
    dates_by_base: Mapping[str, list[str]],
    *,
    mode: str,
    lookback: int,
    excess_floor: float,
    alphai: Sequence[str],
    btc_dates: Sequence[str],
) -> str | None:
    """Pick one alt for ``date`` (signal on close → enter next open)."""
    if "BTC" not in ohlc_by or date not in ohlc_by["BTC"]:
        return None
    btc_hist = [d for d in btc_dates if d <= date]
    if len(btc_hist) < lookback + 2:
        return None
    btc_c = [float(ohlc_by["BTC"][d][4]) for d in btc_hist]

    scored: list[tuple[float, str]] = []
    for base, by in ohlc_by.items():
        if base == "BTC":
            continue
        hist = [d for d in dates_by_base[base] if d <= date]
        if len(hist) < max(lookback + 2, 5):
            continue
        closes = [float(by[d][4]) for d in hist]
        # day return
        r1 = _ret(closes, 1)
        if r1 is None:
            continue
        xs = None
        if lookback >= 3:
            a = _ret(closes, lookback)
            b = _ret(btc_c, lookback)
            if a is not None and b is not None:
                xs = a - b
        qvol = float(by[hist[-1]][5]) * float(by[hist[-1]][4])
        if qvol < 50_000:
            continue
        if mode == "top_day":
            scored.append((float(r1), base))
        elif mode == "top_rs10":
            if xs is None or xs < excess_floor:
                continue
            scored.append((float(xs), base))
        elif mode == "alphai1":
            continue  # handled below
        elif mode == "alphai_or_rs":
            if alphai and base == alphai[0]:
                scored.append((1e6 + float(xs or r1), base))
            elif xs is not None and xs >= excess_floor:
                scored.append((float(xs), base))
        elif mode == "rs_in_alphai":
            if not alphai or base not in alphai:
                continue
            if xs is None:
                scored.append((float(r1), base))
            elif xs >= excess_floor:
                scored.append((float(xs), base))

    if mode == "alphai1":
        for b in alphai:
            if b in ohlc_by and date in ohlc_by[b]:
                return b
        return None

    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][1]


def simulate(
    ohlc: Mapping[str, Sequence[Row]],
    spec: Spec,
    *,
    start: str,
    end: str,
    book: float,
    alphai_by_day: Mapping[str, Sequence[str]],
    fee: float = 0.0015,
    slip: float = 0.001,
) -> dict[str, Any]:
    by = {b: _by_date(rows) for b, rows in ohlc.items()}
    dates_by = {b: sorted(m) for b, m in by.items()}
    btc_dates = dates_by.get("BTC") or []
    cal = [d for d in btc_dates if start <= d <= end]
    if len(cal) < 5:
        return {"name": spec.name, "ok": False, "reason": "short_window"}

    cash = book
    pos: dict[str, Any] | None = None
    pending_buy: str | None = None
    pending_sell = False
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    prev_eq = book

    for i, date in enumerate(cal):
        row_btc = by["BTC"][date]
        # Fill pending at today's open
        if pending_sell and pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                px = float(by[base][date][1]) * (1 - slip)
                proceeds = pos["qty"] * px
                fee_eur = proceeds * fee
                pnl = proceeds - fee_eur - pos["cost"]
                cash += proceeds - fee_eur
                trades.append(
                    {
                        "date": date,
                        "side": "sell",
                        "base": base,
                        "pnl": round(pnl, 2),
                        "reason": pos.get("exit_reason") or "exit",
                    }
                )
                pos = None
            pending_sell = False

        if pending_buy and pos is None:
            base = pending_buy
            if date in by.get(base, {}):
                px = float(by[base][date][1]) * (1 + slip)
                notion = min(cash * 0.98, book)  # fixed book sizing
                if notion >= 40 and px > 0:
                    fee_eur = notion * fee
                    qty = notion / px
                    cash -= notion + fee_eur
                    pos = {
                        "base": base,
                        "qty": qty,
                        "cost": notion + fee_eur,
                        "entry": px,
                        "peak": px,
                        "opened": date,
                        "days": 0,
                    }
                    trades.append(
                        {
                            "date": date,
                            "side": "buy",
                            "base": base,
                            "pnl": 0.0,
                            "reason": spec.pick,
                            "notion": round(notion, 2),
                        }
                    )
            pending_buy = None

        # Mark
        eq = cash
        if pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                hi = float(by[base][date][2])
                cl = float(by[base][date][4])
                pos["peak"] = max(float(pos["peak"]), hi)
                pos["days"] = int(pos.get("days") or 0) + 1
                eq += pos["qty"] * cl
                # Exit checks on close (trail / time)
                trail_hit = cl <= float(pos["peak"]) * (1 - spec.trail_pct)
                time_hit = int(pos["days"]) >= int(spec.time_max_days)
                if trail_hit or time_hit:
                    pos["exit_reason"] = "alt_trail" if trail_hit else "time_stop"
                    pending_sell = True

        # BTC regime
        btc_hist = [float(by["BTC"][d][4]) for d in btc_dates if d <= date]
        s50 = _sma(btc_hist, 50)
        risk_on = s50 is not None and btc_hist[-1] > s50
        allow = risk_on or spec.force_daily or not spec.require_btc_sma

        # Signal for next-day entry if flat (or replacing after pending sell)
        if pos is None and not pending_sell and allow:
            ai = list(alphai_by_day.get(date) or [])
            # Forward-fill AlphaI within window
            if not ai:
                # last known
                for d in reversed(cal[: i + 1]):
                    if d in alphai_by_day:
                        ai = list(alphai_by_day[d])
                        break
            pick = _rank_day(
                by,
                date,
                dates_by,
                mode=spec.pick,
                lookback=max(spec.lookback, 10 if "rs" in spec.pick else 1),
                excess_floor=spec.excess_floor,
                alphai=ai,
                btc_dates=btc_dates,
            )
            if pick:
                pending_buy = pick
        elif pos is None and not pending_sell and not allow:
            pending_buy = None

        day_pnl = eq - prev_eq
        daily.append(
            {
                "date": date,
                "equity": round(eq, 2),
                "day_pnl": round(day_pnl, 2),
                "cum_pnl": round(eq - book, 2),
                "hold": pos["base"] if pos else "cash",
                "in_market": pos is not None,
            }
        )
        prev_eq = eq

    # Liquidate last mark
    if pos is not None:
        base = pos["base"]
        last = cal[-1]
        if last in by.get(base, {}):
            px = float(by[base][last][4]) * (1 - slip)
            proceeds = pos["qty"] * px
            fee_eur = proceeds * fee
            cash += proceeds - fee_eur
            trades.append(
                {
                    "date": last,
                    "side": "sell",
                    "base": base,
                    "pnl": round(proceeds - fee_eur - pos["cost"], 2),
                    "reason": "eow",
                }
            )

    pnls = [float(d["day_pnl"]) for d in daily]
    n = len(pnls) or 1
    green = sum(1 for p in pnls if p > 0)
    red = sum(1 for p in pnls if p < 0)
    in_mkt = sum(1 for d in daily if d["in_market"])
    hit100 = sum(1 for p in pnls if p >= 100)
    peak = book
    max_dd = 0.0
    for d in daily:
        eq = float(d["equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    avg = sum(pnls) / n
    return {
        "ok": True,
        "name": spec.name,
        "pick": spec.pick,
        "trail_pct": spec.trail_pct,
        "time_max_days": spec.time_max_days,
        "require_btc_sma": spec.require_btc_sma,
        "excess_floor": spec.excess_floor,
        "pnl_eur": round(float(daily[-1]["cum_pnl"]), 2) if daily else 0.0,
        "avg_day_pnl": round(avg, 2),
        "median_day_pnl": round(sorted(pnls)[n // 2], 2),
        "pct_days_green": round(green / n, 4),
        "n_days_green": green,
        "n_days_red": red,
        "n_days": n,
        "pct_days_in_market": round(in_mkt / n, 4),
        "n_days_hit_100": hit100,
        "pct_days_hit_100": round(hit100 / n, 4),
        "best_day": round(max(pnls), 2) if pnls else 0.0,
        "worst_day": round(min(pnls), 2) if pnls else 0.0,
        "max_dd_pct": round(max_dd, 4),
        "n_trades": len(trades),
        "daily": daily,
        "trades": trades,
        # Objective: active + green first, then avg day, then less-bad worst day
        "score": round(
            1000 * (in_mkt / n)  # activity
            + 2000 * (green / n)  # green days
            + avg  # euro/day
            + 0.1 * (min(pnls) if pnls else 0)  # worst day penalty
            - 50 * max_dd,
            4,
        ),
    }


def run_daily_green_lab(
    *,
    candle_dir: Path | str = "data/residual_wet_candles",
    alphai_path: Path | str = "data/research/alphai_sessions_merged.json",
    book: float = 1_700.0,
    days: int = 45,
) -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    if "BTC" not in ohlc:
        raise ValueError("BTC required")
    btc_dates = sorted(_by_date(ohlc["BTC"]))
    end = btc_dates[-1]
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    start = (end_dt - timedelta(days=days)).strftime("%Y-%m-%d")
    start = next(d for d in btc_dates if d >= start)
    alphai = _alphai_daily(Path(alphai_path))

    # Two windows: recent tape + AlphaI-overlap only
    ai_days = sorted(d for d in alphai if start <= d <= end)
    windows = {
        "last_period": (start, end),
    }
    if ai_days:
        windows["alphai_overlap"] = (ai_days[0], min(ai_days[-1], end))

    all_specs = specs()
    results: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": book,
        "n_specs": len(all_specs),
        "note": (
            "+€100/day on €1.7k is ~5.9%/day — structurally unavailable every day. "
            "This lab maximizes daily activity + green-day rate + avg €/day."
        ),
        "windows": {},
    }

    for wname, (w0, w1) in windows.items():
        rows: list[dict[str, Any]] = []
        for spec in all_specs:
            # Skip AlphaI-only packs on windows with no picks
            if spec.pick.startswith("alphai") and not any(
                w0 <= d <= w1 for d in alphai
            ):
                continue
            row = simulate(
                ohlc,
                spec,
                start=w0,
                end=w1,
                book=book,
                alphai_by_day=alphai,
            )
            if row.get("ok"):
                # Drop bulky series from ranked list
                slim = {k: v for k, v in row.items() if k not in {"daily", "trades"}}
                rows.append(slim)
        rows.sort(key=lambda r: (-float(r["score"]), -float(r["avg_day_pnl"])))
        active = [r for r in rows if float(r["pct_days_in_market"]) >= 0.7]
        greenish = [
            r
            for r in active
            if float(r["pct_days_green"]) >= 0.45 and float(r["avg_day_pnl"]) > 0
        ]
        best_active = active[0] if active else None
        best_green = (
            max(greenish, key=lambda r: (r["pct_days_green"], r["avg_day_pnl"]))
            if greenish
            else None
        )
        best_avg = (
            max(rows, key=lambda r: float(r["avg_day_pnl"])) if rows else None
        )
        hit100 = [
            r for r in rows if float(r["pct_days_hit_100"]) >= 0.2 and float(r["avg_day_pnl"]) > 0
        ]
        results["windows"][wname] = {
            "start": w0,
            "end": w1,
            "n_rows": len(rows),
            "best_score": rows[0] if rows else None,
            "best_active_green": best_green or best_active,
            "best_avg_day": best_avg,
            "closest_to_100_per_day": max(
                rows, key=lambda r: float(r["avg_day_pnl"])
            )
            if rows
            else None,
            "n_hit100_ge_20pct_days": len(hit100),
            "top": rows[:15],
        }
        # Keep full daily for the winner
        if rows:
            winner_name = (best_green or rows[0])["name"]
            full = next(
                simulate(ohlc, s, start=w0, end=w1, book=book, alphai_by_day=alphai)
                for s in all_specs
                if s.name == winner_name
            )
            results["windows"][wname]["winner_daily"] = full.get("daily")
            results["windows"][wname]["winner_trades"] = full.get("trades")
            results["windows"][wname]["winner"] = {
                k: v for k, v in full.items() if k not in {"daily", "trades"}
            }

    return results


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Daily-green lab — €1.7k active sleeve",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"specs `{payload.get('n_specs')}`",
        "",
        payload.get("note", ""),
        "",
    ]
    for wname, block in (payload.get("windows") or {}).items():
        lines += [
            f"## {wname}  `{block.get('start')}` → `{block.get('end')}`",
            "",
            f"packs evaluated: {block.get('n_rows')}",
            "",
        ]
        for label, key in (
            ("Best score (active+green)", "best_score"),
            ("Best active & green", "best_active_green"),
            ("Best avg €/day", "best_avg_day"),
            ("Closest to +€100/day (avg)", "closest_to_100_per_day"),
            ("Winner used for daily table", "winner"),
        ):
            r = block.get(key)
            if not r:
                lines.append(f"- {label}: _(none)_")
                continue
            lines.append(
                f"- **{label}**: `{r.get('name')}`  "
                f"avg/day **€{r.get('avg_day_pnl'):+.1f}**  "
                f"green **{100*float(r.get('pct_days_green') or 0):.0f}%**  "
                f"in-market **{100*float(r.get('pct_days_in_market') or 0):.0f}%**  "
                f"hit≥€100 **{r.get('n_days_hit_100')}/{r.get('n_days')}**  "
                f"total {r.get('pnl_eur'):+.0f}  "
                f"worst {r.get('worst_day'):+.0f}  "
                f"DD {100*float(r.get('max_dd_pct') or 0):.1f}%"
            )
        lines += [
            "",
            "| Pack | avg/day | green% | in-mkt% | hit€100 | total | worst | DD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in (block.get("top") or [])[:12]:
            lines.append(
                f"| `{r['name']}` | {r['avg_day_pnl']:+.1f} | "
                f"{100*r['pct_days_green']:.0f}% | {100*r['pct_days_in_market']:.0f}% | "
                f"{r['n_days_hit_100']}/{r['n_days']} | {r['pnl_eur']:+.0f} | "
                f"{r['worst_day']:+.0f} | {100*r['max_dd_pct']:.1f}% |"
            )
        lines.append("")
        if block.get("winner_daily"):
            lines += [
                "### Winner daily",
                "",
                "| Date | Day PnL | Cum | Hold |",
                "|---|---:|---:|---|",
            ]
            for d in block["winner_daily"]:
                lines.append(
                    f"| {d['date']} | {d['day_pnl']:+.2f} | {d['cum_pnl']:+.2f} | {d['hold']} |"
                )
            lines.append("")
    lines += [
        "## Reading",
        "",
        "1. **Every day +€100** on €1.7k did not appear as a robust pack.",
        "2. Prefer packs with high **in-market%** + high **green%** + positive avg/day.",
        "3. AlphaI packs are only scored on real pick overlap days.",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab",
        "```",
        "",
    ]
    return "\n".join(lines)
