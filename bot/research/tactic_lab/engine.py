"""Long-only momentum from the published low-turnover designs.

Time-series momentum: own lookback return positive, rank by excess versus
BTC, cash when nothing qualifies (Moskowitz, Ooi, Pedersen; Antonacci dual
momentum; the 2025 long-only crypto comparison).

Multi-horizon trend: equal weight of the 7, 14, 28 and 56 day returns,
skip one day, long the highest score that is still positive. That is the
pre-specified core of the CTREND factor (JFQA 2024) without a model fit on
this tape. The paper's result that survives costs is a hold out to four
weeks, so the clock is 7 or 28 daily bars.

A trail, when set, uses the prior peak and sells on the next open. AlphaI,
when passed, keeps a name only if that day's real session picked it.
No per-coin branches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.live.momentum_btc_rs_clip import quote_vol, rs_excess, sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    bar_date,
    fill_px,
    metrics,
    rows_through,
    year_pnl,
)
from bot.research.consistency_lab.engine import consistency
from bot.research.factor_scoreboard.engine import LIVE_KNOBS

Ohlc = Mapping[str, Sequence[Sequence[float]]]


@dataclass(frozen=True)
class TacticSpec:
    name: str
    kind: str
    lookbacks: tuple[int, ...]
    skip: int = 1
    rebalance_days: int = 7
    excess_floor: float = 0.0
    require_sma: int = 50
    volume_confirm: bool = False
    trail_pct: float = 0.10
    btc_sma: int = 50
    min_qvol_eur: float = 80_000.0
    n_names: int = 1
    universe: tuple[str, ...] = DEFAULT_UNIVERSE


def default_tactic_specs() -> list[TacticSpec]:
    tsmom7 = (7,)
    tsmom28 = (28,)
    horizons = (7, 14, 28, 56)
    return [
        TacticSpec(name="tsmom_7_reb7", kind="tsmom", lookbacks=tsmom7, rebalance_days=7),
        TacticSpec(name="tsmom_28_reb7", kind="tsmom", lookbacks=tsmom28, rebalance_days=7),
        TacticSpec(name="tsmom_28_reb28", kind="tsmom", lookbacks=tsmom28, rebalance_days=28),
        TacticSpec(
            name="tsmom_28_reb28_hold",
            kind="tsmom",
            lookbacks=tsmom28,
            rebalance_days=28,
            trail_pct=0.0,
        ),
        TacticSpec(name="ctrend_reb7", kind="ctrend", lookbacks=horizons, rebalance_days=7),
        TacticSpec(name="ctrend_reb28", kind="ctrend", lookbacks=horizons, rebalance_days=28),
        TacticSpec(
            name="ctrend_reb28_vol",
            kind="ctrend",
            lookbacks=horizons,
            rebalance_days=28,
            volume_confirm=True,
        ),
        TacticSpec(
            name="ctrend_reb28_hold",
            kind="ctrend",
            lookbacks=horizons,
            rebalance_days=28,
            trail_pct=0.0,
        ),
    ]


def _own(closes: Sequence[float], lookback: int, skip: int) -> float | None:
    need = lookback + skip
    if len(closes) <= need or closes[-1 - need] <= 0:
        return None
    return closes[-1 - skip] / closes[-1 - need] - 1.0


def _days(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def _score(
    ohlc: Ohlc,
    spec: TacticSpec,
    base: str,
    date: str,
) -> float | None:
    rows = rows_through(ohlc.get(base) or [], date)
    btc_rows = rows_through(ohlc.get("BTC") or [], date)
    closes = [float(r[4]) for r in rows if float(r[4]) > 0]
    btc = [float(r[4]) for r in btc_rows if float(r[4]) > 0]
    if not closes or quote_vol(rows) < spec.min_qvol_eur:
        return None
    if spec.require_sma:
        level = sma(closes, spec.require_sma)
        if level is None or closes[-1] <= level:
            return None
    if spec.volume_confirm:
        if len(rows) < 40:
            return None
        recent = quote_vol(rows[-20:])
        prior = quote_vol(rows[-40:-20])
        if prior <= 0 or recent <= prior:
            return None
    if spec.kind == "tsmom":
        lookback = spec.lookbacks[0]
        own = _own(closes, lookback, spec.skip)
        excess = rs_excess(closes, btc, lb=lookback, skip=spec.skip)
        if own is None or excess is None or own <= 0 or excess <= spec.excess_floor:
            return None
        return float(excess)
    parts: list[float] = []
    for lookback in spec.lookbacks:
        own = _own(closes, lookback, spec.skip)
        if own is None:
            return None
        parts.append(own)
    score = sum(parts) / len(parts)
    if score <= 0:
        return None
    return score


def run_tactic(
    ohlc: Ohlc,
    spec: TacticSpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    alphai_allow: Mapping[str, set[str]] | None = None,
    alphai_from: str = "",
    keep_trades: bool = False,
) -> dict[str, Any]:
    """Compounding daily book. Signal on the close, fill on the next open."""
    timeline = [r for r in (ohlc.get("BTC") or []) if bar_date(r) <= end]
    book = Book(book_eur)
    legs: dict[str, dict[str, float]] = {}
    pending_sells: list[tuple[str, str]] = []
    pending_buys: list[tuple[str, float]] = []
    last_clock: str | None = None
    trades: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    day_equity: dict[str, float] = {}

    def _note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    def _adv(base: str, date: str) -> float:
        return quote_vol(rows_through(ohlc.get(base) or [], date))

    def _bar(base: str, date: str) -> Sequence[float] | None:
        rows = rows_through(ohlc.get(base) or [], date)
        if not rows or bar_date(rows[-1]) != date:
            return None
        return rows[-1]

    def _mark(date: str) -> float:
        px_map: dict[str, float] = {}
        for base in list(book.lots):
            row = _bar(base, date)
            if row is not None and float(row[4]) > 0:
                px_map[base] = float(row[4])
        return book.mark(px_map)

    for row in timeline:
        date = bar_date(row)
        ts = int(row[0])
        for base, reason in pending_sells:
            live = _bar(base, date)
            lot = book.lots.get(base)
            if live is None or lot is None:
                continue
            raw = float(live[1])
            px = fill_px(raw, "sell", notional=lot.qty * raw, adv=_adv(base, date), model=model)
            book.sell(base, px, model.fee_side)
            trades.append({"date": date, "side": "sell", "base": base, "reason": reason})
            _note(reason)
            legs.pop(base, None)
        pending_sells = []
        for base, notional in pending_buys:
            live = _bar(base, date)
            if live is None or base in book.lots or notional <= 1.0:
                continue
            raw = float(live[1])
            px = fill_px(raw, "buy", notional=notional, adv=_adv(base, date), model=model)
            spent = book.buy(base, "tactic", notional, px, model.fee_side, opened_ms=ts)
            if spent <= 0:
                continue
            legs[base] = {"entry": px, "peak": max(px, float(live[2]))}
            trades.append({"date": date, "side": "buy", "base": base, "reason": spec.kind})
            _note(spec.kind)
        pending_buys = []
        if date < start:
            continue
        if date > end:
            break

        for base, leg in list(legs.items()):
            live = _bar(base, date)
            if live is None or base not in book.lots:
                continue
            leg["peak"] = max(leg["peak"], float(live[2]))
            if spec.trail_pct > 0 and leg["peak"] > 0:
                stop_px = leg["peak"] * (1.0 - spec.trail_pct)
                if float(live[4]) <= stop_px:
                    pending_sells.append((base, "trail"))

        btc_rows_now = rows_through(ohlc.get("BTC") or [], date)
        btc_closes = [float(r[4]) for r in btc_rows_now if float(r[4]) > 0]
        level = sma(btc_closes, spec.btc_sma) if btc_closes else None
        risk_on = level is not None and btc_closes[-1] > level
        due = last_clock is None or _days(last_clock, date) >= spec.rebalance_days
        if any(reason == "trail" for _, reason in pending_sells):
            last_clock = date
            due = False
        if due:
            last_clock = date
            ranked: list[tuple[float, str]] = []
            if risk_on:
                allow = None
                if alphai_allow is not None and alphai_from and date >= alphai_from:
                    allow = alphai_allow.get(date) or set()
                for base in spec.universe:
                    if allow is not None and base not in allow:
                        continue
                    scored = _score(ohlc, spec, base, date)
                    if scored is not None:
                        ranked.append((scored, base))
                ranked.sort(reverse=True)
            picks = [base for _, base in ranked[: max(1, spec.n_names)]]
            for base in list(legs):
                if base not in picks:
                    pending_sells.append((base, "clock"))
            selling = {base for base, _ in pending_sells}
            fresh = [base for base in picks if base not in legs or base in selling]
            if fresh:
                each = _mark(date) / max(1, spec.n_names)
                for base in fresh:
                    if base in legs and base not in selling:
                        continue
                    pending_buys.append((base, each))

        if date >= start:
            day_equity[date] = _mark(date)

    curve = list(day_equity.items())
    extra: dict[str, Any] = {
        "reasons": reasons,
        "n_trail": reasons.get("trail", 0),
        "n_clock": reasons.get("clock", 0),
        "year_pnl": year_pnl([d for d, _ in curve], [v for _, v in curve], book_eur),
        "end_hold": ",".join(sorted(legs)) if legs else "cash",
    }
    extra.update(consistency(curve, book_eur))
    return {
        "strategy": spec.name,
        "model": model.name,
        **({"trades": trades} if keep_trades else {}),
        **metrics(
            [v for _, v in curve],
            start_eur=book_eur,
            n_trades=sum(1 for t in trades if t["side"] == "sell"),
            n_days=len(curve),
            extra=extra,
        ),
    }


def run_residual_row(ohlc: Ohlc, *, start: str, end: str, book_eur: float) -> dict[str, Any]:
    row = run_btc_residual(
        ohlc, start=start, end=end, book_eur=book_eur, keep_curve=True, **LIVE_KNOBS
    )
    curve = [(str(d), float(v)) for d, v in row["curve"]]
    row.update(consistency(curve, book_eur))
    row["strategy"] = "baseline_residual"
    row.pop("curve", None)
    return row
