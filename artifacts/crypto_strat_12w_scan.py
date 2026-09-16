#!/usr/bin/env python3
"""Scan classic crypto strategies vs current WR+survival €20k desk (12 weeks).

Sources mapped into generic, coin-agnostic rules (no per-ticker hardcodes):
  - PyQuantLab: cross-sectional momentum + BTC regime filter
  - Antonacci dual momentum (absolute + relative) → cash gate
  - BTC EMA regime → equal-weight alt basket (long-only / cash)
  - Donchian breakout / EMA trend / TSMOM / RSI & Bollinger mean-reversion
  - Inverse-vol weighted CSMOM; buy&hold benchmarks

Portfolio arms use daily closes from Bitvavo 15m candles, fee on turnover
aligned to desk fee_rt (one-way = fee_rt/2). Desk baseline uses the live
WR+survival knobs via ``simulate``.

Writes ``artifacts/crypto_strat_12w_scan.json`` and prints beaters only.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import BAR_MS, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).with_suffix(".json")
BOOK = 20_000.0
CLIP = 10_000.0
FEE_RT = 0.003
FEE_ONE_WAY = FEE_RT / 2.0
WEEKS = 12


# ---------------------------------------------------------------------------
# Desk baseline
# ---------------------------------------------------------------------------


def live_cfg(**extra: Any) -> DeskConfig:
    knobs: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        clip_eur=CLIP,
        max_positions=3,
        book_eur=BOOK,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        fade_eta_sec=0.0,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        strong_clip_mult=1.3,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        weak_clip_mult=0.7,
        skip_weekend_entries=True,
        refill_on_exit=True,
        macro_caution_mode="reduce",
        alphai_clip_mult=1.0,
        alphai_size_mode="binary",
        outcome_size_enabled=False,
        fee_rt=FEE_RT,
    )
    knobs.update(extra)
    return DeskConfig().with_overrides(**knobs)


# ---------------------------------------------------------------------------
# Daily panel from 15m candles
# ---------------------------------------------------------------------------


def daily_closes(
    candles: Mapping[str, Sequence], start_ms: int, end_ms: int
) -> tuple[list[int], dict[str, list[float | None]]]:
    """UTC daily last close for each base; None if missing that day."""
    by_day: dict[int, dict[str, float]] = {}
    for base, rows in candles.items():
        for r in rows:
            ts = int(r[0])
            if ts < start_ms - 100 * 86_400_000 or ts >= end_ms:
                continue
            day = (ts // 86_400_000) * 86_400_000
            by_day.setdefault(day, {})[base] = float(r[4])
    days = sorted(d for d in by_day if start_ms - 100 * 86_400_000 <= d < end_ms)
    bases = sorted(candles.keys())
    panel = {b: [] for b in bases}
    for d in days:
        row = by_day[d]
        for b in bases:
            panel[b].append(row.get(b))
    return days, panel


def ret(panel: dict[str, list[float | None]], base: str, i: int, lookback: int) -> float | None:
    series = panel[base]
    if i < lookback or i >= len(series):
        return None
    a, b = series[i - lookback], series[i]
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1.0


def ema_at(series: list[float | None], i: int, span: int) -> float | None:
    alpha = 2.0 / (span + 1.0)
    val: float | None = None
    start = max(0, i - span * 5)
    for j in range(start, i + 1):
        px = series[j]
        if px is None:
            continue
        val = px if val is None else alpha * px + (1 - alpha) * val
    return val


def sma_at(series: list[float | None], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    window = series[i - n + 1 : i + 1]
    if any(x is None or x <= 0 for x in window):
        return None
    return sum(window) / n  # type: ignore[arg-type]


def highest(series: list[float | None], i: int, n: int) -> float | None:
    if i < n:
        return None
    window = series[i - n : i]  # prior n bars (exclude today for breakout)
    if any(x is None for x in window):
        return None
    return max(window)  # type: ignore[arg-type]


def lowest(series: list[float | None], i: int, n: int) -> float | None:
    if i < n:
        return None
    window = series[i - n : i]
    if any(x is None for x in window):
        return None
    return min(window)  # type: ignore[arg-type]


def rsi_at(series: list[float | None], i: int, n: int = 14) -> float | None:
    if i < n + 1:
        return None
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        a, b = series[j - 1], series[j]
        if a is None or b is None:
            return None
        d = b - a
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - (100.0 / (1.0 + rs))


def realized_vol(panel: dict[str, list[float | None]], base: str, i: int, n: int = 14) -> float | None:
    if i < n:
        return None
    rets = []
    for j in range(i - n + 1, i + 1):
        a, b = panel[base][j - 1], panel[base][j]
        if a is None or b is None or a <= 0:
            return None
        rets.append(b / a - 1.0)
    if not rets:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) + 1e-12


# ---------------------------------------------------------------------------
# Portfolio engine
# ---------------------------------------------------------------------------


@dataclass
class PortResult:
    label: str
    family: str
    source: str
    why: str
    equity: list[float]
    days: list[int]
    turnover: float
    n_rebalances: int

    def summary(self, book: float = BOOK) -> dict[str, Any]:
        if not self.equity:
            return {
                "total_eur": 0.0,
                "return_pct": 0.0,
                "max_drawdown_eur": 0.0,
                "max_drawdown_pct": 0.0,
                "calmar_like": None,
                "turnover": 0.0,
                "n_rebalances": 0,
                "final_equity": book,
            }
        final = self.equity[-1]
        pnl = final - book
        peak = book
        worst = 0.0
        for e in self.equity:
            peak = max(peak, e)
            worst = min(worst, e - peak)
        calmar = None if worst >= -1e-9 else round(pnl / abs(worst), 3)
        return {
            "total_eur": round(pnl, 2),
            "return_pct": round(100.0 * pnl / book, 3),
            "max_drawdown_eur": round(worst, 2),
            "max_drawdown_pct": round(100.0 * worst / book, 3),
            "calmar_like": calmar,
            "turnover": round(self.turnover, 3),
            "n_rebalances": self.n_rebalances,
            "final_equity": round(final, 2),
        }


WeightFn = Callable[[int, list[int], dict[str, list[float | None]], list[str]], dict[str, float]]


def run_portfolio(
    *,
    label: str,
    family: str,
    source: str,
    why: str,
    days: list[int],
    panel: dict[str, list[float | None]],
    alts: list[str],
    eval_start_ms: int,
    weight_fn: WeightFn,
    rebalance_every: int = 1,
    book: float = BOOK,
) -> PortResult:
    """Share-based daily MTM; rebalance every N days with fee on |Δw|."""
    bases = alts + ["BTC"]
    n = len(days)
    qty = {b: 0.0 for b in bases}
    cash = book
    curve: list[float] = []
    curve_days: list[int] = []
    turnover_sum = 0.0
    n_reb = 0

    def nav_at(i: int) -> float:
        total = cash
        for b, q in qty.items():
            if q == 0.0:
                continue
            px = panel[b][i]
            if px is None:
                continue
            total += q * px
        return total

    def current_weights(i: int) -> dict[str, float]:
        nav = nav_at(i)
        if nav <= 0:
            return {b: 0.0 for b in bases}
        out = {b: 0.0 for b in bases}
        for b, q in qty.items():
            px = panel[b][i]
            if px is None or q == 0.0:
                continue
            out[b] = (q * px) / nav
        return out

    def set_weights(i: int, target: dict[str, float]) -> float:
        """Trade toward target portfolio weights; fee on traded notional."""
        nonlocal cash
        nav = nav_at(i)
        if nav <= 0:
            return 0.0
        cur = current_weights(i)
        tgt = {b: float(target.get(b, 0.0)) for b in bases}
        turn = sum(abs(tgt.get(b, 0.0) - cur.get(b, 0.0)) for b in bases)

        # Sells / reduces first.
        for b in bases:
            px = panel[b][i]
            if px is None or px <= 0:
                continue
            desired_eur = tgt.get(b, 0.0) * nav
            cur_eur = qty[b] * px
            if cur_eur <= desired_eur + 1e-9:
                continue
            sell_eur = cur_eur - desired_eur
            fee = sell_eur * FEE_ONE_WAY
            qty[b] = desired_eur / px
            cash += sell_eur - fee

        # Buys / increases next (use fresh nav after sells).
        nav2 = nav_at(i)
        for b in bases:
            px = panel[b][i]
            if px is None or px <= 0:
                continue
            desired_eur = tgt.get(b, 0.0) * nav2
            cur_eur = qty[b] * px
            if desired_eur <= cur_eur + 1e-9:
                continue
            buy_eur = desired_eur - cur_eur
            fee = buy_eur * FEE_ONE_WAY
            if buy_eur + fee > cash:
                buy_eur = max(0.0, cash / (1.0 + FEE_ONE_WAY))
                fee = buy_eur * FEE_ONE_WAY
            qty[b] += buy_eur / px
            cash -= buy_eur + fee
        return turn

    eval_i = next((i for i, d in enumerate(days) if d >= eval_start_ms), None)
    if eval_i is None or eval_i >= n - 1:
        return PortResult(label, family, source, why, [], [], 0.0, 0)

    # Initial deploy at eval start
    turn = set_weights(eval_i, weight_fn(eval_i, days, panel, alts))
    turnover_sum += turn
    n_reb += 1

    reb_count = 0
    for i in range(eval_i, n - 1):
        # holdings mark to next day's close (qty fixed)
        curve.append(nav_at(i + 1))
        curve_days.append(days[i + 1])
        reb_count += 1
        if reb_count % rebalance_every == 0 and i + 1 < n:
            turn = set_weights(i + 1, weight_fn(i + 1, days, panel, alts))
            if turn > 1e-12:
                turnover_sum += turn
                n_reb += 1

    return PortResult(label, family, source, why, curve, curve_days, turnover_sum, n_reb)


# ---------------------------------------------------------------------------
# Strategy weight functions
# ---------------------------------------------------------------------------


def _top_n(scores: dict[str, float], n: int) -> list[str]:
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [b for b, _ in ranked[:n] if math.isfinite(_)]


def w_cash(_i, _days, _panel, alts) -> dict[str, float]:
    return {}


def w_buy_hold_btc(i, days, panel, alts) -> dict[str, float]:
    return {"BTC": 1.0}


def w_eq_alts(i, days, panel, alts) -> dict[str, float]:
    live = [b for b in alts if panel[b][i] is not None]
    if not live:
        return {}
    w = 1.0 / len(live)
    return {b: w for b in live}


def make_xs_mom(
    *,
    fast: int,
    slow: int,
    n_long: int,
    btc_threshold: float = 0.0,
    include_btc: bool = False,
    inv_vol: bool = False,
    abs_gate: bool = False,
) -> WeightFn:
    """PyQuantLab-style CSMOM (+ optional dual-momentum abs gate / inv-vol)."""

    def fn(i, days, panel, alts) -> dict[str, float]:
        universe = (["BTC"] + alts) if include_btc else alts
        scores: dict[str, float] = {}
        for b in universe:
            mf, ms = ret(panel, b, i, fast), ret(panel, b, i, slow)
            if mf is None or ms is None:
                continue
            scores[b] = 0.3 * mf + 0.7 * ms
        btc_s = scores.get("BTC")
        if btc_s is None:
            # still compute BTC score even if not in universe weights
            mf, ms = ret(panel, "BTC", i, fast), ret(panel, "BTC", i, slow)
            if mf is None or ms is None:
                return {}
            btc_s = 0.3 * mf + 0.7 * ms
        if btc_s <= btc_threshold:
            return {}
        # exclude BTC from ranking unless include_btc
        rank_scores = {b: s for b, s in scores.items() if b != "BTC" or include_btc}
        if abs_gate:
            rank_scores = {b: s for b, s in rank_scores.items() if s > 0}
        picks = _top_n(rank_scores, n_long)
        if not picks:
            return {}
        if inv_vol:
            inv = {}
            for b in picks:
                v = realized_vol(panel, b, i, 14)
                if v is None:
                    return {}
                inv[b] = 1.0 / v
            s = sum(inv.values())
            return {b: inv[b] / s for b in picks}
        w = 1.0 / len(picks)
        return {b: w for b in picks}

    return fn


def make_dual_mom_topn(lookback: int, n_long: int) -> WeightFn:
    """Antonacci dual momentum: abs return > 0, then top-N by relative return."""

    def fn(i, days, panel, alts) -> dict[str, float]:
        scores = {}
        for b in alts:
            r = ret(panel, b, i, lookback)
            if r is not None and r > 0:
                scores[b] = r
        # BTC absolute gate
        btc_r = ret(panel, "BTC", i, lookback)
        if btc_r is None or btc_r <= 0:
            return {}
        picks = _top_n(scores, n_long)
        if not picks:
            return {}
        w = 1.0 / len(picks)
        return {b: w for b in picks}

    return fn


def make_btc_ema_basket(fast: int = 30, slow: int = 90) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        e_f = ema_at(panel["BTC"], i, fast)
        e_s = ema_at(panel["BTC"], i, slow)
        if e_f is None or e_s is None or e_f <= e_s:
            return {}
        return w_eq_alts(i, days, panel, alts)

    return fn


def make_tsmom(lookback: int) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        btc_r = ret(panel, "BTC", i, lookback)
        if btc_r is None or btc_r <= 0:
            return {}
        longs = []
        for b in alts:
            r = ret(panel, b, i, lookback)
            if r is not None and r > 0:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


def make_donchian(n: int = 20, btc_sma: int = 50) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        btc_px = panel["BTC"][i]
        btc_ma = sma_at(panel["BTC"], i, btc_sma)
        if btc_px is None or btc_ma is None or btc_px <= btc_ma:
            return {}
        longs = []
        for b in alts:
            px = panel[b][i]
            hi = highest(panel[b], i, n)
            if px is None or hi is None:
                continue
            if px > hi:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


def make_ema_cross(fast: int = 20, slow: int = 50, btc_sma: int = 50) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        btc_px = panel["BTC"][i]
        btc_ma = sma_at(panel["BTC"], i, btc_sma)
        if btc_px is None or btc_ma is None or btc_px <= btc_ma:
            return {}
        longs = []
        for b in alts:
            ef = ema_at(panel[b], i, fast)
            es = ema_at(panel[b], i, slow)
            if ef is None or es is None:
                continue
            if ef > es:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


def make_rsi_mr(n: int = 14, buy_below: float = 30.0, btc_filter: bool = True) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        if btc_filter:
            btc_r = ret(panel, "BTC", i, 30)
            if btc_r is None or btc_r < -0.05:  # skip deep BTC dump
                return {}
        longs = []
        for b in alts:
            r = rsi_at(panel[b], i, n)
            if r is not None and r < buy_below:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


def make_boll_mr(n: int = 20, k: float = 2.0) -> WeightFn:
    def fn(i, days, panel, alts) -> dict[str, float]:
        longs = []
        for b in alts:
            mid = sma_at(panel[b], i, n)
            if mid is None:
                continue
            window = panel[b][i - n + 1 : i + 1]
            if any(x is None for x in window):
                continue
            var = sum((float(x) - mid) ** 2 for x in window) / n
            sd = math.sqrt(var)
            px = panel[b][i]
            if px is None:
                continue
            if px < mid - k * sd:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


def make_pullback_ema(trend: int = 50, pull: int = 20) -> WeightFn:
    """Trend-following pullback: price > SMA50, near EMA20 (classic continuation)."""

    def fn(i, days, panel, alts) -> dict[str, float]:
        btc_px = panel["BTC"][i]
        btc_ma = sma_at(panel["BTC"], i, trend)
        if btc_px is None or btc_ma is None or btc_px <= btc_ma:
            return {}
        longs = []
        for b in alts:
            px = panel[b][i]
            ma = sma_at(panel[b], i, trend)
            e = ema_at(panel[b], i, pull)
            if px is None or ma is None or e is None or ma <= 0:
                continue
            if px > ma and abs(px / e - 1.0) <= 0.02:
                longs.append(b)
        if not longs:
            return {}
        w = 1.0 / len(longs)
        return {b: w for b in longs}

    return fn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(weeks=WEEKS)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)

    cfg0 = DeskConfig()
    alts = list(cfg0.universe)
    candles = load_candles(("BTC", *alts), days=WEEKS * 7 + 100, end_ms=end_ms)

    # --- desk baseline ---
    desk_res = simulate(
        candles, live_cfg(), start_ms=start_ms, end_ms=end_ms, alphai=None
    )
    desk_s = desk_res.summary()
    baseline_pnl = float(desk_s.get("total_eur") or 0.0)
    baseline_dd = float(desk_s.get("max_drawdown_eur") or 0.0)
    baseline_calmar = (
        None if baseline_dd >= -1e-9 else round(baseline_pnl / abs(baseline_dd), 3)
    )
    baseline = {
        "label": "baseline_wr_survival_desk",
        "family": "live_desk",
        "source": "current WR+survival €20k×€10k×3 (tape-only AlphaI off)",
        "why": "Current production-style desk: RS excess vs BTC, soft regime, "
        "trail 3%/4%→2%, hard 3%, 36h time exit, weekend skip.",
        "summary": desk_s,
        "return_pct": round(100.0 * baseline_pnl / BOOK, 3),
        "total_eur": round(baseline_pnl, 2),
        "max_drawdown_eur": desk_s.get("max_drawdown_eur"),
        "calmar_like": baseline_calmar,
        "trades": desk_s.get("trades"),
        "win_rate": desk_s.get("win_rate"),
    }
    print(
        f"baseline desk: €{baseline_pnl:.2f} WR={desk_s.get('win_rate')} "
        f"n={desk_s.get('trades')} calmar={baseline_calmar}"
    )

    days, panel = daily_closes(candles, start_ms, end_ms)
    print(f"daily panel days={len(days)} alts={len(alts)}")

    specs: list[tuple[str, str, str, str, WeightFn, int]] = [
        (
            "bh_btc",
            "benchmark",
            "classic buy&hold",
            "Hold 100% BTC for the window (no timing).",
            w_buy_hold_btc,
            9999,
        ),
        (
            "bh_eq_alts",
            "benchmark",
            "equal-weight buy&hold",
            "Hold equal-weight universe alts; no rebalance after entry.",
            w_eq_alts,
            9999,
        ),
        (
            "eq_alts_weekly",
            "benchmark",
            "equal-weight weekly rebalance",
            "Equal-weight alts, rebalance weekly (fee drag check).",
            w_eq_alts,
            7,
        ),
        (
            "csmom_7_30_top3_w",
            "cross_sectional_momentum",
            "PyQuantLab XS-mom + BTC filter (7d/30d, weekly)",
            "Score=0.3·mom7+0.7·mom30; long top-3 alts only when BTC score>0; weekly rebalance.",
            make_xs_mom(fast=7, slow=30, n_long=3),
            7,
        ),
        (
            "csmom_7_30_top3_d",
            "cross_sectional_momentum",
            "PyQuantLab XS-mom + BTC filter (daily)",
            "Same score; daily rebalance (higher turnover).",
            make_xs_mom(fast=7, slow=30, n_long=3),
            1,
        ),
        (
            "csmom_14_60_top3_w",
            "cross_sectional_momentum",
            "PyQuantLab XS-mom slower (14d/60d)",
            "Slower momentum blend; weekly; BTC score>0.",
            make_xs_mom(fast=14, slow=60, n_long=3),
            7,
        ),
        (
            "csmom_30_90_top3_w",
            "cross_sectional_momentum",
            "PyQuantLab classic 30/90 bar idea → days",
            "30d/90d blend (literature slow lookback); weekly; BTC filter.",
            make_xs_mom(fast=30, slow=90, n_long=3),
            7,
        ),
        (
            "csmom_7_30_top5_w",
            "cross_sectional_momentum",
            "XS-mom top-5 weekly",
            "Broader basket (top 5) with same BTC filter.",
            make_xs_mom(fast=7, slow=30, n_long=5),
            7,
        ),
        (
            "csmom_invvol_top3_w",
            "cross_sectional_momentum",
            "FXEmpire-style inverse-vol weights",
            "Top-3 by XS-mom, size ∝ 1/realized vol (14d); BTC filter.",
            make_xs_mom(fast=7, slow=30, n_long=3, inv_vol=True),
            7,
        ),
        (
            "dual_mom_30_top3_w",
            "dual_momentum",
            "Antacci dual momentum (30d)",
            "BTC 30d>0 (absolute); among alts with 30d>0 take top-3 relative; else cash.",
            make_dual_mom_topn(30, 3),
            7,
        ),
        (
            "dual_mom_14_top3_w",
            "dual_momentum",
            "Dual momentum (14d)",
            "Faster dual-momentum gate (14d abs+rel).",
            make_dual_mom_topn(14, 3),
            7,
        ),
        (
            "dual_mom_60_top3_w",
            "dual_momentum",
            "Dual momentum (60d)",
            "Slower dual-momentum gate (60d).",
            make_dual_mom_topn(60, 3),
            7,
        ),
        (
            "csmom_abs_gate_top3_w",
            "dual_momentum",
            "XS-mom + absolute score gate",
            "PyQuantLab score + only positive-score names + BTC>0 (dual-style).",
            make_xs_mom(fast=7, slow=30, n_long=3, abs_gate=True),
            7,
        ),
        (
            "btc_ema_30_90_eq",
            "regime_basket",
            "PyQuantLab BTC EMA regime → alt basket",
            "Long equal-weight alts iff BTC EMA30>EMA90; else cash. Weekly rebalance.",
            make_btc_ema_basket(30, 90),
            7,
        ),
        (
            "btc_ema_20_50_eq",
            "regime_basket",
            "Faster BTC EMA regime → alt basket",
            "EMA20>EMA50 BTC gate; equal-weight alts; weekly.",
            make_btc_ema_basket(20, 50),
            7,
        ),
        (
            "tsmom_30_w",
            "time_series_momentum",
            "TSMOM 30d (Moskowitz-style, long-only)",
            "If BTC 30d>0, equal-weight every alt with 30d>0; else cash.",
            make_tsmom(30),
            7,
        ),
        (
            "tsmom_14_w",
            "time_series_momentum",
            "TSMOM 14d",
            "Faster TSMOM with BTC absolute gate.",
            make_tsmom(14),
            7,
        ),
        (
            "donchian_20_w",
            "breakout",
            "Coinquant-style Donchian breakout + BTC SMA",
            "BTC>SMA50; long alts closing above prior 20d high; weekly.",
            make_donchian(20, 50),
            7,
        ),
        (
            "donchian_10_d",
            "breakout",
            "Faster Donchian 10d daily",
            "BTC>SMA50; breakout prior 10d high; daily rebalance.",
            make_donchian(10, 50),
            1,
        ),
        (
            "ema_cross_20_50_w",
            "trend_following",
            "EMA 20/50 cross + BTC SMA filter",
            "Classic trend: alt EMA20>EMA50 and BTC>SMA50; weekly.",
            make_ema_cross(20, 50, 50),
            7,
        ),
        (
            "pullback_ema_w",
            "trend_following",
            "Uptrend pullback to EMA20",
            "BTC>SMA50; alts above SMA50 and within 2% of EMA20 (continuation entry).",
            make_pullback_ema(50, 20),
            7,
        ),
        (
            "rsi_mr_14_w",
            "mean_reversion",
            "RSI-14 <30 mean reversion (Coinquant family)",
            "Buy oversold alts (RSI<30); skip if BTC 30d < -5%; weekly.",
            make_rsi_mr(14, 30.0, True),
            7,
        ),
        (
            "boll_mr_20_w",
            "mean_reversion",
            "Bollinger lower-band mean reversion",
            "Long alts below mid−2σ (20d); no trend filter (naked MR stress).",
            make_boll_mr(20, 2.0),
            7,
        ),
    ]

    results: list[dict[str, Any]] = []
    for label, family, source, why, wfn, every in specs:
        pr = run_portfolio(
            label=label,
            family=family,
            source=source,
            why=why,
            days=days,
            panel=panel,
            alts=alts,
            eval_start_ms=start_ms,
            weight_fn=wfn,
            rebalance_every=every,
        )
        s = pr.summary()
        row = {
            "label": label,
            "family": family,
            "source": source,
            "why": why,
            "rebalance_every_days": every,
            **s,
            "beats_baseline_pnl": s["total_eur"] > baseline_pnl + 1.0,
            "beats_baseline_calmar": (
                s.get("calmar_like") is not None
                and baseline_calmar is not None
                and s["calmar_like"] > baseline_calmar
            ),
            "pnl_delta_eur": round(s["total_eur"] - baseline_pnl, 2),
        }
        results.append(row)
        flag = "BEAT" if row["beats_baseline_pnl"] else "lose"
        print(
            f"[{flag}] {label:28} €{s['total_eur']:+8.2f}  "
            f"Δ€{row['pnl_delta_eur']:+8.2f}  dd€{s['max_drawdown_eur']}  "
            f"calmar={s.get('calmar_like')}"
        )

    beaters = sorted(
        [r for r in results if r["beats_baseline_pnl"]],
        key=lambda r: -r["total_eur"],
    )
    quality = sorted(
        [
            r
            for r in results
            if r["beats_baseline_pnl"]
            and abs(r["max_drawdown_eur"]) <= abs(float(baseline.get("max_drawdown_eur") or 0)) * 1.35
        ],
        key=lambda r: -r["total_eur"],
    )
    calmar_beaters = sorted(
        [r for r in results if r.get("beats_baseline_calmar")],
        key=lambda r: -(r.get("calmar_like") or 0),
    )
    losers = sorted(results, key=lambda r: -r["total_eur"])

    # Desk config variants inspired by literature (still coin-agnostic)
    desk_variants = []
    for name, overrides, why in [
        (
            "desk_btc_trend_filter_sma50",
            {"btc_min_ret": 0.0},  # require BTC flat-or-up (stricter than -1%)
            "Literature BTC regime: only enter when BTC 24h ≥ 0 (stricter absolute momentum).",
        ),
        (
            "desk_higher_breadth",
            {"min_breadth": 0.65},
            "CSMOM-style participation: demand broader tape (≥65% alts green).",
        ),
        (
            "desk_top1_concentrate",
            {"top_n": 1, "top_n_broad": 1, "max_positions": 1, "clip_eur": 20_000.0},
            "Concentrate like dual-momentum TopN=1: single best RS name, full book clip.",
        ),
        (
            "desk_vol_top8",
            {"universe_top_by_volume": 8, "min_volume_eur": 2_000_000.0},
            "Liquidity filter from XS-mom literature: trade only top volume names.",
        ),
        (
            "desk_longer_hold_trail",
            {"time_exit_hours": 72.0, "trail_pct": 0.04, "trail_tight_after": 0.06},
            "Trend-following: give runners more room (wider trail, 72h time).",
        ),
        (
            "desk_tight_breakout_style",
            {
                "max_from_high": 0.01,
                "min_excess": 0.035,
                "trail_pct": 0.025,
                "trail_tight_after": 0.03,
                "trail_tight_pct": 0.015,
            },
            "Breakout-continuation: only near highs, higher excess, tighter trail.",
        ),
    ]:
        res = simulate(
            candles, live_cfg(**overrides), start_ms=start_ms, end_ms=end_ms, alphai=None
        )
        s = res.summary()
        pnl = float(s.get("total_eur") or 0.0)
        row = {
            "label": name,
            "family": "desk_variant",
            "source": "literature-inspired DeskConfig levers",
            "why": why,
            "summary": s,
            "total_eur": round(pnl, 2),
            "return_pct": round(100.0 * pnl / BOOK, 3),
            "max_drawdown_eur": s.get("max_drawdown_eur"),
            "trades": s.get("trades"),
            "win_rate": s.get("win_rate"),
            "beats_baseline": pnl > baseline_pnl + 1.0,
            "beats_baseline_pnl": pnl > baseline_pnl + 1.0,
            "pnl_delta_eur": round(pnl - baseline_pnl, 2),
        }
        desk_variants.append(row)
        flag = "BEAT" if row["beats_baseline_pnl"] else "lose"
        print(
            f"[{flag}] desk:{name:30} €{pnl:+8.2f} Δ€{row['pnl_delta_eur']:+8.2f} "
            f"WR={s.get('win_rate')} n={s.get('trades')}"
        )

    desk_beaters = sorted(
        [r for r in desk_variants if r["beats_baseline_pnl"]], key=lambda r: -r["total_eur"]
    )

    out = {
        "asof": end.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "weeks": WEEKS},
        "book_eur": BOOK,
        "fee_rt": FEE_RT,
        "fee_one_way_portfolio": FEE_ONE_WAY,
        "caveats": [
            "12 weeks is a short sample; rankings are regime-specific.",
            "Portfolio arms use share-based UTC daily closes from Bitvavo 15m candles.",
            "Desk baseline is bar-close fill replay (same engine as prior A/Bs); AlphaI off.",
            "Fees: desk fee_rt on trades; portfolios pay fee_rt/2 on traded notional.",
            "No leverage, no shorts (Bitvavo spot long-only).",
            "Many high-PnL rotators had 2–4× worse drawdowns than the desk — see quality_beaters.",
            "Sources are public strategy families, not guarantees of future edge.",
        ],
        "baseline": baseline,
        "portfolio_all": losers,
        "portfolio_beaters_pnl": beaters,
        "portfolio_quality_beaters": quality,
        "portfolio_calmar_beaters": calmar_beaters,
        "desk_variants_all": desk_variants,
        "desk_beaters": desk_beaters,
        "references": [
            {
                "name": "Cross-Sectional Crypto Momentum with a BTC Regime Filter",
                "url": "https://www.pyquantlab.com/article.php?file=Cross-Sectional+Crypto+Momentum+with+a+BTC+Regime+Filter.html",
            },
            {
                "name": "Dual Momentum (Antacci)",
                "url": "https://www.optimalmomentum.com/dual-relative-absolute-momentum/",
            },
            {
                "name": "BTC EMA regime → alt basket",
                "url": "https://pyquantlab.medium.com/altcoin-basket-strategy-filtered-by-bitcoin-ema-regimes-b85b4f018037",
            },
            {
                "name": "Breakout / trend / MR ranked (Coinquant)",
                "url": "https://www.coinquant.ai/blog/best-crypto-trading-strategy-in-2026-backtested-and-ranked",
            },
            {
                "name": "Cross-sectional momentum how-to (FXEmpire)",
                "url": "https://www.fxempire.com/education/article/cross-sectional-momentum-in-crypto-how-to-trade-the-strongest-trends-1535830",
            },
        ],
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT}")
    print(f"portfolio PnL beaters: {len(beaters)} / {len(results)}")
    for b in beaters:
        print(
            f"  ✓ {b['label']}: €{b['total_eur']} (Δ€{b['pnl_delta_eur']}) "
            f"dd€{b['max_drawdown_eur']} calmar={b.get('calmar_like')} — {b['source']}"
        )
    print(f"quality beaters (PnL↑ & DD≤1.35× baseline): {len(quality)}")
    for b in quality:
        print(f"  ★ {b['label']}: €{b['total_eur']} dd€{b['max_drawdown_eur']}")
    print(f"desk beaters: {len(desk_beaters)} / {len(desk_variants)}")
    for b in desk_beaters:
        print(f"  ✓ {b['label']}: €{b['total_eur']} (Δ€{b['pnl_delta_eur']})")


if __name__ == "__main__":
    main()
