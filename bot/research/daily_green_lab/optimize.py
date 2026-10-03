"""Walk-forward optimize daily sleeve on full liquid universe.

Precomputes entry picks, grids exits, ranks on green weeks + activity − DD.
Uses fixed €book sizing (topped-up sleeve) so early bleed does not starve
later opportunities — matches the intended fixed €1.7k sleeve.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.research.daily_green_lab.engine import (
    Spec,
    _alphai_daily,
    _by_date,
    _load_dir,
    _rank_day,
    _sma,
)

Row = list[float]

_WORKER: dict[str, Any] = {}


@dataclass(frozen=True)
class EntryKey:
    pick: str
    lookback: int
    excess_floor: float

    @property
    def key(self) -> str:
        return f"{self.pick}|lb{self.lookback}|fl{self.excess_floor:.2f}"


def entry_keys(*, include_alphai: bool = True) -> list[EntryKey]:
    """Broader entry set than the first lab — more floors / structure modes."""
    raw: list[tuple[str, int, tuple[float, ...]]] = [
        ("top_day", 1, (0.0, 0.02, 0.04, 0.06, 0.08)),
        ("top_day_above_sma20", 1, (0.0, 0.02, 0.04)),
        ("top_rs5", 5, (0.0, 0.03, 0.05, 0.08, 0.12)),
        ("top_rs10", 10, (0.0, 0.05, 0.08, 0.12, 0.15)),
        ("top_rs20", 20, (0.0, 0.08, 0.12, 0.15)),
        ("brk20_day", 1, (0.0, 0.02, 0.04, 0.06)),
        ("brk20_day6_vol2", 1, (0.0,)),
        ("coil_day", 1, (0.0, 0.02, 0.04, 0.06)),
        ("vol2_day", 1, (0.0, 0.03, 0.05)),
    ]
    if include_alphai:
        raw += [
            ("alphai1", 10, (0.0,)),
            ("alphai_or_rs", 10, (0.0, 0.05)),
            ("rs_in_alphai", 10, (0.0, 0.05)),
            ("alphai_top_day", 1, (0.0,)),
        ]
    out: list[EntryKey] = []
    seen: set[str] = set()
    for pick, lb, floors in raw:
        for fl in floors:
            ek = EntryKey(pick=pick, lookback=lb, excess_floor=float(fl))
            if ek.key in seen:
                continue
            seen.add(ek.key)
            out.append(ek)
    return out


def exit_grid() -> list[tuple[float, int, float, bool]]:
    """trail, time_max, hard_stop, require_btc_sma."""
    trails = (0.03, 0.05, 0.08, 0.10, 0.12, 0.15)
    times = (1, 2, 3, 5)
    stops = (0.0, 0.03, 0.05, 0.08)
    out: list[tuple[float, int, float, bool]] = []
    for t in trails:
        for h in times:
            for hs in stops:
                for btc in (True, False):
                    out.append((float(t), int(h), float(hs), bool(btc)))
    return out


def _week_stats(daily: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    weeks: dict[str, float] = defaultdict(float)
    for d in daily:
        dt = datetime.strptime(str(d["date"]), "%Y-%m-%d")
        weeks[dt.strftime("%Y-W%W")] += float(d["day_pnl"])
    vals = list(weeks.values())
    n = len(vals) or 1
    green = sum(1 for v in vals if v > 0)
    return {
        "n_weeks": float(len(vals)),
        "pct_weeks_green": green / n,
        "avg_week_pnl": sum(vals) / n,
        "best_week": max(vals) if vals else 0.0,
        "worst_week": min(vals) if vals else 0.0,
    }


def struct_score(row: Mapping[str, Any]) -> float:
    """Prefer active + green weeks, punish deep DD; mild PnL term."""
    return (
        120.0 * float(row.get("pct_weeks_green") or 0)
        + float(row.get("avg_week_pnl") or 0)
        + 25.0 * float(row.get("pct_days_in_market") or 0)
        - 100.0 * float(row.get("max_dd_pct") or 0)
        + 0.15 * float(row.get("avg_day_pnl") or 0)
        + 0.02 * float(row.get("pnl_eur") or 0) / max(float(row.get("n_days") or 1), 1)
    )


def precompute_picks(
    ohlc: Mapping[str, Sequence[Row]],
    entries: Sequence[EntryKey],
    *,
    alphai_by_day: Mapping[str, Sequence[str]],
    start: str,
    end: str,
) -> dict[str, dict[str, str | None]]:
    by = {b: _by_date(rows) for b, rows in ohlc.items()}
    dates_by = {b: sorted(m) for b, m in by.items()}
    btc_dates = dates_by.get("BTC") or []
    cal = [d for d in btc_dates if start <= d <= end]
    last_ai: list[str] = []
    out: dict[str, dict[str, str | None]] = {e.key: {} for e in entries}
    for date in cal:
        if date in alphai_by_day:
            last_ai = list(alphai_by_day[date])
        for e in entries:
            if e.pick in {"alphai1", "alphai_top_day", "rs_in_alphai"} and not last_ai:
                out[e.key][date] = None
                continue
            out[e.key][date] = _rank_day(
                by,
                date,
                dates_by,
                mode=e.pick,
                lookback=max(int(e.lookback), 1),
                excess_floor=float(e.excess_floor),
                alphai=last_ai,
                btc_dates=btc_dates,
            )
    return out


def simulate_fixed(
    ohlc: Mapping[str, Sequence[Row]],
    *,
    picks_by_day: Mapping[str, str | None],
    trail_pct: float,
    time_max_days: int,
    hard_stop_pct: float,
    require_btc_sma: bool,
    start: str,
    end: str,
    book: float,
    fee: float = 0.0015,
    slip: float = 0.001,
    name: str = "",
    pick: str = "",
    excess_floor: float = 0.0,
) -> dict[str, Any]:
    """Always size entries at ``book`` (topped-up fixed sleeve)."""
    by = {b: _by_date(rows) for b, rows in ohlc.items()}
    btc_dates = sorted(by.get("BTC") or {})
    cal = [d for d in btc_dates if start <= d <= end]
    if len(cal) < 5:
        return {"name": name, "ok": False, "reason": "short_window"}

    # Precompute risk-on
    risk_on_by: dict[str, bool] = {}
    closes: list[float] = []
    for d in btc_dates:
        closes.append(float(by["BTC"][d][4]))
        s50 = _sma(closes, 50)
        risk_on_by[d] = bool(s50 is not None and closes[-1] > s50)

    cash = book
    realized = 0.0
    pos: dict[str, Any] | None = None
    pending_buy: str | None = None
    pending_sell = False
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    prev_eq = book

    for date in cal:
        if pending_sell and pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                px = float(by[base][date][1]) * (1 - slip)
                proceeds = pos["qty"] * px
                fee_eur = proceeds * fee
                pnl = proceeds - fee_eur - pos["cost"]
                realized += pnl
                cash = book  # topped up to book after exit
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
                notion = book
                if notion >= 40 and px > 0:
                    fee_eur = notion * fee
                    qty = notion / px
                    cash = 0.0
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
                            "reason": pick,
                            "notion": round(notion, 2),
                        }
                    )
            pending_buy = None

        eq = book + realized
        if pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                hi = float(by[base][date][2])
                lo = float(by[base][date][3])
                cl = float(by[base][date][4])
                pos["peak"] = max(float(pos["peak"]), hi)
                pos["days"] = int(pos.get("days") or 0) + 1
                mtm = pos["qty"] * cl - pos["cost"]
                eq = book + realized + mtm
                hard = float(hard_stop_pct or 0.0)
                hard_hit = hard > 0 and lo <= float(pos["entry"]) * (1 - hard)
                trail_hit = cl <= float(pos["peak"]) * (1 - trail_pct)
                time_hit = int(pos["days"]) >= int(time_max_days)
                if hard_hit:
                    pos["exit_reason"] = "hard_stop"
                    pending_sell = True
                elif trail_hit or time_hit:
                    pos["exit_reason"] = "alt_trail" if trail_hit else "time_stop"
                    pending_sell = True
        else:
            eq = book + realized
            cash = book

        allow = (not require_btc_sma) or risk_on_by.get(date, False)
        if pos is None and not pending_sell and allow:
            pick_base = picks_by_day.get(date)
            if pick_base:
                pending_buy = pick_base
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
            pnl = proceeds - fee_eur - pos["cost"]
            realized += pnl
            trades.append(
                {
                    "date": last,
                    "side": "sell",
                    "base": base,
                    "pnl": round(pnl, 2),
                    "reason": "eow",
                }
            )
            if daily:
                daily[-1]["equity"] = round(book + realized, 2)
                daily[-1]["cum_pnl"] = round(realized, 2)
                daily[-1]["hold"] = "cash"
                daily[-1]["in_market"] = False

    pnls = [float(d["day_pnl"]) for d in daily]
    n = len(pnls) or 1
    green = sum(1 for p in pnls if p > 0)
    in_mkt = sum(1 for d in daily if d["in_market"])
    hit100 = sum(1 for p in pnls if p >= 100)
    peak = book
    max_dd = 0.0
    for d in daily:
        eq = float(d["equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    ws = _week_stats(daily)
    row = {
        "ok": True,
        "name": name,
        "pick": pick,
        "trail_pct": trail_pct,
        "time_max_days": time_max_days,
        "hard_stop_pct": hard_stop_pct,
        "require_btc_sma": require_btc_sma,
        "excess_floor": excess_floor,
        "pnl_eur": round(float(daily[-1]["cum_pnl"]), 2) if daily else 0.0,
        "avg_day_pnl": round(sum(pnls) / n, 2),
        "pct_days_green": round(green / n, 4),
        "n_days_green": green,
        "n_days": n,
        "pct_days_in_market": round(in_mkt / n, 4),
        "n_days_hit_100": hit100,
        "best_day": round(max(pnls), 2) if pnls else 0.0,
        "worst_day": round(min(pnls), 2) if pnls else 0.0,
        "max_dd_pct": round(max_dd, 4),
        "n_trades": len(trades),
        **{k: round(v, 4) if isinstance(v, float) else v for k, v in ws.items()},
        "daily": daily,
        "trades": trades,
    }
    row["score"] = round(struct_score(row), 4)
    return row


def _init_opt_worker(
    ohlc: Mapping[str, Sequence[Row]],
    picks: Mapping[str, Mapping[str, str | None]],
    book: float,
    is_start: str,
    is_end: str,
    oos_start: str,
    oos_end: str,
) -> None:
    _WORKER.update(
        ohlc=ohlc,
        picks=picks,
        book=book,
        is_start=is_start,
        is_end=is_end,
        oos_start=oos_start,
        oos_end=oos_end,
    )


def _opt_job(job: dict[str, Any]) -> dict[str, Any]:
    ek = job["entry_key"]
    trail, tmax, hard, btc = (
        job["trail_pct"],
        job["time_max_days"],
        job["hard_stop_pct"],
        job["require_btc_sma"],
    )
    name = (
        f"{job['pick']}_t{int(round(trail * 100))}_h{tmax}"
        f"_hs{int(round(hard * 100))}_btc{int(btc)}_fl{job['excess_floor']:.2f}"
    )
    picks = _WORKER["picks"][ek]
    common = dict(
        ohlc=_WORKER["ohlc"],
        picks_by_day=picks,
        trail_pct=trail,
        time_max_days=tmax,
        hard_stop_pct=hard,
        require_btc_sma=btc,
        book=_WORKER["book"],
        name=name,
        pick=job["pick"],
        excess_floor=job["excess_floor"],
    )
    is_row = simulate_fixed(
        start=_WORKER["is_start"], end=_WORKER["is_end"], **common
    )
    oos_row = simulate_fixed(
        start=_WORKER["oos_start"], end=_WORKER["oos_end"], **common
    )
    if not is_row.get("ok") or not oos_row.get("ok"):
        return {"name": name, "ok": False}
    return {
        "ok": True,
        "name": name,
        "pick": job["pick"],
        "lookback": job["lookback"],
        "excess_floor": job["excess_floor"],
        "trail_pct": trail,
        "time_max_days": tmax,
        "hard_stop_pct": hard,
        "require_btc_sma": btc,
        "is": {k: v for k, v in is_row.items() if k not in {"daily", "trades"}},
        "oos": {k: v for k, v in oos_row.items() if k not in {"daily", "trades"}},
        "score_is": is_row["score"],
        "score_oos": oos_row["score"],
        # robust: require OOS not a disaster, rank blend
        "score_blend": round(0.45 * is_row["score"] + 0.55 * oos_row["score"], 4),
    }


def run_optimize(
    *,
    candle_dir: Path | str = "data/ignition_expand_candles",
    alphai_path: Path | str = "data/research/alphai_sessions_merged.json",
    book: float = 1_700.0,
    is_end: str = "2025-12-31",
    oos_start: str = "2026-01-01",
    workers: int | None = None,
) -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    if "BTC" not in ohlc:
        raise ValueError("BTC required")
    btc_dates = sorted(_by_date(ohlc["BTC"]))
    # warmup for SMA50 / coil
    full_start = btc_dates[60]
    full_end = btc_dates[-1]
    is_start = full_start
    if is_end < is_start:
        raise ValueError("is_end before data start")
    oos_end = full_end
    alphai = _alphai_daily(Path(alphai_path))
    entries = entry_keys(include_alphai=bool(alphai))
    exits = exit_grid()
    print(
        f"optimize precompute entries={len(entries)} days={full_start}→{full_end}",
        flush=True,
    )
    picks = precompute_picks(
        ohlc, entries, alphai_by_day=alphai, start=full_start, end=full_end
    )
    jobs: list[dict[str, Any]] = []
    for e in entries:
        for trail, tmax, hard, btc in exits:
            jobs.append(
                {
                    "entry_key": e.key,
                    "pick": e.pick,
                    "lookback": e.lookback,
                    "excess_floor": e.excess_floor,
                    "trail_pct": trail,
                    "time_max_days": tmax,
                    "hard_stop_pct": hard,
                    "require_btc_sma": btc,
                }
            )
    n_workers = workers if workers is not None else max(1, (os.cpu_count() or 4) - 1)
    print(f"optimize grid jobs={len(jobs)} workers={n_workers}", flush=True)
    rows: list[dict[str, Any]] = []
    if n_workers <= 1:
        _init_opt_worker(ohlc, picks, book, is_start, is_end, oos_start, oos_end)
        for j in jobs:
            r = _opt_job(j)
            if r.get("ok"):
                rows.append(r)
    else:
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_init_opt_worker,
            initargs=(ohlc, picks, book, is_start, is_end, oos_start, oos_end),
        ) as ex:
            for r in ex.map(_opt_job, jobs, chunksize=16):
                if r.get("ok"):
                    rows.append(r)

    rows.sort(key=lambda r: (-float(r["score_blend"]), -float(r["score_oos"])))

    def _pass_oos(r: Mapping[str, Any]) -> bool:
        o = r["oos"]
        return (
            float(o["avg_week_pnl"]) > 0
            and float(o["pct_weeks_green"]) >= 0.40
            and float(o["max_dd_pct"]) <= 0.55
            and float(o["pct_days_in_market"]) >= 0.35
            and float(o["avg_day_pnl"]) > 0
        )

    robust = [r for r in rows if _pass_oos(r)]
    robust.sort(key=lambda r: (-float(r["score_blend"]), -float(r["oos"]["avg_week_pnl"])))

    # best by entry family on blend among robust (else all)
    pool = robust or rows
    by_pick: dict[str, dict[str, Any]] = {}
    for r in pool:
        p = str(r["pick"])
        cur = by_pick.get(p)
        if cur is None or float(r["score_blend"]) > float(cur["score_blend"]):
            by_pick[p] = r

    winner = robust[0] if robust else (rows[0] if rows else None)
    # Full-period + 6w replay for winner with daily
    winner_full = None
    winner_6w = None
    if winner is not None:
        ek = EntryKey(
            pick=str(winner["pick"]),
            lookback=int(winner["lookback"]),
            excess_floor=float(winner["excess_floor"]),
        )
        common = dict(
            ohlc=ohlc,
            picks_by_day=picks[ek.key],
            trail_pct=float(winner["trail_pct"]),
            time_max_days=int(winner["time_max_days"]),
            hard_stop_pct=float(winner["hard_stop_pct"]),
            require_btc_sma=bool(winner["require_btc_sma"]),
            book=book,
            name=str(winner["name"]),
            pick=str(winner["pick"]),
            excess_floor=float(winner["excess_floor"]),
        )
        winner_full = simulate_fixed(start=full_start, end=full_end, **common)
        end_dt = datetime.strptime(full_end, "%Y-%m-%d")
        w0 = (end_dt - timedelta(days=42)).strftime("%Y-%m-%d")
        w0 = next(d for d in btc_dates if d >= w0)
        winner_6w = simulate_fixed(start=w0, end=full_end, **common)

    # also evaluate old coil winner under fixed sizing for comparison
    old = Spec(
        name="coil_day_t12_h5_hs0_btc1_fl0.02",
        pick="coil_day",
        trail_pct=0.12,
        time_max_days=5,
        hard_stop_pct=0.0,
        require_btc_sma=True,
        excess_floor=0.02,
        lookback=1,
        force_daily=False,
    )
    old_ek = EntryKey(pick=old.pick, lookback=old.lookback, excess_floor=old.excess_floor)
    if old_ek.key not in picks:
        picks.update(
            precompute_picks(
                ohlc, [old_ek], alphai_by_day=alphai, start=full_start, end=full_end
            )
        )
    old_full = simulate_fixed(
        ohlc,
        picks_by_day=picks[old_ek.key],
        trail_pct=old.trail_pct,
        time_max_days=old.time_max_days,
        hard_stop_pct=old.hard_stop_pct,
        require_btc_sma=old.require_btc_sma,
        start=full_start,
        end=full_end,
        book=book,
        name=old.name,
        pick=old.pick,
        excess_floor=old.excess_floor,
    )

    return {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": book,
        "candle_dir": str(candle_dir),
        "n_bases": len(ohlc) - 1,
        "sizing": "fixed_book_topup",
        "is": {"start": is_start, "end": is_end},
        "oos": {"start": oos_start, "end": oos_end},
        "n_entries": len(entries),
        "n_exits": len(exits),
        "n_jobs": len(jobs),
        "n_ok": len(rows),
        "n_robust_oos": len(robust),
        "winner": winner,
        "winner_full": {k: v for k, v in (winner_full or {}).items() if k not in {"daily", "trades"}},
        "winner_full_daily": (winner_full or {}).get("daily"),
        "winner_full_trades": (winner_full or {}).get("trades"),
        "winner_6w": {k: v for k, v in (winner_6w or {}).items() if k not in {"daily", "trades"}},
        "winner_6w_daily": (winner_6w or {}).get("daily"),
        "baseline_coil_full_fixed": {
            k: v for k, v in old_full.items() if k not in {"daily", "trades"}
        },
        "best_by_entry": {
            k: {kk: vv for kk, vv in v.items() if kk not in {"is", "oos"} or True}
            for k, v in sorted(by_pick.items(), key=lambda kv: -float(kv[1]["score_blend"]))
        },
        "top_robust": [
            {k: v for k, v in r.items()} for r in (robust or rows)[:25]
        ],
        "note": (
            "Fixed €book sizing (topped-up). Walk-forward IS→OOS. "
            "Score = green weeks + activity − DD + mild PnL."
        ),
    }


def to_markdown(payload: dict[str, Any]) -> str:
    w = payload.get("winner") or {}
    wf = payload.get("winner_full") or {}
    w6 = payload.get("winner_6w") or {}
    base = payload.get("baseline_coil_full_fixed") or {}
    lines = [
        "# Daily-green sleeve — walk-forward optimize",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"bases **{payload.get('n_bases')}**  sizing `{payload.get('sizing')}`",
        "",
        payload.get("note", ""),
        "",
        f"IS `{payload.get('is', {}).get('start')}`→`{payload.get('is', {}).get('end')}`  "
        f"OOS `{payload.get('oos', {}).get('start')}`→`{payload.get('oos', {}).get('end')}`  "
        f"jobs `{payload.get('n_jobs')}`  robust OOS `{payload.get('n_robust_oos')}`",
        "",
        "## Winner",
        "",
    ]
    if w:
        oos = w.get("oos") or {}
        is_ = w.get("is") or {}
        lines += [
            f"**`{w.get('name')}`**",
            "",
            f"- IS: avg/week **€{is_.get('avg_week_pnl'):+.1f}**  "
            f"green weeks **{100*float(is_.get('pct_weeks_green') or 0):.0f}%**  "
            f"in-mkt {100*float(is_.get('pct_days_in_market') or 0):.0f}%  "
            f"DD {100*float(is_.get('max_dd_pct') or 0):.1f}%  "
            f"pnl {is_.get('pnl_eur'):+.0f}",
            f"- OOS: avg/week **€{oos.get('avg_week_pnl'):+.1f}**  "
            f"green weeks **{100*float(oos.get('pct_weeks_green') or 0):.0f}%**  "
            f"in-mkt {100*float(oos.get('pct_days_in_market') or 0):.0f}%  "
            f"DD {100*float(oos.get('max_dd_pct') or 0):.1f}%  "
            f"pnl {oos.get('pnl_eur'):+.0f}",
            f"- Full (fixed book): avg/day **€{wf.get('avg_day_pnl'):+.1f}**  "
            f"green days {100*float(wf.get('pct_days_green') or 0):.0f}%  "
            f"green weeks {100*float(wf.get('pct_weeks_green') or 0):.0f}%  "
            f"in-mkt {100*float(wf.get('pct_days_in_market') or 0):.0f}%  "
            f"DD {100*float(wf.get('max_dd_pct') or 0):.1f}%  "
            f"pnl {wf.get('pnl_eur'):+.0f}",
            f"- Last 6w: avg/day **€{w6.get('avg_day_pnl'):+.1f}**  "
            f"green {100*float(w6.get('pct_days_green') or 0):.0f}%  "
            f"pnl {w6.get('pnl_eur'):+.0f}  DD {100*float(w6.get('max_dd_pct') or 0):.1f}%",
            "",
            f"Baseline old `coil_day` full fixed: pnl {base.get('pnl_eur'):+.0f}  "
            f"avg/day {base.get('avg_day_pnl'):+.1f}  "
            f"green weeks {100*float(base.get('pct_weeks_green') or 0):.0f}%  "
            f"DD {100*float(base.get('max_dd_pct') or 0):.1f}%",
            "",
        ]
    lines += [
        "## Best per entry family (robust pool)",
        "",
        "| Entry | Pack | blend | OOS €/wk | OOS greenW% | OOS DD | OOS in-mkt |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for pick, r in (payload.get("best_by_entry") or {}).items():
        o = r.get("oos") or {}
        lines.append(
            f"| `{pick}` | `{r.get('name')}` | {r.get('score_blend'):+.1f} | "
            f"{o.get('avg_week_pnl'):+.1f} | {100*float(o.get('pct_weeks_green') or 0):.0f}% | "
            f"{100*float(o.get('max_dd_pct') or 0):.1f}% | "
            f"{100*float(o.get('pct_days_in_market') or 0):.0f}% |"
        )
    lines += [
        "",
        "## Top robust packs",
        "",
        "| Pack | blend | OOS €/wk | OOS greenW% | OOS DD | IS €/wk | IS DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in (payload.get("top_robust") or [])[:20]:
        o, i = r.get("oos") or {}, r.get("is") or {}
        lines.append(
            f"| `{r.get('name')}` | {r.get('score_blend'):+.1f} | "
            f"{o.get('avg_week_pnl'):+.1f} | {100*float(o.get('pct_weeks_green') or 0):.0f}% | "
            f"{100*float(o.get('max_dd_pct') or 0):.1f}% | "
            f"{i.get('avg_week_pnl'):+.1f} | {100*float(i.get('max_dd_pct') or 0):.1f}% |"
        )
    if payload.get("winner_6w_daily"):
        lines += [
            "",
            "### Winner last 6w daily",
            "",
            "| Date | Day PnL | Cum | Hold |",
            "|---|---:|---:|---|",
        ]
        for d in payload["winner_6w_daily"]:
            lines.append(
                f"| {d['date']} | {d['day_pnl']:+.2f} | {d['cum_pnl']:+.2f} | {d['hold']} |"
            )
    lines += [
        "",
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab --optimize",
        "```",
        "",
    ]
    return "\n".join(lines)


def winner_to_spec(winner: Mapping[str, Any]) -> Spec:
    return Spec(
        name=str(winner["name"]),
        pick=str(winner["pick"]),
        trail_pct=float(winner["trail_pct"]),
        time_max_days=int(winner["time_max_days"]),
        hard_stop_pct=float(winner["hard_stop_pct"]),
        require_btc_sma=bool(winner["require_btc_sma"]),
        excess_floor=float(winner["excess_floor"]),
        lookback=int(winner["lookback"]),
        force_daily=not bool(winner["require_btc_sma"]),
    )
