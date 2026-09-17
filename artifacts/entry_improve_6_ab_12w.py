#!/usr/bin/env python3
"""12w A/B of six entry-improvement ideas vs live baseline. Research only."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bot.research.momentum_backtest.engine as eng
from bot.live.momentum_desk import (
    BAR_MS,
    BARS_PER_DAY,
    AlphaIView,
    Candidate,
    Candle,
    DeskConfig,
    Position,
    RiskLedger,
    bar_stats,
    classify_regime,
    rank_candidates as orig_rank,
    restrict_by_volume,
    select_entries,
    universe_stats,
)
from bot.research.momentum_backtest.engine import BacktestResult, DecisionLog, load_candles

OUT = Path("artifacts/entry_improve_6_ab_12w.json")
END = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
END_MS = int(END.timestamp() * 1000)
START = END - timedelta(weeks=12)
START_MS = int(START.timestamp() * 1000)

_MIN_CLIP_EUR = 5.0
_MIN_CLIP_FRACTION = 0.5

# Mutable mode for patched _try_entries
_MODE = {"name": "baseline"}


def base_cfg(**extra: Any) -> DeskConfig:
    knobs: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        clip_eur=20_000.0,
        max_positions=1,
        book_eur=20_000.0,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        early_stop_pct=0.02,
        early_stop_until_peak=0.015,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        fade_eta_sec=0.0,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        skip_weekend_entries=True,
        refill_on_exit=True,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        max_chase_ret_24h=0.0,
        max_from_high=0.02,
        min_volume_eur=1_000_000.0,
    )
    knobs.update(extra)
    return DeskConfig().with_overrides(**knobs)


def _ret_lookback(rows: Sequence[Candle], t_ms: int, bars: int) -> float | None:
    closed = [c for c in rows if int(c[0]) + BAR_MS <= t_ms]
    if len(closed) < bars + 1:
        return None
    last = float(closed[-1][4])
    ref = float(closed[-(bars + 1)][4])
    if last <= 0 or ref <= 0:
        return None
    return last / ref - 1.0


def _adx_wilder(
    ohlc: Sequence[tuple[float, float, float, float]], period: int = 14
) -> tuple[float, float] | None:
    n = len(ohlc)
    if n < period * 3 + 2:
        return None
    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        _o, h, l, _c = ohlc[i]
        _po, ph, pl, pc = ohlc[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        down = pl - l
        trs.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    def wilder_smooth(vals: list[float], p: int) -> list[float]:
        out: list[float] = []
        s = sum(vals[:p])
        out.append(s)
        for v in vals[p:]:
            s = s - (s / p) + v
            out.append(s)
        return out

    if len(trs) < period * 2:
        return None
    atr = wilder_smooth(trs, period)
    pdm = wilder_smooth(plus_dm, period)
    mdm = wilder_smooth(minus_dm, period)
    dx: list[float] = []
    for a, p, m in zip(atr, pdm, mdm, strict=False):
        if a <= 0:
            dx.append(0.0)
            continue
        plus_di = 100.0 * p / a
        minus_di = 100.0 * m / a
        s = plus_di + minus_di
        dx.append(0.0 if s <= 0 else 100.0 * abs(plus_di - minus_di) / s)
    if len(dx) < period + 1:
        return None
    adx_series: list[float] = []
    s = sum(dx[:period]) / period
    adx_series.append(s)
    for v in dx[period:]:
        s = (s * (period - 1) + v) / period
        adx_series.append(s)
    if len(adx_series) < 2:
        return None
    return adx_series[-1], adx_series[-2]


def _hourly_ohlc(
    rows: Sequence[Candle], t_ms: int, hours: int = 80
) -> list[tuple[float, float, float, float]]:
    closed = [c for c in rows if int(c[0]) + BAR_MS <= t_ms]
    if not closed:
        return []
    buckets: dict[int, list[Candle]] = {}
    for c in closed:
        hour_ts = (int(c[0]) // 3_600_000) * 3_600_000
        buckets.setdefault(hour_ts, []).append(c)
    keys = sorted(buckets)[-hours:]
    out: list[tuple[float, float, float, float]] = []
    for k in keys:
        bars = buckets[k]
        o = float(bars[0][1])
        h = max(float(b[2]) for b in bars)
        low = min(float(b[3]) for b in bars)
        cl = float(bars[-1][4])
        out.append((o, h, low, cl))
    return out


def _vol_surge_ok(rows: Sequence[Candle], t_ms: int, mult: float = 1.3) -> bool:
    closed = [c for c in rows if int(c[0]) + BAR_MS <= t_ms]
    day = BARS_PER_DAY
    if len(closed) < day * 8:
        return False

    def vol_eur(window: Sequence[Candle]) -> float:
        return sum(float(c[5]) * float(c[4]) for c in window)

    recent = vol_eur(closed[-day:])
    priors: list[float] = []
    for i in range(1, 8):
        end = -day * i
        start = end - day
        chunk = closed[start:end] if end != 0 else closed[start:]
        if len(chunk) < day // 2:
            return False
        priors.append(vol_eur(chunk))
    avg = sum(priors) / len(priors)
    return avg > 0 and recent >= mult * avg


def _filter_cands(
    cands: list[Candidate],
    *,
    mode: str,
    candles: Mapping[str, Sequence[Candle]],
    t_ms: int,
    cfg: DeskConfig,
) -> list[Candidate]:
    if mode in {"baseline", "weekend_on", "hours_us", "hours_us_ext"}:
        return list(cands)

    out: list[Candidate] = []
    for c in cands:
        rows = candles.get(c.base) or []
        btc_rows = candles.get("BTC") or []
        if mode == "adx_25_rising":
            hourly = _hourly_ohlc(rows, t_ms, hours=80)
            adx = _adx_wilder(hourly, period=14)
            if adx is None or adx[0] < 25.0 or adx[0] <= adx[1]:
                continue
        elif mode == "vol_surge_1_3":
            if not _vol_surge_ok(rows, t_ms, mult=1.3):
                continue
        elif mode == "pullback_1_5":
            fh = float(c.from_high)
            if fh > -0.01 or fh < -0.05:
                continue
        elif mode == "multi_horizon":
            r6 = _ret_lookback(rows, t_ms, 24)
            r24 = _ret_lookback(rows, t_ms, 96)
            r72 = _ret_lookback(rows, t_ms, 288)
            b6 = _ret_lookback(btc_rows, t_ms, 24)
            b24 = _ret_lookback(btc_rows, t_ms, 96)
            b72 = _ret_lookback(btc_rows, t_ms, 288)
            if None in (r6, r24, r72, b6, b24, b72):
                continue
            e6 = float(r6) - float(b6)
            e24 = float(r24) - float(b24)
            e72 = float(r72) - float(b72)
            if e6 < 0 or e24 < float(cfg.min_excess):
                continue
            score = 0.5 * e24 + 0.3 * e6 + 0.2 * e72
            if c.alphai_pick:
                score += float(cfg.alphai_rank_boost)
            c = Candidate(
                base=c.base,
                excess=e24,
                ret_24h=float(r24),
                from_high=c.from_high,
                volume_eur=c.volume_eur,
                score=score,
                alphai_pick=c.alphai_pick,
            )
        else:
            raise ValueError(mode)
        out.append(c)
    if mode == "multi_horizon":
        out.sort(key=lambda x: x.score, reverse=True)
    return out


_CANDLES: dict[str, Sequence[Candle]] = {}


def _try_entries_patched(
    res: BacktestResult,
    ledger: RiskLedger,
    positions: list[Position],
    candles_by_base: Mapping[str, Sequence[Candle]],
    cfg: DeskConfig,
    view: AlphaIView | None,
    t: int,
    *,
    log_decision: bool,
) -> None:
    mode = _MODE["name"]
    if len(positions) >= int(cfg.max_positions):
        return
    stats = restrict_by_volume(universe_stats(candles_by_base, t, cfg), cfg)
    btc_rows = candles_by_base.get("BTC")
    btc = bar_stats("BTC", btc_rows, t) if btc_rows else None
    regime = classify_regime(btc, stats, cfg, alphai=view)
    cands: list[Candidate] = []
    if regime.ok:
        rank_cfg = cfg
        if mode == "pullback_1_5":
            rank_cfg = cfg.with_overrides(max_from_high=0.05)
        cands = orig_rank(stats, regime.btc_ret or 0.0, rank_cfg, alphai=view)
        cands = _filter_cands(
            cands, mode=mode, candles=candles_by_base, t_ms=t, cfg=cfg
        )
    allowed, why = ledger.entries_allowed(t)
    entries = []
    if allowed:
        entries = select_entries(
            cands,
            regime,
            cfg,
            held_bases=[p.base for p in positions],
            blocked_bases=ledger.blocked_bases(t, cfg.max_entries_per_base_per_day),
            alphai=view,
            now_ms=t,
        )
    for e in entries:
        clip = e.clip_eur
        if cfg.book_eur > 0:
            free = cfg.book_eur - sum(p.notional_eur for p in positions)
            clip = min(clip, free)
            if clip < max(_MIN_CLIP_EUR, e.clip_eur * _MIN_CLIP_FRACTION):
                continue
        price = stats[e.base].price
        qty = clip / price
        positions.append(
            Position(
                base=e.base,
                entry_price=price,
                quantity=qty,
                notional_eur=clip,
                opened_ms=t,
                peak=price,
                entry_reason=",".join(e.reasons),
            )
        )
        ledger.note_entry(e.base, t)
    if log_decision:
        res.decisions.append(
            DecisionLog(
                t_ms=t,
                regime_ok=regime.ok,
                btc_ret=regime.btc_ret,
                breadth=round(regime.breadth, 3),
                reasons=regime.reasons,
                candidates=[c.base for c in cands[:6]],
                entries=[f"{e.base}@{e.clip_eur:.0f}" for e in entries],
                risk_block="" if allowed else why,
            )
        )


def summarize(label: str, res: BacktestResult, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    sm = res.summary()
    nets = [t.net_eur for t in res.closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    row: dict[str, Any] = {
        "label": label,
        "total_eur": sm["total_eur"],
        "realized_eur": sm["realized_eur"],
        "open_mtm_eur": sm["open_mtm_eur"],
        "max_dd_eur": sm["max_drawdown_eur"],
        "win_rate": sm["win_rate"],
        "trades": sm["trades"],
        "avg_net": sm["avg_net_per_trade_eur"],
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "calmar_proxy": round(sm["total_eur"] / abs(sm["max_drawdown_eur"]), 3)
        if sm["max_drawdown_eur"]
        else None,
        "decision_points": sm["decision_points"],
        "regime_on_points": sm["regime_on_points"],
        "by_reason": sm["by_reason"],
        "bases": sorted({t.base for t in res.closed}),
    }
    if baseline is not None:
        row["delta_pnl"] = round(row["total_eur"] - baseline["total_eur"], 2)
        row["delta_dd"] = round(row["max_dd_eur"] - baseline["max_dd_eur"], 2)
        row["beats_pnl"] = row["total_eur"] > baseline["total_eur"]
        row["beats_dd"] = row["max_dd_eur"] > baseline["max_dd_eur"]
        row["beats_both"] = bool(row["beats_pnl"] and row["beats_dd"])
    return row


def main() -> None:
    print("load candles", flush=True)
    candles = load_candles(("BTC", *DeskConfig().universe), days=120, end_ms=END_MS, refresh=False)
    print("bases", len(candles), flush=True)
    global _CANDLES
    _CANDLES = candles

    eng._try_entries = _try_entries_patched  # type: ignore[attr-defined]

    variants: list[tuple[str, str, dict[str, Any]]] = [
        ("baseline", "baseline", {}),
        ("1_weekend_on", "weekend_on", {"skip_weekend_entries": False}),
        ("2_hours_us_13_16", "hours_us", {"decision_hours_utc": (13, 16)}),
        ("2b_hours_us_13_16_19", "hours_us_ext", {"decision_hours_utc": (13, 16, 19)}),
        ("3_adx_25_rising", "adx_25_rising", {}),
        ("4_vol_surge_1_3", "vol_surge_1_3", {}),
        ("5_pullback_1_5pct", "pullback_1_5", {"max_from_high": 0.05}),
        ("6_multi_horizon", "multi_horizon", {}),
        ("combo_us_hours_plus_vol", "vol_surge_1_3", {"decision_hours_utc": (13, 16)}),
        ("combo_us_hours_plus_adx", "adx_25_rising", {"decision_hours_utc": (13, 16)}),
    ]

    rows: list[dict[str, Any]] = []
    baseline_row: dict[str, Any] | None = None
    for label, mode, overrides in variants:
        print("run", label, flush=True)
        _MODE["name"] = mode
        cfg = base_cfg(**overrides)
        res = eng.simulate(candles, cfg, start_ms=START_MS, end_ms=END_MS)
        if label == "baseline":
            baseline_row = summarize(label, res)
            rows.append(baseline_row)
        else:
            assert baseline_row is not None
            rows.append(summarize(label, res, baseline=baseline_row))
        r = rows[-1]
        print(
            f"  {label}: pnl={r['total_eur']} dd={r['max_dd_eur']} "
            f"n={r['trades']} wr={r['win_rate']} "
            f"Δpnl={r.get('delta_pnl')} Δdd={r.get('delta_dd')}",
            flush=True,
        )

    ranked = sorted(rows, key=lambda r: r["total_eur"], reverse=True)
    outperform_pnl = [r for r in rows if r.get("beats_pnl")]
    outperform_both = [r for r in rows if r.get("beats_both")]

    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "book": "€20k×1, live trail 3/2@+4% + early2, 15m close fills, no AlphaI",
        "ideas_tested": [
            "1 weekend entries ON",
            "2 US-session hours 13/16 (+13/16/19)",
            "3 ADX(14) hourly ≥25 and rising",
            "4 volume surge ≥1.3× prior 7d mean",
            "5 pullback 1–5% off 24h high",
            "6 multi-horizon excess score (6h/24h/72h)",
            "combos: US hours + vol / US hours + ADX",
        ],
        "baseline": baseline_row,
        "ranking_by_pnl": ranked,
        "outperform_pnl": outperform_pnl,
        "outperform_pnl_and_dd": outperform_both,
        "verdict_nl": (
            "Zie outperform_pnl / outperform_pnl_and_dd."
            if outperform_pnl
            else "Geen van de zes ideeën (noch combos) verslaat baseline op PnL in dit 12w-venster."
        ),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print("VERDICT", out["verdict_nl"], flush=True)
    print("TOP", [(r["label"], r["total_eur"], r["max_dd_eur"]) for r in ranked[:6]], flush=True)
    print("beats_pnl", [r["label"] for r in outperform_pnl], flush=True)
    print("beats_both", [r["label"] for r in outperform_both], flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
