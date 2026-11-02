"""Wet one-name sleeve. A name is held while it stays the pick.

Fills are the next Bitvavo open, taker fee plus impact, same as the residual
book. A position still open on the last session is sold at that close.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    _adv,
    bar_date,
    bar_on_or_after,
    fill_px,
    last_px,
)

Ohlc = Mapping[str, Sequence[Sequence[float]]]


def _dates(ohlc: Ohlc, *, start: str, end: str) -> list[str]:
    out: list[str] = []
    for row in ohlc.get("BTC") or []:
        day = bar_date(row)
        if start <= day <= end:
            out.append(day)
    return out


def _on_day(ohlc: Ohlc, base: str, day: str, field: int) -> float | None:
    row = bar_on_or_after(ohlc.get(base) or [], day)
    if row is None or bar_date(row) != day:
        return None
    price = float(row[field])
    return price if price > 0 else None


def _sell(
    book: Book,
    base: str,
    ref: float,
    adv: float,
) -> None:
    lot = book.lots.get(base)
    if lot is None or ref <= 0:
        return
    notion = lot.qty * ref
    px = fill_px(ref, "sell", notional=notion, adv=adv, model=WET)
    book.sell(base, px, WET.fee_side)


def _buy(book: Book, base: str, ref: float, adv: float) -> bool:
    equity = book.cash
    if equity < 1.0 or ref <= 0:
        return False
    px = fill_px(ref, "buy", notional=equity, adv=adv, model=WET)
    spent = book.buy(base, "sleeve", equity, px, WET.fee_side, opened_ms=0)
    return spent > 0


def _mark(book: Book, ohlc: Ohlc, day: str, held: str | None) -> float:
    prices: dict[str, float] = {}
    if held is not None:
        close = last_px(ohlc.get(held) or [], day, field=4)
        if close > 0:
            prices[held] = close
    return book.mark(prices)


def run_sleeve(
    ohlc: Ohlc,
    picks: Mapping[str, str | None],
    *,
    start: str,
    end: str,
    book_eur: float,
) -> dict[str, Any]:
    days = _dates(ohlc, start=start, end=end)
    book = Book(book_eur)
    held: str | None = None
    entry_cash = book_eur
    entry_day = ""
    trips: list[dict[str, Any]] = []
    curve: list[list[float | str]] = []
    days_in_market = 0

    def close_trip(exit_day: str) -> None:
        nonlocal held
        if held is None:
            return
        trips.append(
            {
                "base": held,
                "entry": entry_day,
                "exit": exit_day,
                "pnl_eur": book.cash - entry_cash,
            }
        )
        held = None

    for index, day in enumerate(days):
        if index > 0:
            signal_day = days[index - 1]
            target = picks.get(signal_day)
            if target is not None and target not in ohlc:
                target = None
            if target != held:
                if held is not None:
                    ref = _on_day(ohlc, held, day, 1)
                    if ref is None:
                        target = held
                    else:
                        _sell(book, held, ref, _adv(ohlc, held, signal_day))
                        close_trip(day)
                if target is not None and held is None:
                    ref = _on_day(ohlc, target, day, 1)
                    if ref is not None:
                        entry_cash = book.cash
                        entry_day = day
                        if _buy(book, target, ref, _adv(ohlc, target, signal_day)):
                            held = target
            if held is not None:
                days_in_market += 1
        curve.append([day, round(_mark(book, ohlc, day, held), 2)])

    if held is not None and days:
        last = days[-1]
        ref = last_px(ohlc.get(held) or [], last, field=4)
        if ref > 0:
            _sell(book, held, ref, _adv(ohlc, held, last))
            close_trip(last)
            curve[-1][1] = round(book.cash, 2)

    end_equity = float(curve[-1][1]) if curve else book_eur
    peak = book_eur
    worst = 0.0
    for _day, value in curve:
        equity = float(value)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    largest = max(trips, key=lambda trip: float(trip["pnl_eur"]), default=None)
    return {
        "pnl_eur": end_equity - book_eur,
        "end_eur": end_equity,
        "max_dd_pct": worst,
        "n_trades": len(trips),
        "days_in_market": days_in_market,
        "end_hold": "cash",
        "curve": curve,
        "trips": trips,
        "largest_trip": largest,
        "book_eur": book_eur,
    }
