"""Causal 1d replay: live clip + live Donchian sleeves on separate books.

Clip: SMA50 flatten + weekly RS (``evaluate_clip``).
Donchian pair: donch10 + donch_fri10, equal split, each with its own 10/5
rules (BTC > SMA50). Allocator variant adds mid=cash and risk_off Friday 20/10.

Wet fills = next-open Bitvavo taker. No per-coin hardcodes. Paper shorts
are cash — spot cannot short.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.live.desk_allocator import classify_sma20_50, euros_for
from bot.live.momentum_donchian import DonchianConfig, evaluate_donchian, loop_sleeve_configs
from bot.research.clip_exit_lab.engine import (
    WET,
    Book,
    FillModel,
    Order,
    _adv,
    _apply_orders,
    _px_map,
    bar_date,
    metrics,
    rows_through,
    run_clip_exits,
    year_pnl,
)
from bot.research.clip_exit_lab.policies import ExitPolicy


def _cfgs() -> dict[str, DonchianConfig]:
    return {c.name: c for c in loop_sleeve_configs()}


def run_donchian_sleeve(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    cfg: DonchianConfig,
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel = WET,
    keep_curve: bool = False,
) -> dict[str, Any]:
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    book = Book(book_eur)
    pending: list[Order] = []
    equity: list[float] = []
    eq_dates: list[str] = []
    trades: list[dict[str, Any]] = []

    for date in dates:
        if date < start:
            continue
        if date > end:
            break
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        if model.delay_next_open and pending:
            trades.extend(_apply_orders(book, pending, ohlc, date, model, opened_ms=now_ms))
            pending = []
        sliced = {b: rows_through(rows, date) for b, rows in ohlc.items()}
        px = _px_map(ohlc, date, field=4)
        btc_c = [float(r[4]) for r in sliced.get("BTC") or [] if float(r[4]) > 0]
        deployed = sum(lot.qty * (px.get(lot.base) or lot.entry_px) for lot in book.lots.values())
        decision = evaluate_donchian(
            sliced,
            btc_c,
            cfg,
            held=set(book.lots),
            cash_eur=book.cash,
            deployed_eur=deployed,
            now=now + timedelta(days=1),
        )
        orders: list[Order] = []
        for ex in decision.get("exits") or []:
            base = str(ex["base"])
            if base in book.lots:
                orders.append(
                    Order(
                        side="sell",
                        base=base,
                        role=cfg.name,
                        adv=_adv(ohlc, base, date),
                        reason=str(ex.get("reason") or "exit"),
                    )
                )
        block = str(decision.get("risk_block") or "")
        if block not in {"data_not_ready", "sma_unavailable"}:
            for en in decision.get("entries") or []:
                orders.append(
                    Order(
                        side="buy",
                        base=str(en["base"]),
                        role=cfg.name,
                        adv=_adv(ohlc, str(en["base"]), date),
                        notional=float(en["notional_eur"]),
                        reason="entry",
                    )
                )
        if orders:
            if model.delay_next_open:
                pending = orders
            else:
                trades.extend(_apply_orders(book, orders, ohlc, date, model, opened_ms=now_ms))
        equity.append(book.mark(_px_map(ohlc, date, field=4)))
        eq_dates.append(date)

    hold = ",".join(sorted(book.lots)) or "cash"
    extra = {
        "end_hold": hold,
        "sleeve": cfg.name,
        "year_pnl": year_pnl(eq_dates, equity, book_eur),
    }
    out = {
        "strategy": cfg.name,
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


def _align(curves: Sequence[Sequence[Sequence[Any]]]) -> tuple[list[str], list[list[float]]]:
    sets = [ {str(p[0]) for p in c} for c in curves ]
    dates = sorted(set.intersection(*sets)) if sets else []
    maps = [ {str(p[0]): float(p[1]) for p in c} for c in curves ]
    series = [[m[d] for d in dates] for m in maps]
    return dates, series


def _corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    ra = [a[i] / a[i - 1] - 1.0 for i in range(1, len(a)) if a[i - 1] > 0]
    rb = [b[i] / b[i - 1] - 1.0 for i in range(1, len(b)) if b[i - 1] > 0]
    n = min(len(ra), len(rb))
    if n < 10:
        return None
    ra, rb = ra[:n], rb[:n]
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=False)) / n
    va = sum((x - ma) ** 2 for x in ra) / n
    vb = sum((y - mb) ** 2 for y in rb) / n
    if va <= 0 or vb <= 0:
        return None
    return cov / (va ** 0.5 * vb ** 0.5)


def combine_books(
    parts: Sequence[dict[str, Any]],
    *,
    start_eur: float,
    name: str,
) -> dict[str, Any]:
    curves = [p["curve"] for p in parts]
    dates, series = _align(curves)
    equity = [sum(col) for col in zip(*series, strict=True)]
    extra: dict[str, Any] = {
        "year_pnl": year_pnl(dates, equity, start_eur),
        "parts": [
            {
                "name": p.get("strategy") or p.get("policy") or p.get("sleeve"),
                "pnl_eur": p.get("pnl_eur"),
                "max_dd_pct": p.get("max_dd_pct"),
                "calmar": p.get("calmar"),
                "n_trades": p.get("n_trades"),
                "end_hold": p.get("end_hold"),
                "year_pnl": p.get("year_pnl"),
            }
            for p in parts
        ],
    }
    if len(series) >= 2:
        c = _corr(series[0], series[1])
        extra["corr_daily"] = None if c is None else round(float(c), 3)
        names = [
            str(p.get("strategy") or p.get("policy") or p.get("sleeve") or f"p{i}")
            for i, p in enumerate(parts)
        ]
        pairs: dict[str, float | None] = {}
        for i in range(len(series)):
            for j in range(i + 1, len(series)):
                cj = _corr(series[i], series[j])
                pairs[f"{names[i]}__{names[j]}"] = None if cj is None else round(float(cj), 3)
        extra["corr_pairs"] = pairs
    out = {
        "strategy": name,
        "model": parts[0].get("model") if parts else "",
        **metrics(
            equity,
            start_eur=start_eur,
            n_trades=sum(int(p.get("n_trades") or 0) for p in parts),
            n_days=len(equity),
            extra=extra,
        ),
    }
    out["curve"] = [[d, round(v, 2)] for d, v in zip(dates, equity, strict=False)]
    return out


def run_donch_pair(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel = WET,
) -> dict[str, Any]:
    """donch10 + donch_fri10, equal split, both always allowed to decide."""
    cfgs = _cfgs()
    half = book_eur / 2.0
    sleeves = [
        run_donchian_sleeve(
            ohlc,
            cfgs["donch_fri10"],
            start=start,
            end=end,
            book_eur=half,
            model=model,
            keep_curve=True,
        ),
        run_donchian_sleeve(
            ohlc,
            cfgs["donch10"],
            start=start,
            end=end,
            book_eur=half,
            model=model,
            keep_curve=True,
        ),
    ]
    return combine_books(sleeves, start_eur=book_eur, name="donch_pair_10_5")


def run_donch_allocator(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel = WET,
) -> dict[str, Any]:
    """sma20_50 mix on ``book_eur``. Paper short sleeve is cash."""
    cfgs = _cfgs()
    dates = [bar_date(r) for r in (ohlc.get("BTC") or [])]
    half = book_eur / 2.0
    books = {
        "donch_fri10": Book(half),
        "donch10": Book(half),
        "donch_fri": Book(0.0),
    }
    pending: dict[str, list[Order]] = {name: [] for name in cfgs}
    equity: list[float] = []
    eq_dates: list[str] = []
    trades: list[dict[str, Any]] = []
    n_trades = 0

    for date in dates:
        if date < start:
            continue
        if date > end:
            break
        now = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        now_ms = int(now.timestamp() * 1000)
        sliced = {b: rows_through(rows, date) for b, rows in ohlc.items()}
        px = _px_map(ohlc, date, field=4)
        btc_c = [float(r[4]) for r in sliced.get("BTC") or [] if float(r[4]) > 0]
        for name, book in books.items():
            if model.delay_next_open and pending[name]:
                fills = _apply_orders(book, pending[name], ohlc, date, model, opened_ms=now_ms)
                trades.extend(fills)
                n_trades += len(fills)
                pending[name] = []
        regime = classify_sma20_50(btc_c)
        targets = euros_for(str(regime.get("label") or "mid"), book=book_eur)
        ready = bool(regime.get("ready"))
        for name, book in books.items():
            cfg = cfgs[name]
            target = float(targets.get(name) or 0.0)
            deployed = sum(
                lot.qty * (px.get(lot.base) or lot.entry_px) for lot in book.lots.values()
            )
            orders: list[Order] = []
            if ready and target < cfg.min_notional_eur:
                for base in list(book.lots):
                    orders.append(
                        Order(
                            side="sell",
                            base=base,
                            role=name,
                            adv=_adv(ohlc, base, date),
                            reason="allocator_flatten",
                        )
                    )
            else:
                decision = evaluate_donchian(
                    sliced,
                    btc_c,
                    cfg,
                    held=set(book.lots),
                    cash_eur=book.cash,
                    deployed_eur=deployed,
                    now=now + timedelta(days=1),
                )
                for ex in decision.get("exits") or []:
                    base = str(ex["base"])
                    if base in book.lots:
                        orders.append(
                            Order(
                                side="sell",
                                base=base,
                                role=name,
                                adv=_adv(ohlc, base, date),
                                reason=str(ex.get("reason") or "exit"),
                            )
                        )
                block = str(decision.get("risk_block") or "")
                if target >= cfg.min_notional_eur and block not in {
                    "data_not_ready",
                    "sma_unavailable",
                }:
                    for en in decision.get("entries") or []:
                        orders.append(
                            Order(
                                side="buy",
                                base=str(en["base"]),
                                role=name,
                                adv=_adv(ohlc, str(en["base"]), date),
                                notional=float(en["notional_eur"]),
                                reason="entry",
                            )
                        )
            if orders:
                if model.delay_next_open:
                    pending[name] = orders
                else:
                    fills = _apply_orders(book, orders, ohlc, date, model, opened_ms=now_ms)
                    trades.extend(fills)
                    n_trades += len(fills)
        eq = sum(b.mark(px) for b in books.values())
        equity.append(eq)
        eq_dates.append(date)

    extra = {"year_pnl": year_pnl(eq_dates, equity, book_eur), "end_hold": "mix"}
    out = {
        "strategy": "donch_allocator_sma20_50",
        "model": model.name,
        "held": "mix",
        "trades_tail": trades[-12:],
        **metrics(
            equity,
            start_eur=book_eur,
            n_trades=n_trades,
            n_days=len(equity),
            extra=extra,
        ),
    }
    out["curve"] = [[d, round(v, 2)] for d, v in zip(eq_dates, equity, strict=False)]
    return out


def _strip_curve(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"curve", "trades_tail"}}


def run_clip_donch_mix(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    clip_eur: float = 12_000.0,
    donch_eur: float = 12_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    live = ExitPolicy(name="live")
    clip = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=clip_eur, model=model, keep_curve=True
    )
    donch = run_donch_pair(ohlc, start=start, end=end, book_eur=donch_eur, model=model)
    donch_alloc = run_donch_allocator(
        ohlc, start=start, end=end, book_eur=donch_eur, model=model
    )
    mix = combine_books(
        [clip, donch],
        start_eur=clip_eur + donch_eur,
        name="clip12k_donch12k",
    )
    mix_alloc = combine_books(
        [clip, donch_alloc],
        start_eur=clip_eur + donch_eur,
        name="clip12k_donch_alloc12k",
    )
    total = clip_eur + donch_eur
    clip24 = run_clip_exits(
        ohlc, live, start=start, end=end, book_eur=total, model=model, keep_curve=True
    )
    donch24 = run_donch_pair(ohlc, start=start, end=end, book_eur=total, model=model)
    return {
        "start": start,
        "end": end,
        "clip_eur": clip_eur,
        "donch_eur": donch_eur,
        "model": model.name,
        "clip_12k": _strip_curve(clip),
        "donch_pair_12k": _strip_curve(donch),
        "donch_alloc_12k": _strip_curve(donch_alloc),
        "mix_12_12": _strip_curve(mix),
        "mix_12_alloc12": _strip_curve(mix_alloc),
        "clip_24k": _strip_curve(clip24),
        "donch_pair_24k": _strip_curve(donch24),
    }
