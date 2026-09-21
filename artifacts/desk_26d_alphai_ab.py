#!/usr/bin/env python3
"""Last 26 days: €20k 15m momentum (AlphaI timeline) vs Donchian vs live mix.

Three independent books. AlphaI pick_outcomes drive 15m rank/size/avoid/macro
and mix paper-shorts (block long-picks). Donchian longs stay tape-only — live
does not overlay AlphaI on Donchian.

Does not change the live engine.
"""
from __future__ import annotations

import bisect
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from artifacts.donch_mom_10k_2w_sim import (
    FEE_RT,
    _Sleeve,
    _close_pos,
    _marks,
    _open_pos,
    live_mom_cfg,
    pack_mom,
    sim_donchian_mix,
    weekly_from_daily,
)
from bot.live.desk_allocator import REGIME_MAP, classify_sma20_50
from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, AlphaIView, DeskConfig
from bot.live.momentum_donchian import evaluate_donchian, loop_sleeve_configs
from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    btc_bear_ok,
    evaluate_short_exit,
    fetch_daily_ohlc,
    rank_weakest,
    select_shorts,
)
from bot.research.momentum_backtest.engine import load_candles, simulate

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).with_suffix(".json")
SVG = Path(__file__).with_suffix(".svg")
DAYS = 26
BOOK = 20_000.0

ALPHAI_PATHS = [
    ROOT / "artifacts" / "alphai_pick_outcomes_merged.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_week_snap.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_live_copy.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_live_now.json",
    ROOT / "artifacts" / "alphai_pick_outcomes_snapshot.json",
    ROOT / "data" / "alphai" / "daily_recommendations.json",
]


def mom_cfg(book: float) -> DeskConfig:
    cfg = live_mom_cfg(book)
    return cfg.with_overrides(
        alphai_rank_boost=0.01,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        outcome_size_enabled=False,
        skip_weekend_entries=False,
    )


def short_cfg(*, book: float) -> ShortWeakestConfig:
    return ShortWeakestConfig(book_eur=book, alphai_enabled=True)


def _parse_ms(raw: str) -> int:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _load_sessions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("sessions"):
        return list(data["sessions"])
    if isinstance(data, dict) and data.get("generated_at"):
        return [data]
    return []


def build_alphai_timeline(paths: list[Path]) -> tuple[list[int], list[AlphaIView], dict[str, Any]]:
    by_gen: dict[str, dict[str, Any]] = {}
    used: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            sessions = _load_sessions(path)
        except (OSError, json.JSONDecodeError):
            continue
        used.append(str(path))
        for s in sessions:
            gen = s.get("generated_at")
            if not gen:
                continue
            by_gen[str(gen)] = s
    sessions = sorted(by_gen.values(), key=lambda s: s.get("generated_at") or "")
    times: list[int] = []
    views: list[AlphaIView] = []
    macro_n = 0
    pick_counts: Counter[str] = Counter()
    for s in sessions:
        gen = str(s.get("generated_at"))
        payload = {
            "generated_at": gen,
            "macro_caution": bool(s.get("macro_caution")),
            "picks": s.get("picks") or [],
            "avoid": s.get("avoid") or [],
            "price_confirm_scales": s.get("price_confirm_scales") or {},
            "base_reliability": s.get("base_reliability") or {},
        }
        if payload["macro_caution"]:
            macro_n += 1
        view = AlphaIView.from_recommendations(payload)
        for b in view.picks:
            pick_counts[b] += 1
        times.append(_parse_ms(gen))
        views.append(view)
    days = sorted({datetime.fromtimestamp(t / 1000, UTC).date().isoformat() for t in times})
    meta = {
        "sources": used,
        "sessions": len(views),
        "first": datetime.fromtimestamp(times[0] / 1000, UTC).isoformat() if times else None,
        "last": datetime.fromtimestamp(times[-1] / 1000, UTC).isoformat() if times else None,
        "days": days,
        "n_days": len(days),
        "macro_caution_sessions": macro_n,
        "macro_caution_rate": round(macro_n / max(1, len(views)), 3),
        "top_picks": pick_counts.most_common(12),
    }
    return times, views, meta


def make_alphai_at(times: list[int], views: list[AlphaIView]) -> Callable[[int], AlphaIView | None]:
    def alphai_at(t_ms: int) -> AlphaIView | None:
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        return views[i] if i >= 0 else None

    return alphai_at


@dataclass
class _ShortBook:
    cash: float = 0.0
    book: float = 0.0
    positions: list[ShortPosition] = field(default_factory=list)
    realized: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    last_entry_ms: int = 0

    def deployed(self) -> float:
        return sum(p.notional_eur for p in self.positions)


def _short_eq(sb: _ShortBook, px: dict[str, float]) -> float:
    mtm = 0.0
    for p in sb.positions:
        mark = px.get(p.base) or p.entry_price
        mtm += p.unrealized_net(mark, FEE_RT)
    return sb.cash + sb.deployed() + mtm


def _cover(sb: _ShortBook, pos: ShortPosition, px: float, reason: str, ts_ms: int) -> None:
    ret = pos.short_return(px)
    net = pos.notional_eur * ret - pos.notional_eur * FEE_RT
    proceeds = pos.notional_eur * (1.0 + ret) * (1.0 - FEE_RT / 2)
    sb.cash += proceeds
    sb.realized += net
    sb.positions = [p for p in sb.positions if p.holding_id != pos.holding_id]
    sb.trades.append(
        {
            "event": "exit",
            "base": pos.base,
            "reason": reason,
            "entry_price": round(pos.entry_price, 6),
            "exit_price": round(px, 6),
            "notional_eur": round(pos.notional_eur, 2),
            "net_eur": round(net, 2),
            "opened_ms": pos.opened_ms,
            "closed_ms": ts_ms,
        }
    )


def _open_short(sb: _ShortBook, row: dict[str, Any], px: float, ts_ms: int) -> None:
    notional = float(row["notional_eur"])
    cost = notional * (1.0 + FEE_RT / 2)
    if cost > sb.cash + 1e-6 or px <= 0:
        return
    sb.cash -= cost
    pos = ShortPosition(
        base=str(row["base"]),
        entry_price=px,
        notional_eur=notional,
        opened_ms=ts_ms,
        entry_reason=",".join(str(x) for x in (row.get("reasons") or [])),
    )
    sb.positions.append(pos)
    sb.last_entry_ms = ts_ms
    sb.trades.append(
        {
            "event": "entry",
            "base": pos.base,
            "reason": pos.entry_reason,
            "entry_price": round(px, 6),
            "notional_eur": round(notional, 2),
            "opened_ms": ts_ms,
        }
    )


def _long_eq(sl: _Sleeve, px: dict[str, float]) -> float:
    mtm = 0.0
    for p in sl.positions:
        mark = px.get(p.base) or p.entry_price
        mtm += p.unrealized_net(mark, FEE_RT)
    return sl.cash + sl.deployed() + mtm


def _flatten_long(sl: _Sleeve, px: dict[str, float], ts_ms: int, reason: str) -> None:
    for pos in list(sl.positions):
        _close_pos(sl, pos, px.get(pos.base) or pos.entry_price, reason, ts_ms)


def slim_donch(d: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "book_eur",
        "end_regime",
        "realized_eur",
        "open_mtm_eur",
        "total_eur",
        "max_dd_eur",
        "max_dd_pct",
        "trades",
        "entries_n",
        "win_rate",
        "by_reason",
        "by_sleeve",
        "open",
        "weekly",
    )
    out = {k: d[k] for k in keep if k in d}
    out["daily"] = [
        {
            "day": r["day"],
            "regime": r.get("regime"),
            "equity_eur": r["equity_eur"],
            "realized_eur": r["realized_eur"],
            "open_mtm_eur": r["open_mtm_eur"],
            "deployed_eur": r.get("deployed_eur"),
        }
        for r in (d.get("daily") or [])
    ]
    out["exits"] = d.get("exits") or []
    return out


def slim_mom(m: dict[str, Any]) -> dict[str, Any]:
    trades = m.get("trades_closed") or []
    pick_n = sum(1 for t in trades if "alphai_pick" in str(t.get("entry_reason") or ""))
    return {
        "book_eur": m.get("book_eur"),
        "realized_eur": m["realized_eur"],
        "open_mtm_eur": m["open_mtm_eur"],
        "total_eur": m["total_eur"],
        "trades": m["trades"],
        "win_rate": m.get("win_rate"),
        "by_reason": m.get("by_reason"),
        "open": m.get("open"),
        "weekly": m.get("weekly"),
        "alphai_pick_entries": pick_n,
        "alphai_pick_share": round(pick_n / max(1, len(trades)), 3),
        "max_dd_eur": (m.get("summary") or {}).get("max_drawdown_eur"),
        "trades_closed": trades,
    }


def sim_live_mix(
    ohlc: dict[str, list[list[float]]],
    *,
    book: float,
    start_ms: int,
    end_ms: int,
    alphai_at: Callable[[int], AlphaIView | None],
) -> dict[str, Any]:
    cfgs = {c.name: c for c in loop_sleeve_configs()}
    sleeves = {name: _Sleeve(cfg=cfg) for name, cfg in cfgs.items()}
    shorts = _ShortBook()
    pool = float(book)
    sw_cfg = short_cfg(book=book * 0.7)

    btc_rows = ohlc.get("BTC") or []
    ts = [int(r[0]) for r in btc_rows]
    aligned: dict[str, list[list[float]]] = {"BTC": btc_rows}
    for base, rows in ohlc.items():
        by = {int(r[0]): r for r in rows}
        aligned[base] = [by[t] if t in by else [t, 0, 0, 0, 0, 0] for t in ts]
    i0 = next(i for i, t in enumerate(ts) if t >= start_ms)
    i1 = next(i for i in range(len(ts) - 1, -1, -1) if ts[i] <= end_ms)
    if i0 < 54:
        raise RuntimeError(f"need SMA50 warmup, have {i0} bars before window")

    daily: list[dict[str, Any]] = []
    last_label = ""
    for i in range(i0, i1 + 1):
        now = datetime.fromtimestamp(ts[i] / 1000, UTC) + timedelta(days=1, minutes=5)
        hist = {b: rows[: i + 1] for b, rows in aligned.items()}
        btc_closes = [float(r[4]) for r in aligned["BTC"][: i + 1] if float(r[4]) > 0]
        snap = classify_sma20_50(btc_closes)
        label = str(snap.get("label") or "mid")
        wmap = dict(REGIME_MAP.get(label) or {"cash": 1.0})
        px = _marks(aligned, i)
        view = alphai_at(ts[i]) or AlphaIView()

        # Turn off sleeves whose weight is 0.
        for name, sl in sleeves.items():
            if float(wmap.get(name, 0.0)) > 1e-9:
                continue
            if sl.positions or sl.cash > 1e-9:
                _flatten_long(sl, px, ts[i], "regime_off")
                pool += sl.cash
                sl.cash = 0.0
                sl.book = 0.0
        if float(wmap.get("short_weakest", 0.0)) <= 1e-9:
            for pos in list(shorts.positions):
                _cover(shorts, pos, px.get(pos.base) or pos.entry_price, "regime_cover", ts[i])
            if shorts.cash > 1e-9:
                pool += shorts.cash
                shorts.cash = 0.0
                shorts.book = 0.0

        # Fund active sleeves from the pool up to the *fixed* 20k map (live euros_for).
        for name, sl in sleeves.items():
            w = float(wmap.get(name, 0.0))
            if w <= 1e-9:
                continue
            target = book * w
            current = sl.cash + sl.deployed()
            if current < target:
                take = min(pool, target - current)
                sl.cash += take
                pool -= take
            sl.book = target
        sw_w = float(wmap.get("short_weakest", 0.0))
        if sw_w > 1e-9:
            target = book * sw_w
            current = shorts.cash + shorts.deployed()
            if current < target:
                take = min(pool, target - current)
                shorts.cash += take
                pool -= take
            shorts.book = target
            sw_cfg = short_cfg(book=target)

        # Donchian decisions on funded sleeves.
        for name, sl in sleeves.items():
            if sl.book < sl.cfg.min_notional_eur:
                continue
            held = {p.base for p in sl.positions}
            dec = evaluate_donchian(
                hist,
                btc_closes,
                sl.cfg,
                held=held,
                cash_eur=sl.cash,
                deployed_eur=sl.deployed(),
                now=now,
            )
            for ex in dec.get("exits") or []:
                pos = next((p for p in sl.positions if p.base == ex["base"]), None)
                if pos:
                    _close_pos(
                        sl,
                        pos,
                        px.get(pos.base) or pos.entry_price,
                        str(ex.get("reason") or "exit"),
                        ts[i],
                    )
            for row in dec.get("entries") or []:
                base = str(row["base"])
                if base not in px or px[base] <= 0:
                    continue
                _open_pos(sl, row, px[base], ts[i])

        # Paper shorts when the mix map funds them.
        if sw_w > 1e-9:
            bear_ok, _ = btc_bear_ok(btc_closes, sw_cfg)
            closes_by = {
                b: [float(r[4]) for r in rows[: i + 1] if float(r[4]) > 0]
                for b, rows in aligned.items()
            }
            for pos in list(shorts.positions):
                mark = px.get(pos.base) or pos.entry_price
                pos.peak_return = max(pos.peak_return, pos.short_return(mark))
                series = closes_by.get(pos.base) or []
                day_ret = None
                if len(series) >= 2 and series[-2] > 0:
                    day_ret = series[-1] / series[-2] - 1.0
                decision = evaluate_short_exit(pos, mark=mark, day_ret=day_ret, cfg=sw_cfg)
                if decision:
                    _cover(shorts, pos, mark, str(decision["reason"]), ts[i])
            rebal_due = (ts[i] - shorts.last_entry_ms) >= sw_cfg.rebalance_days * 86_400_000
            if bear_ok and (not shorts.positions or rebal_due):
                if shorts.positions and rebal_due:
                    for pos in list(shorts.positions):
                        _cover(
                            shorts,
                            pos,
                            px.get(pos.base) or pos.entry_price,
                            "rebalance",
                            ts[i],
                        )
                cands, _rej = rank_weakest(
                    closes_by,
                    sw_cfg,
                    alphai=view,
                    mode="absolute",
                    btc_closes=btc_closes,
                )
                rows = select_shorts(
                    cands, sw_cfg, cash_eur=shorts.cash, held={p.base for p in shorts.positions}
                )
                for row in rows:
                    base = str(row["base"])
                    if base not in px or px[base] <= 0:
                        continue
                    _open_short(shorts, row, px[base], ts[i])

        long_mtm = 0.0
        long_real = 0.0
        long_dep = 0.0
        long_cash = 0.0
        for sl in sleeves.values():
            long_real += sl.realized
            long_dep += sl.deployed()
            long_cash += sl.cash
            for p in sl.positions:
                long_mtm += p.unrealized_net(px.get(p.base) or p.entry_price, FEE_RT)
        short_mtm = 0.0
        for p in shorts.positions:
            short_mtm += p.unrealized_net(px.get(p.base) or p.entry_price, FEE_RT)
        equity = pool + long_cash + long_dep + long_mtm + shorts.cash + shorts.deployed() + short_mtm
        daily.append(
            {
                "day": datetime.fromtimestamp(ts[i] / 1000, UTC).date().isoformat(),
                "regime": label,
                "why": snap.get("why"),
                "equity_eur": round(equity, 2),
                "realized_eur": round(long_real + shorts.realized, 2),
                "open_mtm_eur": round(long_mtm + short_mtm, 2),
                "pool_eur": round(pool, 2),
                "donch_deployed_eur": round(long_dep, 2),
                "short_deployed_eur": round(shorts.deployed(), 2),
            }
        )
        last_label = label

    last_px = _marks(aligned, i1)
    open_rows = []
    mtm = 0.0
    for sl in sleeves.values():
        for p in sl.positions:
            u = p.unrealized_net(last_px.get(p.base) or p.entry_price, FEE_RT)
            mtm += u
            open_rows.append(
                {
                    "kind": "long",
                    "sleeve": sl.cfg.name,
                    "base": p.base,
                    "entry_price": round(p.entry_price, 6),
                    "mark": round(last_px.get(p.base) or p.entry_price, 6),
                    "notional_eur": round(p.notional_eur, 2),
                    "unrealized_net_eur": round(u, 2),
                }
            )
    for p in shorts.positions:
        u = p.unrealized_net(last_px.get(p.base) or p.entry_price, FEE_RT)
        mtm += u
        open_rows.append(
            {
                "kind": "short",
                "sleeve": "short_weakest",
                "base": p.base,
                "entry_price": round(p.entry_price, 6),
                "mark": round(last_px.get(p.base) or p.entry_price, 6),
                "notional_eur": round(p.notional_eur, 2),
                "unrealized_net_eur": round(u, 2),
            }
        )
    exits = [t for sl in sleeves.values() for t in sl.trades if t["event"] == "exit"]
    exits += [t for t in shorts.trades if t["event"] == "exit"]
    realized = sum(sl.realized for sl in sleeves.values()) + shorts.realized
    by_reason: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    for t in exits:
        by_reason[str(t["reason"])]["n"] += 1
        by_reason[str(t["reason"])]["net_eur"] += float(t["net_eur"])
    wins = sum(1 for t in exits if float(t["net_eur"]) > 0)
    eq_path = [float(r["equity_eur"]) for r in daily]
    peak = book
    worst = 0.0
    for v in eq_path:
        peak = max(peak, v)
        worst = min(worst, v - peak)
    return {
        "book_eur": book,
        "end_regime": last_label,
        "realized_eur": round(realized, 2),
        "open_mtm_eur": round(mtm, 2),
        "total_eur": round(realized + mtm, 2),
        "max_dd_eur": round(worst, 2),
        "max_dd_pct": round(100.0 * worst / peak, 2) if peak else 0.0,
        "trades": len(exits),
        "win_rate": round(wins / len(exits), 3) if exits else None,
        "by_reason": {
            k: {"n": int(v["n"]), "net_eur": round(float(v["net_eur"]), 2)}
            for k, v in sorted(by_reason.items(), key=lambda kv: -float(kv[1]["net_eur"]))
        },
        "open": open_rows,
        "exits": exits,
        "daily": daily,
        "weekly": weekly_from_daily(daily, start_equity=book),
        "short_trades": shorts.trades,
        "end_pool_eur": round(pool, 2),
    }


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    end_ms = int(end.timestamp() * 1000) // BAR_MS * BAR_MS
    start_ms = int(start.timestamp() * 1000)
    print(f"window {start.date()} → {end.date()} ({DAYS}d)", flush=True)

    times, views, alphai_meta = build_alphai_timeline(ALPHAI_PATHS)
    alphai_at = make_alphai_at(times, views)
    print(
        f"alphai sessions={alphai_meta['sessions']} "
        f"{alphai_meta['first']} → {alphai_meta['last']} "
        f"days={alphai_meta['n_days']} macro={alphai_meta['macro_caution_rate']}",
        flush=True,
    )

    cfg = mom_cfg(BOOK)
    print("loading 15m candles…", flush=True)
    candles = load_candles(("BTC", *cfg.universe), days=DAYS + 10, end_ms=end_ms, refresh=False)

    print("simulating 15m + AlphaI €20k…", flush=True)
    mom_ai = pack_mom(
        simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai_at=alphai_at),
        cfg,
    )
    print("simulating 15m tape-only (no AlphaI) €20k…", flush=True)
    mom_off = pack_mom(
        simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None),
        cfg,
    )

    daily_lookback = min(200, DAYS + 70)
    print(f"loading daily OHLC ({daily_lookback}d)…", flush=True)
    ohlc: dict[str, list[list[float]]] = {}
    for base in ("BTC", *DEFAULT_UNIVERSE):
        try:
            ohlc[base] = fetch_daily_ohlc(base, days=daily_lookback)
            time.sleep(0.12)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip {base}: {exc}", flush=True)

    print("simulating Donchian-only €20k…", flush=True)
    donch = sim_donchian_mix(ohlc, book=BOOK, start_ms=start_ms, end_ms=end_ms)
    donch["weekly"] = weekly_from_daily(donch.get("daily") or [], start_equity=BOOK)

    print("simulating live mix €20k…", flush=True)
    mix = sim_live_mix(
        ohlc, book=BOOK, start_ms=start_ms, end_ms=end_ms, alphai_at=alphai_at
    )

    mom_ai_s = slim_mom(mom_ai)
    mom_off_s = slim_mom(mom_off)
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "capital_eur": BOOK,
        "caveats": [
            "Three independent €20k books (not a shared Bitvavo cash pile).",
            "15m: live knobs, close fills, AlphaI timeline from pick_outcomes (rank/size/avoid/macro).",
            "AlphaI labels only exist ~7–20 Sep; earlier bars have alphai=None (tape-only, requires_alphai_pick=false).",
            "Donchian-only: always-on 10k Friday-10/5 + 10k 10/5. No AlphaI overlay (matches live longs).",
            "Mix: sma20_50 map (risk_on Donchian 50/50, mid cash, risk_off short 70% + donch_fri 30%). Shorts paper + AlphaI block-on-pick.",
            "outcome_size_enabled=False in replay. No maker/taker path.",
        ],
        "alphai": alphai_meta,
        "momentum_15m_alphai": mom_ai_s,
        "momentum_15m_no_alphai": mom_off_s,
        "alphai_delta_eur": round(mom_ai_s["total_eur"] - mom_off_s["total_eur"], 2),
        "donchian_20k": slim_donch(donch),
        "mix_20k": mix,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(OUT),
                "momentum_alphai": {
                    "total": mom_ai_s["total_eur"],
                    "trades": mom_ai_s["trades"],
                    "alphai_pick_share": mom_ai_s["alphai_pick_share"],
                },
                "momentum_no_alphai": {"total": mom_off_s["total_eur"], "trades": mom_off_s["trades"]},
                "alphai_delta_eur": payload["alphai_delta_eur"],
                "donchian": {
                    "total": donch["total_eur"],
                    "trades": donch["trades"],
                    "max_dd_eur": donch.get("max_dd_eur"),
                },
                "mix": {
                    "total": mix["total_eur"],
                    "trades": mix["trades"],
                    "max_dd_eur": mix["max_dd_eur"],
                    "end_regime": mix["end_regime"],
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
