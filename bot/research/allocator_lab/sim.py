"""One-slot wet book: follow the euros, or follow the slow return.

Signal on the completed close, buy the next open. The trail uses the peak
known before the bar; a gap through it fills at the open. The clock sells
the next open after ten bars in the trade. Costs are the Bitvavo taker
plus impact. One name, equity compounded.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from bot.live.momentum_btc_rs_clip import quote_vol
from bot.research.allocator_lab.profile import FORWARD, KINDS, day_of, picks_for
from bot.research.clip_exit_lab.engine import WET, Book, FillModel, fill_px, metrics, year_pnl
from bot.research.consistency_lab.engine import consistency
from bot.research.horizon_lab.bank import level_exit

Ohlc = Mapping[str, Sequence[Sequence[float]]]
TRAIL = 0.10


def _index(ohlc: Ohlc) -> dict[str, dict[str, Sequence[float]]]:
    book: dict[str, dict[str, Sequence[float]]] = {}
    for base, rows in ohlc.items():
        book[base] = {day_of(int(row[0])): row for row in rows}
    return book


def _timeline(ohlc: Ohlc, start: str, end: str) -> list[str]:
    days = {day_of(int(row[0])) for row in ohlc.get("BTC") or []}
    if not days:
        for rows in ohlc.values():
            days.update(day_of(int(row[0])) for row in rows)
    return sorted(day for day in days if start <= day <= end)


def replay(
    ohlc: Ohlc,
    picks: Mapping[str, str],
    *,
    name: str,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    trail_pct: float = TRAIL,
    hold_bars: int = FORWARD,
) -> dict[str, Any]:
    bars = _index(ohlc)
    book = Book(book_eur)
    held: str | None = None
    pending_buy: str | None = None
    pending_sell: str | None = None
    entry_px = 0.0
    peak = 0.0
    bars_held = 0
    spent = 0.0
    reasons: dict[str, int] = {}
    trades: list[tuple[str, float]] = []
    curve: list[tuple[str, float]] = []

    def _adv(base: str, day: str) -> float:
        rows = [row for d, row in sorted((bars.get(base) or {}).items()) if d < day]
        return quote_vol(rows, 20)

    def _sell(base: str, day: str, raw: float, reason: str) -> None:
        nonlocal held, entry_px, peak, bars_held, spent, pending_sell
        lot = book.lots.get(base)
        if lot is None or raw <= 0:
            return
        cash_before = book.cash
        px = fill_px(raw, "sell", notional=lot.qty * raw, adv=_adv(base, day), model=model)
        book.sell(base, px, model.fee_side)
        trades.append((base, book.cash - cash_before - spent))
        reasons[reason] = reasons.get(reason, 0) + 1
        held = None
        pending_sell = None
        entry_px = 0.0
        peak = 0.0
        bars_held = 0
        spent = 0.0

    for day in _timeline(ohlc, start, end):
        if pending_sell and held:
            live = (bars.get(held) or {}).get(day)
            if live is not None and float(live[1]) > 0:
                _sell(held, day, float(live[1]), pending_sell)
        if pending_buy and held is None:
            live = (bars.get(pending_buy) or {}).get(day)
            if live is not None and float(live[1]) > 0:
                raw = float(live[1])
                notional = book.cash
                px = fill_px(raw, "buy", notional=notional, adv=_adv(pending_buy, day), model=model)
                cash_before = book.cash
                filled = book.buy(pending_buy, name, notional, px, model.fee_side, opened_ms=0)
                if filled > 0:
                    held = pending_buy
                    entry_px = px
                    peak = px
                    bars_held = 0
                    spent = cash_before - book.cash
            pending_buy = None

        if held:
            live = (bars.get(held) or {}).get(day)
            if live is not None:
                trail_px = peak * (1.0 - trail_pct) if trail_pct > 0 and peak > 0 else 0.0
                hit = level_exit(
                    entry=entry_px,
                    open_=float(live[1]),
                    high=float(live[2]),
                    low=float(live[3]),
                    tp_pct=0.0,
                    stop_pct=0.0,
                    trail_px=trail_px,
                )
                if hit is not None:
                    _sell(held, day, hit[1], hit[0])
                else:
                    peak = max(peak, float(live[2]))
                    bars_held += 1
                    if hold_bars > 0 and bars_held >= hold_bars and pending_sell is None:
                        pending_sell = "time"

        if (held is None or pending_sell) and pending_buy is None:
            choice = picks.get(day)
            if choice and choice != held:
                pending_buy = choice

        mark_px: dict[str, float] = {}
        if held:
            live = (bars.get(held) or {}).get(day)
            if live is not None and float(live[4]) > 0:
                mark_px[held] = float(live[4])
        curve.append((day, book.mark(mark_px)))

    trade_pnl = [pnl for _base, pnl in trades]
    wins = sum(1 for pnl in trade_pnl if pnl > 0)
    best_base, best_pnl = max(trades, key=lambda item: item[1], default=("", 0.0))
    total_pnl = sum(trade_pnl)
    extra: dict[str, Any] = {
        "strategy": name,
        "reasons": reasons,
        "n_wins": wins,
        "win_pct": (wins / len(trade_pnl)) if trade_pnl else 0.0,
        "avg_trade": (total_pnl / len(trade_pnl)) if trade_pnl else 0.0,
        "median_trade": statistics.median(trade_pnl) if trade_pnl else 0.0,
        "best_trade": best_pnl,
        "best_base": best_base,
        "best_share": (best_pnl / total_pnl) if total_pnl > 0 and best_pnl > 0 else 0.0,
        "year_pnl": year_pnl([day for day, _ in curve], [eq for _, eq in curve], book_eur),
        "end_hold": held or "cash",
    }
    extra.update(consistency(curve, book_eur))
    return {
        "strategy": name,
        "model": model.name,
        **metrics(
            [eq for _, eq in curve],
            start_eur=book_eur,
            n_trades=len(trade_pnl),
            n_days=len(curve),
            extra=extra,
        ),
    }


def run_books(
    ohlc: Ohlc,
    by_day: Mapping[str, Sequence[Any]],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    return [
        replay(ohlc, picks_for(by_day, kind), name=kind, start=start, end=end) for kind in KINDS
    ]
