#!/usr/bin/env python3
"""Short-weakest tactics research (accurate idle/cover accounting).

Uses the same cash/notional/idle-reentry model as combined_desk_12w_sim.py,
plus literature filters, scored on:
  - BEAR window 2025-10-06 → 2026-06-30 (no core coupling)
  - RECENT 12w 2026-06-28 → 2026-09-20 (core occupancy from combined sim)

Research-only — does not modify the live bot.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from artifacts.bear_market_strategy_sim import (
    BEAR_END,
    BEAR_START,
    BOOK_EUR,
    UNIVERSE,
    _align,
    _slice_idx,
    load_daily,
)
from artifacts.combined_desk_12w_sim import CoreOcc, simulate_short_sleeve
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    btc_bear_ok,
    evaluate_short_exit,
    rank_weakest,
    select_shorts,
)

OUT = Path(__file__).resolve().parent / "short_weakest_tactics_research.json"
COMBINED_12W = Path(__file__).resolve().parent / "combined_desk_12w_sim.json"
RECENT_START = "2026-06-28"
RECENT_END = "2026-09-20"


@dataclass(frozen=True)
class TacticExtras:
    """Filters beyond ShortWeakestConfig (research-only)."""

    name: str
    skip_days: int = 0
    lookback2: int = 0
    mom_floor2: float = -0.06
    require_below_sma: int = 0
    bounce_block: float = 0.0
    weight_mode_override: str | None = None  # equal | magnitude | inv_vol
    score_mode_force: str | None = None  # absolute | excess | None


def core_occ_from_combined() -> CoreOcc:
    """Rebuild daily core-active intervals from the 12w equity path."""
    if not COMBINED_12W.exists():
        return CoreOcc()
    raw = json.loads(COMBINED_12W.read_text())
    intervals: list[tuple[int, int]] = []
    for row in raw.get("short", {}).get("equity_path", []):
        if row.get("core_idle", True):
            continue
        d = str(row["date"])
        start = int(datetime.fromisoformat(d + "T00:00:00+00:00").timestamp() * 1000)
        intervals.append((start, start + 86_400_000))
    return CoreOcc(intervals=intervals)


def _sma_at(series: list[float], i: int, period: int) -> float | None:
    if i + 1 < period:
        return None
    return sum(series[i + 1 - period : i + 1]) / period


def _mom(series: list[float], i: int, lookback: int, skip: int) -> float | None:
    end = i - skip
    start = end - lookback
    if start < 0 or end < 0 or end > i:
        return None
    a, b = series[start], series[end]
    if a <= 0 or b <= 0:
        return None
    return b / a - 1.0


def rank_with_extras(
    daily_closes: dict[str, list[float]],
    i: int,
    cfg: ShortWeakestConfig,
    extras: TacticExtras,
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """Rank like live, then apply literature quality filters."""
    closes_map = {
        b: daily_closes[b][: i + 1]
        for b in ("BTC", *cfg.universe)
        if b in daily_closes
    }
    # Optional lookback override via temporary cfg for skip/dual
    cfg_rank = cfg
    if extras.skip_days > 0 and mode == "absolute":
        # Approximate skip by shortening the series end used for mom:
        # rebuild mom manually instead of rank_weakest.
        pass

    if extras.skip_days > 0 or extras.lookback2 > 0 or extras.require_below_sma or extras.bounce_block:
        use_excess = mode == "excess"
        lb = int(cfg.idle_lookback_days if use_excess else cfg.lookback_days)
        floor = float(cfg.idle_excess_floor if use_excess else cfg.mom_floor)
        skip = extras.skip_days if not use_excess else 0
        btc = daily_closes["BTC"]
        cands: list[dict[str, Any]] = []
        for base in cfg.universe:
            series = daily_closes[base]
            mom = _mom(series, i, lb, skip)
            if mom is None:
                continue
            score = mom
            if use_excess:
                bm = _mom(btc, i, lb, skip)
                if bm is None:
                    continue
                score = mom - bm
            if score > floor:
                continue
            if extras.lookback2 > 0 and not use_excess:
                mom2 = _mom(series, i, extras.lookback2, skip)
                if mom2 is None or mom2 > extras.mom_floor2:
                    continue
            if extras.require_below_sma > 0:
                sma = _sma_at(series, i, extras.require_below_sma)
                if sma is None or series[i] >= sma:
                    continue
            if extras.bounce_block > 0 and i > 0:
                day_ret = series[i] / series[i - 1] - 1.0
                if day_ret >= extras.bounce_block:
                    continue
            atr = 0.02
            if i >= 14:
                rets = [
                    abs(series[j] / series[j - 1] - 1.0)
                    for j in range(i - 13, i + 1)
                    if series[j - 1] > 0
                ]
                if rets:
                    atr = sum(rets) / len(rets)
            cands.append(
                {
                    "base": base,
                    "mom": mom,
                    "score": score,
                    "size_mult": 1.0,
                    "reasons": [f"mode={mode}", "extras"],
                    "atr": atr,
                }
            )
        cands.sort(key=lambda r: float(r["score"]))
        return cands

    cands, _ = rank_weakest(
        closes_map, cfg_rank, mode=mode, btc_closes=closes_map.get("BTC"), alphai=None
    )
    return cands


def select_with_inv_vol(
    cands: list[dict[str, Any]],
    cfg: ShortWeakestConfig,
    *,
    cash_eur: float,
    weight_mode: str,
) -> list[dict[str, Any]]:
    picks = cands[: int(cfg.top_n)]
    if not picks:
        return []
    deploy = max(0.0, cash_eur) * float(cfg.deploy_frac)
    if weight_mode == "inv_vol":
        inv = [1.0 / max(float(c.get("atr") or 0.02), 0.005) for c in picks]
        s = sum(inv) or 1.0
        raw_w = [x / s for x in inv]
    elif weight_mode == "magnitude":
        mag = sum(abs(float(c.get("score", c["mom"]))) for c in picks) or 1.0
        raw_w = [abs(float(c.get("score", c["mom"]))) / mag for c in picks]
    else:
        raw_w = [1.0 / len(picks)] * len(picks)
    out: list[dict[str, Any]] = []
    for w, c in zip(raw_w, picks):
        w = min(float(w), float(cfg.max_weight))
        notional = deploy * w
        notional = min(notional, deploy * float(cfg.max_weight))
        if notional < cfg.min_notional_eur:
            continue
        out.append(
            {
                "base": c["base"],
                "notional_eur": round(notional, 2),
                "mom": round(float(c["mom"]), 4),
                "reasons": list(c.get("reasons") or []),
                "atr": float(c.get("atr") or 0.02),
            }
        )
    return out


def simulate_tactics(
    *,
    daily_ts: list[int],
    daily_closes: dict[str, list[float]],
    i0: int,
    i1: int,
    core_occ: CoreOcc | None,
    cfg: ShortWeakestConfig,
    extras: TacticExtras,
) -> dict[str, Any]:
    """Mirror combined_desk_12w_sim.simulate_short_sleeve with extras."""
    cash = float(cfg.book_eur)
    positions: list[ShortPosition] = []
    trades: list[dict[str, Any]] = []
    equity_path: list[dict[str, Any]] = []
    last_reb_i = -10**9
    day_realized = 0.0
    week_realized = 0.0
    day_key = ""
    week_key = ""
    occ = core_occ or CoreOcc()  # empty → always idle

    def week_key_of(ms: int) -> str:
        dt = datetime.fromtimestamp(ms / 1000, UTC)
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    for i in range(i0, i1 + 1):
        t_ms = daily_ts[i]
        dt = datetime.fromtimestamp(t_ms / 1000, UTC)
        dk = dt.strftime("%Y-%m-%d")
        wk = week_key_of(t_ms)
        if dk != day_key:
            day_key = dk
            day_realized = 0.0
        if wk != week_key:
            week_key = wk
            week_realized = 0.0

        marks = {b: daily_closes[b][i] for b in cfg.universe if b in daily_closes}
        marks["BTC"] = daily_closes["BTC"][i]
        core_active = occ.active_at(t_ms + 12 * 3_600_000)

        if core_active and positions and cfg.cover_when_core_active:
            for pos in list(positions):
                mark = marks.get(pos.base, pos.entry_price)
                ret = pos.short_return(mark)
                net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
                cash += pos.notional_eur + net
                day_realized += net
                week_realized += net
                trades.append({"reason": "core_active_cover", "net_eur": net})
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
                day_realized += net
                week_realized += net
                trades.append({"reason": decision["reason"], "net_eur": net})
                positions.remove(pos)

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
            if positions:
                for pos in list(positions):
                    mark = marks.get(pos.base, pos.entry_price)
                    ret = pos.short_return(mark)
                    net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
                    cash += pos.notional_eur + net
                    trades.append({"reason": "rebalance", "net_eur": net})
                    positions.remove(pos)

            mode = "excess" if use_idle else "absolute"
            if extras.score_mode_force == "excess" and bear_ok:
                mode = "excess"
            cands = rank_with_extras(daily_closes, i, cfg, extras, mode=mode)
            wmode = extras.weight_mode_override or cfg.weight_mode
            if wmode == "inv_vol" or extras.skip_days or extras.lookback2 or extras.require_below_sma or extras.bounce_block:
                planned = select_with_inv_vol(cands, cfg, cash_eur=cash, weight_mode=wmode)
            else:
                planned = select_shorts(cands, cfg, cash_eur=cash, held=set())
            for row in planned:
                base = row["base"]
                notional = float(row["notional_eur"])
                mark = marks.get(base)
                if not mark or notional > cash:
                    continue
                fee = notional * (cfg.fee_rt / 2)
                cash -= fee + notional
                positions.append(
                    ShortPosition(
                        base=base,
                        entry_price=mark,
                        notional_eur=notional,
                        opened_ms=t_ms,
                        atr14=float(row.get("atr") or 0.02),
                    )
                )
                trades.append({"reason": "entry", "net_eur": 0.0})
            last_reb_i = i

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

    if positions:
        i = i1
        for pos in list(positions):
            mark = daily_closes[pos.base][i]
            ret = pos.short_return(mark)
            net = pos.notional_eur * ret - pos.notional_eur * (cfg.fee_rt / 2)
            cash += pos.notional_eur + net
            trades.append({"reason": "window_end", "net_eur": net})
            positions.clear()
        if equity_path:
            equity_path[-1]["equity_eur"] = round(cash, 2)
            equity_path[-1]["n_pos"] = 0

    closed = [t for t in trades if t.get("reason") != "entry"]
    wins = sum(1 for t in closed if float(t.get("net_eur") or 0) > 0)
    eq0 = float(cfg.book_eur)
    eq1 = float(equity_path[-1]["equity_eur"]) if equity_path else eq0
    peak = eq0
    mdd = 0.0
    for row in equity_path:
        v = float(row["equity_eur"])
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    by_reason: dict[str, dict[str, Any]] = {}
    for t in closed:
        r = str(t["reason"])
        slot = by_reason.setdefault(r, {"n": 0, "net_eur": 0.0})
        slot["n"] += 1
        slot["net_eur"] = round(slot["net_eur"] + float(t["net_eur"]), 2)
    pnl = eq1 - eq0
    calmar = (pnl / abs(mdd)) if abs(mdd) > 1 else (999.0 if pnl > 0 else 0.0)
    return {
        "pnl_eur": round(pnl, 2),
        "return_pct": round(100 * pnl / eq0, 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(100 * mdd / eq0, 2),
        "calmar": round(calmar, 3),
        "trades": len(closed),
        "win_rate": round(wins / len(closed), 3) if closed else None,
        "active_days": sum(1 for r in equity_path if r["n_pos"] > 0),
        "by_reason": by_reason,
    }


def base_cfg(**kw: Any) -> ShortWeakestConfig:
    defaults = dict(
        book_eur=BOOK_EUR,
        lookback_days=15,
        top_n=3,
        rebalance_days=14,
        mom_floor=-0.08,
        trail_pct=0.18,
        hard_stop_pct=0.12,
        max_weight=0.15,
        deploy_frac=1.0,
        weight_mode="equal",
        vol_spike_exit=True,
        only_when_core_idle=True,
        cover_when_core_active=True,
        idle_fill_enabled=True,
        idle_lookback_days=14,
        idle_excess_floor=-0.025,
        alphai_enabled=False,
        universe=DEFAULT_UNIVERSE,
        day_loss_limit_eur=600.0,
        week_loss_limit_eur=1600.0,
    )
    defaults.update(kw)
    return ShortWeakestConfig(**defaults)


def named_cases() -> list[tuple[ShortWeakestConfig, TacticExtras]]:
    live = (
        base_cfg(),
        TacticExtras(name="live_idle_fill_defaults"),
    )
    cases: list[tuple[ShortWeakestConfig, TacticExtras]] = [
        live,
        (
            base_cfg(idle_fill_enabled=False, deploy_frac=0.75),
            TacticExtras(name="bear_only_calmar_winner_dep75"),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="bear_only_deploy1"),
        ),
        (
            base_cfg(idle_fill_enabled=False, cover_when_core_active=False, only_when_core_idle=False),
            TacticExtras(name="always_on_bear_gate"),
        ),
        (
            base_cfg(idle_fill_enabled=True, idle_excess_floor=-0.06),
            TacticExtras(name="idle_stricter_floor_m6"),
        ),
        (
            base_cfg(idle_fill_enabled=True, idle_excess_floor=-0.08, idle_lookback_days=7),
            TacticExtras(name="idle_lb7_floor_m8"),
        ),
        (
            base_cfg(idle_fill_enabled=True, cover_when_core_active=False),
            TacticExtras(name="idle_no_cover"),
        ),
        (
            base_cfg(idle_fill_enabled=False, vol_spike_exit=False),
            TacticExtras(name="bear_only_no_volspike"),
        ),
        (
            base_cfg(idle_fill_enabled=False, hard_stop_pct=0.08, trail_pct=0.12),
            TacticExtras(name="bear_only_tight_stops"),
        ),
        (
            base_cfg(idle_fill_enabled=False, max_weight=0.10, deploy_frac=0.5),
            TacticExtras(name="modest_budget_mw10_dep50"),
        ),
        (
            base_cfg(idle_fill_enabled=False, mom_floor=-0.12),
            TacticExtras(name="harder_mom_floor_m12"),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="asness_skip2", skip_days=2),
        ),
        (
            base_cfg(idle_fill_enabled=False, lookback_days=7),
            TacticExtras(name="dual_horizon_7_30", lookback2=30, mom_floor2=-0.08),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="below_sma50", require_below_sma=50),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="bounce_block_4pct", bounce_block=0.04),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="inv_vol_sizing", weight_mode_override="inv_vol"),
        ),
        (
            base_cfg(idle_fill_enabled=False),
            TacticExtras(name="excess_rank_in_bear", score_mode_force="excess"),
        ),
        (
            base_cfg(
                idle_fill_enabled=False,
                hard_stop_pct=0.08,
                trail_pct=0.12,
                vol_spike_exit=False,
                max_weight=0.12,
                deploy_frac=0.75,
            ),
            TacticExtras(
                name="combo_quality_pack",
                skip_days=2,
                lookback2=30,
                mom_floor2=-0.06,
                require_below_sma=50,
                bounce_block=0.035,
                weight_mode_override="inv_vol",
            ),
        ),
        (
            base_cfg(
                idle_fill_enabled=True,
                idle_excess_floor=-0.06,
                hard_stop_pct=0.08,
                trail_pct=0.12,
                vol_spike_exit=False,
                max_weight=0.12,
                deploy_frac=0.75,
            ),
            TacticExtras(
                name="combo_plus_strict_idle",
                skip_days=2,
                require_below_sma=50,
                bounce_block=0.035,
            ),
        ),
        (
            base_cfg(idle_fill_enabled=False, lookback_days=21, top_n=3, mom_floor=-0.08),
            TacticExtras(name="lb21_skip2_volspike", skip_days=2),
        ),
    ]
    return cases


def grid_cases() -> list[tuple[ShortWeakestConfig, TacticExtras]]:
    out: list[tuple[ShortWeakestConfig, TacticExtras]] = []
    # A: bear-only quality × risk (~768)
    for lb, floor, trail, hard, mw, dep, vspike, skip, sma_f, bounce, lb2 in itertools.product(
        [15, 21],
        [-0.08, -0.12],
        [0.12, 0.18],
        [0.08, 0.12],
        [0.10, 0.15],
        [0.75, 1.0],
        [False, True],
        [0, 2],
        [0, 50],
        [0.0, 0.04],
        [0, 30],
    ):
        cfg = base_cfg(
            idle_fill_enabled=False,
            cover_when_core_active=False,
            only_when_core_idle=False,
            lookback_days=lb,
            mom_floor=floor,
            trail_pct=trail,
            hard_stop_pct=hard,
            max_weight=mw,
            deploy_frac=dep,
            vol_spike_exit=vspike,
        )
        extras = TacticExtras(
            name=(
                f"bear_lb{lb}_f{floor}_t{trail}_h{hard}_mw{mw}_d{dep}"
                f"_vs{int(vspike)}_sk{skip}_sma{sma_f}_bb{bounce}_lb2{lb2}"
            ),
            skip_days=skip,
            require_below_sma=sma_f,
            bounce_block=bounce,
            lookback2=lb2,
            mom_floor2=-0.06 if lb2 else -0.05,
        )
        out.append((cfg, extras))
    # B: idle diagnosis on quality base
    for idle, idle_fl, idle_lb, cover, vspike, dep in itertools.product(
        [True, False],
        [-0.025, -0.04, -0.06, -0.08],
        [7, 14],
        [True, False],
        [False, True],
        [0.5, 0.75, 1.0],
    ):
        if not idle and (idle_fl != -0.025 or idle_lb != 14):
            continue
        cfg = base_cfg(
            idle_fill_enabled=idle,
            idle_excess_floor=idle_fl,
            idle_lookback_days=idle_lb,
            cover_when_core_active=cover,
            only_when_core_idle=True,
            vol_spike_exit=vspike,
            deploy_frac=dep,
            hard_stop_pct=0.08,
            trail_pct=0.12,
            max_weight=0.12,
        )
        extras = TacticExtras(
            name=f"idle_on{int(idle)}_if{idle_fl}_ilb{idle_lb}_cv{int(cover)}_vs{int(vspike)}_d{dep}",
            skip_days=2,
            require_below_sma=50,
            bounce_block=0.035,
        )
        out.append((cfg, extras))
    return out


def _rank_key(r: dict[str, Any]) -> tuple:
    b, q = r["bear"], r["recent"]
    both = int(b["pnl_eur"] > 0 and q["pnl_eur"] > 0)
    # Prefer both profitable, then min calmar, then sum pnl, then less-negative DD
    return (
        both,
        min(b["calmar"], q["calmar"]),
        b["pnl_eur"] + q["pnl_eur"],
        min(b["max_dd_pct"], q["max_dd_pct"]),
    )


def slim(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": r["name"],
        "sum_pnl_eur": r["sum_pnl_eur"],
        "min_calmar": r["min_calmar"],
        "worst_dd_pct": r["worst_dd_pct"],
        "both_profitable": r["both_profitable"],
        "bear": {k: r["bear"][k] for k in ("pnl_eur", "return_pct", "max_dd_pct", "calmar", "trades", "win_rate", "by_reason")},
        "recent": {k: r["recent"][k] for k in ("pnl_eur", "return_pct", "max_dd_pct", "calmar", "trades", "win_rate", "by_reason")},
        "cfg": r["cfg"],
        "extras": r["extras"],
    }


def main() -> None:
    print("loading candles…", flush=True)
    series = load_daily(("BTC", *UNIVERSE), days=430)
    ts, closes = _align(series, ("BTC", *UNIVERSE))
    # ensure DEFAULT_UNIVERSE keys present
    for b in DEFAULT_UNIVERSE:
        if b not in closes and b in UNIVERSE:
            pass
    i_bear0, i_bear1 = _slice_idx(ts, BEAR_START, BEAR_END)
    i_rec0, i_rec1 = _slice_idx(ts, RECENT_START, RECENT_END)
    core_occ = core_occ_from_combined()
    print(
        f"bear days={i_bear1-i_bear0+1} recent={i_rec1-i_rec0+1} "
        f"core_active_intervals={len(core_occ.intervals)}",
        flush=True,
    )

    # Sanity: live defaults should ≈ combined short −€1547
    live_cfg, live_ex = named_cases()[0]
    sanity = simulate_tactics(
        daily_ts=ts,
        daily_closes=closes,
        i0=i_rec0,
        i1=i_rec1,
        core_occ=core_occ,
        cfg=live_cfg,
        extras=live_ex,
    )
    print(f"SANITY live recent pnl={sanity['pnl_eur']} dd={sanity['max_dd_pct']}% (expect ~-1547)", flush=True)

    # Also compare stock simulate_short_sleeve
    stock = simulate_short_sleeve(
        daily_ts=ts,
        daily_closes=closes,
        i0=i_rec0,
        i1=i_rec1,
        core_occ=core_occ,
        cfg=live_cfg,
    )
    print(f"STOCK simulate_short_sleeve pnl={stock['pnl_eur']} dd={stock['max_dd_pct']}%", flush=True)

    cases = named_cases() + grid_cases()
    seen: set[str] = set()
    uniq: list[tuple[ShortWeakestConfig, TacticExtras]] = []
    for cfg, ex in cases:
        if ex.name in seen:
            continue
        seen.add(ex.name)
        uniq.append((cfg, ex))
    print(f"evaluating {len(uniq)} tactics…", flush=True)

    rows: list[dict[str, Any]] = []
    for n, (cfg, ex) in enumerate(uniq, 1):
        bear = simulate_tactics(
            daily_ts=ts,
            daily_closes=closes,
            i0=i_bear0,
            i1=i_bear1,
            core_occ=None,  # no core in bear research window
            cfg=replace(cfg, only_when_core_idle=False, cover_when_core_active=False),
            extras=ex,
        )
        recent = simulate_tactics(
            daily_ts=ts,
            daily_closes=closes,
            i0=i_rec0,
            i1=i_rec1,
            core_occ=core_occ if (cfg.idle_fill_enabled or cfg.only_when_core_idle) else None,
            cfg=cfg,
            extras=ex,
        )
        row = {
            "name": ex.name,
            "bear": bear,
            "recent": recent,
            "sum_pnl_eur": round(bear["pnl_eur"] + recent["pnl_eur"], 2),
            "min_calmar": round(min(bear["calmar"], recent["calmar"]), 3),
            "worst_dd_pct": round(min(bear["max_dd_pct"], recent["max_dd_pct"]), 2),
            "both_profitable": bear["pnl_eur"] > 0 and recent["pnl_eur"] > 0,
            "cfg": {
                "lookback_days": cfg.lookback_days,
                "top_n": cfg.top_n,
                "rebalance_days": cfg.rebalance_days,
                "mom_floor": cfg.mom_floor,
                "trail_pct": cfg.trail_pct,
                "hard_stop_pct": cfg.hard_stop_pct,
                "max_weight": cfg.max_weight,
                "deploy_frac": cfg.deploy_frac,
                "vol_spike_exit": cfg.vol_spike_exit,
                "idle_fill_enabled": cfg.idle_fill_enabled,
                "idle_lookback_days": cfg.idle_lookback_days,
                "idle_excess_floor": cfg.idle_excess_floor,
                "cover_when_core_active": cfg.cover_when_core_active,
                "only_when_core_idle": cfg.only_when_core_idle,
            },
            "extras": asdict(ex),
        }
        rows.append(row)
        if n % 200 == 0 or n == len(uniq):
            print(f"  {n}/{len(uniq)}", flush=True)

    ranked = sorted(rows, key=_rank_key, reverse=True)
    live = next(r for r in rows if r["name"] == "live_idle_fill_defaults")
    both_ok = [r for r in ranked if r["both_profitable"]]
    low_dd = [r for r in both_ok if r["bear"]["max_dd_pct"] > -12 and r["recent"]["max_dd_pct"] > -12]
    bear_ok_recent = [
        r
        for r in ranked
        if r["bear"]["pnl_eur"] > 1000 and r["bear"]["max_dd_pct"] > -10 and r["recent"]["pnl_eur"] > 0
    ]
    # Best recent PnL among DD recent > -8% and bear pnl > 0
    best_recent = sorted(
        [r for r in ranked if r["bear"]["pnl_eur"] > 0 and r["recent"]["max_dd_pct"] > -8],
        key=lambda r: (r["recent"]["pnl_eur"], -abs(r["recent"]["max_dd_pct"])),
        reverse=True,
    )
    named_names = {ex.name for _, ex in named_cases()}

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "book_eur": BOOK_EUR,
        "windows": {
            "bear": {"start": BEAR_START, "end": BEAR_END},
            "recent_12w": {"start": RECENT_START, "end": RECENT_END},
        },
        "sanity": {
            "tactics_sim_recent": sanity,
            "stock_sim_recent": {
                "pnl_eur": stock["pnl_eur"],
                "max_dd_pct": stock["max_dd_pct"],
                "by_reason": stock.get("by_reason"),
            },
            "combined_json_short_pnl": json.loads(COMBINED_12W.read_text())["short"]["pnl_eur"]
            if COMBINED_12W.exists()
            else None,
        },
        "n_tactics": len(rows),
        "sources": [
            "Cross-sectional momentum: short laggards / excess vs BTC",
            "Asness skip-recent lookback (2d)",
            "Multi-horizon weakness confirm (7d+30d)",
            "Trend filter price < SMA50",
            "Bounce block after large green day (squeeze)",
            "Inverse-vol sizing; modest max_weight/deploy",
            "Own data: prior Calmar sweep + 12w idle-fill failure",
        ],
        "live_baseline": slim(live),
        "winner_overall": slim(ranked[0]),
        "winner_both_profit": slim(both_ok[0]) if both_ok else None,
        "winner_both_dd_gt_m12": slim(low_dd[0]) if low_dd else None,
        "winner_bear_strong_recent_profit": slim(bear_ok_recent[0]) if bear_ok_recent else None,
        "best_recent_among_bear_profit_dd8": slim(best_recent[0]) if best_recent else None,
        "named_presets_ranked": [slim(r) for r in ranked if r["name"] in named_names][:25],
        "top25": [slim(r) for r in ranked[:25]],
        "top25_both_profit": [slim(r) for r in both_ok[:25]],
        "recommendation": None,  # filled below
    }

    # Build recommendation textually from winners
    pick = out["winner_both_dd_gt_m12"] or out["winner_both_profit"] or out["winner_overall"]
    out["recommendation"] = {
        "pick": pick["name"] if pick else None,
        "why": (
            "Maximize dual-window Calmar (both profitable, DD controlled). "
            "Prefer configs that fix the 12w idle-fill bleed without wrecking the bear Calmar edge."
        ),
        "cfg": pick.get("cfg") if pick else None,
        "extras": pick.get("extras") if pick else None,
        "bear": pick.get("bear") if pick else None,
        "recent": pick.get("recent") if pick else None,
    }

    OUT.write_text(json.dumps(out, indent=2))
    print("wrote", OUT, flush=True)
    print("LIVE recent", live["recent"]["pnl_eur"], live["recent"]["max_dd_pct"])
    print("WIN", ranked[0]["name"], "sum", ranked[0]["sum_pnl_eur"], "minc", ranked[0]["min_calmar"])
    if both_ok:
        print("BOTH", both_ok[0]["name"], both_ok[0]["sum_pnl_eur"], both_ok[0]["min_calmar"])


if __name__ == "__main__":
    main()
