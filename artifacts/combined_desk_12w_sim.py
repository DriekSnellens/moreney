#!/usr/bin/env python3
"""12-week combined replay: live momentum desk + short-weakest idle-fill.

Accuracy goals vs live micro (8020):
  - Core: 15m Bitvavo candles, DeskConfig mirrored from live-micro.env
  - Short: daily closes, idle-fill when core has 0 open positions,
    cover when core is long; hard-bear absolute mode when BTC < SMA200
  - Separate €20k books; combined = core equity + short equity

Caveats (stated in output JSON): no AlphaI timeline, 15m close fills for
core, synthetic paper shorts (no funding path).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, DeskConfig
from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    btc_bear_ok,
    evaluate_short_exit,
    fetch_daily_closes,
    rank_weakest,
    select_shorts,
    _atr14,
    fetch_daily_ohlc,
)
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "combined_desk_12w_sim.json"
DAYS = 84  # 12 weeks
CORE_BOOK = 20_000.0
SHORT_BOOK = 20_000.0


def live_core_cfg() -> DeskConfig:
    """Mirror live-micro momentum desk (Sep 2026)."""
    return DeskConfig().with_overrides(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        refill_on_exit=True,
        clip_eur=20_000.0,
        book_eur=20_000.0,
        max_positions=1,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.09,
        chase_near_high=0.008,
        trail_pct=0.05,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        hard_stop_eur=300.0,
        time_exit_hours=36.0,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        skip_weekend_entries=False,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        macro_caution_mode="reduce",
        macro_caution_requires_alphai_pick=False,
        requires_alphai_pick=False,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
        exit_on_touch=False,
        fee_rt=0.003,
    )


def short_cfg() -> ShortWeakestConfig:
    return ShortWeakestConfig(
        book_eur=SHORT_BOOK,
        lookback_days=15,
        top_n=3,
        rebalance_days=14,
        mom_floor=-0.08,
        trail_pct=0.18,
        hard_stop_pct=0.12,
        max_weight=0.15,
        deploy_frac=1.0,
        vol_spike_exit=True,
        only_when_core_idle=True,
        cover_when_core_active=True,
        idle_fill_enabled=True,
        idle_lookback_days=14,
        idle_excess_floor=-0.025,
        day_loss_limit_eur=600.0,
        week_loss_limit_eur=1600.0,
        alphai_enabled=False,  # no AlphaI timeline in this replay
        universe=DEFAULT_UNIVERSE,
    )


def _week_key(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, UTC)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _day_ms(ts: int) -> int:
    dt = datetime.fromtimestamp(ts / 1000, UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(dt.timestamp() * 1000)


@dataclass
class CoreOcc:
    """Open intervals for the core desk (ms half-open [start, end))."""

    intervals: list[tuple[int, int]] = field(default_factory=list)

    def active_at(self, t_ms: int) -> bool:
        return any(a <= t_ms < b for a, b in self.intervals)


def build_core_occupancy(res) -> CoreOcc:
    occ = CoreOcc()
    for t in res.closed:
        occ.intervals.append((int(t.opened_ms), int(t.closed_ms)))
    # Still-open at end: treat as open until end_ms
    for row in res.open_mtm:
        # opened string — recover from closed list if needed; open rows have opened iso
        # BacktestResult open_mtm doesn't store opened_ms; approximate via matching
        pass
    return occ


def build_core_occupancy_full(res, end_ms: int) -> CoreOcc:
    occ = CoreOcc()
    for t in res.closed:
        occ.intervals.append((int(t.opened_ms), int(t.closed_ms)))
    # Reconstruct still-open: positions not in closed that appear in open_mtm.
    # Use opened timestamp from ISO in open_mtm if present.
    for row in res.open_mtm:
        opened = row.get("opened")
        if not opened:
            continue
        try:
            opened_ms = int(datetime.fromisoformat(str(opened).replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            continue
        occ.intervals.append((opened_ms, end_ms + 1))
    return occ


def load_daily_map(bases: tuple[str, ...], *, days: int) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = {}
    for b in bases:
        rows = fetch_daily_closes(b, days=days)
        out[b] = rows
        time.sleep(0.05)
    return out


def align_daily(series: dict[str, list[tuple[int, float]]], bases: tuple[str, ...]) -> tuple[list[int], dict[str, list[float]]]:
    common = set(t for t, _ in series[bases[0]])
    for b in bases[1:]:
        common &= {t for t, _ in series[b]}
    ts = sorted(common)
    closes: dict[str, list[float]] = {}
    for b in bases:
        idx = {t: c for t, c in series[b]}
        closes[b] = [idx[t] for t in ts]
    return ts, closes


def simulate_short_sleeve(
    *,
    daily_ts: list[int],
    daily_closes: dict[str, list[float]],
    i0: int,
    i1: int,
    core_occ: CoreOcc,
    cfg: ShortWeakestConfig,
) -> dict[str, Any]:
    cash = float(cfg.book_eur)
    positions: list[ShortPosition] = []
    realized = 0.0
    trades: list[dict[str, Any]] = []
    equity_path: list[dict[str, Any]] = []
    last_reb_i = -10**9
    day_realized = 0.0
    week_realized = 0.0
    day_key = ""
    week_key = ""

    def mtm() -> float:
        pnl = 0.0
        for p in positions:
            # mark = last known close for that base at current i (caller sets)
            pass
        return cash  # overwritten below

    atr_cache: dict[str, float] = {}

    for i in range(i0, i1 + 1):
        t_ms = daily_ts[i]
        dt = datetime.fromtimestamp(t_ms / 1000, UTC)
        dk = dt.strftime("%Y-%m-%d")
        wk = _week_key(t_ms)
        if dk != day_key:
            day_key = dk
            day_realized = 0.0
        if wk != week_key:
            week_key = wk
            week_realized = 0.0

        marks = {b: daily_closes[b][i] for b in cfg.universe if b in daily_closes}
        marks["BTC"] = daily_closes["BTC"][i]
        core_active = core_occ.active_at(t_ms + 12 * 3_600_000)  # midday sample

        # exits / cover
        if core_active and positions and cfg.cover_when_core_active:
            for pos in list(positions):
                mark = marks.get(pos.base, pos.entry_price)
                ret = pos.short_return(mark)
                net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
                cash += pos.notional_eur + net
                realized += net
                day_realized += net
                week_realized += net
                trades.append(
                    {
                        "base": pos.base,
                        "side": "short",
                        "opened_ms": pos.opened_ms,
                        "closed_ms": t_ms,
                        "net_eur": round(net, 2),
                        "reason": "core_active_cover",
                        "mode": "cover",
                    }
                )
                positions.remove(pos)

        for pos in list(positions):
            mark = marks.get(pos.base, pos.entry_price)
            ret = pos.short_return(mark)
            pos.peak_return = max(pos.peak_return, ret)
            day_ret = None
            if i > 0 and pos.base in daily_closes:
                prev = daily_closes[pos.base][i - 1]
                if prev > 0:
                    day_ret = mark / prev - 1.0
            decision = evaluate_short_exit(pos, mark=mark, day_ret=day_ret, cfg=cfg)
            if decision:
                net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
                cash += pos.notional_eur + net
                realized += net
                day_realized += net
                week_realized += net
                trades.append(
                    {
                        "base": pos.base,
                        "side": "short",
                        "opened_ms": pos.opened_ms,
                        "closed_ms": t_ms,
                        "net_eur": round(net, 2),
                        "reason": decision["reason"],
                        "mode": "exit",
                    }
                )
                positions.remove(pos)

        # entries
        core_idle = not core_active
        risk_ok = day_realized > -abs(cfg.day_loss_limit_eur) and week_realized > -abs(
            cfg.week_loss_limit_eur
        )
        btc_series = daily_closes["BTC"][: i + 1]
        bear_ok, _ = btc_bear_ok(btc_series, cfg)
        use_idle = cfg.idle_fill_enabled and core_idle and not bear_ok
        due = (i - last_reb_i) >= int(cfg.rebalance_days)
        if last_reb_i < 0:
            due = True
        if use_idle and not positions:
            due = True

        can_enter = risk_ok and core_idle if cfg.only_when_core_idle else risk_ok
        if can_enter and (bear_ok or use_idle) and due:
            # flatten for rebalance if holding
            if positions:
                for pos in list(positions):
                    mark = marks.get(pos.base, pos.entry_price)
                    ret = pos.short_return(mark)
                    net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
                    cash += pos.notional_eur + net
                    realized += net
                    trades.append(
                        {
                            "base": pos.base,
                            "side": "short",
                            "opened_ms": pos.opened_ms,
                            "closed_ms": t_ms,
                            "net_eur": round(net, 2),
                            "reason": "rebalance",
                            "mode": "rebalance",
                        }
                    )
                    positions.remove(pos)

            closes_map = {
                b: daily_closes[b][: i + 1]
                for b in ("BTC", *cfg.universe)
                if b in daily_closes
            }
            mode = "excess" if use_idle else "absolute"
            cands, _ = rank_weakest(
                closes_map, cfg, mode=mode, btc_closes=closes_map.get("BTC")
            )
            planned = select_shorts(cands, cfg, cash_eur=cash, held=set())
            for row in planned:
                base = row["base"]
                notional = float(row["notional_eur"])
                mark = marks.get(base)
                if not mark or notional > cash:
                    continue
                atr = atr_cache.get(base, 0.02)
                fee = notional * (cfg.fee_rt / 2)
                cash -= fee + notional
                positions.append(
                    ShortPosition(
                        base=base,
                        entry_price=mark,
                        notional_eur=notional,
                        opened_ms=t_ms,
                        entry_reason=f"mode={mode},{','.join(row.get('reasons') or [])}",
                        atr14=atr,
                    )
                )
                trades.append(
                    {
                        "base": base,
                        "side": "short",
                        "opened_ms": t_ms,
                        "closed_ms": None,
                        "net_eur": 0.0,
                        "reason": "entry",
                        "mode": mode,
                        "notional_eur": notional,
                    }
                )
            last_reb_i = i

        # equity mark
        u = 0.0
        for p in positions:
            m = marks.get(p.base, p.entry_price)
            u += p.unrealized_net(m, cfg.fee_rt)
        eq = cash + u
        # cash already excludes reserved notional; unrealized is PnL only
        # restore: equity = cash + sum(notional) + short_pnl - but cash had notional subtracted
        # so equity = cash + sum(notional) + pnl_without_exit_fee_approx
        # p.unrealized_net includes -exit fee/2; use gross MTM for path
        u_gross = sum(
            p.notional_eur * p.short_return(marks.get(p.base, p.entry_price))
            for p in positions
        )
        eq = cash + sum(p.notional_eur for p in positions) + u_gross
        equity_path.append(
            {
                "date": dk,
                "equity_eur": round(eq, 2),
                "n_pos": len(positions),
                "core_idle": core_idle,
                "bear_ok": bear_ok,
                "mode": ("excess" if use_idle else ("absolute" if bear_ok else "flat")),
            }
        )

    # flatten end
    if positions:
        i = i1
        t_ms = daily_ts[i]
        for pos in list(positions):
            mark = daily_closes[pos.base][i]
            ret = pos.short_return(mark)
            net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
            cash += pos.notional_eur + net
            realized += net
            trades.append(
                {
                    "base": pos.base,
                    "side": "short",
                    "opened_ms": pos.opened_ms,
                    "closed_ms": t_ms,
                    "net_eur": round(net, 2),
                    "reason": "window_end",
                    "mode": "flatten",
                }
            )
            positions.clear()
        if equity_path:
            equity_path[-1]["equity_eur"] = round(cash, 2)
            equity_path[-1]["n_pos"] = 0

    closed = [t for t in trades if t.get("reason") != "entry"]
    wins = sum(1 for t in closed if float(t.get("net_eur") or 0) > 0)
    eq0 = float(cfg.book_eur)
    eq1 = float(equity_path[-1]["equity_eur"]) if equity_path else eq0
    # max DD on equity path
    peak = eq0
    mdd = 0.0
    for row in equity_path:
        v = float(row["equity_eur"])
        peak = max(peak, v)
        mdd = min(mdd, v - peak)

    idle_days = sum(1 for r in equity_path if r["core_idle"])
    short_active_days = sum(1 for r in equity_path if r["n_pos"] > 0)

    return {
        "book_eur": cfg.book_eur,
        "end_equity_eur": round(eq1, 2),
        "pnl_eur": round(eq1 - eq0, 2),
        "return_pct": round(100 * (eq1 / eq0 - 1), 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(100 * mdd / eq0, 2),
        "realized_eur": round(realized, 2),
        "trades": len(closed),
        "wins": wins,
        "win_rate": round(wins / len(closed), 3) if closed else None,
        "idle_core_days": idle_days,
        "short_active_days": short_active_days,
        "by_reason": _agg_reason(closed),
        "trade_rows": closed,
        "equity_path": equity_path[:: max(1, len(equity_path) // 60)] + ([equity_path[-1]] if equity_path else []),
        "equity_path_full": equity_path,
    }


def _agg_reason(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, dict[str, float | int]] = defaultdict(lambda: {"n": 0, "net_eur": 0.0})
    for r in rows:
        k = str(r.get("reason") or "?")
        out[k]["n"] = int(out[k]["n"]) + 1
        out[k]["net_eur"] = float(out[k]["net_eur"]) + float(r.get("net_eur") or 0)
    return {k: {"n": v["n"], "net_eur": round(float(v["net_eur"]), 2)} for k, v in out.items()}


def core_daily_equity(
    res,
    *,
    daily_ts: list[int],
    daily_closes: dict[str, list[float]],
    start_ms: int,
    end_ms: int,
    book: float,
) -> list[dict[str, Any]]:
    """Approximate core equity path: cash + MTM using daily marks."""
    # Build position schedule from trades
    events: list[tuple[int, str, Any]] = []
    for t in res.closed:
        events.append((t.opened_ms, "open", t))
        events.append((t.closed_ms, "close", t))
    # open_mtm still open
    open_from: dict[str, tuple[int, float, float]] = {}  # base -> opened, entry, notional
    for row in res.open_mtm:
        try:
            opened_ms = int(
                datetime.fromisoformat(str(row["opened"]).replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except Exception:  # noqa: BLE001
            continue
        open_from[str(row["base"])] = (
            opened_ms,
            float(row["entry_price"]),
            float(row.get("notional_eur") or book),
        )

    # Simpler approach: cumulative realized by close day + open MTM at each day
    closed_sorted = sorted(res.closed, key=lambda t: t.closed_ms)
    path = []
    for t_ms in daily_ts:
        if t_ms < start_ms or t_ms > end_ms:
            continue
        realized = sum(t.net_eur for t in closed_sorted if t.closed_ms <= t_ms + 86_400_000)
        # open positions at end of day
        u = 0.0
        for t in res.closed:
            if t.opened_ms <= t_ms < t.closed_ms:
                # need mark — use daily close of base
                # find index
                pass
        # Use intervals again
        for t in res.closed:
            if t.opened_ms <= t_ms < t.closed_ms:
                # find close price on/after day
                series = daily_closes.get(t.base) or []
                # map by scanning — expensive but n small
                # use last close at or before t_ms
                mark = t.entry_price
                # binary via aligned ts outside — skip; use entry*(1+0) if missing
                # We'll pass idx separately
                u += 0.0  # filled below
        path.append({"date": datetime.fromtimestamp(t_ms / 1000, UTC).strftime("%Y-%m-%d"), "t_ms": t_ms, "realized": realized})

    # Rebuild with marks
    ts_index = {t: i for i, t in enumerate(daily_ts)}
    out = []
    peak = book
    mdd = 0.0
    for t_ms in daily_ts:
        if t_ms < _day_ms(start_ms) or t_ms > end_ms:
            continue
        realized = sum(float(t.net_eur) for t in res.closed if t.closed_ms <= t_ms + 86_399_999)
        unreal = 0.0
        deployed = 0.0
        for t in res.closed:
            if t.opened_ms <= t_ms < t.closed_ms:
                i = ts_index.get(t_ms)
                if i is None or t.base not in daily_closes:
                    continue
                mark = daily_closes[t.base][i]
                # long MTM
                unreal += t.notional_eur * (mark / t.entry_price - 1.0)
                deployed += t.notional_eur
        for base, (opened_ms, entry, notional) in open_from.items():
            if opened_ms <= t_ms <= end_ms:
                # still open if not closed in window
                closed_already = any(
                    t.base == base and t.opened_ms == opened_ms for t in res.closed
                )
                if closed_already:
                    continue
                i = ts_index.get(t_ms)
                if i is None or base not in daily_closes:
                    continue
                mark = daily_closes[base][i]
                unreal += notional * (mark / entry - 1.0)
                deployed += notional
        # fee-aware rough: subtract half fee on open notional unrealized
        equity = book + realized + unreal
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
        out.append(
            {
                "date": datetime.fromtimestamp(t_ms / 1000, UTC).strftime("%Y-%m-%d"),
                "equity_eur": round(equity, 2),
                "realized_eur": round(realized, 2),
                "unreal_eur": round(unreal, 2),
                "deployed_eur": round(deployed, 2),
                "active": deployed > 1,
            }
        )
    return out


def combine_paths(
    core_path: list[dict[str, Any]], short_path: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_d = {r["date"]: r for r in short_path}
    out = []
    peak = CORE_BOOK + SHORT_BOOK
    mdd = 0.0
    for c in core_path:
        s = by_d.get(c["date"])
        if not s:
            continue
        eq = float(c["equity_eur"]) + float(s["equity_eur"])
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        out.append(
            {
                "date": c["date"],
                "core_eur": c["equity_eur"],
                "short_eur": s["equity_eur"],
                "combined_eur": round(eq, 2),
                "core_active": c.get("active"),
                "short_n": s.get("n_pos"),
                "core_idle": s.get("core_idle"),
            }
        )
    return out


def main() -> None:
    cfg = live_core_cfg()
    scfg = short_cfg()
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    print(
        f"CORE 15m replay {DAYS}d "
        f"{datetime.fromtimestamp(start_ms/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(end_ms/1000, UTC).date()}",
        flush=True,
    )
    candles = load_candles(
        ("BTC", *cfg.universe), days=DAYS + 3, end_ms=end_ms, refresh=True
    )
    print("simulating core…", flush=True)
    core_res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    core_sum = core_res.summary()
    occ = build_core_occupancy_full(core_res, end_ms)
    print(
        f"core trades={core_sum.get('trades')} realized={core_sum.get('realized_eur')} "
        f"open_mtm={core_sum.get('open_mtm_eur')} intervals={len(occ.intervals)}",
        flush=True,
    )

    print("loading daily closes for short sleeve…", flush=True)
    bases = ("BTC", *DEFAULT_UNIVERSE)
    series = load_daily_map(bases, days=DAYS + 220)  # SMA200 warmup
    daily_ts, daily_closes = align_daily(series, bases)
    # find window indices
    i0 = next(i for i, t in enumerate(daily_ts) if t >= _day_ms(start_ms))
    i1 = len(daily_ts) - 1 - next(
        i for i, t in enumerate(reversed(daily_ts)) if t <= end_ms
    )
    print(f"short daily window idx {i0}→{i1} ({i1-i0+1} days)", flush=True)

    # Preload ATR cache lightly (optional skip)
    short = simulate_short_sleeve(
        daily_ts=daily_ts,
        daily_closes=daily_closes,
        i0=i0,
        i1=i1,
        core_occ=occ,
        cfg=scfg,
    )
    print(
        f"short pnl={short['pnl_eur']} dd={short['max_dd_pct']}% "
        f"active_days={short['short_active_days']} idle_core_days={short['idle_core_days']}",
        flush=True,
    )

    core_path = core_daily_equity(
        core_res,
        daily_ts=daily_ts,
        daily_closes=daily_closes,
        start_ms=start_ms,
        end_ms=end_ms,
        book=CORE_BOOK,
    )
    combined_path = combine_paths(core_path, short["equity_path_full"])
    peak = CORE_BOOK + SHORT_BOOK
    mdd = 0.0
    for row in combined_path:
        v = float(row["combined_eur"])
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    combined_end = combined_path[-1]["combined_eur"] if combined_path else CORE_BOOK + SHORT_BOOK
    combined_pnl = combined_end - (CORE_BOOK + SHORT_BOOK)

    # weekly
    by_week: dict[str, dict[str, float]] = defaultdict(
        lambda: {"core_pnl": 0.0, "short_pnl": 0.0, "combined_pnl": 0.0}
    )
    prev_c = CORE_BOOK
    prev_s = SHORT_BOOK
    prev_w = None
    for row in combined_path:
        wk = _week_key(
            int(datetime.fromisoformat(row["date"]).replace(tzinfo=UTC).timestamp() * 1000)
        )
        if prev_w is None:
            prev_w = wk
        if wk != prev_w:
            prev_w = wk
        # accumulate end-of-week later
    # simpler: group last equity per week
    week_last: dict[str, dict[str, float]] = {}
    for row in combined_path:
        wk = _week_key(
            int(datetime.fromisoformat(row["date"]).replace(tzinfo=UTC).timestamp() * 1000)
        )
        week_last[wk] = row
    week_rows = []
    prev_eq = CORE_BOOK + SHORT_BOOK
    prev_core = CORE_BOOK
    prev_short = SHORT_BOOK
    for wk in sorted(week_last):
        row = week_last[wk]
        week_rows.append(
            {
                "week": wk,
                "core_pnl_eur": round(float(row["core_eur"]) - prev_core, 2),
                "short_pnl_eur": round(float(row["short_eur"]) - prev_short, 2),
                "combined_pnl_eur": round(float(row["combined_eur"]) - prev_eq, 2),
                "combined_equity_eur": row["combined_eur"],
            }
        )
        prev_eq = float(row["combined_eur"])
        prev_core = float(row["core_eur"])
        prev_short = float(row["short_eur"])

    # both idle / both active stats
    both_idle = sum(1 for r in combined_path if not r.get("core_active") and not r.get("short_n"))
    covered = sum(1 for r in combined_path if (not r.get("core_active")) or r.get("short_n"))

    core_open_mtm = float(core_sum.get("open_mtm_eur") or 0)
    core_realized = float(core_sum.get("realized_eur") or 0)
    core_total = core_realized + core_open_mtm

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "days": DAYS,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).date().isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).date().isoformat(),
        },
        "books": {"core_eur": CORE_BOOK, "short_eur": SHORT_BOOK, "combined_eur": CORE_BOOK + SHORT_BOOK},
        "caveats": [
            "Core: 15m close fills; no maker path; no AlphaI timeline",
            "Short: daily bars; paper synthetic; no funding; AlphaI off in replay",
            "Idle-fill uses excess-vs-BTC when core flat and BTC above SMA200",
            "Core occupancy from backtest open/close intervals (midday sample)",
        ],
        "core": {
            "summary": core_sum,
            "pnl_realized_eur": round(core_realized, 2),
            "open_mtm_eur": round(core_open_mtm, 2),
            "pnl_total_eur": round(core_total, 2),
            "return_pct": round(100 * core_total / CORE_BOOK, 2),
            "trades": int(core_sum.get("trades") or 0),
            "win_rate": core_sum.get("win_rate"),
            "max_drawdown_eur_trade_path": core_sum.get("max_drawdown_eur"),
            "by_reason": core_sum.get("by_reason"),
            "end_equity_approx_eur": core_path[-1]["equity_eur"] if core_path else CORE_BOOK,
        },
        "short": {
            k: v
            for k, v in short.items()
            if k not in {"equity_path_full", "trade_rows"}
        },
        "combined": {
            "start_eur": CORE_BOOK + SHORT_BOOK,
            "end_eur": round(combined_end, 2),
            "pnl_eur": round(combined_pnl, 2),
            "return_pct": round(100 * combined_pnl / (CORE_BOOK + SHORT_BOOK), 2),
            "max_dd_eur": round(mdd, 2),
            "max_dd_pct": round(100 * mdd / (CORE_BOOK + SHORT_BOOK), 2),
            "days_both_idle": both_idle,
            "days_with_activity": covered,
            "activity_rate": round(covered / max(1, len(combined_path)), 3),
        },
        "by_week": week_rows,
        "equity_sample": combined_path[:: max(1, len(combined_path) // 60)]
        + ([combined_path[-1]] if combined_path else []),
        "core_trades": [
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "closed": datetime.fromtimestamp(t.closed_ms / 1000, UTC).isoformat(),
                "net_eur": round(t.net_eur, 2),
                "reason": t.reason,
                "notional_eur": round(t.notional_eur, 2),
            }
            for t in core_res.closed
        ],
        "short_trades": short.get("trade_rows"),
    }
    # strip heavy full path from short in file already
    OUT.write_text(json.dumps(payload, indent=2))
    print("\n=== COMBINED 12W ===", flush=True)
    print(
        f"core   pnl={payload['core']['pnl_total_eur']:+.2f}€  "
        f"({payload['core']['return_pct']:+.2f}%) trades={payload['core']['trades']}",
        flush=True,
    )
    print(
        f"short  pnl={payload['short']['pnl_eur']:+.2f}€  "
        f"({payload['short']['return_pct']:+.2f}%) trades={payload['short']['trades']} "
        f"dd={payload['short']['max_dd_pct']}%",
        flush=True,
    )
    print(
        f"COMBINED pnl={payload['combined']['pnl_eur']:+.2f}€  "
        f"({payload['combined']['return_pct']:+.2f}%) on €{CORE_BOOK+SHORT_BOOK:.0f}  "
        f"dd={payload['combined']['max_dd_pct']}%  "
        f"activity_days={payload['combined']['days_with_activity']}/{len(combined_path)}",
        flush=True,
    )
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
