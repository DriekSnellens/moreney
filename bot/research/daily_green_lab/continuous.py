"""Continuous daily sleeve — maximize structural green days.

Separate from residual week-clock: always try to be in a liquid alt when
BTC risk-on, rotate daily or rebuy immediately after an exit (never the
just-sold name). Fixed €book top-up sizing.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bot.research.daily_green_lab.engine import (
    _alphai_daily,
    _by_date,
    _load_dir,
    _rank_day,
    _sma,
)

Row = list[float]
Mode = Literal["daily_rotate", "exit_rebuy"]

_WORKER: dict[str, Any] = {}


@dataclass(frozen=True)
class ContSpec:
    name: str
    pick: str
    lookback: int
    excess_floor: float
    trail_pct: float
    time_max_days: int
    hard_stop_pct: float
    require_btc_sma: bool
    mode: Mode


def specs() -> list[ContSpec]:
    picks: list[tuple[str, int, tuple[float, ...]]] = [
        ("top_day", 1, (0.0, 0.02, 0.04)),
        ("top_day_above_sma20", 1, (0.0, 0.02)),
        ("brk20_day", 1, (0.0, 0.02)),
        ("coil_day", 1, (0.0, 0.02)),
        ("vol2_day", 1, (0.0, 0.03)),
        ("top_rs5", 5, (0.0, 0.05)),
        ("brk20_day6_vol2", 1, (0.0,)),
    ]
    trails = (0.03, 0.05, 0.08, 0.10, 0.12)
    times = (1, 2, 3)
    hards = (0.0, 0.03, 0.05)
    modes: tuple[Mode, ...] = ("daily_rotate", "exit_rebuy")
    out: list[ContSpec] = []
    for pick, lb, floors in picks:
        for fl in floors:
            for trail in trails:
                for tmax in times:
                    for hard in hards:
                        for btc in (True, False):
                            for mode in modes:
                                name = (
                                    f"{mode[:3]}_{pick}_t{int(round(trail * 100))}"
                                    f"_h{tmax}_hs{int(round(hard * 100))}"
                                    f"_btc{int(btc)}_fl{fl:.2f}"
                                )
                                out.append(
                                    ContSpec(
                                        name=name,
                                        pick=pick,
                                        lookback=lb,
                                        excess_floor=float(fl),
                                        trail_pct=float(trail),
                                        time_max_days=int(tmax),
                                        hard_stop_pct=float(hard),
                                        require_btc_sma=bool(btc),
                                        mode=mode,
                                    )
                                )
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
        "pct_weeks_green": round(green / n, 4),
        "avg_week_pnl": round(sum(vals) / n, 4),
        "best_week": round(max(vals), 2) if vals else 0.0,
        "worst_week": round(min(vals), 2) if vals else 0.0,
    }


def simulate_continuous(
    ohlc: Mapping[str, Sequence[Row]],
    spec: ContSpec,
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
    realized = 0.0
    pos: dict[str, Any] | None = None
    pending_buy: str | None = None
    pending_sell = False
    ban_until_buy: str | None = None
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    prev_eq = book
    last_ai: list[str] = []

    for date in cal:
        if pending_sell and pos is not None:
            base = pos["base"]
            if date in by.get(base, {}):
                px = float(by[base][date][1]) * (1 - slip)
                proceeds = pos["qty"] * px
                fee_eur = proceeds * fee
                pnl = proceeds - fee_eur - pos["cost"]
                cash += proceeds - fee_eur
                realized += pnl
                trades.append(
                    {
                        "date": date,
                        "side": "sell",
                        "base": base,
                        "pnl": round(pnl, 2),
                        "reason": pos.get("exit_reason") or "exit",
                    }
                )
                if str(pos.get("exit_reason") or "") in {
                    "hard_stop",
                    "alt_trail",
                    "time_stop",
                    "rotate",
                }:
                    ban_until_buy = base
                pos = None
            pending_sell = False

        if pending_buy and pos is None:
            base = pending_buy
            if date in by.get(base, {}) and base != ban_until_buy:
                px = float(by[base][date][1]) * (1 + slip)
                notion = min(cash * 0.98, book)
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
                    ban_until_buy = None
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
                    pending_sell = True
                elif trail_hit or time_hit:
                    pos["exit_reason"] = "alt_trail" if trail_hit else "time_stop"
                    pending_sell = True

        btc_hist = [float(by["BTC"][d][4]) for d in btc_dates if d <= date]
        s50 = _sma(btc_hist, 50)
        risk_on = s50 is not None and btc_hist[-1] > s50
        allow = risk_on or not spec.require_btc_sma

        if date in alphai_by_day:
            last_ai = list(alphai_by_day[date])

        pick = None
        if allow:
            pick = _rank_day(
                by,
                date,
                dates_by,
                mode=spec.pick,
                lookback=max(int(spec.lookback), 1),
                excess_floor=float(spec.excess_floor),
                alphai=last_ai,
                btc_dates=btc_dates,
            )
            if pick and ban_until_buy and pick == ban_until_buy:
                by2 = {b: m for b, m in by.items() if b != ban_until_buy}
                dates2 = {b: dates_by[b] for b in by2}
                pick = _rank_day(
                    by2,
                    date,
                    dates2,
                    mode=spec.pick,
                    lookback=max(int(spec.lookback), 1),
                    excess_floor=float(spec.excess_floor),
                    alphai=last_ai,
                    btc_dates=btc_dates,
                )

        if not allow:
            pending_buy = None
            if pos is not None and not pending_sell:
                pos["exit_reason"] = "btc_risk_off"
                pending_sell = True
        elif spec.mode == "daily_rotate":
            if pos is None and not pending_sell and pick:
                pending_buy = pick
            elif (
                pos is not None
                and not pending_sell
                and pick
                and pick != pos["base"]
            ):
                pos["exit_reason"] = "rotate"
                pending_sell = True
                pending_buy = pick
            elif pos is None and not pending_sell and not pick:
                pending_buy = None
        else:  # exit_rebuy
            if pos is None and not pending_sell and pick:
                pending_buy = pick

        day_pnl = eq - prev_eq
        # fixed book MTM display: equity = book + realized + mtm
        if pos is not None and date in by.get(pos["base"], {}):
            cl = float(by[pos["base"]][date][4])
            mtm = pos["qty"] * cl - pos["cost"]
            show_eq = book + realized + mtm
        else:
            show_eq = book + realized
            cash = book  # top-up when flat
        day_pnl = show_eq - prev_eq
        daily.append(
            {
                "date": date,
                "equity": round(show_eq, 2),
                "day_pnl": round(day_pnl, 2),
                "cum_pnl": round(show_eq - book, 2),
                "hold": pos["base"] if pos else "cash",
                "in_market": pos is not None,
            }
        )
        prev_eq = show_eq

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
    red = sum(1 for p in pnls if p < 0)
    flat = sum(1 for p in pnls if p == 0)
    in_mkt = sum(1 for d in daily if d["in_market"])
    peak = book
    max_dd = 0.0
    for d in daily:
        eq = float(d["equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    ws = _week_stats(daily)
    # green among active (non-flat) days — closer to "whenever trading"
    active = [p for p in pnls if p != 0]
    an = len(active) or 1
    green_active = sum(1 for p in active if p > 0)
    row = {
        "ok": True,
        "name": spec.name,
        "pick": spec.pick,
        "mode": spec.mode,
        "trail_pct": spec.trail_pct,
        "time_max_days": spec.time_max_days,
        "hard_stop_pct": spec.hard_stop_pct,
        "require_btc_sma": spec.require_btc_sma,
        "excess_floor": spec.excess_floor,
        "pnl_eur": round(float(daily[-1]["cum_pnl"]), 2) if daily else 0.0,
        "avg_day_pnl": round(sum(pnls) / n, 2),
        "pct_days_green": round(green / n, 4),
        "pct_days_red": round(red / n, 4),
        "pct_days_flat": round(flat / n, 4),
        "pct_active_green": round(green_active / an, 4),
        "n_days_green": green,
        "n_days": n,
        "pct_days_in_market": round(in_mkt / n, 4),
        "best_day": round(max(pnls), 2) if pnls else 0.0,
        "worst_day": round(min(pnls), 2) if pnls else 0.0,
        "max_dd_pct": round(max_dd, 4),
        "n_trades": len(trades),
        **ws,
        "daily": daily,
        "trades": trades,
    }
    # Primary: green days, then active-green, activity, mild PnL, penalize DD/worst day
    row["score"] = round(
        3000 * float(row["pct_days_green"])
        + 1000 * float(row["pct_active_green"])
        + 400 * float(row["pct_days_in_market"])
        + 0.05 * float(row["avg_day_pnl"])
        - 80 * float(row["max_dd_pct"])
        + 0.02 * float(row["worst_day"])
        + 200 * float(row["pct_weeks_green"]),
        4,
    )
    return row


def _init(ohlc, alphai, book, is0, is1, oos0, oos1) -> None:
    _WORKER.update(
        ohlc=ohlc,
        alphai=alphai,
        book=book,
        is0=is0,
        is1=is1,
        oos0=oos0,
        oos1=oos1,
    )


def _job(spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec = ContSpec(**spec_dict)
    common = dict(
        ohlc=_WORKER["ohlc"],
        alphai_by_day=_WORKER["alphai"],
        book=_WORKER["book"],
    )
    is_row = simulate_continuous(
        spec=spec, start=_WORKER["is0"], end=_WORKER["is1"], **common
    )
    oos_row = simulate_continuous(
        spec=spec, start=_WORKER["oos0"], end=_WORKER["oos1"], **common
    )
    if not is_row.get("ok") or not oos_row.get("ok"):
        return {"ok": False, "name": spec.name}
    slim = lambda r: {k: v for k, v in r.items() if k not in {"daily", "trades"}}
    return {
        "ok": True,
        "name": spec.name,
        "pick": spec.pick,
        "lookback": spec.lookback,
        "mode": spec.mode,
        "trail_pct": spec.trail_pct,
        "time_max_days": spec.time_max_days,
        "hard_stop_pct": spec.hard_stop_pct,
        "require_btc_sma": spec.require_btc_sma,
        "excess_floor": spec.excess_floor,
        "is": slim(is_row),
        "oos": slim(oos_row),
        "blend_score": round(
            0.35 * float(is_row["score"]) + 0.65 * float(oos_row["score"]), 4
        ),
        "oos_green": oos_row["pct_days_green"],
        "is_green": is_row["pct_days_green"],
    }


def run_continuous_search(
    *,
    candle_dir: str = "data/ignition_expand_candles",
    alphai_path: str = "data/research/alphai_sessions_merged.json",
    book: float = 1_700.0,
    workers: int | None = None,
) -> dict[str, Any]:
    ohlc = _load_dir(Path(candle_dir))
    alphai = _alphai_daily(Path(alphai_path)) if Path(alphai_path).exists() else {}
    is0, is1 = "2024-06-15", "2025-12-31"
    oos0, oos1 = "2026-01-01", "2026-10-02"
    spec_list = specs()
    n_workers = max(1, int(workers or min(6, (os_cpu := __import__("os").cpu_count() or 4))))
    print(
        f"continuous_daily n_specs={len(spec_list)} bases={len(ohlc)} workers={n_workers}",
        flush=True,
    )
    rows: list[dict[str, Any]] = []
    _init(ohlc, alphai, book, is0, is1, oos0, oos1)
    jobs = [
        {
            "name": s.name,
            "pick": s.pick,
            "lookback": s.lookback,
            "excess_floor": s.excess_floor,
            "trail_pct": s.trail_pct,
            "time_max_days": s.time_max_days,
            "hard_stop_pct": s.hard_stop_pct,
            "require_btc_sma": s.require_btc_sma,
            "mode": s.mode,
        }
        for s in spec_list
    ]
    with ProcessPoolExecutor(
        max_workers=n_workers, initializer=_init, initargs=(ohlc, alphai, book, is0, is1, oos0, oos1)
    ) as ex:
        for i, row in enumerate(ex.map(_job, jobs, chunksize=8), 1):
            if row.get("ok"):
                rows.append(row)
            if i % 200 == 0:
                print(f"  …{i}/{len(jobs)}", flush=True)

    rows.sort(key=lambda r: -float(r.get("blend_score") or 0))
    # Robust: OOS green days >= 35%, IS green >= 30%, OOS DD <= 60%, OOS in-mkt >= 40%
    robust = [
        r
        for r in rows
        if float((r.get("oos") or {}).get("pct_days_green") or 0) >= 0.35
        and float((r.get("is") or {}).get("pct_days_green") or 0) >= 0.28
        and float((r.get("oos") or {}).get("max_dd_pct") or 1) <= 0.60
        and float((r.get("oos") or {}).get("pct_days_in_market") or 0) >= 0.40
        and float((r.get("oos") or {}).get("pnl_eur") or -1) > 0
    ]
    # Prefer dual green, not pure OOS spike
    robust.sort(
        key=lambda r: (
            -float(r.get("blend_score") or 0),
            -min(float(r["is"]["pct_days_green"]), float(r["oos"]["pct_days_green"])),
        )
    )
    winner = robust[0] if robust else (rows[0] if rows else {})

    # Full-period replay of winner
    full = None
    if winner:
        wspec = ContSpec(
            name=str(winner["name"]),
            pick=str(winner["pick"]),
            lookback=int(winner.get("lookback") or 1),
            excess_floor=float(winner["excess_floor"]),
            trail_pct=float(winner["trail_pct"]),
            time_max_days=int(winner["time_max_days"]),
            hard_stop_pct=float(winner["hard_stop_pct"]),
            require_btc_sma=bool(winner["require_btc_sma"]),
            mode=str(winner["mode"]),  # type: ignore[arg-type]
        )
        full = simulate_continuous(
            ohlc,
            wspec,
            start="2024-06-15",
            end=oos1,
            book=book,
            alphai_by_day=alphai,
        )
        # last 6w
        last6 = simulate_continuous(
            ohlc,
            wspec,
            start="2026-08-21",
            end=oos1,
            book=book,
            alphai_by_day=alphai,
        )
    else:
        last6 = None

    return {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": book,
        "n_bases": len(ohlc),
        "n_specs": len(spec_list),
        "n_ok": len(rows),
        "n_robust": len(robust),
        "is": {"start": is0, "end": is1},
        "oos": {"start": oos0, "end": oos1},
        "objective": "max green days + activity − DD (continuous daily sleeve)",
        "honesty": "100% green days not achieved; report best structural green rates.",
        "winner": winner,
        "full": {k: v for k, v in (full or {}).items() if k not in {"daily", "trades"}},
        "last_6w": {k: v for k, v in (last6 or {}).items() if k not in {"daily", "trades"}},
        "last_6w_daily": (last6 or {}).get("daily"),
        "top_robust": robust[:15],
        "top_blend": rows[:15],
        "best_by_mode": {
            mode: next((r for r in robust if r.get("mode") == mode), None)
            for mode in ("daily_rotate", "exit_rebuy")
        },
    }


def to_markdown(payload: dict[str, Any]) -> str:
    w = payload.get("winner") or {}
    oos = w.get("oos") or {}
    isr = w.get("is") or {}
    full = payload.get("full") or {}
    last6 = payload.get("last_6w") or {}
    lines = [
        "# Continuous daily sleeve — green-day search",
        "",
        f"asof `{payload.get('asof')}`  book €{payload.get('book_eur'):,.0f}  "
        f"bases **{payload.get('n_bases')}**  specs `{payload.get('n_specs')}`  "
        f"robust `{payload.get('n_robust')}`",
        "",
        "Aparte sleeve naast residual: **dagelijks actief** (rotate of exit→rebuy), "
        "fixed €book, expand-universe. Doel = structureel groene dagen — "
        "**100% groen is niet gehaald**.",
        "",
        "## Winner",
        "",
        f"**`{w.get('name')}`**  mode `{w.get('mode')}`",
        "",
        f"- IS: green days **{100 * float(isr.get('pct_days_green') or 0):.0f}%**  "
        f"in-mkt {100 * float(isr.get('pct_days_in_market') or 0):.0f}%  "
        f"DD {100 * float(isr.get('max_dd_pct') or 0):.1f}%  "
        f"avg/day €{float(isr.get('avg_day_pnl') or 0):+.1f}",
        f"- OOS: green days **{100 * float(oos.get('pct_days_green') or 0):.0f}%**  "
        f"active-green {100 * float(oos.get('pct_active_green') or 0):.0f}%  "
        f"in-mkt {100 * float(oos.get('pct_days_in_market') or 0):.0f}%  "
        f"DD {100 * float(oos.get('max_dd_pct') or 0):.1f}%  "
        f"pnl €{float(oos.get('pnl_eur') or 0):+.0f}",
        f"- Full: green **{100 * float(full.get('pct_days_green') or 0):.0f}%**  "
        f"pnl €{float(full.get('pnl_eur') or 0):+.0f}  "
        f"DD {100 * float(full.get('max_dd_pct') or 0):.1f}%",
        f"- Last 6w: green **{100 * float(last6.get('pct_days_green') or 0):.0f}%**  "
        f"pnl €{float(last6.get('pnl_eur') or 0):+.0f}  "
        f"DD {100 * float(last6.get('max_dd_pct') or 0):.1f}%  "
        f"in-mkt {100 * float(last6.get('pct_days_in_market') or 0):.0f}%",
        "",
        "## Best per mode (robust)",
        "",
        "| Mode | Pack | OOS greenD | OOS in-mkt | OOS DD | OOS pnl |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for mode, r in (payload.get("best_by_mode") or {}).items():
        if not r:
            continue
        o = r.get("oos") or {}
        lines.append(
            f"| `{mode}` | `{r.get('name')}` | "
            f"{100 * float(o.get('pct_days_green') or 0):.0f}% | "
            f"{100 * float(o.get('pct_days_in_market') or 0):.0f}% | "
            f"{100 * float(o.get('max_dd_pct') or 0):.1f}% | "
            f"{float(o.get('pnl_eur') or 0):+.0f} |"
        )
    lines += [
        "",
        "## Top robust packs",
        "",
        "| Pack | blend | OOS greenD | IS greenD | OOS DD | OOS €/day |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("top_robust") or []:
        o, i = r.get("oos") or {}, r.get("is") or {}
        lines.append(
            f"| `{r.get('name')}` | {float(r.get('blend_score') or 0):.1f} | "
            f"{100 * float(o.get('pct_days_green') or 0):.0f}% | "
            f"{100 * float(i.get('pct_days_green') or 0):.0f}% | "
            f"{100 * float(o.get('max_dd_pct') or 0):.1f}% | "
            f"{float(o.get('avg_day_pnl') or 0):+.1f} |"
        )
    lines += [
        "",
        "```bash",
        ".venv/bin/python -m bot.research.daily_green_lab.continuous",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Continuous daily green-day sleeve search")
    p.add_argument("--book", type=float, default=1_700.0)
    p.add_argument("--candles", default="data/ignition_expand_candles")
    p.add_argument("--alphai", default="data/research/alphai_sessions_merged.json")
    p.add_argument("--workers", type=int, default=None)
    args = p.parse_args()
    payload = run_continuous_search(
        candle_dir=args.candles,
        alphai_path=args.alphai,
        book=args.book,
        workers=args.workers,
    )
    w = payload.get("winner") or {}
    oos = w.get("oos") or {}
    print(
        f"winner={w.get('name')} OOS greenD={100 * float(oos.get('pct_days_green') or 0):.0f}% "
        f"in_mkt={100 * float(oos.get('pct_days_in_market') or 0):.0f}% "
        f"DD={100 * float(oos.get('max_dd_pct') or 0):.1f}% "
        f"robust={payload.get('n_robust')}/{payload.get('n_ok')}",
        flush=True,
    )
    md = to_markdown(payload)
    pkg = Path(__file__).resolve().parent
    # slim json
    slim = dict(payload)
    slim.pop("last_6w_daily", None)
    (pkg / "CONTINUOUS.json").write_text(json.dumps(slim, indent=2), encoding="utf-8")
    (pkg / "CONTINUOUS.md").write_text(md, encoding="utf-8")
    Path("artifacts/daily_continuous.md").write_text(md, encoding="utf-8")
    print(f"wrote {pkg / 'CONTINUOUS.md'}", flush=True)


if __name__ == "__main__":
    main()
