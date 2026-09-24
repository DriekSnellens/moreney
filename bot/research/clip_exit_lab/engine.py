"""Causal 1d clip replay with generic exit overlays.

Signals use the completed daily close (same 00:05 UTC cadence as live).
Default fills are Bitvavo-taker next-open (wet). No per-coin branches.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.core.venue_fees import VENUE_TAKER_FEE
from bot.live.momentum_btc_rs_clip import (
    FEE_RT,
    SLIP,
    ClipConfig,
    closes_of,
    evaluate_clip,
    quote_vol,
    rs_excess,
)
from bot.research.clip_exit_lab.policies import ExitPolicy

DAY_MS = 86_400_000
IMPACT_K = 0.02
IMPACT_CAP = 0.03
BITVAVO_TAKER = float(VENUE_TAKER_FEE["bitvavo"])


@dataclass(frozen=True)
class FillModel:
    name: str
    delay_next_open: bool
    fee_side: float
    base_slip: float
    impact_k: float
    impact_cap: float


DRY = FillModel(
    name="dry",
    delay_next_open=False,
    fee_side=FEE_RT / 2.0,
    base_slip=SLIP,
    impact_k=0.0,
    impact_cap=0.0,
)
WET = FillModel(
    name="wet",
    delay_next_open=True,
    fee_side=BITVAVO_TAKER,
    base_slip=0.0,
    impact_k=IMPACT_K,
    impact_cap=IMPACT_CAP,
)


@dataclass
class Lot:
    base: str
    qty: float
    role: str
    entry_px: float
    opened_ms: int = 0
    peak_px: float = 0.0
    partial_done: bool = False


@dataclass
class Order:
    side: str
    base: str
    role: str
    adv: float
    qty: float = 0.0
    notional: float = 0.0
    reason: str = ""


def bar_date(row: Sequence[float]) -> str:
    return datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")


_DATE_CACHE: dict[int, list[str]] = {}


def _dates_of(rows: Sequence[Sequence[float]]) -> list[str]:
    key = id(rows)
    cached = _DATE_CACHE.get(key)
    if cached is None or len(cached) != len(rows):
        cached = [bar_date(r) for r in rows]
        _DATE_CACHE[key] = cached
    return cached


def _cut(rows: Sequence[Sequence[float]], date: str) -> int:
    return bisect.bisect_right(_dates_of(rows), date)


def extra_slip(notional: float, adv: float, model: FillModel) -> float:
    if model.impact_k <= 0 or model.impact_cap <= 0:
        return 0.0
    if adv <= 0:
        return model.impact_cap
    return min(model.impact_cap, model.impact_k * math.sqrt(max(0.0, notional) / adv))


def fill_px(ref: float, side: str, *, notional: float, adv: float, model: FillModel) -> float:
    if ref <= 0:
        return 0.0
    slip = model.base_slip + extra_slip(notional, adv, model)
    if side == "buy":
        return ref * (1.0 + slip)
    return ref * (1.0 - slip)


def rows_through(rows: Sequence[Sequence[float]], date: str) -> list[list[float]]:
    return list(rows[: _cut(rows, date)])


def bar_on_or_after(rows: Sequence[Sequence[float]], date: str) -> list[float] | None:
    i = bisect.bisect_left(_dates_of(rows), date)
    if i >= len(rows):
        return None
    return list(rows[i])


def last_px(rows: Sequence[Sequence[float]], date: str, *, field: int = 4) -> float:
    i = _cut(rows, date) - 1
    if i < 0:
        return 0.0
    return float(rows[i][field])


def _atr14(rows: Sequence[Sequence[float]]) -> float:
    if len(rows) < 15:
        return 0.02
    rets = []
    for i in range(-14, 0):
        prev = float(rows[i - 1][4])
        cur = float(rows[i][4])
        if prev > 0:
            rets.append(abs(cur / prev - 1.0))
    return sum(rets) / len(rets) if rets else 0.02


def _donch_low(rows: Sequence[Sequence[float]], n: int) -> float | None:
    use = rows[-n:] if len(rows) >= n else rows
    lows = [float(r[3]) for r in use if float(r[3]) > 0]
    return min(lows) if lows else None


def metrics(
    equity: Sequence[float],
    *,
    start_eur: float,
    n_trades: int,
    n_days: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not equity or start_eur <= 0:
        out = {
            "start_eur": round(start_eur, 2),
            "end_eur": round(start_eur, 2),
            "pnl_eur": 0.0,
            "pnl_pct": 0.0,
            "max_dd_pct": 0.0,
            "calmar": 0.0,
            "ann_pct": 0.0,
            "n_trades": n_trades,
            "n_days": n_days,
        }
        if extra:
            out.update(extra)
        return out
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            max_dd = max(max_dd, 1.0 - v / peak)
    end = equity[-1]
    pnl = end - start_eur
    years = max(n_days, 1) / 365.25
    ann = (end / start_eur) ** (1.0 / years) - 1.0 if end > 0 and years > 0 else 0.0
    calmar = (ann / max_dd) if max_dd > 1e-9 else 0.0
    out = {
        "start_eur": round(start_eur, 2),
        "end_eur": round(end, 2),
        "pnl_eur": round(pnl, 2),
        "pnl_pct": round(pnl / start_eur, 4),
        "max_dd_pct": round(max_dd, 4),
        "calmar": round(calmar, 3),
        "ann_pct": round(ann, 4),
        "n_trades": n_trades,
        "n_days": n_days,
    }
    if extra:
        out.update(extra)
    return out


def year_pnl(dates: Sequence[str], equity: Sequence[float], start_eur: float) -> dict[str, float]:
    year_end: dict[str, float] = {}
    for d, e in zip(dates, equity, strict=False):
        year_end[d[:4]] = e
    out: dict[str, float] = {}
    prev = start_eur
    for y in sorted(year_end):
        out[y] = round(year_end[y] - prev, 2)
        prev = year_end[y]
    return out


class Book:
    def __init__(self, cash: float) -> None:
        self.cash = float(cash)
        self.lots: dict[str, Lot] = {}

    def mark(self, px: Mapping[str, float]) -> float:
        eq = self.cash
        for lot in self.lots.values():
            p = float(px.get(lot.base) or 0.0)
            if p <= 0:
                p = lot.entry_px
            eq += lot.qty * p
        return eq

    def sell(self, base: str, px: float, fee_side: float, *, qty: float | None = None) -> float:
        lot = self.lots.get(base)
        if lot is None or px <= 0 or lot.qty <= 0:
            return 0.0
        take = lot.qty if qty is None else min(lot.qty, max(0.0, qty))
        if take <= 0:
            return 0.0
        proceeds = take * px
        fee = proceeds * fee_side
        self.cash += proceeds - fee
        lot.qty -= take
        if lot.qty <= 1e-12:
            self.lots.pop(base, None)
        return proceeds

    def buy(
        self,
        base: str,
        role: str,
        notional: float,
        px: float,
        fee_side: float,
        *,
        opened_ms: int,
    ) -> float:
        if px <= 0 or notional <= 0:
            return 0.0
        spend = min(notional, max(0.0, self.cash / (1.0 + fee_side)))
        if spend < 1.0:
            return 0.0
        fee = spend * fee_side
        if spend + fee > self.cash:
            spend = self.cash / (1.0 + fee_side)
            fee = spend * fee_side
        qty = spend / px
        self.cash -= spend + fee
        prev = self.lots.get(base)
        if prev:
            new_qty = prev.qty + qty
            prev.entry_px = (prev.entry_px * prev.qty + px * qty) / new_qty if new_qty else px
            prev.qty = new_qty
            prev.role = role
            prev.peak_px = max(prev.peak_px, px)
        else:
            self.lots[base] = Lot(
                base=base,
                qty=qty,
                role=role,
                entry_px=px,
                opened_ms=opened_ms,
                peak_px=px,
            )
        return spend


def _px_map(
    ohlc: Mapping[str, Sequence[Sequence[float]]], date: str, *, field: int = 4
) -> dict[str, float]:
    out: dict[str, float] = {}
    for base, rows in ohlc.items():
        p = last_px(rows, date, field=field)
        if p > 0:
            out[base] = p
    return out


def _adv(ohlc: Mapping[str, Sequence[Sequence[float]]], base: str, date: str) -> float:
    rows = ohlc.get(base) or []
    i = _cut(rows, date)
    return quote_vol(rows[max(0, i - 20) : i])


def _apply_orders(
    book: Book,
    orders: Sequence[Order],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    fill_date: str,
    model: FillModel,
    *,
    opened_ms: int,
) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    ref_i = 1 if model.delay_next_open else 4
    for order in orders:
        if order.side != "sell":
            continue
        row = bar_on_or_after(ohlc.get(order.base) or [], fill_date)
        lot = book.lots.get(order.base)
        if row is None or lot is None:
            continue
        ref = float(row[ref_i])
        take = order.qty if order.qty > 0 else lot.qty
        notion = take * ref
        px = fill_px(ref, "sell", notional=notion, adv=order.adv, model=model)
        proceeds = book.sell(order.base, px, model.fee_side, qty=take)
        fills.append(
            {
                "date": fill_date,
                "side": "sell",
                "base": order.base,
                "px": round(px, 8),
                "eur": round(proceeds, 2),
                "role": order.role,
                "reason": order.reason,
            }
        )
    for order in orders:
        if order.side != "buy":
            continue
        row = bar_on_or_after(ohlc.get(order.base) or [], fill_date)
        if row is None:
            continue
        ref = float(row[ref_i])
        px = fill_px(ref, "buy", notional=order.notional, adv=order.adv, model=model)
        spent = book.buy(
            order.base, order.role, order.notional, px, model.fee_side, opened_ms=opened_ms
        )
        if spent > 0:
            fills.append(
                {
                    "date": fill_date,
                    "side": "buy",
                    "base": order.base,
                    "px": round(px, 8),
                    "eur": round(spent, 2),
                    "role": order.role,
                    "reason": order.reason or "entry",
                }
            )
    return fills


def _overlay_orders(
    book: Book,
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    policy: ExitPolicy,
    now_ms: int,
) -> list[Order]:
    orders: list[Order] = []
    close_px = _px_map(ohlc, date, field=4)
    high_px = _px_map(ohlc, date, field=2)
    btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
    fold_eur = 0.0
    for lot in list(book.lots.values()):
        if lot.role != "alt":
            continue
        close = float(close_px.get(lot.base) or 0.0)
        if close <= 0:
            continue
        high = float(high_px.get(lot.base) or close)
        lot.peak_px = max(lot.peak_px, high, close)
        ret = close / lot.entry_px - 1.0 if lot.entry_px > 0 else 0.0
        peak_dd = 1.0 - close / lot.peak_px if lot.peak_px > 0 else 0.0
        rows = rows_through(ohlc.get(lot.base) or [], date)
        reasons: list[str] = []
        if policy.alt_tp_pct > 0 and ret >= policy.alt_tp_pct:
            reasons.append("alt_tp")
        if policy.alt_stop_pct > 0 and ret <= -policy.alt_stop_pct:
            reasons.append("alt_stop")
        if policy.alt_trail_pct > 0 and peak_dd >= policy.alt_trail_pct:
            reasons.append("alt_trail")
        if policy.alt_atr_k > 0:
            atr = _atr14(rows)
            if peak_dd >= policy.alt_atr_k * atr:
                reasons.append("alt_atr_trail")
        if policy.alt_max_days > 0 and lot.opened_ms > 0:
            age = (now_ms - lot.opened_ms) / DAY_MS
            if age >= policy.alt_max_days:
                reasons.append("alt_time")
        if policy.alt_min_excess is not None:
            xs = rs_excess(closes_of(rows), btc_c, lb=20, skip=1)
            if xs is not None and xs < policy.alt_min_excess:
                reasons.append("alt_excess")
        if policy.alt_donch_n > 0:
            floor = _donch_low(rows[:-1] if len(rows) > 1 else rows, policy.alt_donch_n)
            if floor is not None and close < floor:
                reasons.append("alt_donch")
        if (
            policy.alt_partial_tp_pct > 0
            and not lot.partial_done
            and ret >= policy.alt_partial_tp_pct
            and policy.alt_partial_frac > 0
        ):
            take = lot.qty * policy.alt_partial_frac
            orders.append(
                Order(
                    side="sell",
                    base=lot.base,
                    role="alt",
                    adv=_adv(ohlc, lot.base, date),
                    qty=take,
                    reason="alt_partial_tp",
                )
            )
            lot.partial_done = True
            continue
        if not reasons:
            continue
        mark = lot.qty * close
        orders.append(
            Order(
                side="sell",
                base=lot.base,
                role="alt",
                adv=_adv(ohlc, lot.base, date),
                reason="+".join(reasons),
            )
        )
        if policy.fold_alt_to_btc:
            fold_eur += mark
    if fold_eur >= 50.0:
        orders.append(
            Order(
                side="buy",
                base="BTC",
                role="btc",
                adv=_adv(ohlc, "BTC", date),
                notional=fold_eur,
                reason="fold_alt_to_btc",
            )
        )
    return orders


def run_clip_exits(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    policy: ExitPolicy,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    cfg = ClipConfig(sma_n=policy.sma_n, alt_frac=policy.alt_frac)
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    trades: list[dict[str, Any]] = []
    last_reb = 0
    n_flatten = 0
    n_overlay = 0
    overlay_reasons: dict[str, int] = {}

    for date in dates:
        if date < start:
            continue
        if date > end:
            break
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if model.delay_next_open and pending:
            trades.extend(
                _apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms)
            )
            pending = []

        overlay = _overlay_orders(book, ohlc, date, policy, now_ms)
        if overlay:
            n_overlay += sum(1 for o in overlay if o.side == "sell")
            for o in overlay:
                if o.reason:
                    overlay_reasons[o.reason] = overlay_reasons.get(o.reason, 0) + 1
            if model.delay_next_open:
                pending = overlay
                # Still run clip decide on remaining lots after overlay is queued.
            else:
                trades.extend(
                    _apply_orders(book, overlay, ohlc, date, model, opened_ms=now_ms)
                )

        sliced = {b: rows_through(rows, date) for b, rows in ohlc.items()}
        px = _px_map(ohlc, date, field=4)
        deployed = sum(lot.qty * (px.get(lot.base) or lot.entry_px) for lot in book.lots.values())
        held = {lot.base: lot.role for lot in book.lots.values()}
        reb_ms = last_reb if not policy.daily_rs else 0
        decision = evaluate_clip(
            sliced,
            cfg,
            held=held,
            cash_eur=book.cash,
            deployed_eur=deployed,
            now_ms=now_ms,
            last_rebalance_ms=reb_ms,
            now=now + timedelta(days=1),
        )
        orders: list[Order] = []
        if decision.get("ok"):
            for ex in decision.get("exits") or []:
                reason = str(ex.get("reason") or "exit")
                if policy.ignore_sma_flatten and reason == "btc_below_sma50":
                    continue
                if policy.no_weekly_rotate and reason in {"rs_rotate", "rs_drop"}:
                    continue
                base = str(ex["base"])
                if base in book.lots:
                    orders.append(
                        Order(
                            side="sell",
                            base=base,
                            role=str(ex.get("role") or book.lots[base].role),
                            adv=_adv(ohlc, base, date),
                            reason=reason,
                        )
                    )
            for en in decision.get("entries") or []:
                orders.append(
                    Order(
                        side="buy",
                        base=str(en["base"]),
                        role=str(en.get("role") or "alt"),
                        adv=_adv(ohlc, str(en["base"]), date),
                        notional=float(en["notional_eur"]),
                        reason="entry",
                    )
                )
            if decision.get("rebalance_due"):
                last_reb = now_ms
            if not decision.get("risk_on") and held and not policy.ignore_sma_flatten:
                n_flatten += 1
                last_reb = 0
        if orders:
            if model.delay_next_open:
                pending = overlay + orders if overlay else orders
            else:
                trades.extend(
                    _apply_orders(book, orders, ohlc, date, model, opened_ms=now_ms)
                )
        equity.append(book.mark(_px_map(ohlc, date, field=4)))
        eq_dates.append(date)

    hold = ",".join(sorted(book.lots)) or "cash"
    extra = {
        "end_hold": hold,
        "n_flatten": n_flatten,
        "n_overlay_exits": n_overlay,
        "overlay_reasons": overlay_reasons,
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
        "policy": policy.name,
    }
    return {
        "strategy": "btc_rs_clip",
        "policy": policy.name,
        "model": model.name,
        "held": hold,
        "trades_tail": trades[-16:],
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra=extra,
        ),
    }
