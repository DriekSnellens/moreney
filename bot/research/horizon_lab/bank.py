"""Momentum book that banks a rise while it is printing.

The day-session lab sells on the next open, so a move that tagged +3% and
closed flat is booked as a loser. This book watches the bar. A take-profit
fills at the target when the high trades through it. A stop fills at the
stop when the low trades through it. If both print in the same bar the stop
is booked, because the path inside the bar is unknown.

Entry is the next 4h open after a close that is still rising: own lookback
return positive and excess versus BTC above the floor. Once that lookback
return is no longer positive the position is sold on the next open. Fees are
the Bitvavo taker on both sides. No per-coin branches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_btc_rs_clip import quote_vol, rs_excess, sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.clip_exit_lab.engine import WET, Book, FillModel, fill_px, metrics, year_pnl
from bot.research.consistency_lab.engine import consistency

Ohlc = Mapping[str, Sequence[Sequence[float]]]


@dataclass(frozen=True)
class BankSpec:
    name: str
    lookback: int = 6
    skip: int = 0
    excess_floor: float = 0.015
    tp_pct: float = 0.03
    stop_pct: float = 0.03
    partial_frac: float = 0.0
    n_names: int = 1
    require_rising: bool = True
    fail_when_flat: bool = True
    sma_bars: int = 0
    btc_sma_bars: int = 300
    min_qvol_eur: float = 15_000.0
    cooldown_bars: int = 1
    risk_off_blocks_entry: bool = True
    universe: tuple[str, ...] = DEFAULT_UNIVERSE


@dataclass
class Leg:
    base: str
    entry: float
    partial: bool = False
    stop_floor: float = 0.0


def level_exit(
    *,
    entry: float,
    open_: float,
    high: float,
    low: float,
    tp_pct: float,
    stop_pct: float,
    stop_floor: float = 0.0,
    want_tp: bool = True,
) -> tuple[str, float] | None:
    """First structural exit inside one bar, before fees.

    ``None`` means the bar did not trade through a level. A gap through a
    level fills at the open. When the stop and the target both print, the
    stop is booked.
    """
    if entry <= 0 or open_ <= 0:
        return None
    stops: list[tuple[float, str]] = []
    if stop_pct > 0:
        stops.append((entry * (1.0 - stop_pct), "stop"))
    if stop_floor > 0:
        stops.append((stop_floor, "be_stop"))
    stop_px = 0.0
    stop_reason = "stop"
    if stops:
        stop_px, stop_reason = max(stops, key=lambda item: item[0])
    hit_stop = stop_px > 0 and low <= stop_px
    tp_px = entry * (1.0 + tp_pct) if tp_pct > 0 and want_tp else 0.0
    hit_tp = tp_px > 0 and high >= tp_px
    if hit_stop:
        px = open_ if open_ <= stop_px else stop_px
        return stop_reason, px
    if hit_tp:
        px = open_ if open_ >= tp_px else tp_px
        return "tp", px
    return None


def _date(ts: int) -> str:
    return datetime.fromtimestamp(int(ts) / 1000, UTC).strftime("%Y-%m-%d")


def _contig(series: Sequence[float | None], i: int, n: int) -> list[float] | None:
    if n <= 0 or i + 1 < n:
        return None
    chunk = series[i - n + 1 : i + 1]
    if len(chunk) < n or any(c is None for c in chunk):
        return None
    return [float(c) for c in chunk]


def _own_return(closes: Sequence[float], lookback: int, skip: int) -> float | None:
    need = lookback + skip
    if len(closes) <= need or closes[-1 - need] <= 0:
        return None
    return closes[-1 - skip] / closes[-1 - need] - 1.0


def run_momentum_bank(
    ohlc: Ohlc,
    spec: BankSpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    keep_trades: bool = False,
) -> dict[str, Any]:
    """Compounding 4h momentum book. Structural targets fill inside the bar."""
    btc_rows = list(ohlc.get("BTC") or [])
    timeline = [int(r[0]) for r in btc_rows]
    by_ts: dict[str, dict[int, Sequence[float]]] = {
        base: {int(r[0]): r for r in rows} for base, rows in ohlc.items()
    }
    closes: dict[str, list[float | None]] = {}
    for base in ("BTC", *spec.universe):
        table = by_ts.get(base) or {}
        closes[base] = [float(table[ts][4]) if ts in table else None for ts in timeline]

    book = Book(book_eur)
    legs: dict[str, Leg] = {}
    # Buys carry a notional. Sells do not.
    pending_buys: list[tuple[str, float]] = []
    pending_sells: list[tuple[str, str]] = []
    cooldown: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    day_equity: dict[str, float] = {}

    def _note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    def _adv(base: str, i: int) -> float:
        rows: list[Sequence[float]] = []
        table = by_ts.get(base) or {}
        for k in range(max(0, i - 19), i + 1):
            row = table.get(timeline[k])
            if row is not None:
                rows.append(row)
        return quote_vol(rows, n=20)

    def _sell(base: str, ts: int, raw: float, qty: float, reason: str, i: int) -> None:
        lot = book.lots.get(base)
        if lot is None or qty <= 0 or raw <= 0:
            return
        take = min(lot.qty, qty)
        px = fill_px(raw, "sell", notional=take * raw, adv=_adv(base, i), model=model)
        book.sell(base, px, model.fee_side, qty=take)
        trades.append(
            {
                "ts": ts,
                "date": _date(ts),
                "side": "sell",
                "base": base,
                "px": round(px, 8),
                "reason": reason,
            }
        )
        _note(reason)
        if base not in book.lots:
            legs.pop(base, None)
            cooldown[base] = i + max(0, spec.cooldown_bars)

    def _mark(i: int) -> float:
        px_map: dict[str, float] = {}
        for base in list(book.lots):
            row = (by_ts.get(base) or {}).get(timeline[i])
            if row is not None and float(row[4]) > 0:
                px_map[base] = float(row[4])
        return book.mark(px_map)

    def _rising(base: str, i: int) -> tuple[float, float] | None:
        need = spec.lookback + spec.skip + 1
        width = max(need, spec.sma_bars) if spec.sma_bars else need
        alt = _contig(closes.get(base) or [], i, width)
        btc = _contig(closes.get("BTC") or [], i, need)
        if alt is None or btc is None:
            return None
        xs = rs_excess(alt, btc, lb=spec.lookback, skip=spec.skip)
        own = _own_return(alt, spec.lookback, spec.skip)
        if xs is None or own is None or xs <= spec.excess_floor:
            return None
        if spec.require_rising and own <= 0:
            return None
        if spec.sma_bars:
            level = sma(alt, spec.sma_bars)
            if level is None or float(alt[-1]) <= level:
                return None
        return float(xs), float(own)

    for i, ts in enumerate(timeline):
        date = _date(ts)
        if date < start:
            continue
        for base, reason in pending_sells:
            row = (by_ts.get(base) or {}).get(ts)
            lot = book.lots.get(base)
            if row is None or lot is None:
                continue
            _sell(base, ts, float(row[1]), lot.qty, reason, i)
        pending_sells = []
        for base, notional in pending_buys:
            row = (by_ts.get(base) or {}).get(ts)
            if row is None or base in book.lots or notional <= 1.0:
                continue
            raw = float(row[1])
            px = fill_px(raw, "buy", notional=notional, adv=_adv(base, i), model=model)
            spent = book.buy(base, "mom", notional, px, model.fee_side, opened_ms=ts)
            if spent <= 0:
                continue
            legs[base] = Leg(base=base, entry=px)
            trades.append(
                {
                    "ts": ts,
                    "date": date,
                    "side": "buy",
                    "base": base,
                    "px": round(px, 8),
                    "reason": "momentum",
                }
            )
            _note("momentum")
        pending_buys = []
        if date > end:
            break

        for base, leg in list(legs.items()):
            row = (by_ts.get(base) or {}).get(ts)
            lot = book.lots.get(base)
            if row is None or lot is None:
                continue
            hit = level_exit(
                entry=leg.entry,
                open_=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                tp_pct=spec.tp_pct,
                stop_pct=spec.stop_pct,
                stop_floor=leg.stop_floor,
                want_tp=not leg.partial,
            )
            if hit is None:
                continue
            reason, raw = hit
            if reason == "tp" and spec.partial_frac > 0 and not leg.partial:
                _sell(base, ts, raw, lot.qty * spec.partial_frac, "tp_partial", i)
                kept = legs.get(base)
                if kept is not None:
                    kept.partial = True
                    kept.stop_floor = kept.entry * (1.0 + model.fee_side * 2.0)
            else:
                _sell(base, ts, raw, lot.qty, reason, i)

        btc_win = _contig(closes.get("BTC") or [], i, spec.btc_sma_bars)
        risk_on = True
        if spec.risk_off_blocks_entry:
            if btc_win is None:
                risk_on = False
            else:
                level = sma(btc_win, spec.btc_sma_bars)
                risk_on = level is not None and float(btc_win[-1]) > level

        if spec.fail_when_flat:
            need = spec.lookback + spec.skip + 1
            for base in list(legs):
                window = _contig(closes.get(base) or [], i, need)
                ret = None if window is None else _own_return(window, spec.lookback, spec.skip)
                if ret is not None and ret <= 0:
                    pending_sells.append((base, "momentum_fail"))

        slots = max(1, spec.n_names)
        held = set(legs) | {base for base, _ in pending_sells}
        if risk_on and len(held) < slots and i + 1 < len(timeline):
            nxt = _date(timeline[i + 1])
            if nxt <= end:
                ranked: list[tuple[float, str]] = []
                for base in spec.universe:
                    if base in held or cooldown.get(base, -1) > i:
                        continue
                    scored = _rising(base, i)
                    if scored is None:
                        continue
                    if (by_ts.get(base) or {}).get(ts) is None:
                        continue
                    if _adv(base, i) < spec.min_qvol_eur:
                        continue
                    ranked.append((scored[0], base))
                ranked.sort(reverse=True)
                room = slots - len(held)
                take = [base for _, base in ranked[:room]]
                if take:
                    eq = _mark(i)
                    each = eq / slots
                    for base in take:
                        pending_buys.append((base, each))

        day_equity[date] = _mark(i)

    curve = list(day_equity.items())
    closes_n = reasons.get("tp", 0) + reasons.get("tp_partial", 0)
    extra: dict[str, Any] = {
        "spec": spec.name,
        "lookback": spec.lookback,
        "tp_pct": spec.tp_pct,
        "stop_pct": spec.stop_pct,
        "reasons": reasons,
        "n_tp": closes_n,
        "n_stop": reasons.get("stop", 0) + reasons.get("be_stop", 0),
        "n_fail": reasons.get("momentum_fail", 0),
        "n_entries": reasons.get("momentum", 0),
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


def _floored(base: dict[str, Any], floor: float) -> dict[str, Any]:
    out = dict(base)
    out["excess_floor"] = floor
    return out


def default_bank_specs() -> list[BankSpec]:
    """Day (24h) and week (7d) momentum, banking the rise inside the bar.

    The follow rows have no target and no stop: the lot is sold on the next
    open once its own lookback return is no longer positive. The same entry
    with a target or a stop is the test of taking the level while it prints.
    """
    day = dict(lookback=6, skip=0, excess_floor=0.015, btc_sma_bars=300, min_qvol_eur=15_000.0)
    week = dict(lookback=42, skip=0, excess_floor=0.03, btc_sma_bars=300, min_qvol_eur=15_000.0)
    specs = [
        BankSpec(name="bank_d_tp2_s2", tp_pct=0.02, stop_pct=0.02, **day),
        BankSpec(name="bank_d_tp3_s2", tp_pct=0.03, stop_pct=0.02, **day),
        BankSpec(name="bank_d_tp3_s3", tp_pct=0.03, stop_pct=0.03, **day),
        BankSpec(name="bank_d_tp4_s3", tp_pct=0.04, stop_pct=0.03, **day),
        BankSpec(name="bank_d_tp6_s4", tp_pct=0.06, stop_pct=0.04, **day),
        BankSpec(name="bank_d_follow", tp_pct=0.0, stop_pct=0.04, **day),
        BankSpec(name="bank_d_tp3_part", tp_pct=0.03, stop_pct=0.03, partial_frac=0.5, **day),
        BankSpec(name="bank_d_tp3_n2", tp_pct=0.03, stop_pct=0.03, n_names=2, **day),
        BankSpec(name="bank_w_tp4_s3", tp_pct=0.04, stop_pct=0.03, **week),
        BankSpec(name="bank_w_tp6_s4", tp_pct=0.06, stop_pct=0.04, **week),
        BankSpec(name="bank_w_follow", tp_pct=0.0, stop_pct=0.05, **week),
        BankSpec(
            name="bank_d_tp3_norisk",
            tp_pct=0.03,
            stop_pct=0.03,
            risk_off_blocks_entry=False,
            **day,
        ),
    ]
    # Floor cliff on the 7-day rise, exit only when that rise dies.
    for floor, tag in ((0.03, "f3"), (0.04, "f4"), (0.06, "f6"), (0.08, "f8")):
        specs.append(
            BankSpec(
                name=f"bank_w_{tag}_hold",
                tp_pct=0.0,
                stop_pct=0.0,
                **_floored(week, floor),
            )
        )
    for floor, tag in ((0.04, "f4"), (0.08, "f8")):
        specs.append(
            BankSpec(
                name=f"bank_d_{tag}_hold",
                tp_pct=0.0,
                stop_pct=0.0,
                **_floored(day, floor),
            )
        )
        held = _floored(week, floor)
        specs.append(BankSpec(name=f"bank_w_{tag}_tp4", tp_pct=0.04, stop_pct=0.0, **held))
        specs.append(BankSpec(name=f"bank_w_{tag}_s8", tp_pct=0.0, stop_pct=0.08, **held))
        specs.append(BankSpec(name=f"bank_w_{tag}_tp4s8", tp_pct=0.04, stop_pct=0.08, **held))
    return specs
