"""Causal 1d BTC + residual-weekly mix.

Residual pick: 20d skip-1 excess vs BTC (excess > floor). BTC sleeve is a
generic fraction so the book is not 100% one alt. Flatten: SMA50 on all
lots, BTC only, none, or regime (risk-off = 100% residual).

Wet = next-open Bitvavo taker. Not armed live. No per-coin branches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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
) -> dict[str, Any]:
    btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
    ranked: list[dict[str, Any]] = []
    for base in universe:
        rows = rows_through(ohlc.get(base) or [], date)
        xs = rs_excess(closes_of(rows), btc_c, lb=lookback_days, skip=skip_days)
        qv = quote_vol(rows)
        if xs is None or qv < min_qvol_eur:
            continue
        ranked.append({"base": base, "excess": xs, "qvol": round(qv, 0)})
    ranked.sort(key=lambda r: float(r["excess"]), reverse=True)
    want = "BTC"
    if ranked and float(ranked[0]["excess"]) > excess_floor:
        want = str(ranked[0]["base"])
    return {"want": want, "ranked": ranked[:8]}


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
    model: FillModel = WET,
    policy: ExitPolicy | None = None,
    keep_curve: bool = False,
    keep_weeks: bool = False,
    strategy: str = "",
) -> dict[str, Any]:
    """One book: BTC fraction + residual winner on the rest.

    ``btc_frac=0`` is 100% residual weekly. ``btc_frac=1`` is BTC-only
    (SMA50 cash if flatten is all/regime).
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
    want_alt = ""
    n_rotate = 0
    n_overlay = 0
    overlay_reasons: dict[str, int] = {}
    need = 22
    alt_frac = max(0.0, 1.0 - float(btc_frac))

    for date in dates:
        if date < start or date > end:
            continue
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if model.delay_next_open and pending:
            trades.extend(_apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms))
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
                if want_alt in overlay_sold:
                    want_alt = ""
                last_reb = now_ms
        btc_c = closes_of(rows_through(ohlc.get("BTC") or [], date))
        s50 = sma(btc_c, sma_n)
        last = float(btc_c[-1]) if btc_c else 0.0
        risk_on = s50 is not None and last > s50
        due = last_reb <= 0 or (now_ms - last_reb) >= rebalance_days * DAY_MS
        if due and len(btc_c) >= need:
            pick = pick_residual(ohlc, date, excess_floor=excess_floor)
            winner = str(pick["want"])
            last_reb = now_ms
            px = _px_map(ohlc, date, field=4)
            eq = book.mark(px)
            held = _held(book)
            next_btc, next_alt = _targets(
                winner=winner,
                risk_on=risk_on,
                btc_frac=btc_frac,
                flatten=flatten,
            )
            changed = (next_btc != want_btc) or (next_alt != want_alt) or not held
            if changed and (next_btc or next_alt or held):
                orders: list[Order] = []
                for base, role in list(held.items()):
                    keep = (role == "btc" and base == next_btc) or (
                        role == "alt" and base == next_alt
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
                    notion = eq if not next_alt else eq * btc_frac
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
                if next_alt and held.get(next_alt) != "alt" and next_alt not in overlay_sold:
                    notion = eq if not next_btc else eq * alt_frac
                    orders.append(
                        Order(
                            side="buy",
                            base=next_alt,
                            role="alt",
                            adv=_adv(ohlc, next_alt, date),
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
            want_btc, want_alt = next_btc, next_alt
            if overlay_sold and want_alt in overlay_sold:
                want_alt = ""
            if not next_btc and not next_alt:
                last_reb = 0
        if overlay:
            if model.delay_next_open:
                pending = overlay
            else:
                trades.extend(_apply_orders(book, overlay, ohlc, date, model, opened_ms=now_ms))
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
        "n_overlay_exits": n_overlay,
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
