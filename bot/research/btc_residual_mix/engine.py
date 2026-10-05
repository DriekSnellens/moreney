"""Causal 1d BTC + residual-weekly mix.

Residual pick: 20d skip-1 excess vs BTC (excess > floor). BTC sleeve is a
generic fraction so the book is not 100% one alt. Flatten: SMA50 on all
lots, BTC only, none, or regime (risk-off = 100% residual).

Wet = next-open Bitvavo taker. Not armed live. No per-coin branches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from bot.live.momentum_btc_rs_clip import closes_of, quote_vol, rs_excess, sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    Order,
    _adv,
    _apply_orders,
    _overlay_orders,
    _px_map,
    bar_date,
    metrics,
    rows_through,
    run_clip_exits,
    year_pnl,
)
from bot.research.clip_exit_lab.policies import ExitPolicy

DAY_MS = 86_400_000
MIN_QVOL = 80_000.0
Flatten = Literal["all", "btc", "none", "regime"]


def pick_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    *,
    universe: Sequence[str] = DEFAULT_UNIVERSE,
    min_qvol_eur: float = MIN_QVOL,
    lookback_days: int = 20,
    skip_days: int = 1,
    excess_floor: float = 0.0,
    n_alts: int = 1,
    require_alt_sma: bool = False,
    sma_n: int = 50,
    exclude_bases: Sequence[str] | None = None,
) -> dict[str, Any]:
    ban = {str(b).upper() for b in (exclude_bases or ())}
    btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
    ranked: list[dict[str, Any]] = []
    for base in universe:
        if str(base).upper() in ban:
            continue
        rows = rows_through(ohlc.get(base) or [], date)
        cl = closes_of(rows)
        xs = rs_excess(cl, btc_c, lb=lookback_days, skip=skip_days)
        qv = quote_vol(rows)
        if xs is None or qv < min_qvol_eur:
            continue
        if require_alt_sma:
            s = sma(cl, sma_n)
            last_px = float(cl[-1]) if cl else 0.0
            if s is None or last_px <= s:
                continue
        ranked.append({"base": base, "excess": xs, "qvol": round(qv, 0)})
    ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    alts = [
        str(r["base"])
        for r in ranked
        if float(r["excess"]) > excess_floor
    ][: max(1, int(n_alts))]
    want = alts[0] if alts else "BTC"
    return {"want": want, "wants": alts, "ranked": ranked[:8]}


def apply_alt_allow(
    pick: Mapping[str, Any],
    allowed: set[str] | None,
    *,
    mode: str = "gate",
    excess_floor: float = 0.0,
    n_alts: int = 1,
    excess_override: float | None = None,
) -> list[str]:
    """Keep residual rank. ``allowed is None`` leaves the pick unchanged.

    Modes:
      gate            — residual winner only if it is allowed; else empty
      intersect       — strongest residual names that are allowed + clear floor
      prefer          — winner if allowed; else intersect; else raw residual
      overlap_or_rs   — intersect when any allowed name clears floor; else RS
      override_gate   — like gate, but keep RS winner when excess >= override
    """
    wants = [str(b) for b in (pick.get("wants") or []) if b and b != "BTC"]
    ranked = list(pick.get("ranked") or [])
    n = max(1, int(n_alts))
    if allowed is None:
        return wants[:n]

    def _intersect() -> list[str]:
        return [
            str(row["base"])
            for row in ranked
            if str(row.get("base") or "") in allowed
            and float(row.get("excess") or 0.0) > excess_floor
        ][:n]

    m = str(mode or "gate").lower()
    if m == "intersect":
        return _intersect()
    if m in {"prefer", "soft"}:
        if wants and wants[0] in allowed:
            return wants[:1]
        hit = _intersect()
        return hit if hit else wants[:n]
    if m in {"overlap_or_rs", "overlap"}:
        hit = _intersect()
        return hit if hit else wants[:n]
    if m in {"override_gate", "gate_override"}:
        if wants and wants[0] in allowed:
            return wants[:1]
        if (
            excess_override is not None
            and wants
            and ranked
            and str(ranked[0].get("base") or "") == wants[0]
            and float(ranked[0].get("excess") or 0.0) >= float(excess_override)
        ):
            return wants[:1]
        return []
    # Default hard gate.
    if wants and wants[0] in allowed:
        return wants[:1]
    return []


def _iso_week(date: str) -> str:
    now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _held(book: Book) -> dict[str, str]:
    return {lot.base: lot.role for lot in book.lots.values()}


def _targets(
    *,
    winner: str,
    risk_on: bool,
    btc_frac: float,
    flatten: Flatten,
) -> tuple[str, str]:
    alt = winner if winner != "BTC" else ""
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    if flatten == "all" and not risk_on:
        return "", ""
    if flatten in {"regime", "btc"} and not risk_on:
        return "", alt
    if alt and alt_frac >= 0.05:
        return ("BTC" if btc_frac >= 0.05 else ""), alt
    if flatten == "none" or risk_on:
        return "BTC", ""
    return "", ""


def _targets_multi(
    *,
    winners: Sequence[str],
    risk_on: bool,
    btc_frac: float,
    flatten: Flatten,
) -> tuple[str, tuple[str, ...]]:
    alts = tuple(w for w in winners if w and w != "BTC")
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    if flatten == "all" and not risk_on:
        return "", ()
    if flatten in {"regime", "btc"} and not risk_on:
        return "", alts
    if alts and alt_frac >= 0.05:
        return ("BTC" if btc_frac >= 0.05 else ""), alts
    if flatten == "none" or risk_on:
        return "BTC", ()
    return "", ()


def _spike_reclaim_orders(
    book: Book,
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    date: str,
    policy: ExitPolicy,
    watches: list[dict[str, Any]],
    overlay: Sequence[Order],
    *,
    risk_on: bool,
    alt_frac: float,
    excess_floor: float,
    lookback_days: int,
    skip_days: int,
    n_alts: int,
    require_alt_sma: bool,
    sma_n: int,
) -> list[Order]:
    """Buy a spiked alt back if price reclaims that day's high within the window."""
    days = int(policy.alt_spike_reclaim_days or 0)
    if days <= 0:
        return []
    highs = _px_map(ohlc, date, field=2)
    for order in overlay:
        if order.side != "sell" or order.reason != "alt_spike":
            continue
        high = float(highs.get(order.base) or 0.0)
        if high <= 0:
            continue
        deadline = (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)
        ).strftime("%Y-%m-%d")
        watches[:] = [w for w in watches if w["base"] != order.base]
        watches.append(
            {
                "base": order.base,
                "high": high,
                "signal_date": date,
                "deadline": deadline,
            }
        )
    if not risk_on:
        # A one-day SMA dip must not erase the watch. Buy only while risk is on.
        watches[:] = [w for w in watches if date <= str(w["deadline"])]
        return []
    closes = _px_map(ohlc, date, field=4)
    held_alt = next((lot.base for lot in book.lots.values() if lot.role == "alt"), None)
    still: list[dict[str, Any]] = []
    buys: list[Order] = []
    for watch in watches:
        base = str(watch["base"])
        if date <= str(watch["signal_date"]):
            still.append(watch)
            continue
        if date > str(watch["deadline"]) or held_alt not in (None, base):
            continue
        if base in book.lots:
            continue
        close = float(closes.get(base) or 0.0)
        if close < float(watch["high"]):
            still.append(watch)
            continue
        pick = pick_residual(
            ohlc,
            date,
            excess_floor=excess_floor,
            lookback_days=lookback_days,
            skip_days=skip_days,
            n_alts=n_alts,
            require_alt_sma=require_alt_sma,
            sma_n=sma_n,
        )
        if base not in (pick.get("wants") or []):
            still.append(watch)
            continue
        eq = book.mark(closes)
        buys.append(
            Order(
                side="buy",
                base=base,
                role="alt",
                adv=_adv(ohlc, base, date),
                notional=eq * alt_frac,
                reason="alt_reclaim",
            )
        )
    watches[:] = still
    return buys


def run_btc_residual(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    btc_frac: float = 0.75,
    excess_floor: float = 0.0,
    flatten: Flatten = "none",
    sma_n: int = 50,
    rebalance_days: int = 7,
    lookback_days: int = 20,
    skip_days: int = 1,
    n_alts: int = 1,
    require_alt_sma: bool = False,
    model: FillModel = WET,
    policy: ExitPolicy | None = None,
    keep_curve: bool = False,
    keep_weeks: bool = False,
    keep_trades: bool = False,
    strategy: str = "",
    alt_allow: Mapping[str, set[str]] | None = None,
    alt_allow_mode: str = "gate",
    alt_allow_excess_override: float | None = None,
    cash_when_no_alt: bool = False,
    rebalance_weekday: int | None = None,
    immediate_rebuy: bool = False,
) -> dict[str, Any]:
    """One book: BTC fraction + residual winner on the rest.

    ``btc_frac=0`` is 100% residual weekly. ``btc_frac=1`` is BTC-only
    (SMA50 cash if flatten is all/regime).

    Sizing uses marked equity each rebalance (compound / winst herbelegd).
    ``rebalance_weekday`` (0=Mon … 6=Sun) matches live week-clock pin.

    ``immediate_rebuy``: after a trail/overlay sell, queue a next-open buy of
    the best qualifying alt **excluding** just-sold bases (no 7d cash wait).
    """
    name = strategy or f"btc{int(btc_frac * 100)}_res_f{flatten}"
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    holds: list[str] = []
    trades: list[dict[str, Any]] = []
    last_reb = 0
    want_btc = ""
    want_alts: tuple[str, ...] = ()
    n_rotate = 0
    n_overlay = 0
    n_immediate_rebuy = 0
    overlay_reasons: dict[str, int] = {}
    reclaim_watches: list[dict[str, Any]] = []
    need = int(lookback_days) + int(skip_days) + 1
    alt_frac = max(0.0, 1.0 - float(btc_frac))
    wd_pin = (
        int(rebalance_weekday)
        if rebalance_weekday is not None and int(rebalance_weekday) >= 0
        else None
    )

    for date in dates:
        if date < start or date > end:
            continue
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if model.delay_next_open and pending:
            fills = _apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms)
            trades.extend(fills)
            for fill in fills:
                if fill.get("reason") == "alt_reclaim":
                    lot = book.lots.get(str(fill["base"]))
                    if lot is not None:
                        lot.spike_done = True
            pending = []
        overlay: list[Order] = []
        overlay_sold: set[str] = set()
        if policy is not None:
            overlay = _overlay_orders(book, ohlc, date, policy, now_ms)
            overlay_sold = {o.base for o in overlay if o.side == "sell"}
            n_overlay += sum(1 for o in overlay if o.side == "sell")
            for o in overlay:
                if o.reason:
                    overlay_reasons[o.reason] = overlay_reasons.get(o.reason, 0) + 1
            if overlay_sold:
                want_alts = tuple(a for a in want_alts if a not in overlay_sold)
                last_reb = now_ms
        btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
        s50 = sma(btc_c, sma_n)
        last = float(btc_c[-1]) if btc_c else 0.0
        risk_on = s50 is not None and last > s50
        # Immediate rebuy after trail/overlay: same next-open as the sell, skip
        # the sold name(s). Weekly rotate path below still handles due clocks.
        if (
            immediate_rebuy
            and overlay_sold
            and risk_on
            and float(btc_frac) <= 0.0
            and len(btc_c) >= need
        ):
            pick_now = pick_residual(
                ohlc,
                date,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
                exclude_bases=sorted(overlay_sold),
            )
            winners = list(pick_now.get("wants") or [])
            if winners:
                px = _px_map(ohlc, date, field=4)
                eq = book.mark(px)
                # After pending sells fill next open, notion ≈ current mark eq.
                for alt in winners:
                    if alt in overlay_sold:
                        continue
                    overlay.append(
                        Order(
                            side="buy",
                            base=alt,
                            role="alt",
                            adv=_adv(ohlc, alt, date),
                            notional=eq / max(1, len(winners)),
                            reason="immediate_rebuy",
                        )
                    )
                    n_immediate_rebuy += 1
                want_alts = tuple(winners)
                want_btc = ""
                last_reb = now_ms
        age_due = last_reb <= 0 or (now_ms - last_reb) >= rebalance_days * DAY_MS
        if wd_pin is not None:
            due = age_due and now.weekday() == wd_pin
        else:
            due = age_due
        if due and len(btc_c) >= need:
            pick = pick_residual(
                ohlc,
                date,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
            )
            if alt_allow is not None and date in alt_allow:
                pick = dict(pick)
                pick["wants"] = apply_alt_allow(
                    pick,
                    set(alt_allow[date]),
                    mode=alt_allow_mode,
                    excess_floor=excess_floor,
                    n_alts=n_alts,
                    excess_override=alt_allow_excess_override,
                )
            last_reb = now_ms
            px = _px_map(ohlc, date, field=4)
            eq = book.mark(px)
            held = _held(book)
            next_btc, next_alts = _targets_multi(
                winners=list(pick.get("wants") or []),
                risk_on=risk_on,
                btc_frac=btc_frac,
                flatten=flatten,
            )
            changed = (next_btc != want_btc) or (next_alts != want_alts) or not held
            if changed and (next_btc or next_alts or held):
                orders: list[Order] = []
                for base, role in list(held.items()):
                    keep = (role == "btc" and base == next_btc) or (
                        role == "alt" and base in next_alts
                    )
                    if not keep:
                        orders.append(
                            Order(
                                side="sell",
                                base=base,
                                role=role,
                                adv=_adv(ohlc, base, date),
                                reason="rotate",
                            )
                        )
                if next_btc and held.get("BTC") != "btc":
                    full_btc = not next_alts and not cash_when_no_alt
                    notion = eq if full_btc else eq * btc_frac
                    orders.append(
                        Order(
                            side="buy",
                            base="BTC",
                            role="btc",
                            adv=_adv(ohlc, "BTC", date),
                            notional=notion,
                            reason="btc",
                        )
                    )
                split = max(1, len(next_alts))
                for alt in next_alts:
                    if held.get(alt) == "alt" or alt in overlay_sold:
                        continue
                    notion = (eq if not next_btc else eq * alt_frac) / split
                    orders.append(
                        Order(
                            side="buy",
                            base=alt,
                            role="alt",
                            adv=_adv(ohlc, alt, date),
                            notional=notion,
                            reason="residual",
                        )
                    )
                merged = overlay + orders
                if merged:
                    n_rotate += int(bool(orders))
                    if model.delay_next_open:
                        pending = merged
                    else:
                        trades.extend(
                            _apply_orders(book, merged, ohlc, date, model, opened_ms=now_ms)
                        )
                    overlay = []
            want_btc, want_alts = next_btc, next_alts
            if overlay_sold:
                want_alts = tuple(a for a in want_alts if a not in overlay_sold)
            if not next_btc and not next_alts:
                last_reb = 0
        if overlay:
            if model.delay_next_open:
                pending = overlay
            else:
                trades.extend(_apply_orders(book, overlay, ohlc, date, model, opened_ms=now_ms))
        if policy is not None and int(policy.alt_spike_reclaim_days or 0) > 0:
            reclaim = _spike_reclaim_orders(
                book,
                ohlc,
                date,
                policy,
                reclaim_watches,
                overlay,
                risk_on=risk_on,
                alt_frac=alt_frac,
                excess_floor=excess_floor,
                lookback_days=lookback_days,
                skip_days=skip_days,
                n_alts=n_alts,
                require_alt_sma=require_alt_sma,
                sma_n=sma_n,
            )
            for order in reclaim:
                overlay_reasons[order.reason] = overlay_reasons.get(order.reason, 0) + 1
            if reclaim:
                if model.delay_next_open:
                    pending = list(pending) + reclaim
                else:
                    fills = _apply_orders(book, reclaim, ohlc, date, model, opened_ms=now_ms)
                    trades.extend(fills)
                    for fill in fills:
                        if fill.get("reason") == "alt_reclaim":
                            lot = book.lots.get(str(fill["base"]))
                            if lot is not None:
                                lot.spike_done = True
        px = _px_map(ohlc, date, field=4)
        equity.append(book.mark(px))
        eq_dates.append(date)
        holds.append(
            ",".join(sorted(f"{lot.role}:{lot.base}" for lot in book.lots.values())) or "cash"
        )

    hold = holds[-1] if holds else "cash"
    extra: dict[str, Any] = {
        "end_hold": hold,
        "n_rotates": n_rotate,
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
        "btc_frac": btc_frac,
        "excess_floor": excess_floor,
        "flatten": flatten,
        "sma_n": sma_n,
        "lookback_days": lookback_days,
        "skip_days": skip_days,
        "rebalance_days": rebalance_days,
        "n_alts": int(n_alts),
        "require_alt_sma": bool(require_alt_sma),
        "n_overlay_exits": n_overlay,
        "n_immediate_rebuy": n_immediate_rebuy,
        "immediate_rebuy": bool(immediate_rebuy),
        "overlay_reasons": overlay_reasons,
        "exit_policy": None if policy is None else policy.name,
    }
    if keep_weeks:
        extra["weeks"] = _weeks(eq_dates, equity, holds, book_eur)
    out = {
        "strategy": name,
        "model": model.name,
        "held": hold,
        "trades_tail": trades[-12:],
        **({"trades": trades} if keep_trades else {}),
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


def _weeks(
    dates: Sequence[str],
    equity: Sequence[float],
    holds: Sequence[str],
    start_eur: float,
) -> list[dict[str, Any]]:
    by: dict[str, list[tuple[str, float, str]]] = {}
    for d, e, h in zip(dates, equity, holds, strict=False):
        by.setdefault(_iso_week(d), []).append((d, e, h))
    rows: list[dict[str, Any]] = []
    prev = start_eur
    for week in by:
        pts = by[week]
        end_e = pts[-1][1]
        rows.append(
            {
                "week": week,
                "start": pts[0][0],
                "end": pts[-1][0],
                "hold": pts[-1][2],
                "end_eur": round(end_e, 2),
                "pnl_eur": round(end_e - prev, 2),
                "min_eur": round(min(v for _, v, _ in pts), 2),
                "max_eur": round(max(v for _, v, _ in pts), 2),
            }
        )
        prev = end_e
    return rows


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"curve", "trades_tail"}}


def run_mix_scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    live = ExitPolicy(name="live")
    clip = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=book_eur, model=model, keep_curve=True
    )
    specs: list[tuple[str, float, float, Flatten]] = [
        ("residual_100", 0.0, 0.0, "none"),
        ("btc_sma50", 1.0, 0.0, "all"),
        ("btc75_res25_flat", 0.75, 0.0, "all"),
        ("btc75_res25_hold", 0.75, 0.0, "none"),
        ("btc50_res50_flat", 0.50, 0.0, "all"),
        ("btc50_res50_hold", 0.50, 0.0, "none"),
        ("btc75_res25_regime", 0.75, 0.0, "regime"),
        ("btc50_res50_regime", 0.50, 0.0, "regime"),
        ("btc75_res25_floor8", 0.75, 0.08, "all"),
    ]
    rows: dict[str, Any] = {"clip_live": _strip(clip)}
    for name, frac, floor, flat in specs:
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=frac,
            excess_floor=floor,
            flatten=flat,
            model=model,
            keep_curve=True,
            keep_weeks=True,
            strategy=name,
        )
        rows[name] = _strip(row)
    return {"start": start, "end": end, "book_eur": book_eur, "model": model.name, **rows}


def MIX_EXIT_POLICIES() -> list[ExitPolicy]:
    """Overlays on the alt sleeve only. BTC still follows SMA50 / weekly mix."""
    rows = [
        ExitPolicy(name="none"),
        ExitPolicy(name="alt_stop_8", alt_stop_pct=0.08),
        ExitPolicy(name="alt_stop_12", alt_stop_pct=0.12),
        ExitPolicy(name="alt_stop_15", alt_stop_pct=0.15),
        ExitPolicy(name="alt_stop_20", alt_stop_pct=0.20),
        ExitPolicy(name="alt_trail_8", alt_trail_pct=0.08),
        ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10),
        ExitPolicy(name="alt_trail_12", alt_trail_pct=0.12),
        ExitPolicy(name="alt_trail_15", alt_trail_pct=0.15),
        ExitPolicy(name="alt_tp_15", alt_tp_pct=0.15),
        ExitPolicy(name="alt_tp_20", alt_tp_pct=0.20),
        ExitPolicy(name="alt_tp_30", alt_tp_pct=0.30),
        ExitPolicy(name="alt_time_14", alt_max_days=14),
        ExitPolicy(name="alt_donch_10", alt_donch_n=10),
        ExitPolicy(name="alt_excess_lte_0", alt_min_excess=0.0),
        ExitPolicy(name="alt_stop8_fold", alt_stop_pct=0.08, fold_alt_to_btc=True),
        ExitPolicy(name="alt_trail12_fold", alt_trail_pct=0.12, fold_alt_to_btc=True),
        ExitPolicy(name="alt_partial_tp15", alt_partial_tp_pct=0.15, alt_partial_frac=0.5),
    ]
    return rows


def run_exit_scan(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    btc_frac: float = 0.5,
    flatten: Flatten = "all",
    model: FillModel = WET,
) -> dict[str, Any]:
    live: dict[str, Any] | None = None
    ranked: list[dict[str, Any]] = []
    for policy in MIX_EXIT_POLICIES():
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=btc_frac,
            excess_floor=0.0,
            flatten=flatten,
            model=model,
            policy=policy,
            strategy=f"btc{int(btc_frac * 100)}_{flatten}_{policy.name}",
        )
        slim = _strip(row)
        if policy.name == "none":
            live = slim
        ranked.append(slim)
    base_pnl = float((live or {}).get("pnl_eur") or 0.0)
    ranked.sort(key=lambda r: (-float(r["calmar"]), -float(r["pnl_eur"])))
    for row in ranked:
        row["delta_vs_none"] = round(float(row["pnl_eur"]) - base_pnl, 2)
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "btc_frac": btc_frac,
        "flatten": flatten,
        "none": live,
        "ranked": ranked,
    }


TRAIL10 = ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10)
TRAIL8 = ExitPolicy(name="alt_trail_8", alt_trail_pct=0.08)
TRAIL12 = ExitPolicy(name="alt_trail_12", alt_trail_pct=0.12)


def owner_pack_specs() -> list[dict[str, Any]]:
    """Directional €20k owner packs on the residual/clip tape.

    Not a claim of all strategies — HFT/arb/funding/15m/AlphaI stay out.
    """
    specs: list[dict[str, Any]] = []
    for frac in (0.0, 0.25, 0.4, 0.5, 0.6, 0.75, 1.0):
        for policy in (None, TRAIL10):
            tag = "none" if policy is None else policy.name
            specs.append(
                {
                    "family": "residual_mix",
                    "name": f"btc{int(frac * 100)}_flat50_{tag}",
                    "btc_frac": frac,
                    "flatten": "all",
                    "sma_n": 50,
                    "lookback_days": 20,
                    "skip_days": 1,
                    "rebalance_days": 7,
                    "policy": policy,
                }
            )
    for frac in (0.5, 0.75):
        for policy in (None, TRAIL10):
            tag = "none" if policy is None else policy.name
            specs.append(
                {
                    "family": "residual_mix",
                    "name": f"btc{int(frac * 100)}_neverflat_{tag}",
                    "btc_frac": frac,
                    "flatten": "none",
                    "sma_n": 50,
                    "lookback_days": 20,
                    "skip_days": 1,
                    "rebalance_days": 7,
                    "policy": policy,
                }
            )
    for sma_n in (20, 100, 200):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat{sma_n}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": sma_n,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": 7,
                "policy": TRAIL10,
            }
        )
    for lb, skip in ((10, 1), (40, 1), (60, 5)):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_lb{lb}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": lb,
                "skip_days": skip,
                "rebalance_days": 7,
                "policy": TRAIL10,
            }
        )
    for reb in (14, 30):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_reb{reb}_alt_trail_10",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": reb,
                "policy": TRAIL10,
            }
        )
    for policy in (TRAIL8, TRAIL12):
        specs.append(
            {
                "family": "residual_mix",
                "name": f"btc50_flat50_{policy.name}",
                "btc_frac": 0.5,
                "flatten": "all",
                "sma_n": 50,
                "lookback_days": 20,
                "skip_days": 1,
                "rebalance_days": 7,
                "policy": policy,
            }
        )
    specs.append(
        {
            "family": "residual_mix",
            "name": "btc100_flat200_none",
            "btc_frac": 1.0,
            "flatten": "all",
            "sma_n": 200,
            "lookback_days": 20,
            "skip_days": 1,
            "rebalance_days": 7,
            "policy": None,
        }
    )
    specs.append(
        {
            "family": "residual_mix",
            "name": "btc100_neverflat_none",
            "btc_frac": 1.0,
            "flatten": "none",
            "sma_n": 50,
            "lookback_days": 20,
            "skip_days": 1,
            "rebalance_days": 7,
            "policy": None,
        }
    )
    return specs


def run_owner_grid(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    include_clip: bool = True,
    include_donch: bool = True,
) -> dict[str, Any]:
    """Rank directional owner packs by Calmar. Research only — not live."""
    ranked: list[dict[str, Any]] = []
    if include_clip:
        live = ExitPolicy(name="live")
        clip = run_clip_exits(
            ohlc, live, start=start, end=end, book_eur=book_eur, model=model
        )
        row = _strip(clip)
        row["family"] = "clip_live"
        row["name"] = "clip_live_75_25_floor8"
        ranked.append(row)
    if include_donch:
        from bot.live.momentum_donchian import loop_sleeve_configs
        from bot.research.clip_donch_mix.engine import run_donchian_sleeve

        for cfg in loop_sleeve_configs():
            don = run_donchian_sleeve(
                ohlc, cfg, start=start, end=end, book_eur=book_eur, model=model
            )
            row = _strip(don)
            row["family"] = "donchian"
            row["name"] = f"donch20k_{cfg.name}"
            ranked.append(row)
    for spec in owner_pack_specs():
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            btc_frac=float(spec["btc_frac"]),
            excess_floor=0.0,
            flatten=spec["flatten"],
            sma_n=int(spec["sma_n"]),
            lookback_days=int(spec["lookback_days"]),
            skip_days=int(spec["skip_days"]),
            rebalance_days=int(spec["rebalance_days"]),
            model=model,
            policy=spec.get("policy"),
            strategy=str(spec["name"]),
        )
        slim = _strip(row)
        slim["family"] = spec["family"]
        slim["name"] = spec["name"]
        ranked.append(slim)
    ranked.sort(key=lambda r: (-float(r.get("calmar") or 0.0), -float(r.get("pnl_eur") or 0.0)))
    champ = ranked[0] if ranked else {}
    for row in ranked:
        row["delta_calmar_vs_top"] = round(
            float(row.get("calmar") or 0.0) - float(champ.get("calmar") or 0.0), 3
        )
        row["delta_pnl_vs_top"] = round(
            float(row.get("pnl_eur") or 0.0) - float(champ.get("pnl_eur") or 0.0), 2
        )
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "model": model.name,
        "n_packs": len(ranked),
        "best": champ.get("name"),
        "best_calmar": champ.get("calmar"),
        "ranked": ranked,
    }
