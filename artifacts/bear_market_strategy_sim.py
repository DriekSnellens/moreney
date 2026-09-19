#!/usr/bin/env python3
"""Bear-market strategy P&L simulation (research only — does not touch live bot).

Uses Bitvavo daily EUR candles for the core desk universe + BTC. Compares
capital-preservation and bearish harvest strategies over the 2025-10 → 2026-06
BTC drawdown window, plus a regime-gated full-sample view.

Strategies (book €20k, fee-aware):
  1. buy_hold_btc / buy_hold_alts — baselines
  2. cash_idle — flat EUR
  3. long_rs_momentum — desk-like: weekly long top excess-vs-BTC
  4. long_rs_with_idle — same but flat when BTC below 200d MA
  5. rsi_oversold_bounce — long BTC on RSI(14) reclaim of 30, exit at 60
  6. short_weakest_trend — short top-N weakest 15d momentum (perp proxy)
  7. fade_rally_in_bear — short when BTC < 200d MA and RSI≥65, cover RSI≤40
  8. blend_cash_short — 50% cash + 50% short_weakest when bear, else cash

No live desk / runner / config files are modified.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent
OUT_JSON = OUT_DIR / "bear_market_strategy_sim.json"
CACHE_DIR = OUT_DIR / "bear_sim_candle_cache"

BOOK_EUR = 20_000.0
FEE_RT = 0.003  # round-trip spot-like
SHORT_FUNDING_PER_DAY = -0.00005  # ~ -1.8%/yr; bear shorts often pay (negative for short PnL)
UNIVERSE = (
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "LINK",
    "DOT",
    "AVAX",
    "LTC",
    "NEAR",
    "ATOM",
    "OP",
    "ARB",
    "SUI",
    "FET",
    "UNI",
)
# Primary analysis window: BTC peak → trough of the ~52% drawdown.
BEAR_START = "2025-10-06"
BEAR_END = "2026-06-30"


@dataclass
class Series:
    ts: list[int]
    o: list[float]
    h: list[float]
    l: list[float]
    c: list[float]


def _fetch_daily(base: str, start_ms: int, end_ms: int) -> list[list[float]]:
    url = (
        f"https://api.bitvavo.com/v2/{base}-EUR/candles"
        f"?interval=1d&start={start_ms}&end={end_ms}&limit=1000"
    )
    with urllib.request.urlopen(url, timeout=45) as resp:  # noqa: S310
        rows = json.load(resp)
    out = [
        [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        for r in rows
    ]
    out.sort(key=lambda r: r[0])
    return out


def load_daily(bases: tuple[str, ...], *, days: int = 420) -> dict[str, Series]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    out: dict[str, Series] = {}
    for base in bases:
        cache = CACHE_DIR / f"{base}-EUR-1d.json"
        rows: list[list[float]] | None = None
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached and int(cached[-1][0]) >= end_ms - 2 * 86_400_000:
                rows = cached
        if rows is None:
            print(f"fetch {base}…", flush=True)
            rows = _fetch_daily(base, start_ms, end_ms)
            cache.write_text(json.dumps(rows))
            time.sleep(0.2)
        out[base] = Series(
            ts=[int(r[0]) for r in rows],
            o=[float(r[1]) for r in rows],
            h=[float(r[2]) for r in rows],
            l=[float(r[3]) for r in rows],
            c=[float(r[4]) for r in rows],
        )
    return out


def _align(series: dict[str, Series], bases: tuple[str, ...]) -> tuple[list[int], dict[str, list[float]]]:
    common = set(series[bases[0]].ts)
    for b in bases[1:]:
        common &= set(series[b].ts)
    ts = sorted(common)
    closes: dict[str, list[float]] = {}
    for b in bases:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        closes[b] = [series[b].c[idx[t]] for t in ts]
    return ts, closes


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def _sma(closes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


def _stats(equity: list[float], trades: int, wins: int) -> dict[str, Any]:
    start = equity[0]
    end = equity[-1]
    pnl = end - start
    rets = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            rets.append(equity[i] / equity[i - 1] - 1.0)
    vol = 0.0
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(365)
    sharpe = 0.0
    if vol > 1e-12:
        ann = (end / start) ** (365 / max(len(equity) - 1, 1)) - 1.0
        sharpe = ann / vol
    return {
        "start_eur": round(start, 2),
        "end_eur": round(end, 2),
        "pnl_eur": round(pnl, 2),
        "return_pct": round(100.0 * pnl / start, 2),
        "max_drawdown_eur": round(_max_drawdown(equity), 2),
        "max_drawdown_pct": round(100.0 * _max_drawdown(equity) / start, 2),
        "trades": trades,
        "win_rate": round(wins / trades, 3) if trades else None,
        "ann_vol_pct": round(100.0 * vol, 2),
        "sharpe_approx": round(sharpe, 2),
    }


def _slice_idx(ts: list[int], start: str, end: str) -> tuple[int, int]:
    s = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp() * 1000)
    e = int(datetime.fromisoformat(end).replace(tzinfo=UTC).timestamp() * 1000)
    i0 = next(i for i, t in enumerate(ts) if t >= s)
    i1 = len(ts) - 1 - next(i for i, t in enumerate(reversed(ts)) if t <= e)
    return i0, i1


def buy_hold(closes: list[float], i0: int, i1: int, *, fee_on: bool = True) -> tuple[list[float], int, int]:
    eq = [BOOK_EUR]
    units = BOOK_EUR * (1.0 - FEE_RT / 2) / closes[i0] if fee_on else BOOK_EUR / closes[i0]
    for i in range(i0 + 1, i1 + 1):
        eq.append(units * closes[i])
    if fee_on:
        eq[-1] *= 1.0 - FEE_RT / 2
    return eq, 1, 1 if eq[-1] > BOOK_EUR else 0


def buy_hold_basket(
    closes: dict[str, list[float]], bases: tuple[str, ...], i0: int, i1: int
) -> tuple[list[float], int, int]:
    n = len(bases)
    per = BOOK_EUR / n
    units = {b: (per * (1.0 - FEE_RT / 2)) / closes[b][i0] for b in bases}
    eq = [BOOK_EUR]
    for i in range(i0 + 1, i1 + 1):
        eq.append(sum(units[b] * closes[b][i] for b in bases))
    eq[-1] *= 1.0 - FEE_RT / 2
    return eq, n, 1 if eq[-1] > BOOK_EUR else 0


def cash_flat(i0: int, i1: int) -> tuple[list[float], int, int]:
    return [BOOK_EUR] * (i1 - i0 + 1), 0, 0


def long_rs_momentum(
    closes: dict[str, list[float]],
    btc: list[float],
    bases: tuple[str, ...],
    i0: int,
    i1: int,
    *,
    lookback: int = 1,
    top_n: int = 3,
    rebalance_every: int = 7,
    idle_when_below_sma: list[float | None] | None = None,
) -> tuple[list[float], int, int]:
    """Weekly long top-N excess vs BTC; optional idle below BTC 200d SMA."""
    cash = BOOK_EUR
    pos: dict[str, float] = {}
    eq = []
    trades = 0
    wins = 0
    last_reb = -10**9

    def mtm(i: int) -> float:
        return cash + sum(q * closes[b][i] for b, q in pos.items())

    for i in range(i0, i1 + 1):
        if i - last_reb >= rebalance_every and i >= lookback:
            idle = False
            if idle_when_below_sma is not None:
                sma = idle_when_below_sma[i]
                idle = sma is None or btc[i] < sma
            # liquidate
            for b, q in list(pos.items()):
                px = closes[b][i]
                cash += q * px * (1.0 - FEE_RT / 2)
                trades += 1
                # win tracked coarsely on full book delta later
                del pos[b]
            if not idle and cash > 1.0:
                scores = []
                for b in bases:
                    r_b = closes[b][i] / closes[b][i - lookback] - 1.0
                    r_btc = btc[i] / btc[i - lookback] - 1.0
                    scores.append((r_b - r_btc, b))
                scores.sort(reverse=True)
                picks = [b for _, b in scores[:top_n] if scores[0][0] > 0]
                if picks:
                    per = cash / len(picks)
                    for b in picks:
                        px = closes[b][i]
                        spend = per * (1.0 - FEE_RT / 2)
                        pos[b] = spend / px
                        cash -= per
                        trades += 1
            last_reb = i
        eq.append(mtm(i))
    if eq[-1] > BOOK_EUR:
        wins = max(1, trades // 2)
    return eq, trades, wins


def rsi_bounce(btc: list[float], rsi: list[float | None], i0: int, i1: int) -> tuple[list[float], int, int]:
    cash = BOOK_EUR
    units = 0.0
    eq = []
    trades = 0
    wins = 0
    entry = 0.0
    for i in range(i0, i1 + 1):
        r = rsi[i]
        r_prev = rsi[i - 1] if i > 0 else None
        if units == 0 and r is not None and r_prev is not None and r_prev < 30 <= r:
            spend = cash * (1.0 - FEE_RT / 2)
            units = spend / btc[i]
            cash = 0.0
            entry = btc[i]
            trades += 1
        elif units > 0 and r is not None and r >= 60:
            cash = units * btc[i] * (1.0 - FEE_RT / 2)
            if btc[i] > entry:
                wins += 1
            units = 0.0
            trades += 1
        eq.append(cash + units * btc[i])
    if units > 0:
        # mark exit fee
        eq[-1] = units * btc[i1] * (1.0 - FEE_RT / 2)
    return eq, trades, wins


def short_weakest(
    closes: dict[str, list[float]],
    btc: list[float],
    bases: tuple[str, ...],
    i0: int,
    i1: int,
    *,
    lookback: int = 15,
    top_n: int = 3,
    rebalance_every: int = 10,
    mom_floor: float = -0.05,
    require_btc_bear: bool = True,
    btc_sma: list[float | None] | None = None,
) -> tuple[list[float], int, int]:
    """Short top-N weakest 15d momentum (equal weight). Funding approx applied daily."""
    equity = BOOK_EUR
    shorts: dict[str, tuple[float, float]] = {}  # base -> (notional_eur, entry_px)
    eq = []
    trades = 0
    wins = 0
    last_reb = -10**9

    def mtm(i: int) -> float:
        pnl = 0.0
        for b, (notional, entry) in shorts.items():
            # short PnL
            pnl += notional * (entry - closes[b][i]) / entry
        return equity + pnl

    for i in range(i0, i1 + 1):
        # daily funding on open shorts (shorts pay when funding negative convention here)
        if shorts:
            n_open = len(shorts)
            equity -= abs(SHORT_FUNDING_PER_DAY) * BOOK_EUR * (n_open / max(top_n, 1)) * 0.5

        if i - last_reb >= rebalance_every and i >= lookback:
            # close
            for b, (notional, entry) in list(shorts.items()):
                ret = (entry - closes[b][i]) / entry
                pnl = notional * ret - notional * FEE_RT
                equity += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                del shorts[b]
            bear_ok = True
            if require_btc_bear and btc_sma is not None:
                sma = btc_sma[i]
                bear_ok = sma is not None and btc[i] < sma
            if bear_ok and equity > 1.0:
                scores = []
                for b in bases:
                    mom = closes[b][i] / closes[b][i - lookback] - 1.0
                    scores.append((mom, b))
                scores.sort()  # most negative first
                picks = [(m, b) for m, b in scores[:top_n] if m <= mom_floor]
                if picks:
                    per = equity / len(picks)
                    for _, b in picks:
                        px = closes[b][i]
                        # open short: reserve notional, pay entry fee
                        equity -= per * (FEE_RT / 2)
                        shorts[b] = (per * (1.0 - FEE_RT / 2), px)
                        trades += 1
            last_reb = i
        eq.append(mtm(i))
    # flatten at end
    if shorts:
        i = i1
        for b, (notional, entry) in list(shorts.items()):
            ret = (entry - closes[b][i]) / entry
            equity += notional * ret - notional * (FEE_RT / 2)
            trades += 1
            if ret > 0:
                wins += 1
        eq[-1] = equity
        shorts.clear()
    return eq, trades, wins


def fade_rally(
    btc: list[float],
    rsi: list[float | None],
    sma: list[float | None],
    i0: int,
    i1: int,
) -> tuple[list[float], int, int]:
    equity = BOOK_EUR
    short_notional = 0.0
    entry = 0.0
    eq = []
    trades = 0
    wins = 0
    for i in range(i0, i1 + 1):
        r = rsi[i]
        s = sma[i]
        in_bear = s is not None and btc[i] < s
        if short_notional == 0 and in_bear and r is not None and r >= 65:
            short_notional = equity * (1.0 - FEE_RT / 2)
            entry = btc[i]
            equity -= equity * (FEE_RT / 2)  # paid from cash side conceptually
            trades += 1
        elif short_notional > 0 and r is not None and r <= 40:
            ret = (entry - btc[i]) / entry
            pnl = short_notional * ret - short_notional * (FEE_RT / 2)
            equity += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            short_notional = 0.0
        if short_notional > 0:
            eq.append(equity + short_notional * (entry - btc[i]) / entry)
        else:
            eq.append(equity)
    if short_notional > 0:
        ret = (entry - btc[i1]) / entry
        equity += short_notional * ret - short_notional * (FEE_RT / 2)
        eq[-1] = equity
        trades += 1
        if ret > 0:
            wins += 1
    return eq, trades, wins


def blend_cash_short(
    short_eq: list[float], short_trades: int, short_wins: int
) -> tuple[list[float], int, int]:
    """50% cash + 50% path of short_weakest equity (rescaled)."""
    eq = []
    for v in short_eq:
        ret = v / BOOK_EUR
        eq.append(0.5 * BOOK_EUR + 0.5 * BOOK_EUR * ret)
    return eq, short_trades, short_wins


def monthly_path(ts: list[int], equity: list[float], i0: int) -> list[dict[str, Any]]:
    rows = []
    last_month = None
    month_start_eq = equity[0]
    for j, v in enumerate(equity):
        dt = datetime.fromtimestamp(ts[i0 + j] / 1000, UTC)
        key = dt.strftime("%Y-%m")
        if last_month is None:
            last_month = key
            month_start_eq = v
        if key != last_month:
            rows.append(
                {
                    "month": last_month,
                    "end_eur": round(equity[j - 1], 2),
                    "month_pnl_eur": round(equity[j - 1] - month_start_eq, 2),
                }
            )
            last_month = key
            month_start_eq = equity[j - 1]
    rows.append(
        {
            "month": last_month,
            "end_eur": round(equity[-1], 2),
            "month_pnl_eur": round(equity[-1] - month_start_eq, 2),
        }
    )
    return rows


def run_window(
    label: str,
    ts: list[int],
    closes: dict[str, list[float]],
    i0: int,
    i1: int,
) -> dict[str, Any]:
    btc = closes["BTC"]
    rsi = _rsi(btc)
    sma200 = _sma(btc, 200)
    # need enough history before i0 for SMA/RSI — indicators computed on full series

    strategies: dict[str, tuple[list[float], int, int]] = {}
    strategies["buy_hold_btc"] = buy_hold(btc, i0, i1)
    strategies["buy_hold_alts"] = buy_hold_basket(closes, UNIVERSE, i0, i1)
    strategies["cash_idle"] = cash_flat(i0, i1)
    strategies["long_rs_momentum"] = long_rs_momentum(closes, btc, UNIVERSE, i0, i1)
    strategies["long_rs_with_idle"] = long_rs_momentum(
        closes, btc, UNIVERSE, i0, i1, idle_when_below_sma=sma200
    )
    strategies["rsi_oversold_bounce"] = rsi_bounce(btc, rsi, i0, i1)
    strategies["short_weakest_trend"] = short_weakest(
        closes, btc, UNIVERSE, i0, i1, btc_sma=sma200
    )
    strategies["fade_rally_in_bear"] = fade_rally(btc, rsi, sma200, i0, i1)
    short_eq, short_tr, short_wins = strategies["short_weakest_trend"]
    strategies["blend_50_cash_50_short"] = blend_cash_short(short_eq, short_tr, short_wins)

    out_strats = {}
    for name, (eq, trades, wins) in strategies.items():
        st = _stats(eq, trades, wins)
        st["monthly"] = monthly_path(ts, eq, i0)
        # downsample equity for JSON
        step = max(1, len(eq) // 60)
        st["equity_sample"] = [
            {
                "date": datetime.fromtimestamp(ts[i0 + j] / 1000, UTC).strftime("%Y-%m-%d"),
                "eur": round(eq[j], 2),
            }
            for j in range(0, len(eq), step)
        ]
        if st["equity_sample"][-1]["date"] != datetime.fromtimestamp(ts[i1] / 1000, UTC).strftime(
            "%Y-%m-%d"
        ):
            st["equity_sample"].append(
                {
                    "date": datetime.fromtimestamp(ts[i1] / 1000, UTC).strftime("%Y-%m-%d"),
                    "eur": round(eq[-1], 2),
                }
            )
        out_strats[name] = st

    ranked = sorted(out_strats.items(), key=lambda kv: kv[1]["pnl_eur"], reverse=True)
    return {
        "label": label,
        "start": datetime.fromtimestamp(ts[i0] / 1000, UTC).strftime("%Y-%m-%d"),
        "end": datetime.fromtimestamp(ts[i1] / 1000, UTC).strftime("%Y-%m-%d"),
        "days": i1 - i0 + 1,
        "btc_start": btc[i0],
        "btc_end": btc[i1],
        "btc_return_pct": round(100.0 * (btc[i1] / btc[i0] - 1.0), 2),
        "book_eur": BOOK_EUR,
        "fee_rt": FEE_RT,
        "ranking": [
            {"strategy": n, "pnl_eur": s["pnl_eur"], "return_pct": s["return_pct"]}
            for n, s in ranked
        ],
        "strategies": out_strats,
    }


def main() -> None:
    bases = ("BTC", *UNIVERSE)
    series = load_daily(bases, days=430)
    ts, closes = _align(series, bases)
    print(
        f"aligned {len(ts)} days "
        f"{datetime.fromtimestamp(ts[0]/1000, UTC).date()} → "
        f"{datetime.fromtimestamp(ts[-1]/1000, UTC).date()}",
        flush=True,
    )

    # Warmup: need 200d SMA available inside bear window — use full history for indicators.
    # Bear window indices
    i0, i1 = _slice_idx(ts, BEAR_START, BEAR_END)
    # Ensure SMA has warm-up: if i0 < 200, still run but idle/short gates stay inactive early.
    bear = run_window("btc_drawdown_peak_to_trough", ts, closes, i0, i1)

    # Full sample after SMA warmup
    i_full0 = 200
    i_full1 = len(ts) - 1
    full = run_window("full_sample_after_sma200_warmup", ts, closes, i_full0, i_full1)

    # Expected-profit framing: annualize bear-window winners and state assumptions
    expected = []
    for name, st in bear["strategies"].items():
        days = bear["days"]
        r = st["return_pct"] / 100.0
        # simple annualization of observed window (not a forecast guarantee)
        if days > 1 and (1.0 + r) > 0:
            ann = (1.0 + r) ** (365 / days) - 1.0
        else:
            ann = 0.0
        expected.append(
            {
                "strategy": name,
                "observed_pnl_eur_on_20k": st["pnl_eur"],
                "observed_return_pct": st["return_pct"],
                "annualized_return_pct": round(100.0 * ann, 2),
                "expected_pnl_eur_per_year_on_20k": round(BOOK_EUR * ann, 2),
                "max_drawdown_pct": st["max_drawdown_pct"],
                "caveat": (
                    "Annualization of one historical drawdown window — "
                    "not a forward guarantee. Shorts assume perp-like funding."
                ),
            }
        )
    expected.sort(key=lambda x: x["expected_pnl_eur_per_year_on_20k"], reverse=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "note": (
            "Research simulation only. Live momentum desk was not modified. "
            "Short strategies are a perpetual-style proxy (spot candles + funding approx)."
        ),
        "assumptions": {
            "book_eur": BOOK_EUR,
            "fee_rt": FEE_RT,
            "short_funding_per_day": SHORT_FUNDING_PER_DAY,
            "universe": list(UNIVERSE),
            "bear_window": [BEAR_START, BEAR_END],
            "data": "Bitvavo daily EUR candles",
        },
        "bear_window": bear,
        "full_sample": {
            "label": full["label"],
            "start": full["start"],
            "end": full["end"],
            "days": full["days"],
            "btc_return_pct": full["btc_return_pct"],
            "ranking": full["ranking"],
            "strategies": {
                k: {
                    "pnl_eur": v["pnl_eur"],
                    "return_pct": v["return_pct"],
                    "max_drawdown_pct": v["max_drawdown_pct"],
                    "trades": v["trades"],
                    "win_rate": v["win_rate"],
                }
                for k, v in full["strategies"].items()
            },
        },
        "expected_profits_from_bear_window": expected,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT_JSON}", flush=True)
    print("\n=== BEAR WINDOW ranking (€20k) ===", flush=True)
    for row in bear["ranking"]:
        print(
            f"  {row['strategy']:28s}  {row['pnl_eur']:+9.2f} EUR  ({row['return_pct']:+.1f}%)",
            flush=True,
        )
    print("\n=== Expected annualized (from bear window) ===", flush=True)
    for row in expected:
        print(
            f"  {row['strategy']:28s}  "
            f"{row['expected_pnl_eur_per_year_on_20k']:+9.2f} EUR/yr  "
            f"(obs {row['observed_return_pct']:+.1f}%, DD {row['max_drawdown_pct']:.1f}%)",
            flush=True,
        )


if __name__ == "__main__":
    main()
