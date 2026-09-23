"""Causal 1d book replay: close+10bps dry vs next-open Bitvavo-taker wet.

Residual-weekly: 100% of the book in the most liquid 20d skip-1 excess
name (min quote-volume floor); BTC if no alt has positive excess. Weekly
check; trade only on a name change.

Clip: live ``evaluate_clip`` (75% BTC above SMA50, 25% one alt at ≥8%
excess, weekly RS). No trail, no hard stop, no % take-profit.

Wet fills: next bar OPEN, Bitvavo taker 25 bps/side, extra impact
``k * sqrt(notional/ADV)`` capped at 3%. No per-coin hardcodes.
"""

from __future__ import annotations

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
from bot.live.momentum_desk import DEFAULT_UNIVERSE

DAY_MS = 86_400_000
IMPACT_K = 0.02
IMPACT_CAP = 0.03
MIN_QVOL_EUR = 80_000.0
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


@dataclass
class Order:
    side: str
    base: str
    role: str
    adv: float
    qty: float = 0.0
    notional: float = 0.0


def bar_date(row: Sequence[float]) -> str:
    return datetime.fromtimestamp(int(row[0]) / 1000, UTC).strftime("%Y-%m-%d")


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
    return [list(r) for r in rows if bar_date(r) <= date]


def bar_on_or_after(rows: Sequence[Sequence[float]], date: str) -> list[float] | None:
    for r in rows:
        if bar_date(r) >= date:
            return list(r)
    return None


def next_date(dates: Sequence[str], date: str) -> str | None:
    for d in dates:
        if d > date:
            return d
    return None


def last_px(rows: Sequence[Sequence[float]], date: str, *, use_open: bool = False) -> float:
    through = rows_through(rows, date)
    if not through:
        return 0.0
    return float(through[-1][1 if use_open else 4])


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

    def sell(self, base: str, px: float, fee_side: float) -> float:
        lot = self.lots.pop(base, None)
        if lot is None or px <= 0 or lot.qty <= 0:
            return 0.0
        proceeds = lot.qty * px
        fee = proceeds * fee_side
        self.cash += proceeds - fee
        return proceeds

    def buy(self, base: str, role: str, notional: float, px: float, fee_side: float) -> float:
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
            prev.entry_px = (
                (prev.entry_px * prev.qty + px * qty) / new_qty if new_qty else px
            )
            prev.qty = new_qty
            prev.role = role
        else:
            self.lots[base] = Lot(base=base, qty=qty, role=role, entry_px=px)
        return spend


def _px_map(
    ohlc: Mapping[str, Sequence[Sequence[float]]], date: str, *, use_open: bool
) -> dict[str, float]:
    out: dict[str, float] = {}
    for base, rows in ohlc.items():
        p = last_px(rows, date, use_open=use_open)
        if p > 0:
            out[base] = p
    return out


def _apply_orders(
    book: Book,
    orders: Sequence[Order],
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    fill_date: str,
    model: FillModel,
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
        notion = lot.qty * ref
        px = fill_px(ref, "sell", notional=notion, adv=order.adv, model=model)
        proceeds = book.sell(order.base, px, model.fee_side)
        fills.append(
            {
                "date": fill_date,
                "side": "sell",
                "base": order.base,
                "px": round(px, 8),
                "eur": round(proceeds, 2),
                "role": order.role,
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
        spent = book.buy(order.base, order.role, order.notional, px, model.fee_side)
        if spent > 0:
            fills.append(
                {
                    "date": fill_date,
                    "side": "buy",
                    "base": order.base,
                    "px": round(px, 8),
                    "eur": round(spent, 2),
                    "role": order.role,
                }
            )
    return fills


def pick_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    min_qvol_eur: float = MIN_QVOL_EUR,
    lookback_days: int = 20,
    skip_days: int = 1,
) -> dict[str, Any]:
    """Top liquid 20d skip-1 excess vs BTC; BTC when max excess ≤ 0."""
    btc_rows = rows_through(ohlc.get("BTC") or [], date)
    btc_c = closes_of(btc_rows)
    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for base in universe:
        rows = rows_through(ohlc.get(base) or [], date)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=lookback_days, skip=skip_days)
        qv = quote_vol(rows)
        if xs is None:
            skipped.append({"base": base, "reason": "short_history"})
            continue
        if qv < min_qvol_eur:
            skipped.append(
                {
                    "base": base,
                    "reason": "thin_volume",
                    "qvol": round(qv, 0),
                    "excess": round(xs, 4),
                }
            )
            continue
        ranked.append({"base": base, "excess": xs, "qvol": round(qv, 0)})
    ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    want = "BTC"
    if ranked and float(ranked[0]["excess"]) > 0:
        want = str(ranked[0]["base"])
    return {"want": want, "ranked": ranked[:8], "skipped": skipped[:12]}


def _calendar(ohlc: Mapping[str, Sequence[Sequence[float]]]) -> list[str]:
    rows = ohlc.get("BTC") or []
    return [bar_date(r) for r in rows]


def _adv(ohlc: Mapping[str, Sequence[Sequence[float]]], base: str, date: str) -> float:
    return quote_vol(rows_through(ohlc.get(base) or [], date))


def run_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = DRY,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    min_qvol_eur: float = MIN_QVOL_EUR,
    rebalance_days: int = 7,
    lookback_days: int = 20,
    skip_days: int = 1,
) -> dict[str, Any]:
    dates = _calendar(ohlc)
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    trades: list[dict[str, Any]] = []
    last_reb = 0
    held = ""
    picks: list[dict[str, Any]] = []
    need = lookback_days + skip_days + 1

    for date in dates:
        if date < start:
            continue
        if date > end:
            break
        if model.delay_next_open and pending:
            trades.extend(_apply_orders(book, pending, ohlc, date, model))
            pending = []
        btc_n = len(closes_of(rows_through(ohlc.get("BTC") or [], date)))
        ts = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
        due = (ts - last_reb) >= rebalance_days * DAY_MS or not held
        if due and btc_n >= need:
            pick = pick_residual(
                ohlc,
                date,
                universe=universe,
                min_qvol_eur=min_qvol_eur,
                lookback_days=lookback_days,
                skip_days=skip_days,
            )
            want = str(pick["want"])
            last_reb = ts
            if want != held:
                orders: list[Order] = []
                eq = book.mark(_px_map(ohlc, date, use_open=False))
                for base in list(book.lots):
                    orders.append(
                        Order(
                            side="sell",
                            base=base,
                            role=book.lots[base].role,
                            adv=_adv(ohlc, base, date),
                        )
                    )
                orders.append(
                    Order(
                        side="buy",
                        base=want,
                        role="core",
                        adv=_adv(ohlc, want, date),
                        notional=eq,
                    )
                )
                picks.append(
                    {
                        "date": date,
                        "from": held or "cash",
                        "to": want,
                        "ranked": pick["ranked"][:3],
                    }
                )
                if model.delay_next_open:
                    pending = orders
                else:
                    trades.extend(_apply_orders(book, orders, ohlc, date, model))
                held = want
        equity.append(book.mark(_px_map(ohlc, date, use_open=False)))

    return {
        "strategy": "residual_weekly",
        "model": model.name,
        "held": held,
        "picks": picks[-16:],
        "n_rotates": len(picks),
        "trades_tail": trades[-12:],
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra={"end_hold": held, "n_rotates": len(picks)},
        ),
    }


def run_clip(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = DRY,
    cfg: ClipConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or ClipConfig()
    dates = _calendar(ohlc)
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    trades: list[dict[str, Any]] = []
    last_reb = 0
    n_flatten = 0

    for date in dates:
        if date < start:
            continue
        if date > end:
            break
        if model.delay_next_open and pending:
            trades.extend(_apply_orders(book, pending, ohlc, date, model))
            pending = []
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        sliced = {b: rows_through(rows, date) for b, rows in ohlc.items()}
        px = _px_map(ohlc, date, use_open=False)
        deployed = sum(lot.qty * (px.get(lot.base) or lot.entry_px) for lot in book.lots.values())
        held = {lot.base: lot.role for lot in book.lots.values()}
        decision = evaluate_clip(
            sliced,
            cfg,
            held=held,
            cash_eur=book.cash,
            deployed_eur=deployed,
            now_ms=now_ms,
            last_rebalance_ms=last_reb,
            now=now + timedelta(days=1),
        )
        orders: list[Order] = []
        if decision.get("ok"):
            for ex in decision.get("exits") or []:
                base = str(ex["base"])
                if base in book.lots:
                    orders.append(
                        Order(
                            side="sell",
                            base=base,
                            role=str(ex.get("role") or book.lots[base].role),
                            adv=_adv(ohlc, base, date),
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
                    )
                )
            if decision.get("rebalance_due"):
                last_reb = now_ms
            if not decision.get("risk_on") and held:
                n_flatten += 1
                last_reb = 0
        if orders:
            if model.delay_next_open:
                pending = orders
            else:
                trades.extend(_apply_orders(book, orders, ohlc, date, model))
        equity.append(book.mark(_px_map(ohlc, date, use_open=False)))

    hold = ",".join(sorted(book.lots)) or "cash"
    return {
        "strategy": "btc_rs_clip",
        "model": model.name,
        "held": hold,
        "n_flatten": n_flatten,
        "trades_tail": trades[-12:],
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra={"end_hold": hold, "n_flatten": n_flatten},
        ),
    }
