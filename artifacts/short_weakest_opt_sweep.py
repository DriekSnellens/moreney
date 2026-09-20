#!/usr/bin/env python3
"""Sweep short-weakest defenses to maximize Calmar (PnL / |max DD|) on bear window.

Research-only. Feeds default knobs for the paper short-weakest sleeve.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from artifacts.bear_market_strategy_sim import (
    BEAR_END,
    BEAR_START,
    BOOK_EUR,
    FEE_RT,
    UNIVERSE,
    Series,
    _align,
    _max_drawdown,
    _slice_idx,
    _sma,
    load_daily,
)

OUT = Path(__file__).resolve().parent / "short_weakest_opt_sweep.json"


@dataclass(frozen=True)
class Knobs:
    lookback: int = 15
    top_n: int = 3
    rebalance_every: int = 10
    mom_floor: float = -0.05
    trail_pct: float = 0.0  # 0 = off; else trail from peak short return
    max_weight: float = 1.0  # per-name cap as fraction of book
    deploy_frac: float = 1.0  # fraction of book deployed when active
    weight_mode: str = "equal"  # equal | magnitude
    vol_spike_exit: bool = False  # exit if day ret > +3*ATR proxy (green reclaim)


def simulate(closes: dict[str, list[float]], btc: list[float], sma: list[float | None],
             i0: int, i1: int, k: Knobs) -> dict:
    equity = BOOK_EUR
    cash_reserve = BOOK_EUR * (1.0 - k.deploy_frac)
    trading_book = BOOK_EUR * k.deploy_frac
    # shorts: base -> (notional, entry, peak_ret)
    shorts: dict[str, tuple[float, float, float]] = {}
    eq: list[float] = []
    trades = wins = 0
    last_reb = -10**9
    # ATR proxy: 14d mean abs daily return
    atr: dict[str, float] = {b: 0.02 for b in UNIVERSE}

    def update_atr(i: int) -> None:
        if i < 15:
            return
        for b in UNIVERSE:
            rets = [
                abs(closes[b][j] / closes[b][j - 1] - 1.0)
                for j in range(i - 13, i + 1)
            ]
            atr[b] = sum(rets) / len(rets)

    def mtm(i: int) -> float:
        pnl = 0.0
        for b, (notional, entry, _) in shorts.items():
            pnl += notional * (entry - closes[b][i]) / entry
        return cash_reserve + (trading_book - sum(n for n, _, _ in shorts.values())
                               ) + sum(n for n, _, _ in shorts.values()) + pnl
        # simpler: equity tracks closed PnL; open MTM separately
        # rewrite below

    # Simpler accounting: equity = cash + MTM shorts
    cash = BOOK_EUR
    shorts = {}

    def equity_now(i: int) -> float:
        pnl = 0.0
        for b, (notional, entry, _) in shorts.items():
            pnl += notional * (entry - closes[b][i]) / entry
        return cash + pnl

    for i in range(i0, i1 + 1):
        update_atr(i)
        # trail / vol-spike exits intraday-of-bar
        for b in list(shorts):
            notional, entry, peak = shorts[b]
            ret = (entry - closes[b][i]) / entry  # short return
            peak = max(peak, ret)
            shorts[b] = (notional, entry, peak)
            exit_reason = None
            if k.trail_pct > 0 and peak - ret >= k.trail_pct:
                exit_reason = "trail"
            if k.vol_spike_exit and i > 0:
                day_ret = closes[b][i] / closes[b][i - 1] - 1.0
                if day_ret >= 3.0 * max(atr[b], 0.01):
                    exit_reason = "vol_spike"
            if exit_reason:
                pnl = notional * ret - notional * FEE_RT
                cash += pnl
                trades += 1
                wins += int(pnl > 0)
                del shorts[b]

        if i - last_reb >= k.rebalance_every and i >= k.lookback:
            # close all for rebalance
            for b, (notional, entry, _) in list(shorts.items()):
                ret = (entry - closes[b][i]) / entry
                pnl = notional * ret - notional * FEE_RT
                cash += pnl
                trades += 1
                wins += int(pnl > 0)
                del shorts[b]
            sma_v = sma[i]
            bear_ok = sma_v is not None and btc[i] < sma_v
            if bear_ok and cash > 1.0:
                scores = []
                for b in UNIVERSE:
                    mom = closes[b][i] / closes[b][i - k.lookback] - 1.0
                    scores.append((mom, b))
                scores.sort()
                picks = [(m, b) for m, b in scores[: k.top_n] if m <= k.mom_floor]
                if picks:
                    deploy = cash * k.deploy_frac
                    if k.weight_mode == "magnitude":
                        mag = sum(abs(m) for m, _ in picks)
                        weights = [abs(m) / mag for m, _ in picks]
                    else:
                        weights = [1.0 / len(picks)] * len(picks)
                    for w, (m, b) in zip(weights, picks):
                        w = min(w, k.max_weight)
                        notional = deploy * w
                        if notional < 50:
                            continue
                        cash -= notional * (FEE_RT / 2)
                        shorts[b] = (notional * (1.0 - FEE_RT / 2), closes[b][i], 0.0)
                        trades += 1
            last_reb = i
        eq.append(equity_now(i))

    # flatten
    if shorts:
        for b, (notional, entry, _) in list(shorts.items()):
            ret = (entry - closes[b][i1]) / entry
            cash += notional * ret - notional * (FEE_RT / 2)
            trades += 1
            wins += int(ret > 0)
        shorts.clear()
        eq[-1] = cash

    pnl = eq[-1] - BOOK_EUR
    mdd = _max_drawdown(eq)
    calmar = (pnl / abs(mdd)) if abs(mdd) > 1 else (999.0 if pnl > 0 else 0.0)
    return {
        "pnl_eur": round(pnl, 2),
        "return_pct": round(100 * pnl / BOOK_EUR, 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(100 * mdd / BOOK_EUR, 2),
        "calmar": round(calmar, 3),
        "trades": trades,
        "win_rate": round(wins / trades, 3) if trades else None,
        "knobs": asdict(k),
    }


def main() -> None:
    series = load_daily(("BTC", *UNIVERSE), days=430)
    ts, closes = _align(series, ("BTC", *UNIVERSE))
    btc = closes["BTC"]
    sma = _sma(btc, 200)
    i0, i1 = _slice_idx(ts, BEAR_START, BEAR_END)

    grid = list(
        itertools.product(
            [10, 15, 20],          # lookback
            [3, 5, 8],            # top_n
            [7, 10, 14],          # rebalance
            [-0.03, -0.05, -0.08],  # mom_floor
            [0.0, 0.12, 0.18],    # trail
            [0.10, 0.20, 1.0],    # max_weight
            [0.5, 0.75, 1.0],     # deploy_frac
            ["equal", "magnitude"],
            [False, True],        # vol_spike
        )
    )
    # Too many — subsample smartly: fix some dims
    grid = list(
        itertools.product(
            [15, 20],
            [3, 5],
            [10, 14],
            [-0.05, -0.08],
            [0.12, 0.18, 0.0],
            [0.10, 0.15, 1.0],
            [0.5, 0.75, 1.0],
            ["equal", "magnitude"],
            [True, False],
        )
    )
    print(f"sweep {len(grid)} configs…", flush=True)
    results = []
    for lb, tn, reb, mf, tr, mw, df, wm, vs in grid:
        k = Knobs(lb, tn, reb, mf, tr, mw, df, wm, vs)
        r = simulate(closes, btc, sma, i0, i1, k)
        results.append(r)

    # Prefer positive PnL, then Calmar, then lower DD
    results.sort(key=lambda r: (r["pnl_eur"] > 0, r["calmar"], -abs(r["max_dd_pct"])), reverse=True)
    top = results[:25]
    # Also best by min DD among PnL > 2000
    profitable = [r for r in results if r["pnl_eur"] >= 2000]
    by_dd = sorted(profitable, key=lambda r: (abs(r["max_dd_pct"]), -r["pnl_eur"]))[:10]
    by_calmar = sorted(profitable, key=lambda r: r["calmar"], reverse=True)[:10]

    # Baseline (sim v1)
    baseline = simulate(
        closes, btc, sma, i0, i1,
        Knobs(15, 3, 10, -0.05, 0.0, 1.0, 1.0, "equal", False),
    )

    winner = by_calmar[0] if by_calmar else top[0]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": [BEAR_START, BEAR_END],
        "book_eur": BOOK_EUR,
        "n_configs": len(grid),
        "baseline": baseline,
        "winner_calmar": winner,
        "top_calmar": by_calmar,
        "top_low_dd_among_pnl_ge_2k": by_dd,
        "top_overall": top,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print("baseline", baseline)
    print("winner", winner)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
