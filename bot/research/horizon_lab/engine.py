"""Day-session and week-clock replays on the residual tape.

Signals use completed bars only. Fills are Bitvavo taker on the next open
(the same wet model as the residual pack). No per-coin branches.

Week clock
    ``age`` fires every ``rebalance_days`` (the live pack uses 7).
    ``sunday`` fires on the Sunday close, for the next UTC week.
    ``daily`` fires every completed day.
    BTC under SMA50 flattens the book on that clock only, the same way the
    residual pack does — a mid-week dip does not sell a runner.
    A lot whose close has reached ``runner_arm`` above entry is sticky: with
    ``keep_runners`` the clock does not sell it. The trail and the lock still
    do, and either of those exits restarts the clock. The same leader is never
    sold and bought back on the same clock.

Day session
    A day lot is bought on the next open and sold on the open after that.
    It never spans a second session unless ``day_graduate`` is set and the
    entry session closed through ``runner_arm``; that lot becomes a week lot
    and the trail takes over.

4h day book
    One entry per UTC day, at the open of the bar after ``decision_hour``.
    The position is sold on the first open of the next UTC date, or earlier
    when the close hits the trail or the hard stop.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_btc_rs_clip import quote_vol, rs_excess, sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import pick_residual
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    Order,
    _adv,
    _apply_orders,
    _px_map,
    bar_date,
    fill_px,
    metrics,
    year_pnl,
)
from bot.research.consistency_lab.engine import consistency

Ohlc = Mapping[str, Sequence[Sequence[float]]]


@dataclass(frozen=True)
class HorizonSpec:
    name: str
    clock: str = "age"  # age | sunday | daily
    keep_runners: bool = True
    runner_arm: float = 0.12
    trail_pct: float = 0.10
    lock_arm: float = 0.12
    lock_floor: float = 0.06
    rebalance_days: int = 7
    lookback_days: int = 10
    skip_days: int = 1
    excess_floor: float = 0.035
    near_high_weight: float = 0.25
    require_alt_sma: bool = True
    sma_n: int = 50
    flatten_risk_off: bool = True
    min_qvol_eur: float = 80_000.0
    week_frac: float = 1.0
    day_frac: float = 0.0
    day_lookback: int = 3
    day_skip: int = 1
    day_floor: float = 0.03
    day_only_when_flat: bool = True
    day_graduate: bool = False
    universe: tuple[str, ...] = DEFAULT_UNIVERSE


@dataclass(frozen=True)
class DaySpec:
    """One-entry-per-UTC-day book on 4h bars."""

    name: str
    decision_hour: int = 20
    lookback: int = 6
    skip: int = 1
    excess_floor: float = 0.035
    sma_bars: int = 300
    btc_sma_bars: int = 300
    min_qvol_eur: float = 15_000.0
    trail_pct: float = 0.0
    hard_stop_pct: float = 0.0
    n_names: int = 1
    require_alt_sma: bool = True
    flatten_risk_off: bool = True
    universe: tuple[str, ...] = DEFAULT_UNIVERSE


def week_actions(
    *,
    held: str | None,
    runner: bool,
    pick: str | None,
    keep_runners: bool,
    pending_exit: bool,
) -> tuple[str | None, str | None]:
    """Clock decision: ``(sell_base, buy_base)``.

    ``None`` means no order on that side. A queued trail exit blocks a new
    buy until the next clock. A runner is not sold when ``keep_runners``.
    Holding the name the clock would buy does not round-trip it.
    """
    if pending_exit:
        return None, None
    if held and runner and keep_runners:
        return None, None
    if held and pick and held == pick:
        return None, None
    return held or None, pick or None


def day_session_actions(
    *,
    held: str | None,
    runner: bool,
    pick: str | None,
    week_busy: bool,
    only_when_week_flat: bool,
    graduate: bool,
) -> tuple[str | None, str | None]:
    """One UTC session: an open day lot is sold on the next open.

    The next session's pick is bought on that same open, including when it
    is the same name — staying in still pays the round trip, which is the
    cost of a day book. A lot that closed through the runner arm is not
    flattened when ``graduate`` is set; the caller moves it onto the trail.
    """
    if held and runner and graduate:
        return None, None
    blocked = only_when_week_flat and week_busy
    sell = held or None
    buy = None if blocked or not pick else pick
    return sell, buy


def _close_exit(
    *,
    entry: float,
    peak: float,
    close: float,
    trail_pct: float,
    lock_arm: float,
    lock_floor: float,
) -> str:
    if entry <= 0 or close <= 0 or peak <= 0:
        return ""
    if lock_arm > 0 and peak / entry - 1.0 >= lock_arm and close <= entry * (1.0 + lock_floor):
        return "alt_lock"
    if trail_pct > 0 and close <= peak * (1.0 - trail_pct):
        return "alt_trail"
    return ""


def _clock_due(
    spec: HorizonSpec,
    *,
    date: str,
    now_ms: int,
    last_clock_ms: int,
) -> bool:
    if spec.clock == "daily":
        return True
    if spec.clock == "sunday":
        return datetime.strptime(date, "%Y-%m-%d").weekday() == 6
    age = spec.rebalance_days * 86_400_000
    return last_clock_ms <= 0 or (now_ms - last_clock_ms) >= age


def _lot(book: Book, role: str):
    for lot in book.lots.values():
        if lot.role == role:
            return lot
    return None


def _queue(orders: list[Order], order: Order | None) -> None:
    if order is None:
        return
    if any(o.side == order.side and o.base == order.base and o.role == order.role for o in orders):
        return
    orders.append(order)


def _sell(base: str, role: str, ohlc: Ohlc, date: str, reason: str) -> Order:
    return Order(
        side="sell", base=base, role=role, adv=_adv(ohlc, base, date), reason=reason
    )


def _buy(base: str, role: str, ohlc: Ohlc, date: str, notional: float, reason: str) -> Order:
    return Order(
        side="buy",
        base=base,
        role=role,
        adv=_adv(ohlc, base, date),
        notional=notional,
        reason=reason,
    )


def run_horizon(
    ohlc: Ohlc,
    spec: HorizonSpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    keep_trades: bool = False,
    keep_holds: bool = False,
    keep_curve: bool = False,
) -> dict[str, Any]:
    """Replay one daily horizon spec. Compounding, wet next-open fills."""
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = Book(book_eur)
    pending: list[Order] = []
    runners: set[str] = set()
    last_clock_ms = 0
    equity: list[float] = []
    eq_dates: list[str] = []
    holds: list[str] = []
    trades: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}

    def _note(reason: str) -> None:
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1

    for date in dates:
        if date < start or date > end:
            continue
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if pending:
            fills = _apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms)
            trades.extend(fills)
            for fill in fills:
                _note(str(fill.get("reason") or fill.get("side") or ""))
            pending = []

        for lot in list(book.lots.values()):
            rows = ohlc.get(lot.base) or []
            px_hi = 0.0
            px_cl = 0.0
            for row in rows:
                if bar_date(row) == date:
                    px_hi = float(row[2])
                    px_cl = float(row[4])
                    break
            lot.peak_px = max(float(lot.peak_px or 0.0), float(lot.entry_px or 0.0), px_hi, px_cl)
            if (
                lot.entry_px > 0
                and px_cl > 0
                and px_cl / lot.entry_px - 1.0 >= spec.runner_arm
            ):
                runners.add(lot.base)
        runners.intersection_update(book.lots)

        btc_rows = [r for r in (ohlc.get("BTC") or []) if bar_date(r) <= date]
        btc_cl = [float(r[4]) for r in btc_rows if float(r[4]) > 0]
        s50 = sma(btc_cl, spec.sma_n)
        last = float(btc_cl[-1]) if btc_cl else 0.0
        sma_ready = s50 is not None
        risk_on = bool(sma_ready and last > float(s50 or 0.0))
        orders: list[Order] = []

        week_held: str | None = None
        if sma_ready:
            for lot in list(book.lots.values()):
                if lot.role == "day" and spec.day_graduate and lot.base in runners:
                    lot.role = "week"
                if lot.role != "week":
                    continue
                reason = _close_exit(
                    entry=lot.entry_px,
                    peak=lot.peak_px,
                    close=_close_on(ohlc, lot.base, date),
                    trail_pct=spec.trail_pct,
                    lock_arm=spec.lock_arm,
                    lock_floor=spec.lock_floor,
                )
                if reason:
                    _queue(orders, _sell(lot.base, "week", ohlc, date, reason))
            exiting = {o.base for o in orders if o.side == "sell"}
            week = _lot(book, "week")
            week_held = None if week is None or week.base in exiting else week.base
            pending_exit = week is not None and week.base in exiting
            due = _clock_due(spec, date=date, now_ms=now_ms, last_clock_ms=last_clock_ms)
            if any(o.reason in {"alt_trail", "alt_lock"} for o in orders):
                # A trail or lock sell restarts the week, matching the residual pack.
                last_clock_ms = now_ms
            if due and spec.flatten_risk_off and not risk_on:
                for lot in list(book.lots.values()):
                    if lot.role == "week" and lot.base not in exiting:
                        _queue(orders, _sell(lot.base, "week", ohlc, date, "btc_below_sma50"))
                        exiting.add(lot.base)
                week_held = None
                last_clock_ms = 0
            elif due and risk_on and spec.week_frac > 0:
                pick = _pick(
                    ohlc,
                    date,
                    universe=spec.universe,
                    lookback=spec.lookback_days,
                    skip=spec.skip_days,
                    floor=spec.excess_floor,
                    sma_n=spec.sma_n,
                    require_sma=spec.require_alt_sma,
                    near_high=spec.near_high_weight,
                    min_qvol=spec.min_qvol_eur,
                )
                sell_b, buy_b = week_actions(
                    held=week.base if week is not None else None,
                    runner=bool(week is not None and week.base in runners),
                    pick=pick,
                    keep_runners=spec.keep_runners,
                    pending_exit=pending_exit,
                )
                if sell_b and sell_b not in exiting:
                    _queue(orders, _sell(sell_b, "week", ohlc, date, "week_clock"))
                    exiting.add(sell_b)
                    week_held = None
                if buy_b and buy_b not in {o.base for o in orders if o.side == "buy"}:
                    px = _px_map(ohlc, date, field=4)
                    _queue(
                        orders,
                        _buy(
                            buy_b,
                            "week",
                            ohlc,
                            date,
                            book.mark(px) * spec.week_frac,
                            "week_entry",
                        ),
                    )
                    week_held = buy_b
                last_clock_ms = now_ms

            if spec.day_frac > 0:
                day = _lot(book, "day")
                day_exit_pending = day is not None and day.base in exiting
                week_busy = week_held is not None or any(
                    o.role == "week" and o.side == "buy" for o in orders
                )
                day_pick = None
                if risk_on and (day is None or day.base in exiting):
                    day_pick = _pick(
                        ohlc,
                        date,
                        universe=spec.universe,
                        lookback=spec.day_lookback,
                        skip=spec.day_skip,
                        floor=spec.day_floor,
                        sma_n=spec.sma_n,
                        require_sma=spec.require_alt_sma,
                        near_high=0.0,
                        min_qvol=spec.min_qvol_eur,
                        ban={week_held} if week_held else set(),
                    )
                sell_d, buy_d = day_session_actions(
                    held=None if day is None or day_exit_pending else day.base,
                    runner=bool(day is not None and day.base in runners),
                    pick=day_pick,
                    week_busy=week_busy,
                    only_when_week_flat=spec.day_only_when_flat,
                    graduate=spec.day_graduate,
                )
                graduated = (
                    day is not None
                    and spec.day_graduate
                    and day.base in runners
                    and day.role == "day"
                )
                if graduated:
                    day.role = "week"
                    sell_d = None
                if sell_d:
                    _queue(orders, _sell(sell_d, "day", ohlc, date, "day_flat"))
                if buy_d:
                    px = _px_map(ohlc, date, field=4)
                    eq = book.mark(px)
                    reserved = sum(o.notional for o in orders if o.side == "buy")
                    room = max(0.0, eq - reserved)
                    notion = min(eq * spec.day_frac, room)
                    if notion > 1.0:
                        _queue(orders, _buy(buy_d, "day", ohlc, date, notion, "day_entry"))

        if orders:
            if model.delay_next_open:
                pending = orders
            else:
                fills = _apply_orders(book, orders, ohlc, date, model, opened_ms=now_ms)
                trades.extend(fills)
                for fill in fills:
                    _note(str(fill.get("reason") or ""))
        px = _px_map(ohlc, date, field=4)
        equity.append(book.mark(px))
        eq_dates.append(date)
        hold = ",".join(sorted(f"{lot.role}:{lot.base}" for lot in book.lots.values())) or "cash"
        holds.append(hold)

    extra: dict[str, Any] = {
        "spec": spec.name,
        "clock": spec.clock,
        "keep_runners": spec.keep_runners,
        "end_hold": holds[-1] if holds else "cash",
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
        "reasons": reasons,
        "n_day_entries": reasons.get("day_entry", 0),
        "n_week_entries": reasons.get("week_entry", 0),
    }
    curve = [(d, v) for d, v in zip(eq_dates, equity, strict=False)]
    extra.update(consistency(curve, book_eur))
    out = {
        "strategy": spec.name,
        "model": model.name,
        **({"trades": trades} if keep_trades else {}),
        **({"holds": list(zip(eq_dates, holds, strict=False))} if keep_holds else {}),
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=len(trades),
            n_days=len(equity),
            extra=extra,
        ),
    }
    if keep_curve:
        out["curve"] = [[d, round(v, 2)] for d, v in curve]
    return out


def _close_on(ohlc: Ohlc, base: str, date: str) -> float:
    for row in ohlc.get(base) or []:
        if bar_date(row) == date:
            return float(row[4])
    return 0.0


def _pick(
    ohlc: Ohlc,
    date: str,
    *,
    universe: Sequence[str],
    lookback: int,
    skip: int,
    floor: float,
    sma_n: int,
    require_sma: bool,
    near_high: float,
    min_qvol: float,
    ban: set[str] | None = None,
) -> str | None:
    picked = pick_residual(
        ohlc,
        date,
        universe=universe,
        min_qvol_eur=min_qvol,
        lookback_days=lookback,
        skip_days=skip,
        excess_floor=floor,
        n_alts=1,
        require_alt_sma=require_sma,
        sma_n=sma_n,
        exclude_bases=sorted(ban or ()),
        near_high_weight=near_high,
    )
    wants = [str(b) for b in (picked.get("wants") or []) if b and b != "BTC"]
    if ban:
        wants = [b for b in wants if b not in ban]
    return wants[0] if wants else None


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts) / 1000, UTC)


def _date_of(ts: int) -> str:
    return _utc(ts).strftime("%Y-%m-%d")


def run_day_4h(
    ohlc: Ohlc,
    spec: DaySpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    keep_trades: bool = False,
) -> dict[str, Any]:
    """One UTC-day book on 4h bars. Compounding, wet next-bar-open fills."""
    btc_rows = list(ohlc.get("BTC") or [])
    timeline = [int(r[0]) for r in btc_rows]
    by_ts: dict[str, dict[int, Sequence[float]]] = {}
    for base, rows in ohlc.items():
        by_ts[base] = {int(r[0]): r for r in rows}
    closes: dict[str, list[float | None]] = {}
    for base in ("BTC", *spec.universe):
        table = by_ts.get(base) or {}
        closes[base] = [float(table[ts][4]) if ts in table else None for ts in timeline]

    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    day_equity: dict[str, float] = {}
    trades: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    entered_on: set[str] = set()

    def _note(reason: str) -> None:
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1

    def _fill(ts: int) -> None:
        nonlocal pending
        if not pending:
            return
        opened = ts
        for order in pending:
            if order.side != "sell":
                continue
            row = (by_ts.get(order.base) or {}).get(ts)
            lot = book.lots.get(order.base)
            if row is None or lot is None:
                continue
            ref = float(row[1])
            px = fill_px(ref, "sell", notional=lot.qty * ref, adv=order.adv, model=model)
            book.sell(order.base, px, model.fee_side)
            trades.append(
                {
                    "ts": ts,
                    "date": _date_of(ts),
                    "side": "sell",
                    "base": order.base,
                    "px": round(px, 8),
                    "reason": order.reason,
                }
            )
            _note(order.reason)
        for order in pending:
            if order.side != "buy":
                continue
            row = (by_ts.get(order.base) or {}).get(ts)
            if row is None or order.base in book.lots:
                continue
            ref = float(row[1])
            px = fill_px(ref, "buy", notional=order.notional, adv=order.adv, model=model)
            spent = book.buy(
                order.base, order.role, order.notional, px, model.fee_side, opened_ms=opened
            )
            if spent <= 0:
                continue
            trades.append(
                {
                    "ts": ts,
                    "date": _date_of(ts),
                    "side": "buy",
                    "base": order.base,
                    "px": round(px, 8),
                    "reason": order.reason,
                }
            )
            _note(order.reason)
        pending = []

    def _window(base: str, i: int, n: int) -> list[float] | None:
        if i + 1 < n:
            return None
        chunk = closes[base][i - n + 1 : i + 1]
        if any(c is None for c in chunk):
            return None
        return [float(c) for c in chunk]

    for i, ts in enumerate(timeline):
        date = _date_of(ts)
        if date < start:
            continue
        _fill(ts)
        if date > end:
            break
        hour = _utc(ts).hour
        for lot in list(book.lots.values()):
            row = (by_ts.get(lot.base) or {}).get(ts)
            if row is None:
                continue
            lot.peak_px = max(lot.peak_px, float(lot.entry_px), float(row[2]), float(row[4]))

        orders: list[Order] = []
        btc_sma_src = _window("BTC", i, spec.btc_sma_bars)
        risk_on = True
        if spec.flatten_risk_off:
            if btc_sma_src is None:
                risk_on = False
            else:
                level = sma(btc_sma_src, spec.btc_sma_bars)
                risk_on = level is not None and float(btc_sma_src[-1]) > level

        nxt = timeline[i + 1] if i + 1 < len(timeline) else None
        new_day_next = nxt is not None and _date_of(nxt) != date

        if not risk_on:
            for lot in list(book.lots.values()):
                adv = _adv_recent(by_ts, timeline, lot.base, i)
                orders.append(_order_h4("sell", lot.base, adv, "btc_below_sma50"))
        else:
            for lot in list(book.lots.values()):
                row = (by_ts.get(lot.base) or {}).get(ts)
                close = float(row[4]) if row is not None else 0.0
                reason = ""
                if spec.hard_stop_pct > 0 and lot.entry_px > 0 and close <= lot.entry_px * (
                    1.0 - spec.hard_stop_pct
                ):
                    reason = "hard_stop"
                elif spec.trail_pct > 0 and lot.peak_px > 0 and close <= lot.peak_px * (
                    1.0 - spec.trail_pct
                ):
                    reason = "day_trail"
                elif new_day_next:
                    reason = "day_flat"
                if reason:
                    adv = _adv_recent(by_ts, timeline, lot.base, i)
                    orders.append(_order_h4("sell", lot.base, adv, reason))

            can_enter = (
                hour == int(spec.decision_hour)
                and not book.lots
                and not orders
                and date not in entered_on
                and nxt is not None
                and _date_of(nxt) <= end
            )
            if can_enter:
                ranked = _rank_4h(closes, by_ts, timeline, i, spec)
                px_map = {}
                for base, table in by_ts.items():
                    row = table.get(ts)
                    if row is not None and float(row[4]) > 0:
                        px_map[base] = float(row[4])
                eq = book.mark(px_map)
                take = ranked[: max(1, spec.n_names)]
                if take:
                    each = eq / len(take)
                    for base in take:
                        orders.append(
                            _order_h4(
                                "buy",
                                base,
                                _adv_recent(by_ts, timeline, base, i),
                                "day_entry",
                                notional=each,
                            )
                        )
                    entered_on.add(date)

        pending = orders
        px_map = {}
        for lot in book.lots.values():
            row = (by_ts.get(lot.base) or {}).get(ts)
            if row is not None:
                px_map[lot.base] = float(row[4])
        marked = book.mark(px_map)
        equity.append(marked)
        eq_dates.append(date)
        day_equity[date] = marked

    curve = [(d, v) for d, v in day_equity.items()]
    extra: dict[str, Any] = {
        "spec": spec.name,
        "decision_hour": spec.decision_hour,
        "end_hold": (
            ",".join(sorted(book.lots)) if book.lots else "cash"
        ),
        "year_pnl": year_pnl([d for d, _ in curve], [v for _, v in curve], book_eur),
        "reasons": reasons,
        "n_entries": reasons.get("day_entry", 0),
        "bars": len(equity),
    }
    extra.update(consistency(curve, book_eur))
    return {
        "strategy": spec.name,
        "model": model.name,
        **({"trades": trades} if keep_trades else {}),
        **metrics(
            [v for _, v in curve] or equity,
            start_eur=book_eur,
            n_trades=sum(1 for t in trades if t["side"] == "sell"),
            n_days=len(curve),
            extra=extra,
        ),
    }


def _rank_4h(
    closes: Mapping[str, Sequence[float | None]],
    by_ts: Mapping[str, Mapping[int, Sequence[float]]],
    timeline: Sequence[int],
    i: int,
    spec: DaySpec,
) -> list[str]:
    need = spec.lookback + spec.skip + 1
    btc = _contig(closes.get("BTC") or [], i, need)
    if btc is None:
        return []
    ranked: list[tuple[float, str]] = []
    for base in spec.universe:
        width = spec.sma_bars if spec.require_alt_sma else need
        alt = _contig(closes.get(base) or [], i, max(need, width))
        if alt is None:
            continue
        xs = rs_excess(alt, btc if len(btc) >= need else alt, lb=spec.lookback, skip=spec.skip)
        if xs is None or xs <= spec.excess_floor:
            continue
        if spec.require_alt_sma:
            level = sma(alt, spec.sma_bars)
            if level is None or float(alt[-1]) <= level:
                continue
        ts = timeline[i]
        row = (by_ts.get(base) or {}).get(ts)
        if row is None:
            continue
        # Average quote of the lookback window, same units as the daily floor's bar.
        window_rows = []
        for k in range(max(0, i - 19), i + 1):
            r = (by_ts.get(base) or {}).get(timeline[k])
            if r is not None:
                window_rows.append(r)
        if quote_vol(window_rows, n=20) < spec.min_qvol_eur:
            continue
        ranked.append((float(xs), base))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [base for _, base in ranked]


def _contig(series: Sequence[float | None], i: int, n: int) -> list[float] | None:
    if n <= 0 or i + 1 < n:
        return None
    chunk = series[i - n + 1 : i + 1]
    if len(chunk) < n or any(c is None for c in chunk):
        return None
    return [float(c) for c in chunk]


def _order_h4(
    side: str, base: str, adv: float, reason: str, *, notional: float = 0.0
) -> Order:
    return Order(
        side=side, base=base, role="day", adv=adv, notional=notional, reason=reason
    )


def _adv_recent(
    by_ts: Mapping[str, Mapping[int, Sequence[float]]],
    timeline: Sequence[int],
    base: str,
    i: int,
) -> float:
    rows: list[Sequence[float]] = []
    table = by_ts.get(base) or {}
    for k in range(max(0, i - 19), i + 1):
        row = table.get(timeline[k])
        if row is not None:
            rows.append(row)
    return quote_vol(rows, n=20)


def default_week_specs() -> list[HorizonSpec]:
    """Week clocks, with and without the runner exemption, plus day overlays."""
    live = dict(
        lookback_days=10,
        skip_days=1,
        excess_floor=0.035,
        near_high_weight=0.25,
        require_alt_sma=True,
        sma_n=50,
        trail_pct=0.10,
        lock_arm=0.12,
        lock_floor=0.06,
        runner_arm=0.12,
    )
    rows = [
        HorizonSpec(name="week_age_cut", clock="age", keep_runners=False, **live),
        HorizonSpec(name="week_age_keep", clock="age", keep_runners=True, **live),
        HorizonSpec(
            name="week_age_keep_arm8",
            clock="age",
            keep_runners=True,
            **{**live, "runner_arm": 0.08},
        ),
        HorizonSpec(
            name="week_age_keep_arm18",
            clock="age",
            keep_runners=True,
            **{**live, "runner_arm": 0.18},
        ),
        HorizonSpec(name="week_sun_cut", clock="sunday", keep_runners=False, **live),
        HorizonSpec(name="week_sun_keep", clock="sunday", keep_runners=True, **live),
        HorizonSpec(name="week_daily_cut", clock="daily", keep_runners=False, **live),
        HorizonSpec(name="week_daily_keep", clock="daily", keep_runners=True, **live),
        HorizonSpec(
            name="day_session",
            clock="daily",
            keep_runners=False,
            week_frac=0.0,
            day_frac=1.0,
            day_only_when_flat=False,
            day_lookback=3,
            day_floor=0.03,
            runner_arm=0.08,
            **{k: v for k, v in live.items() if k != "runner_arm"},
        ),
        HorizonSpec(
            name="day_graduate",
            clock="daily",
            keep_runners=True,
            week_frac=0.0,
            day_frac=1.0,
            day_only_when_flat=True,
            day_graduate=True,
            day_lookback=3,
            day_floor=0.03,
            runner_arm=0.08,
            **{k: v for k, v in live.items() if k != "runner_arm"},
        ),
        HorizonSpec(
            name="week_keep_plus_idle_day",
            clock="age",
            keep_runners=True,
            week_frac=1.0,
            day_frac=1.0,
            day_only_when_flat=True,
            day_graduate=False,
            day_lookback=3,
            day_floor=0.03,
            **live,
        ),
    ]
    return rows


def default_day_specs() -> list[DaySpec]:
    base = dict(lookback=6, skip=1, sma_bars=300, btc_sma_bars=300, min_qvol_eur=15_000.0)
    return [
        DaySpec(name="day4h_utc_f2", decision_hour=20, excess_floor=0.02, **base),
        DaySpec(name="day4h_utc_f35", decision_hour=20, excess_floor=0.035, **base),
        DaySpec(name="day4h_utc_f6", decision_hour=20, excess_floor=0.06, **base),
        DaySpec(
            name="day4h_utc_f35_trail",
            decision_hour=20,
            excess_floor=0.035,
            trail_pct=0.06,
            hard_stop_pct=0.04,
            **base,
        ),
        DaySpec(name="day4h_open_f35", decision_hour=0, excess_floor=0.035, **base),
        DaySpec(name="day4h_utc_f35_n2", decision_hour=20, excess_floor=0.035, n_names=2, **base),
    ]
