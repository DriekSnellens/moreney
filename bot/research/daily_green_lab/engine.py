"""Daily-active sleeve grid — full liquid universe, broad entry×exit search.

Default candles: ``data/ignition_expand_candles`` (~80+ liquid EUR names),
not the 16-name desk residual set.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

Row = list[float]

_WORKER: dict[str, Any] = {}


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
    pick: str
    trail_pct: float
    time_max_days: int
    hard_stop_pct: float
    require_btc_sma: bool
    excess_floor: float
    lookback: int
    force_daily: bool


def specs(*, broad: bool = True) -> list[Spec]:
    """Entry × exit grid. ``broad`` adds structure entries + more exits."""
    picks = [
        ("top_day", 1, (0.0,)),
        ("top_rs5", 5, (0.0, 0.05, 0.10)),
        ("top_rs10", 10, (0.0, 0.05, 0.10, 0.15)),
        ("top_rs20", 20, (0.0, 0.08, 0.15)),
        ("alphai1", 10, (0.0,)),
        ("alphai_or_rs", 10, (0.0, 0.05)),
        ("rs_in_alphai", 10, (0.0, 0.05)),
        ("alphai_top_day", 1, (0.0,)),
    ]
    if broad:
        picks += [
            ("brk20_day", 1, (0.0, 0.02)),
            ("brk20_day6_vol2", 1, (0.0,)),
            ("coil_day", 1, (0.0, 0.02)),
            ("vol2_day", 1, (0.0, 0.03)),
            ("top_day_above_sma20", 1, (0.0,)),
        ]
    trails = (0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20) if broad else (0.05, 0.08, 0.12)
    times = (1, 2, 3, 5, 7) if broad else (1, 2, 3, 5)
    stops = (0.0, 0.03, 0.05, 0.08) if broad else (0.0, 0.05)
    out: list[Spec] = []
    for pick, lb, floors in picks:
        for trail in trails:
            for tmax in times:
                for hard in stops:
                    for btc_sma in (True, False):
                        for floor in floors:
                            name = (
                                f"{pick}_t{int(round(trail * 100))}_h{tmax}"
                                f"_hs{int(round(hard * 100))}"
                                f"_btc{int(btc_sma)}_fl{floor:.2f}"
                            )
                            out.append(
                                Spec(
                                    name=name,
                                    pick=pick,
                                    trail_pct=float(trail),
                                    time_max_days=int(tmax),
                                    hard_stop_pct=float(hard),
                                    require_btc_sma=bool(btc_sma),
                                    excess_floor=float(floor),
                                    lookback=int(lb),
                                    force_daily=not btc_sma,
                                )
                            )
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
    if "BTC" not in ohlc_by or date not in ohlc_by["BTC"]:
        return None
    btc_hist = [d for d in btc_dates if d <= date]
    need = max(lookback + 2, 25)
    if len(btc_hist) < need:
        return None
    btc_c = [float(ohlc_by["BTC"][d][4]) for d in btc_hist]

    scored: list[tuple[float, str]] = []
    for base, by in ohlc_by.items():
        if base == "BTC":
            continue
        hist = [d for d in dates_by_base[base] if d <= date]
        if len(hist) < need:
            continue
        closes = [float(by[d][4]) for d in hist]
        highs = [float(by[d][2]) for d in hist]
        lows = [float(by[d][3]) for d in hist]
        vols = [float(by[d][5]) for d in hist]
        r1 = _ret(closes, 1)
        if r1 is None:
            continue
        xs = None
        a = _ret(closes, lookback) if lookback >= 2 else None
        b = _ret(btc_c, lookback) if lookback >= 2 else None
        if a is not None and b is not None:
            xs = a - b
        qvol = vols[-1] * closes[-1]
        if qvol < 50_000:
            continue
        vol_ma = sum(vols[-20:]) / 20.0 if len(vols) >= 20 else 0.0
        volx = (vols[-1] / vol_ma) if vol_ma > 0 else 0.0
        prior_hi20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
        brk20 = closes[-1] >= prior_hi20
        span5 = (
            max(highs[-5:]) / min(lows[-5:]) - 1.0 if min(lows[-5:]) > 0 else 0.0
        )
        span20 = (
            max(highs[-20:]) / min(lows[-20:]) - 1.0 if min(lows[-20:]) > 0 else 0.0
        )
        coil = span20 > 1e-9 and span5 / span20 < 0.5
        s20 = _sma(closes, 20)

        if mode == "top_day":
            if float(r1) < excess_floor:
                continue
            scored.append((float(r1), base))
        elif mode == "top_day_above_sma20":
            if s20 is None or closes[-1] <= s20:
                continue
            if float(r1) < excess_floor:
                continue
            scored.append((float(r1), base))
        elif mode in {"top_rs5", "top_rs10", "top_rs20"}:
            if xs is None or xs < excess_floor:
                continue
            scored.append((float(xs), base))
        elif mode == "brk20_day":
            if not brk20 or float(r1) < max(0.0, excess_floor):
                continue
            scored.append((float(r1), base))
        elif mode == "brk20_day6_vol2":
            if not (brk20 and float(r1) >= 0.06 and volx >= 2.0):
                continue
            scored.append((float(r1) * volx, base))
        elif mode == "coil_day":
            if not coil or float(r1) < max(0.0, excess_floor):
                continue
            scored.append((float(r1), base))
        elif mode == "vol2_day":
            if volx < 2.0 or float(r1) < max(0.0, excess_floor):
                continue
            scored.append((float(r1) * volx, base))
        elif mode == "day_cap12":
            # Same-day chase cap used live: buy the leader only while the
            # closed day is still inside the band the sleeve is willing to enter.
            if not (0.0 < float(r1) <= 0.12):
                continue
            scored.append((float(r1), base))
        elif mode == "alphai1":
            continue
        elif mode == "alphai_top_day":
            if not alphai or base not in alphai:
                continue
            scored.append((float(r1), base))
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
    compound: bool = False,
) -> dict[str, Any]:
    """Wet next-open sleeve replay.

    Sizing:
    - ``compound=False`` (default / live sleeve): ``notion = min(cash×0.98, book)``
      — wins do not grow the next ticket above €book; losses shrink it.
    - ``compound=True``: ``notion = cash×0.98`` — full equity reinvestment.
    """
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
    last_ai: list[str] = []

    for i, date in enumerate(cal):
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
                notion = cash * 0.98 if compound else min(cash * 0.98, book)
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

        eq = cash
        if pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                hi = float(by[base][date][2])
                lo = float(by[base][date][3])
                cl = float(by[base][date][4])
                pos["peak"] = max(float(pos["peak"]), hi)
                pos["days"] = int(pos.get("days") or 0) + 1
                eq += pos["qty"] * cl
                hard = float(spec.hard_stop_pct or 0.0)
                hard_hit = hard > 0 and lo <= float(pos["entry"]) * (1 - hard)
                trail_hit = cl <= float(pos["peak"]) * (1 - spec.trail_pct)
                time_hit = int(pos["days"]) >= int(spec.time_max_days)
                if hard_hit:
                    pos["exit_reason"] = "hard_stop"
                    # approximate fill at stop
                    pending_sell = True
                elif trail_hit or time_hit:
                    pos["exit_reason"] = "alt_trail" if trail_hit else "time_stop"
                    pending_sell = True

        btc_hist = [float(by["BTC"][d][4]) for d in btc_dates if d <= date]
        s50 = _sma(btc_hist, 50)
        risk_on = s50 is not None and btc_hist[-1] > s50
        allow = risk_on or spec.force_daily or not spec.require_btc_sma

        if date in alphai_by_day:
            last_ai = list(alphai_by_day[date])
        ai = last_ai

        if pos is None and not pending_sell and allow:
            pick = _rank_day(
                by,
                date,
                dates_by,
                mode=spec.pick,
                lookback=max(int(spec.lookback), 1),
                excess_floor=float(spec.excess_floor),
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
    weeks: dict[str, float] = defaultdict(float)
    for d in daily:
        dt = datetime.strptime(str(d["date"]), "%Y-%m-%d")
        weeks[dt.strftime("%Y-W%W")] += float(d["day_pnl"])
    wvals = list(weeks.values())
    wn = len(wvals) or 1
    wgreen = sum(1 for v in wvals if v > 0)
    end_eq = float(daily[-1]["equity"]) if daily else book
    return {
        "ok": True,
        "name": spec.name,
        "pick": spec.pick,
        "trail_pct": spec.trail_pct,
        "time_max_days": spec.time_max_days,
        "hard_stop_pct": spec.hard_stop_pct,
        "require_btc_sma": spec.require_btc_sma,
        "excess_floor": spec.excess_floor,
        "sizing": "compound" if compound else "fixed_book_cap",
        "pnl_eur": round(float(daily[-1]["cum_pnl"]), 2) if daily else 0.0,
        "end_equity": round(end_eq, 2),
        "return_pct": round((end_eq / book - 1.0) if book else 0.0, 4),
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
        "n_weeks": float(len(wvals)),
        "pct_weeks_green": round(wgreen / wn, 4),
        "avg_week_pnl": round(sum(wvals) / wn, 4),
        "best_week": round(max(wvals), 2) if wvals else 0.0,
        "worst_week": round(min(wvals), 2) if wvals else 0.0,
        "weeks": {k: round(v, 2) for k, v in sorted(weeks.items())},
        "daily": daily,
        "trades": trades,
        "score": round(
            1000 * (in_mkt / n)
            + 2000 * (green / n)
            + avg
            + 0.1 * (min(pnls) if pnls else 0)
            - 50 * max_dd,
            4,
        ),
    }


def _init_worker(
    ohlc: Mapping[str, Sequence[Row]],
    alphai: Mapping[str, Sequence[str]],
    book: float,
    start: str,
    end: str,
) -> None:
    _WORKER.update(ohlc=ohlc, alphai=alphai, book=book, start=start, end=end)


def _job(spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec = Spec(**spec_dict)
    if spec.pick.startswith("alphai") and not _WORKER["alphai"]:
        return {"name": spec.name, "ok": False, "reason": "no_alphai"}
    row = simulate(
        _WORKER["ohlc"],
        spec,
        start=_WORKER["start"],
        end=_WORKER["end"],
        book=_WORKER["book"],
        alphai_by_day=_WORKER["alphai"],
    )
    if row.get("ok"):
        return {k: v for k, v in row.items() if k not in {"daily", "trades"}}
    return row


def run_daily_green_lab(
    *,
    candle_dir: Path | str = "data/ignition_expand_candles",
    alphai_path: Path | str = "data/research/alphai_sessions_merged.json",
    book: float = 1_700.0,
    days: int = 45,
    workers: int | None = None,
    broad: bool = True,
) -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    # Merge BTC from residual if expand BTC shorter
    res_btc = Path("data/residual_wet_candles/BTC.json")
    if "BTC" not in ohlc and res_btc.exists():
        ohlc["BTC"] = _load_dir(res_btc.parent)["BTC"]
    if "BTC" not in ohlc:
        raise ValueError("BTC required")
    btc_dates = sorted(_by_date(ohlc["BTC"]))
    end = btc_dates[-1]
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    start = (end_dt - timedelta(days=days)).strftime("%Y-%m-%d")
    start = next(d for d in btc_dates if d >= start)
    alphai = _alphai_daily(Path(alphai_path))
    ai_days = sorted(d for d in alphai if start <= d <= end)
    windows = {"last_period": (start, end)}
    if ai_days:
        windows["alphai_overlap"] = (ai_days[0], min(ai_days[-1], end))

    all_specs = specs(broad=broad)
    n_workers = workers if workers is not None else max(1, (os.cpu_count() or 4) - 1)
    results: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": book,
        "candle_dir": str(candle_dir),
        "n_bases": len(ohlc) - 1,
        "bases": sorted(b for b in ohlc if b != "BTC"),
        "n_specs": len(all_specs),
        "broad": broad,
        "note": (
            f"Full liquid universe ({len(ohlc) - 1} names), broad entry×exit grid. "
            "+€100/day every day on €1.7k (~5.9%/day) is the aspiration — "
            "this ranks what the tape allows."
        ),
        "windows": {},
    }

    for wname, (w0, w1) in windows.items():
        print(
            f"  window {wname} {w0}→{w1} specs={len(all_specs)} workers={n_workers}",
            flush=True,
        )
        spec_dicts = [asdict(s) for s in all_specs]
        rows: list[dict[str, Any]] = []
        if n_workers <= 1:
            _init_worker(ohlc, alphai, book, w0, w1)
            for sd in spec_dicts:
                row = _job(sd)
                if row.get("ok"):
                    rows.append(row)
        else:
            with ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_init_worker,
                initargs=(ohlc, alphai, book, w0, w1),
            ) as ex:
                for row in ex.map(_job, spec_dicts, chunksize=8):
                    if row.get("ok"):
                        rows.append(row)
        rows.sort(key=lambda r: (-float(r["score"]), -float(r["avg_day_pnl"])))
        active = [r for r in rows if float(r["pct_days_in_market"]) >= 0.7]
        greenish = [
            r
            for r in active
            if float(r["pct_days_green"]) >= 0.45 and float(r["avg_day_pnl"]) > 0
        ]
        best_green = (
            max(greenish, key=lambda r: (r["pct_days_green"], r["avg_day_pnl"]))
            if greenish
            else (active[0] if active else None)
        )
        # Best by entry family
        by_pick: dict[str, dict[str, Any]] = {}
        for r in rows:
            p = str(r.get("pick") or "")
            cur = by_pick.get(p)
            if cur is None or float(r["score"]) > float(cur["score"]):
                by_pick[p] = r

        results["windows"][wname] = {
            "start": w0,
            "end": w1,
            "n_rows": len(rows),
            "best_score": rows[0] if rows else None,
            "best_active_green": best_green,
            "best_avg_day": max(rows, key=lambda r: float(r["avg_day_pnl"]))
            if rows
            else None,
            "closest_to_100_per_day": max(rows, key=lambda r: float(r["avg_day_pnl"]))
            if rows
            else None,
            "best_by_entry": by_pick,
            "top": rows[:25],
        }
        if best_green or rows:
            winner_name = (best_green or rows[0])["name"]
            wspec = next(s for s in all_specs if s.name == winner_name)
            full = simulate(
                ohlc, wspec, start=w0, end=w1, book=book, alphai_by_day=alphai
            )
            results["windows"][wname]["winner"] = {
                k: v for k, v in full.items() if k not in {"daily", "trades"}
            }
            results["windows"][wname]["winner_daily"] = full.get("daily")
            results["windows"][wname]["winner_trades"] = full.get("trades")

    return results


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Daily-green lab — full universe entry×exit search",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"bases **{payload.get('n_bases')}**  specs `{payload.get('n_specs')}`  "
        f"candles `{payload.get('candle_dir')}`",
        "",
        payload.get("note", ""),
        "",
        f"Universe sample: {', '.join((payload.get('bases') or [])[:25])}…",
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
            ("Best score", "best_score"),
            ("Best active & green", "best_active_green"),
            ("Best avg €/day", "best_avg_day"),
            ("Winner", "winner"),
        ):
            r = block.get(key)
            if not r:
                continue
            lines.append(
                f"- **{label}**: `{r.get('name')}`  "
                f"avg/day **€{r.get('avg_day_pnl'):+.1f}**  "
                f"green **{100 * float(r.get('pct_days_green') or 0):.0f}%**  "
                f"in-market **{100 * float(r.get('pct_days_in_market') or 0):.0f}%**  "
                f"hit≥€100 **{r.get('n_days_hit_100')}/{r.get('n_days')}**  "
                f"total {r.get('pnl_eur'):+.0f}  worst {r.get('worst_day'):+.0f}  "
                f"DD {100 * float(r.get('max_dd_pct') or 0):.1f}%"
            )
        if block.get("best_by_entry"):
            lines += [
                "",
                "### Best pack per entry family",
                "",
                "| Entry | Pack | avg/day | green% | in-mkt% | total |",
                "|---|---|---:|---:|---:|---:|",
            ]
            for pick, r in sorted(
                block["best_by_entry"].items(),
                key=lambda kv: -float(kv[1].get("score") or 0),
            ):
                lines.append(
                    f"| `{pick}` | `{r['name']}` | {r['avg_day_pnl']:+.1f} | "
                    f"{100 * r['pct_days_green']:.0f}% | "
                    f"{100 * r['pct_days_in_market']:.0f}% | {r['pnl_eur']:+.0f} |"
                )
        lines += [
            "",
            "| Pack | avg/day | green% | in-mkt% | hit€100 | total | worst | DD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in (block.get("top") or [])[:20]:
            lines.append(
                f"| `{r['name']}` | {r['avg_day_pnl']:+.1f} | "
                f"{100 * r['pct_days_green']:.0f}% | "
                f"{100 * r['pct_days_in_market']:.0f}% | "
                f"{r['n_days_hit_100']}/{r['n_days']} | {r['pnl_eur']:+.0f} | "
                f"{r['worst_day']:+.0f} | {100 * r['max_dd_pct']:.1f}% |"
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
                    f"| {d['date']} | {d['day_pnl']:+.2f} | "
                    f"{d['cum_pnl']:+.2f} | {d['hold']} |"
                )
            lines.append("")
    lines += [
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab --candles data/ignition_expand_candles --broad",
        "```",
        "",
    ]
    return "\n".join(lines)
