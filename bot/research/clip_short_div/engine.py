"""Causal 1d replay: live clip + paper short-weakest on separate books.

Clip is long when BTC > SMA50. Short-weakest is on when BTC < SMA20
(cover the same day the gate flips). Wet fills = next-open Bitvavo taker.

Short PnL is paper: Bitvavo spot cannot short. AlphaI off (not in the 1d
cache). No per-coin hardcodes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_short_weakest import (
    ShortPosition,
    ShortWeakestConfig,
    btc_bear_ok,
    evaluate_short_exit,
    rank_weakest,
    select_shorts,
)
from bot.research.clip_donch_mix.engine import combine_books, run_donch_pair
from bot.research.clip_exit_lab.engine import (
    WET,
    FillModel,
    _adv,
    _px_map,
    bar_date,
    bar_on_or_after,
    fill_px,
    metrics,
    rows_through,
    run_clip_exits,
    year_pnl,
)
from bot.research.clip_exit_lab.policies import ExitPolicy


@dataclass
class ShortLot:
    base: str
    notional: float
    entry_px: float
    opened_ms: int
    peak_return: float = 0.0


@dataclass
class ShortOrder:
    side: str  # open | cover
    base: str
    adv: float
    notional: float = 0.0
    reason: str = ""


class ShortBook:
    """Margin + MTM short book. Equity = free cash + reserved + short PnL."""

    def __init__(self, cash: float) -> None:
        self.cash = float(cash)
        self.lots: dict[str, ShortLot] = {}

    def mark(self, px: Mapping[str, float]) -> float:
        eq = self.cash
        for lot in self.lots.values():
            p = float(px.get(lot.base) or 0.0)
            if p <= 0:
                p = lot.entry_px
            ret = (lot.entry_px - p) / lot.entry_px if lot.entry_px > 0 else 0.0
            eq += lot.notional + lot.notional * ret
        return eq

    def open_short(self, base: str, notional: float, px: float, fee_side: float) -> float:
        if px <= 0 or notional <= 0 or base in self.lots:
            return 0.0
        fee = notional * fee_side
        need = notional + fee
        if need > self.cash:
            notional = self.cash / (1.0 + fee_side) if fee_side > -0.999 else 0.0
            fee = notional * fee_side
        if notional < 1.0:
            return 0.0
        self.cash -= notional + fee
        self.lots[base] = ShortLot(base=base, notional=notional, entry_px=px, opened_ms=0)
        return notional

    def cover(self, base: str, px: float, fee_side: float) -> float:
        lot = self.lots.get(base)
        if lot is None or px <= 0:
            return 0.0
        ret = (lot.entry_px - px) / lot.entry_px if lot.entry_px > 0 else 0.0
        fee = lot.notional * fee_side
        pnl = lot.notional * ret - fee
        self.cash += lot.notional + pnl
        self.lots.pop(base, None)
        return pnl


def _cfg(book_eur: float) -> ShortWeakestConfig:
    scale = book_eur / 14_000.0 if book_eur > 0 else 1.0
    return ShortWeakestConfig(
        book_eur=book_eur,
        alphai_enabled=False,
        idle_fill_enabled=False,
        cover_when_core_active=False,
        only_when_core_idle=False,
        day_loss_limit_eur=600.0 * scale,
        week_loss_limit_eur=1_600.0 * scale,
    )


def _closes(rows: Sequence[Sequence[float]]) -> list[float]:
    return [float(r[4]) for r in rows if float(r[4]) > 0]


def _iso_week(date: str) -> str:
    now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _apply_short_orders(
    book: ShortBook,
    orders: Sequence[ShortOrder],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    fill_date: str,
    model: FillModel,
    *,
    opened_ms: int,
) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    ref_i = 1 if model.delay_next_open else 4
    for order in orders:
        if order.side != "cover":
            continue
        row = bar_on_or_after(ohlc.get(order.base) or [], fill_date)
        lot = book.lots.get(order.base)
        if row is None or lot is None:
            continue
        ref = float(row[ref_i])
        px = fill_px(ref, "buy", notional=lot.notional, adv=order.adv, model=model)
        pnl = book.cover(order.base, px, model.fee_side)
        fills.append(
            {
                "date": fill_date,
                "side": "cover",
                "base": order.base,
                "px": round(px, 8),
                "pnl": round(pnl, 2),
                "reason": order.reason,
            }
        )
    for order in orders:
        if order.side != "open":
            continue
        row = bar_on_or_after(ohlc.get(order.base) or [], fill_date)
        if row is None or order.base in book.lots:
            continue
        ref = float(row[ref_i])
        px = fill_px(ref, "sell", notional=order.notional, adv=order.adv, model=model)
        taken = book.open_short(order.base, order.notional, px, model.fee_side)
        if taken > 0:
            lot = book.lots[order.base]
            lot.opened_ms = opened_ms
            fills.append(
                {
                    "date": fill_date,
                    "side": "open",
                    "base": order.base,
                    "px": round(px, 8),
                    "eur": round(taken, 2),
                    "reason": order.reason or "entry",
                }
            )
    return fills


def btc_gate_days(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    sma_short: int = 20,
    sma_clip: int = 50,
) -> dict[str, Any]:
    """How often clip vs short gates can be on at the same time."""
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    n_clip = n_short = n_both = n_neither = n = 0
    for date in dates:
        if date < start or date > end:
            continue
        closes = _closes(rows_through(ohlc.get("BTC") or [], date))
        if len(closes) < sma_clip:
            continue
        last = closes[-1]
        s20 = sum(closes[-sma_short:]) / sma_short
        s50 = sum(closes[-sma_clip:]) / sma_clip
        clip_on = last > s50
        short_on = last < s20
        n += 1
        n_clip += int(clip_on)
        n_short += int(short_on)
        n_both += int(clip_on and short_on)
        n_neither += int(not clip_on and not short_on)
    return {
        "n_days": n,
        "clip_gate_on": n_clip,
        "short_gate_on": n_short,
        "both_gates": n_both,
        "neither_gate": n_neither,
        "clip_gate_frac": round(n_clip / n, 4) if n else 0.0,
        "short_gate_frac": round(n_short / n, 4) if n else 0.0,
        "both_gate_frac": round(n_both / n, 4) if n else 0.0,
        "neither_gate_frac": round(n_neither / n, 4) if n else 0.0,
    }


def run_short_weakest(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel = WET,
    keep_curve: bool = False,
) -> dict[str, Any]:
    cfg = _cfg(book_eur)
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = ShortBook(book_eur)
    pending: list[ShortOrder] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    trades: list[dict[str, Any]] = []
    last_reb_ms = 0
    day_realized = 0.0
    week_realized = 0.0
    day_key = ""
    week_key = ""
    n_deployed = 0
    n_cover_bull = 0
    n_hard_stop = 0
    n_rebalance = 0

    for date in dates:
        if date < start or date > end:
            continue
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        week = _iso_week(date)
        if date != day_key:
            day_key = date
            day_realized = 0.0
        if week != week_key:
            week_key = week
            week_realized = 0.0
        if model.delay_next_open and pending:
            fills = _apply_short_orders(
                book, pending, ohlc, date, model, opened_ms=now_ms
            )
            trades.extend(fills)
            for fill in fills:
                if fill["side"] == "cover":
                    pnl = float(fill.get("pnl") or 0.0)
                    day_realized += pnl
                    week_realized += pnl
            pending = []

        sliced = {b: rows_through(rows, date) for b, rows in ohlc.items()}
        px = _px_map(ohlc, date, field=4)
        btc_c = _closes(sliced.get("BTC") or [])
        bear_ok, _bear = btc_bear_ok(btc_c, cfg)
        closes_map = {b: _closes(rows) for b, rows in sliced.items()}

        orders: list[ShortOrder] = []
        covering: set[str] = set()

        for lot in list(book.lots.values()):
            mark = float(px.get(lot.base) or 0.0)
            if mark <= 0:
                continue
            ret = (lot.entry_px - mark) / lot.entry_px if lot.entry_px > 0 else 0.0
            lot.peak_return = max(lot.peak_return, ret)
            series = closes_map.get(lot.base) or []
            day_ret = None
            if len(series) >= 2 and series[-2] > 0:
                day_ret = series[-1] / series[-2] - 1.0
            pos = ShortPosition(
                base=lot.base,
                entry_price=lot.entry_px,
                notional_eur=lot.notional,
                opened_ms=lot.opened_ms,
                peak_return=lot.peak_return,
            )
            ex = evaluate_short_exit(pos, mark=mark, day_ret=day_ret, cfg=cfg)
            if ex:
                orders.append(
                    ShortOrder(
                        side="cover",
                        base=lot.base,
                        adv=_adv(ohlc, lot.base, date),
                        reason=str(ex.get("reason") or "exit"),
                    )
                )
                covering.add(lot.base)
                if str(ex.get("reason")) == "hard_stop":
                    n_hard_stop += 1

        if cfg.cover_on_bull and not bear_ok:
            for lot in list(book.lots.values()):
                if lot.base in covering:
                    continue
                orders.append(
                    ShortOrder(
                        side="cover",
                        base=lot.base,
                        adv=_adv(ohlc, lot.base, date),
                        reason="cover_on_bull",
                    )
                )
                covering.add(lot.base)
                n_cover_bull += 1
            last_reb_ms = now_ms

        allowed = True
        if day_realized <= -abs(cfg.day_loss_limit_eur):
            allowed = False
        if week_realized <= -abs(cfg.week_loss_limit_eur):
            allowed = False
        due = last_reb_ms <= 0 or (now_ms - last_reb_ms) >= cfg.rebalance_days * 86_400_000
        if allowed and bear_ok and due:
            for lot in list(book.lots.values()):
                if lot.base in covering:
                    continue
                orders.append(
                    ShortOrder(
                        side="cover",
                        base=lot.base,
                        adv=_adv(ohlc, lot.base, date),
                        reason="rebalance",
                    )
                )
                covering.add(lot.base)
            n_rebalance += 1
            cands, _rej = rank_weakest(closes_map, cfg, alphai=None, mode="absolute")
            cash_for = book.mark(px)
            planned = select_shorts(cands, cfg, cash_eur=cash_for, held=set())
            for row in planned:
                orders.append(
                    ShortOrder(
                        side="open",
                        base=str(row["base"]),
                        adv=_adv(ohlc, str(row["base"]), date),
                        notional=float(row["notional_eur"]),
                        reason="entry",
                    )
                )
            last_reb_ms = now_ms

        if orders:
            if model.delay_next_open:
                pending = orders
            else:
                fills = _apply_short_orders(
                    book, orders, ohlc, date, model, opened_ms=now_ms
                )
                trades.extend(fills)
                for fill in fills:
                    if fill["side"] == "cover":
                        pnl = float(fill.get("pnl") or 0.0)
                        day_realized += pnl
                        week_realized += pnl

        if book.lots:
            n_deployed += 1
        equity.append(book.mark(px))
        eq_dates.append(date)

    hold = ",".join(sorted(book.lots)) or "cash"
    extra = {
        "end_hold": hold,
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
        "n_days_deployed": n_deployed,
        "deployed_frac": round(n_deployed / len(equity), 4) if equity else 0.0,
        "n_cover_bull": n_cover_bull,
        "n_hard_stop": n_hard_stop,
        "n_rebalance": n_rebalance,
        "paper_only": True,
    }
    out = {
        "strategy": "short_weakest",
        "model": model.name,
        "held": hold,
        "trades_tail": trades[-12:],
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra=extra,
        ),
    }
    if keep_curve:
        out["curve"] = [[d, round(v, 2)] for d, v in zip(eq_dates, equity, strict=False)]
    return out


def _strip_curve(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"curve", "trades_tail"}}


def run_clip_short_div(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    total_eur: float = 24_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    live = ExitPolicy(name="live")
    half = total_eur / 2.0
    clip18 = total_eur * 0.75
    sat = total_eur * 0.25
    third = total_eur / 4.0

    clip24 = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=total_eur, model=model, keep_curve=True
    )
    short24 = run_short_weakest(
        ohlc, start=start, end=end, book_eur=total_eur, model=model, keep_curve=True
    )
    donch24 = run_donch_pair(ohlc, start=start, end=end, book_eur=total_eur, model=model)

    clip12 = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=half, model=model, keep_curve=True
    )
    short12 = run_short_weakest(
        ohlc, start=start, end=end, book_eur=half, model=model, keep_curve=True
    )
    donch12 = run_donch_pair(ohlc, start=start, end=end, book_eur=half, model=model)

    clip18k = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=clip18, model=model, keep_curve=True
    )
    short6 = run_short_weakest(
        ohlc, start=start, end=end, book_eur=sat, model=model, keep_curve=True
    )
    donch6 = run_donch_pair(ohlc, start=start, end=end, book_eur=third, model=model)

    mix_cs = combine_books(
        [clip12, short12], start_eur=total_eur, name="clip12_short12"
    )
    mix_cs_18 = combine_books(
        [clip18k, short6], start_eur=total_eur, name="clip18_short6"
    )
    mix_cd = combine_books(
        [clip12, donch12], start_eur=total_eur, name="clip12_donch12"
    )
    mix_cds = combine_books(
        [clip12, short6, donch6],
        start_eur=total_eur,
        name="clip12_short6_donch6",
    )
    gates = btc_gate_days(ohlc, start=start, end=end)
    return {
        "start": start,
        "end": end,
        "total_eur": total_eur,
        "model": model.name,
        "gates": gates,
        "clip_24k": _strip_curve(clip24),
        "short_24k": _strip_curve(short24),
        "donch_24k": _strip_curve(donch24),
        "clip_12k": _strip_curve(clip12),
        "short_12k": _strip_curve(short12),
        "donch_12k": _strip_curve(donch12),
        "short_6k": _strip_curve(short6),
        "donch_6k": _strip_curve(donch6),
        "mix_clip12_short12": _strip_curve(mix_cs),
        "mix_clip18_short6": _strip_curve(mix_cs_18),
        "mix_clip12_donch12": _strip_curve(mix_cd),
        "mix_clip12_short6_donch6": _strip_curve(mix_cds),
    }
