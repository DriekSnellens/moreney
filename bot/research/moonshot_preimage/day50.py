"""Small sleeve for a +50% day — coin-agnostic, walk-forward.

Question: which generic tape gate, entered at the next open, most accurately
catches a liquid name that trades +50% or more *from that open* the same
session, and what PnL does a €1.7k one-slot sleeve actually keep.

Label (no lookahead): signal uses the session close. The entry is the next
session's open. ``hit50`` means that entry session's high / open - 1 >= 0.50.
A +50% wick that closes red is still a hit on the label; the fill model only
books the take-profit when the path heuristic says the high traded before a
stop (up-close days prefer the target, down-close days prefer the stop).

Costs: 15 bp fee each side, 10 bp slip. Size = min(cash×0.98, book) — wins
do not scale the next ticket above the book.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.moonshot_preimage.engine import _by_date, _load_dir, _wilson

Row = list[float]
FEE = 0.0015
SLIP = 0.001
MIN_QVOL = 50_000.0
MIN_NOTION = 40.0


@dataclass
class Series:
    dates: list[str]
    o: list[float]
    h: list[float]
    l: list[float]
    c: list[float]
    v: list[float]


@dataclass
class Sig:
    base: str
    signal: str
    entry: str
    bi: int
    ei: int
    r1: float
    r3: float
    xs3: float
    xs10: float
    volx: float
    dayloc: float
    rngx: float
    gap: float
    brk20: bool
    trend: bool
    coil: bool
    hit50: bool
    hit50_close: bool


@dataclass(frozen=True)
class Gate:
    name: str
    r1_min: float = -9.0
    vol_min: float = 0.0
    dayloc_min: float = 0.0
    r3_min: float = -9.0
    xs_min: float = -9.0
    rng_min: float = 0.0
    brk20: bool = False
    trend: bool = False
    coil: bool = False


@dataclass(frozen=True)
class Exit:
    name: str
    tp: float | None
    hard: float
    hold: int
    trail: float


def gates() -> list[Gate]:
    out: list[Gate] = [Gate("all")]
    for r1 in (0.08, 0.12, 0.18, 0.25, 0.35):
        for vol in (1.5, 2.5, 4.0):
            for loc in (0.0, 0.75):
                loc_tag = "loc75" if loc else "loc0"
                out.append(
                    Gate(
                        f"r1_{int(r1 * 100)}_vol{vol:.1f}_{loc_tag}",
                        r1_min=r1,
                        vol_min=vol,
                        dayloc_min=loc,
                    )
                )
    for r1 in (0.06, 0.12, 0.20):
        for vol in (2.0, 3.0):
            out.append(
                Gate(
                    f"brk_r1_{int(r1 * 100)}_vol{vol:.1f}",
                    r1_min=r1,
                    vol_min=vol,
                    dayloc_min=0.6,
                    brk20=True,
                )
            )
    for r3 in (0.15, 0.25, 0.40):
        for xs in (0.15, 0.25, 0.40):
            out.append(
                Gate(
                    f"r3_{int(r3 * 100)}_xs{int(xs * 100)}_trend",
                    r3_min=r3,
                    xs_min=xs,
                    trend=True,
                )
            )
    for r1 in (0.04, 0.08, 0.12):
        for vol in (2.0, 3.0):
            out.append(
                Gate(
                    f"coil_r1_{int(r1 * 100)}_vol{vol:.1f}",
                    r1_min=r1,
                    vol_min=vol,
                    coil=True,
                )
            )
    for vol in (3.0, 5.0, 8.0):
        for r1 in (0.05, 0.15):
            out.append(
                Gate(
                    f"climax_vol{vol:.0f}_r1_{int(r1 * 100)}",
                    r1_min=r1,
                    vol_min=vol,
                    dayloc_min=0.8,
                )
            )
    for rng in (2.0, 3.0):
        for r1 in (0.08, 0.15):
            out.append(
                Gate(
                    f"rng{rng:.0f}_r1_{int(r1 * 100)}_vol2",
                    r1_min=r1,
                    vol_min=2.0,
                    rng_min=rng,
                )
            )
    return out


def exits() -> list[Exit]:
    return [
        Exit("tp50_hs12_d1", tp=0.50, hard=0.12, hold=1, trail=0.0),
        Exit("tp50_hs20_d1", tp=0.50, hard=0.20, hold=1, trail=0.0),
        Exit("tp50_hs8_d1", tp=0.50, hard=0.08, hold=1, trail=0.0),
        Exit("tp100_hs20_d2", tp=1.00, hard=0.20, hold=2, trail=0.0),
        Exit("tp100_hs20_d3", tp=1.00, hard=0.20, hold=3, trail=0.0),
        Exit("hold1_hs15", tp=None, hard=0.15, hold=1, trail=0.0),
        Exit("trail30_hs15_d4", tp=None, hard=0.15, hold=4, trail=0.30),
        Exit("trail15_hs10_d2", tp=None, hard=0.10, hold=2, trail=0.15),
    ]


SCORES = ("r1", "r1vol", "rs", "vol")


def _score(sig: Sig, name: str) -> float:
    if name == "r1":
        return sig.r1
    if name == "r1vol":
        return sig.r1 * sig.volx
    if name == "rs":
        return sig.r3 + sig.xs10
    return sig.volx


def _passes(sig: Sig, gate: Gate) -> bool:
    if sig.r1 < gate.r1_min or sig.volx < gate.vol_min:
        return False
    if sig.dayloc < gate.dayloc_min or sig.r3 < gate.r3_min or sig.xs10 < gate.xs_min:
        return False
    if sig.rngx < gate.rng_min:
        return False
    if gate.brk20 and not sig.brk20:
        return False
    if gate.trend and not sig.trend:
        return False
    if gate.coil and not sig.coil:
        return False
    return True


def resolve_bar(
    *,
    open_raw: float,
    o: float,
    h: float,
    l: float,
    c: float,
    hard: float,
    tp: float | None,
    slip: float = SLIP,
) -> tuple[str, float] | None:
    """Intraday stop / take-profit on one daily bar.

    Open gaps fill at the open. Otherwise an up-close (close >= open) checks
    the target before the stop; a down-close checks the stop first. That is
    the path we can justify from OHLC without inventing a tick tape.
    """
    stop_px = open_raw * (1.0 - hard) if hard > 0 else None
    tp_px = open_raw * (1.0 + tp) if tp is not None else None
    if stop_px is not None and o <= stop_px:
        return "hard_stop", o * (1.0 - slip)
    if tp_px is not None and o >= tp_px:
        return "take_profit", o * (1.0 - slip)
    up = c >= o
    if up:
        if tp_px is not None and h >= tp_px:
            return "take_profit", tp_px * (1.0 - slip)
        if stop_px is not None and l <= stop_px:
            return "hard_stop", stop_px * (1.0 - slip)
    else:
        if stop_px is not None and l <= stop_px:
            return "hard_stop", stop_px * (1.0 - slip)
        if tp_px is not None and h >= tp_px:
            return "take_profit", tp_px * (1.0 - slip)
    return None


def _sma_at(xs: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(xs[i - n + 1 : i + 1]) / float(n)


def _ret_at(xs: list[float], i: int, n: int) -> float | None:
    j = i - n
    if j < 0 or xs[j] <= 0:
        return None
    return xs[i] / xs[j] - 1.0


def _as_series(rows: list[Row]) -> Series:
    by = _by_date(rows)
    dates = sorted(by)
    return Series(
        dates=dates,
        o=[float(by[d][1]) for d in dates],
        h=[float(by[d][2]) for d in dates],
        l=[float(by[d][3]) for d in dates],
        c=[float(by[d][4]) for d in dates],
        v=[float(by[d][5]) for d in dates],
    )


def load_series(candle_dir: Path | str) -> dict[str, Series]:
    raw = _load_dir(Path(candle_dir))
    if "BTC" not in raw:
        btc_path = Path("data/residual_wet_candles/BTC.json")
        if btc_path.exists():
            raw["BTC"] = _load_dir(btc_path.parent)["BTC"]
    if "BTC" not in raw:
        raise ValueError("BTC candles required")
    return {base: _as_series(rows) for base, rows in raw.items()}


def _btc_risk_on(btc: Series) -> set[str]:
    on: set[str] = set()
    for i, day in enumerate(btc.dates):
        sma = _sma_at(btc.c, i, 50)
        if sma is not None and btc.c[i] > sma:
            on.add(day)
    return on


def build_signals(
    series: dict[str, Series],
    *,
    min_qvol: float = MIN_QVOL,
) -> list[Sig]:
    if "BTC" not in series:
        raise ValueError("BTC candles required")
    btc = series["BTC"]
    btc_r3: dict[str, float] = {}
    btc_r10: dict[str, float] = {}
    for i, day in enumerate(btc.dates):
        a = _ret_at(btc.c, i, 3)
        b = _ret_at(btc.c, i, 10)
        if a is not None:
            btc_r3[day] = a
        if b is not None:
            btc_r10[day] = b

    names = [b for b in series if b != "BTC"]
    out: list[Sig] = []
    for bi, base in enumerate(names):
        ser = series[base]
        n = len(ser.dates)
        for i in range(54, n - 1):
            day = ser.dates[i]
            if day not in btc_r10 or day not in btc_r3:
                continue
            r1 = _ret_at(ser.c, i, 1)
            r3 = _ret_at(ser.c, i, 3)
            r10 = _ret_at(ser.c, i, 10)
            if r1 is None or r3 is None or r10 is None:
                continue
            c = ser.c[i]
            if c <= 0 or ser.o[i + 1] <= 0:
                continue
            vol_ma = _sma_at(ser.v, i, 20)
            volx = (ser.v[i] / vol_ma) if vol_ma and vol_ma > 0 else 0.0
            qvol = ser.v[i] * c
            if qvol < min_qvol:
                continue
            s20 = _sma_at(ser.c, i, 20)
            s50 = _sma_at(ser.c, i, 50)
            if s20 is None or s50 is None or s20 <= 0:
                continue
            hi = ser.h[i]
            lo = ser.l[i]
            dayloc = ((c - lo) / (hi - lo)) if hi > lo else 0.5
            rng = (hi - lo) / c if c > 0 else 0.0
            rngs: list[float] = []
            for k in range(i - 19, i + 1):
                ck = ser.c[k]
                if ck > 0:
                    rngs.append((ser.h[k] - ser.l[k]) / ck)
            rng_ma = sum(rngs) / len(rngs) if rngs else 0.0
            rngx = (rng / rng_ma) if rng_ma > 0 else 0.0
            prior_hi = max(ser.h[i - 20 : i]) if i >= 20 else max(ser.h[:i])
            brk20 = c >= prior_hi
            span5_hi = max(ser.h[i - 4 : i + 1])
            span5_lo = min(ser.l[i - 4 : i + 1])
            span20_hi = max(ser.h[i - 19 : i + 1])
            span20_lo = min(ser.l[i - 19 : i + 1])
            span5 = (span5_hi / span5_lo - 1.0) if span5_lo > 0 else 0.0
            span20 = (span20_hi / span20_lo - 1.0) if span20_lo > 0 else 0.0
            coil = span20 > 1e-9 and span5 / span20 < 0.5
            trend = c > s20 > s50
            entry_o = ser.o[i + 1]
            out.append(
                Sig(
                    base=base,
                    signal=day,
                    entry=ser.dates[i + 1],
                    bi=bi,
                    ei=i + 1,
                    r1=float(r1),
                    r3=float(r3),
                    xs3=float(r3) - btc_r3[day],
                    xs10=float(r10) - btc_r10[day],
                    volx=float(volx),
                    dayloc=float(dayloc),
                    rngx=float(rngx),
                    gap=entry_o / c - 1.0,
                    brk20=bool(brk20),
                    trend=bool(trend),
                    coil=bool(coil),
                    hit50=(ser.h[i + 1] / entry_o - 1.0) >= 0.50,
                    hit50_close=(ser.c[i + 1] / entry_o - 1.0) >= 0.50,
                )
            )
    return out


def _index_bases(series: dict[str, Series]) -> list[Series]:
    return [series[b] for b in series if b != "BTC"]


def _book_sell(cash: float, qty: float, cost: float, px: float) -> tuple[float, float]:
    proceeds = qty * px
    fee_eur = proceeds * FEE
    pnl = proceeds - fee_eur - cost
    return cash + proceeds - fee_eur, pnl


def simulate(
    bases: list[Series],
    sigs: list[Sig],
    pick: dict[str, int],
    exit_: Exit,
    cal: list[str],
    *,
    book: float,
    arm_start: str,
    arm_end: str,
    keep_trades: bool = False,
) -> dict[str, Any]:
    """One-slot sleeve. ``pick`` maps signal-date → signal index."""
    cash = book
    pos: dict[str, Any] | None = None
    pending: int | None = None
    n_trades = 0
    n_hit = 0
    n_hit_close = 0
    peak = book
    max_dd = 0.0
    in_mkt = 0
    by_base: dict[str, float] = defaultdict(float)
    month: dict[str, float] = defaultdict(float)
    prev_eq = book
    closed: list[dict[str, Any]] = []
    started = False

    def _flatten_reason(reason: str, px: float, when: str) -> None:
        nonlocal cash, pos
        assert pos is not None
        cash, pnl = _book_sell(cash, float(pos["qty"]), float(pos["cost"]), px)
        by_base[str(pos["base"])] += pnl
        closed.append(
            {
                "date": when,
                "base": pos["base"],
                "pnl": round(pnl, 2),
                "reason": reason,
                "hit50": bool(pos["hit50"]),
                "signal": pos["signal"],
            }
        )
        pos = None

    for date in cal:
        if pos is not None and pos.get("sell_on") == date:
            ser = bases[int(pos["bi"])]
            bar = int(pos["bar"])
            if bar < len(ser.dates) and ser.dates[bar] == date:
                _flatten_reason(str(pos.get("exit_reason") or "time"), ser.o[bar] * (1.0 - SLIP), date)

        if pending is not None and pos is None:
            sig = sigs[pending]
            if date == sig.entry:
                ser = bases[sig.bi]
                o = ser.o[sig.ei]
                px = o * (1.0 + SLIP)
                notion = min(cash * 0.98, book)
                if notion >= MIN_NOTION and px > 0:
                    fee_eur = notion * FEE
                    qty = notion / px
                    cash -= notion + fee_eur
                    pos = {
                        "base": sig.base,
                        "bi": sig.bi,
                        "qty": qty,
                        "cost": notion + fee_eur,
                        "open_raw": o,
                        "peak": o,
                        "days": 0,
                        "bar": sig.ei,
                        "hit50": sig.hit50,
                        "signal": sig.signal,
                        "sell_on": None,
                        "exit_reason": None,
                    }
                    n_trades += 1
                    if sig.hit50:
                        n_hit += 1
                    if sig.hit50_close:
                        n_hit_close += 1
                pending = None
            elif date > sig.entry:
                pending = None

        if pos is not None and pos.get("sell_on") is None:
            ser = bases[int(pos["bi"])]
            bar = int(pos["bar"])
            if bar < len(ser.dates) and ser.dates[bar] == date:
                o, h, l, c = ser.o[bar], ser.h[bar], ser.l[bar], ser.c[bar]
                hit = resolve_bar(
                    open_raw=float(pos["open_raw"]),
                    o=o,
                    h=h,
                    l=l,
                    c=c,
                    hard=exit_.hard,
                    tp=exit_.tp,
                )
                if hit is not None:
                    _flatten_reason(hit[0], hit[1], date)
                else:
                    pos["peak"] = max(float(pos["peak"]), h)
                    pos["days"] = int(pos["days"]) + 1
                    nxt = bar + 1
                    trail_hit = exit_.trail > 0 and c <= float(pos["peak"]) * (1.0 - exit_.trail)
                    time_hit = int(pos["days"]) >= int(exit_.hold)
                    if trail_hit or time_hit:
                        pos["exit_reason"] = "trail" if trail_hit else "time"
                        if nxt < len(ser.dates):
                            pos["sell_on"] = ser.dates[nxt]
                            pos["bar"] = nxt
                        else:
                            _flatten_reason("eow", c * (1.0 - SLIP), date)
                    else:
                        pos["bar"] = nxt

        eq = cash
        if pos is not None:
            ser = bases[int(pos["bi"])]
            held_bar = int(pos["bar"]) - 1
            if 0 <= held_bar < len(ser.dates):
                eq += float(pos["qty"]) * ser.c[held_bar]
            in_mkt += 1
        in_window = arm_start <= date <= arm_end
        if in_window or pos is not None or (started and date <= _plus(arm_end, 6)):
            if in_window:
                started = True
            peak = max(peak, eq)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak)
            month[date[:7]] += eq - prev_eq
            prev_eq = eq

        if pos is None and pending is None and in_window:
            sig_i = pick.get(date, -1)
            if sig_i >= 0:
                pending = sig_i

    if pos is not None:
        ser = bases[int(pos["bi"])]
        prev_i = min(int(pos["bar"]), len(ser.dates) - 1)
        if pos.get("sell_on") is not None:
            prev_i = max(0, int(pos["bar"]) - 1)
        px = ser.c[prev_i] * (1.0 - SLIP)
        _flatten_reason("eow", px, ser.dates[prev_i])
        eq = cash
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        month[ser.dates[prev_i][:7]] += eq - prev_eq
        prev_eq = eq

    pnl = cash - book
    prec = (n_hit / n_trades) if n_trades else 0.0
    return {
        "pnl": round(pnl, 2),
        "end_eq": round(cash, 2),
        "max_dd": round(max_dd, 4),
        "trades": n_trades,
        "hits": n_hit,
        "hits_close": n_hit_close,
        "precision": round(prec, 4),
        "precision_close": round((n_hit_close / n_trades) if n_trades else 0.0, 4),
        "wilson": round(_wilson(n_hit, n_trades), 4) if n_trades else 0.0,
        "in_market_days": in_mkt,
        "by_base": {k: round(v, 2) for k, v in sorted(by_base.items(), key=lambda kv: -kv[1])},
        "months": {k: round(v, 2) for k, v in sorted(month.items())},
        "trades_detail": closed if keep_trades else [],
    }


def _build_pick(
    sigs: list[Sig],
    by_day: dict[str, list[int]],
    gate: Gate,
    score: str,
    *,
    require_btc: bool,
    gap_cap: float | None,
    btc_on: set[str],
) -> dict[str, int]:
    pick: dict[str, int] = {}
    for day, idxs in by_day.items():
        if require_btc and day not in btc_on:
            continue
        best_i = -1
        best_s = -1e18
        for i in idxs:
            sig = sigs[i]
            if gap_cap is not None and sig.gap > gap_cap:
                continue
            if not _passes(sig, gate):
                continue
            sc = _score(sig, score)
            if sc > best_s:
                best_s = sc
                best_i = i
        if best_i >= 0:
            pick[day] = best_i
    return pick


def _rule_precision(
    sigs: list[Sig],
    gate: Gate,
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    sel = [s for s in sigs if start <= s.signal <= end and _passes(s, gate)]
    n = len(sel)
    k = sum(1 for s in sel if s.hit50)
    kc = sum(1 for s in sel if s.hit50_close)
    return {
        "n": n,
        "k": k,
        "p50": round(k / n, 4) if n else 0.0,
        "p50_close": round(kc / n, 4) if n else 0.0,
        "wilson": round(_wilson(k, n), 4) if n else 0.0,
    }


def _oracle(sigs: list[Sig], *, start: str, end: str, book: float) -> dict[str, Any]:
    """Ceiling: one slot, enter the largest tradable +50% day, limit at +50%.

    Uses the entry-day high, so this is not a strategy. It is the cash a
    perfect picker would collect on this tape with the same fees.
    """
    by_entry: dict[str, list[Sig]] = defaultdict(list)
    for s in sigs:
        if start <= s.signal <= end:
            by_entry[s.entry].append(s)
    days = 0
    # Analytical fill: buy open*(1+slip), sell open*1.5*(1-slip), one name/day.
    notion = min(book * 0.98, book)
    unit = 0.0
    if notion >= MIN_NOTION:
        fill = 1.0 + SLIP
        sell = 1.50 * (1.0 - SLIP)
        qty = notion / fill
        proceeds = qty * sell
        unit = proceeds * (1.0 - FEE) - (notion + notion * FEE)
    # Distinct entry dates that contain at least one tradable +50%.
    event_days = 0
    for day, rows in by_entry.items():
        if any(r.hit50 for r in rows):
            event_days += 1
        days += 0
    return {
        "event_days": event_days,
        "pnl_if_perfect_tp50": round(event_days * unit, 2),
        "eur_per_hit": round(unit, 2),
        "note": (
            "One perfect +50% limit-fill per event day, no losses, "
            "fresh notion each day (the slot is free after the intraday target). "
            "Ignores same-bar path risk."
        ),
    }


def _specs() -> list[tuple[Gate, str, bool, float | None, Exit]]:
    gap_caps: tuple[float | None, ...] = (None, 0.10, 0.25)
    out: list[tuple[Gate, str, bool, float | None, Exit]] = []
    for gate in gates():
        for score in SCORES:
            for btc in (False, True):
                for gap in gap_caps:
                    for ex in exits():
                        out.append((gate, score, btc, gap, ex))
    return out


def _spec_name(gate: Gate, score: str, btc: bool, gap: float | None, ex: Exit) -> str:
    gap_s = "gapNA" if gap is None else f"gap{int(round(gap * 100))}"
    return f"{gate.name}__{score}__btc{int(btc)}__{gap_s}__{ex.name}"


def _calendar(series: dict[str, Series], sigs: list[Sig]) -> list[str]:
    days: set[str] = set()
    for ser in series.values():
        days.update(ser.dates)
    for s in sigs:
        days.add(s.signal)
        days.add(s.entry)
    return sorted(days)


def run_day50(
    *,
    candle_dir: Path | str = "data/ignition_expand_candles",
    book: float = 1_700.0,
    train_end: str = "2025-12-31",
    start_floor: str = "2024-06-15",
) -> dict[str, Any]:
    series = load_series(candle_dir)
    bases = _index_bases(series)
    # build_signals enumerates bases in the same order as _index_bases
    sigs = build_signals(series)
    if not sigs:
        raise ValueError("no signals")
    btc_on = _btc_risk_on(series["BTC"])
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(sigs):
        by_day[s.signal].append(i)
    cal = _calendar(series, sigs)
    last = series["BTC"].dates[-1]
    train_cal = [d for d in cal if start_floor <= d <= _plus(train_end, 6)]
    test_cal = [d for d in cal if train_end < d <= _plus(last, 6)]
    full_cal = [d for d in cal if start_floor <= d <= _plus(last, 6)]

    def _split_rate(start: str, end: str) -> dict[str, Any]:
        rows = [s for s in sigs if start <= s.signal <= end]
        n = len(rows)
        k = sum(1 for s in rows if s.hit50)
        kc = sum(1 for s in rows if s.hit50_close)
        days = {s.entry for s in rows if s.hit50}
        return {
            "n": n,
            "p50": round(k / n, 4) if n else 0.0,
            "p50_close": round(kc / n, 4) if n else 0.0,
            "k": k,
            "event_days": len(days),
            "wilson": round(_wilson(k, n), 4) if n else 0.0,
        }

    base_train = _split_rate(start_floor, train_end)
    base_test = _split_rate(_next_day_str(train_end), last)
    base_full = _split_rate(start_floor, last)

    rule_rows = []
    for gate in gates():
        tr = _rule_precision(sigs, gate, start=start_floor, end=train_end)
        te = _rule_precision(sigs, gate, start=_next_day_str(train_end), end=last)
        lift = (
            round(te["p50"] / base_test["p50"], 2)
            if base_test["p50"] > 0 and te["n"]
            else 0.0
        )
        rule_rows.append({"gate": gate.name, "train": tr, "test": te, "lift": lift})
    rule_rows.sort(key=lambda r: (-float(r["test"]["p50"]), -int(r["test"]["n"])))

    specs = _specs()
    print(
        f"day50 bases={len(bases)} signals={len(sigs)} specs={len(specs)} "
        f"train_p50={base_train['p50']:.4f} test_p50={base_test['p50']:.4f}",
        flush=True,
    )
    rows: list[dict[str, Any]] = []
    # Cache picks: gate/score/btc/gap is shared across exits.
    pick_cache: dict[tuple[str, str, bool, float | None], dict[str, int]] = {}
    for n_done, (gate, score, btc, gap, ex) in enumerate(specs, start=1):
        key = (gate.name, score, btc, gap)
        pick = pick_cache.get(key)
        if pick is None:
            pick = _build_pick(
                sigs, by_day, gate, score, require_btc=btc, gap_cap=gap, btc_on=btc_on
            )
            pick_cache[key] = pick
        if n_done % 400 == 0:
            print(f"  sim {n_done}/{len(specs)}", flush=True)
        tr = simulate(
            bases, sigs, pick, ex, train_cal, book=book, arm_start=start_floor, arm_end=train_end
        )
        te = simulate(
            bases,
            sigs,
            pick,
            ex,
            test_cal,
            book=book,
            arm_start=_next_day_str(train_end),
            arm_end=last,
        )
        rows.append(
            {
                "name": _spec_name(gate, score, btc, gap, ex),
                "gate": gate.name,
                "score": score,
                "btc": btc,
                "gap": gap,
                "exit": ex.name,
                "train": _compact(tr),
                "test": _compact(te),
            }
        )

    def _money_key(r: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(r["train"]["pnl"]),
            -float(r["train"]["max_dd"]),
            float(r["train"]["precision"]),
        )

    def _acc_key(r: dict[str, Any]) -> tuple[float, float, int]:
        return (
            float(r["train"]["wilson"]),
            float(r["train"]["pnl"]),
            int(r["train"]["trades"]),
        )

    eligible = [r for r in rows if int(r["train"]["trades"]) >= 12]
    acc_pool = [r for r in eligible if float(r["train"]["pnl"]) > 0 and int(r["train"]["hits"]) >= 3]
    money_pool = [r for r in eligible if float(r["train"]["max_dd"]) <= 0.70]
    acc = max(acc_pool, key=_acc_key) if acc_pool else (max(eligible, key=_acc_key) if eligible else None)
    money = max(money_pool, key=_money_key) if money_pool else None
    best_test = max(rows, key=lambda r: (float(r["test"]["pnl"]), float(r["test"]["precision"])))
    best_test_n = [
        r for r in rows if int(r["test"]["trades"]) >= 8 and int(r["train"]["trades"]) >= 8
    ]
    best_test_floor = (
        max(best_test_n, key=lambda r: float(r["test"]["pnl"])) if best_test_n else best_test
    )

    def _materialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        gate = next(g for g in gates() if g.name == row["gate"])
        ex = next(e for e in exits() if e.name == row["exit"])
        pick = _build_pick(
            sigs,
            by_day,
            gate,
            str(row["score"]),
            require_btc=bool(row["btc"]),
            gap_cap=row["gap"],
            btc_on=btc_on,
        )
        full = simulate(
            bases,
            sigs,
            pick,
            ex,
            full_cal,
            book=book,
            arm_start=start_floor,
            arm_end=last,
            keep_trades=True,
        )
        test = simulate(
            bases,
            sigs,
            pick,
            ex,
            test_cal,
            book=book,
            arm_start=_next_day_str(train_end),
            arm_end=last,
            keep_trades=True,
        )
        packed = dict(row)
        packed["full"] = _public_sim(full)
        packed["test_detail"] = _public_sim(test)
        return packed

    print("  materialize winners", flush=True)
    acc_m = _materialize(acc)
    money_m = _materialize(money) if money and (acc is None or money["name"] != acc["name"]) else (
        acc_m if money and acc and money["name"] == acc["name"] else _materialize(money)
    )
    best_m = _materialize(best_test_floor)

    oracle_test = _oracle(sigs, start=_next_day_str(train_end), end=last, book=book)
    oracle_full = _oracle(sigs, start=start_floor, end=last, book=book)
    oracle_train = _oracle(sigs, start=start_floor, end=train_end, book=book)

    viable_rules = [r for r in rule_rows if int(r["test"]["n"]) >= 20]
    best_rule = max(viable_rules, key=lambda r: float(r["test"]["p50"])) if viable_rules else None
    hit40 = [
        r
        for r in rule_rows
        if float(r["test"]["p50"]) >= 0.40 and int(r["test"]["n"]) >= 10
    ]

    return {
        "asof": datetime.now(UTC).isoformat(),
        "candle_dir": str(candle_dir),
        "book_eur": book,
        "n_bases": len(bases),
        "n_signals": len(sigs),
        "n_specs": len(rows),
        "train_end": train_end,
        "start": start_floor,
        "end": last,
        "label": "entry session high / open - 1 >= +50% (signal = prior close)",
        "costs": {"fee_per_side": FEE, "slip": SLIP, "sizing": "min(cash*0.98, book)"},
        "base_rate_train": base_train,
        "base_rate_test": base_test,
        "base_rate_full": base_full,
        "oracle_train": oracle_train,
        "oracle_test": oracle_test,
        "oracle_full": oracle_full,
        "best_rule_n20": best_rule,
        "rules_p50_ge_40_n10": hit40,
        "rule_top": rule_rows[:25],
        "walkforward_accuracy": acc_m,
        "walkforward_pnl": money_m,
        "best_test_pnl": best_m,
        "top_test_pnl": sorted(rows, key=lambda r: -float(r["test"]["pnl"]))[:15],
        "top_test_precision": sorted(
            [r for r in rows if int(r["test"]["trades"]) >= 8],
            key=lambda r: (-float(r["test"]["precision"]), -float(r["test"]["pnl"])),
        )[:15],
        "note": (
            "Walk-forward accuracy = hoogste train-Wilson op packs met train-PnL>0, "
            "≥3 hits en ≥12 trades. Walk-forward PnL = hoogste train-PnL met ≥12 trades "
            "en train-DD≤70%. Best test PnL is het maximum van de grid op 2026 "
            "(zoekmaximum, geen schone keuze)."
        ),
    }


def _compact(sim: dict[str, Any]) -> dict[str, Any]:
    return {
        "pnl": sim["pnl"],
        "max_dd": sim["max_dd"],
        "trades": sim["trades"],
        "hits": sim["hits"],
        "hits_close": sim["hits_close"],
        "precision": sim["precision"],
        "precision_close": sim["precision_close"],
        "wilson": sim["wilson"],
        "in_market_days": sim["in_market_days"],
    }


def _public_sim(sim: dict[str, Any]) -> dict[str, Any]:
    detail = list(sim.get("trades_detail") or [])
    detail_sorted = sorted(detail, key=lambda t: -float(t["pnl"]))
    pos = sum(float(t["pnl"]) for t in detail if float(t["pnl"]) > 0)
    top_base_share = 0.0
    bases = sim.get("by_base") or {}
    if pos > 0 and bases:
        top_pnl = max(bases.values())
        top_base_share = round(max(0.0, float(top_pnl)) / pos, 4)
    return {
        "pnl": sim["pnl"],
        "end_eq": sim["end_eq"],
        "max_dd": sim["max_dd"],
        "trades": sim["trades"],
        "hits": sim["hits"],
        "hits_close": sim["hits_close"],
        "precision": sim["precision"],
        "precision_close": sim["precision_close"],
        "wilson": sim["wilson"],
        "in_market_days": sim["in_market_days"],
        "by_base": bases,
        "months": sim.get("months") or {},
        "top_trades": detail_sorted[:8],
        "worst_trades": list(reversed(detail_sorted[-5:])) if detail_sorted else [],
        "top_base_share_of_gross_wins": top_base_share,
    }


def _plus(day: str, n: int) -> str:
    # ISO date + n days, used only to keep exit bars inside the calendar slice.
    dt = datetime.strptime(day, "%Y-%m-%d")
    from datetime import timedelta

    return (dt + timedelta(days=n)).strftime("%Y-%m-%d")


def _next_day_str(day: str) -> str:
    return _plus(day, 1)


def _fmt_pct(x: float) -> str:
    return f"{100.0 * float(x):.1f}%"


def _fmt_eur(x: float) -> str:
    return f"{float(x):+.0f}"


def _pack_line(row: dict[str, Any], window: str) -> str:
    w = row[window]
    return (
        f"`{row['name']}`  PnL **€{float(w['pnl']):+,.0f}**  "
        f"precision **{_fmt_pct(w['precision'])}**  "
        f"trades {w['trades']}  hits {w['hits']}  "
        f"DD {_fmt_pct(w['max_dd'])}"
    )


def to_markdown(payload: dict[str, Any]) -> str:
    bt = payload["base_rate_train"]
    be = payload["base_rate_test"]
    bf = payload["base_rate_full"]
    lines = [
        "# 1-dag +50% sleeve",
        "",
        f"asof `{payload['asof']}`  book €{payload['book_eur']:,.0f}  "
        f"bases **{payload['n_bases']}**  specs `{payload['n_specs']}`",
        "",
        payload["note"],
        "",
        f"Label: {payload['label']}.",
        "Kosten: 15 bp fee/side, 10 bp slip, vaste book-cap (winst schaalt het ticket niet boven €book).",
        "Eén slot. Signaal op de slotkoers, koop de volgende open. Geen munt-specifieke regels.",
        "",
        "## Basiskans",
        "",
        "Een willekeurige liquide naam, volgende sessie, high t.o.v. de open:",
        "",
        "| Split | signalen | P(high ≥ +50%) | P(close ≥ +50%) | dagen met ≥1 hit |",
        "|---|---:|---:|---:|---:|",
        f"| train ≤{payload['train_end']} | {bt['n']} | {_fmt_pct(bt['p50'])} | {_fmt_pct(bt['p50_close'])} | {bt['event_days']} |",
        f"| test 2026+ | {be['n']} | {_fmt_pct(be['p50'])} | {_fmt_pct(be['p50_close'])} | {be['event_days']} |",
        f"| full | {bf['n']} | {_fmt_pct(bf['p50'])} | {_fmt_pct(bf['p50_close'])} | {bf['event_days']} |",
        "",
        "## Plafond (perfecte picker, geen verliezen)",
        "",
        "Als het ene slot elke dag precies de munt pakt die die sessie +50% vanaf de open handelt, "
        f"en de limit op +50% vult: **€{payload['oracle_full']['eur_per_hit']:+.0f} per hit**.",
        "",
        "| Window | event-dagen | plafond-PnL |",
        "|---|---:|---:|",
        f"| train | {payload['oracle_train']['event_days']} | €{payload['oracle_train']['pnl_if_perfect_tp50']:+,.0f} |",
        f"| test 2026 | {payload['oracle_test']['event_days']} | €{payload['oracle_test']['pnl_if_perfect_tp50']:+,.0f} |",
        f"| full | {payload['oracle_full']['event_days']} | €{payload['oracle_full']['pnl_if_perfect_tp50']:+,.0f} |",
        "",
        "Dat plafond telt geen stop-outs en geen dagen waarop de +50% alleen een wick in een rode candle was.",
        "",
        "## Nauwkeurigheid van het signaal",
        "",
        "Hier telt élk signaal, niet alleen het ene slot. Test-precisie, n≥20.",
        "",
    ]
    best = payload.get("best_rule_n20")
    if best:
        lines.append(
            f"Beste gate (test n≥20): **`{best['gate']}`** → "
            f"P50 **{_fmt_pct(best['test']['p50'])}** "
            f"(n={best['test']['n']}, lift×{best['lift']}, "
            f"wilson≥{_fmt_pct(best['test']['wilson'])}). "
            f"Train P50 {_fmt_pct(best['train']['p50'])} (n={best['train']['n']})."
        )
    hit40 = payload.get("rules_p50_ge_40_n10") or []
    if hit40:
        lines.append("")
        lines.append(
            "Gates met test P50≥40% en n≥10: "
            + ", ".join(f"`{r['gate']}` ({_fmt_pct(r['test']['p50'])}, n={r['test']['n']})" for r in hit40)
        )
    else:
        lines.append("")
        lines.append("Geen gate haalt 40% test-precisie met n≥10. +50% in één dag blijft een zeldzame staart.")
    lines += [
        "",
        "| Gate | train n | train P50 | test n | test P50 | test P(close) | lift× |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("rule_top") or []:
        lines.append(
            f"| `{r['gate']}` | {r['train']['n']} | {_fmt_pct(r['train']['p50'])} | "
            f"{r['test']['n']} | {_fmt_pct(r['test']['p50'])} | {_fmt_pct(r['test']['p50_close'])} | {r['lift']} |"
        )

    lines += [
        "",
        "## Walk-forward — meest accurate sleeve die in-sample geld verdiende",
        "",
        "Gekozen op train (t/m 2025). 2026 is onaangeroerd.",
        "",
    ]
    acc = payload.get("walkforward_accuracy")
    if acc:
        lines += [
            f"- Train: {_pack_line(acc, 'train')}",
            f"- Test 2026: {_pack_line(acc, 'test')}",
            f"- Full: €{float(acc['full']['pnl']):+,.0f}  "
            f"precision {_fmt_pct(acc['full']['precision'])}  "
            f"DD {_fmt_pct(acc['full']['max_dd'])}  "
            f"trades {acc['full']['trades']}",
            "",
            _setup_block(acc),
        ]
        lines += _detail_block(acc["full"], "Full path")
    else:
        lines.append("Geen pack met ≥12 train-trades.")

    lines += [
        "",
        "## Walk-forward — hoogste train-PnL",
        "",
    ]
    money = payload.get("walkforward_pnl")
    if money:
        lines += [
            f"- Train: {_pack_line(money, 'train')}",
            f"- Test 2026: {_pack_line(money, 'test')}",
            f"- Full: €{float(money['full']['pnl']):+,.0f}  "
            f"precision {_fmt_pct(money['full']['precision'])}  "
            f"DD {_fmt_pct(money['full']['max_dd'])}  "
            f"trades {money['full']['trades']}",
            "",
            _setup_block(money),
        ]
        if acc and money["name"] == acc["name"]:
            lines.append("Zelfde pack als de accuracy-keuze.")
        else:
            lines += _detail_block(money["full"], "Full path")
    else:
        lines.append("Geen pack met ≥12 train-trades en DD≤70%.")

    lines += [
        "",
        "## Hoogste PnL in de zoektocht (2026, vers boek)",
        "",
        "Dit is het maximum over de hele grid op de testperiode. "
        "Het is het best behaalde test-cijfer, niet de walk-forward keuze.",
        "",
    ]
    best_te = payload.get("best_test_pnl")
    if best_te:
        lines += [
            f"- Test 2026: {_pack_line(best_te, 'test')}",
            f"- Train: {_pack_line(best_te, 'train')}",
            f"- Full: €{float(best_te['full']['pnl']):+,.0f}  "
            f"precision {_fmt_pct(best_te['full']['precision'])}  "
            f"DD {_fmt_pct(best_te['full']['max_dd'])}  "
            f"trades {best_te['full']['trades']}",
            "",
            _setup_block(best_te),
        ]
        lines += _detail_block(best_te["test_detail"], "Test 2026")
        lines += _detail_block(best_te["full"], "Full path van dat pack")

    lines += [
        "",
        "### Top 10 test-PnL",
        "",
        "| Pack | test PnL | test P50 | test DD | train PnL | train P50 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in (payload.get("top_test_pnl") or [])[:10]:
        lines.append(
            f"| `{r['name']}` | €{float(r['test']['pnl']):+,.0f} | {_fmt_pct(r['test']['precision'])} | "
            f"{_fmt_pct(r['test']['max_dd'])} | €{float(r['train']['pnl']):+,.0f} | "
            f"{_fmt_pct(r['train']['precision'])} |"
        )
    lines += [
        "",
        "### Top 10 test-precisie (sleeve, ≥8 trades)",
        "",
        "| Pack | test P50 | test trades | test PnL | train P50 | train PnL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in (payload.get("top_test_precision") or [])[:10]:
        lines.append(
            f"| `{r['name']}` | {_fmt_pct(r['test']['precision'])} | {r['test']['trades']} | "
            f"€{float(r['test']['pnl']):+,.0f} | {_fmt_pct(r['train']['precision'])} | "
            f"€{float(r['train']['pnl']):+,.0f} |"
        )
    lines += [
        "",
        "## Hoe te lezen",
        "",
        "1. De basiskans van een +50% sessie (high t.o.v. de open die je nog kunt kopen) ligt ver onder de 7-daagse +50% uit `RESULTS.md`.",
        "2. Een gate kan de kans een paar keer verhogen en toch ver van 'meestal goed' blijven. Precisie van het signaal en PnL van het slot zijn verschillende dingen: een zeldzame +50% betaalt de stops alleen als de win-rate én de exit kloppen.",
        "3. Het plafond laat zien hoeveel er op tafel ligt als elke event-dag perfect geraakt wordt. De sleeve-PnL daaronder is wat de tape met deze features echt afgeeft.",
        "4. Deel van de winst in één naam betekent dat het pad door een paar spikes loopt. De regel zelf blijft generiek (drempels op return, volume, range, trend).",
        "",
        "Reproduce:",
        "",
        "```bash",
        ".venv/bin/python -m bot.research.moonshot_preimage.day50",
        "```",
        "",
    ]
    return "\n".join(lines)


def _setup_block(row: dict[str, Any]) -> str:
    gap = row["gap"]
    gap_s = "geen" if gap is None else f"sla over als de open al >{100 * float(gap):.0f}% boven de signaal-slot noteert"
    btc_s = "alleen als BTC > SMA50" if row["btc"] else "geen BTC-filter"
    return (
        f"Opstelling: gate `{row['gate']}`, rank `{row['score']}`, {btc_s}, gap-filter: {gap_s}, "
        f"exit `{row['exit']}`."
    )


def _detail_block(sim: dict[str, Any], title: str) -> list[str]:
    lines = [
        "",
        f"### {title}",
        "",
        f"Top-naam aandeel in bruto winst: {_fmt_pct(sim.get('top_base_share_of_gross_wins') or 0)}.",
        "",
        "| Naam | PnL |",
        "|---|---:|",
    ]
    items = list((sim.get("by_base") or {}).items())[:8]
    if not items:
        lines.append("| — | — |")
    for base, pnl in items:
        lines.append(f"| {base} | €{float(pnl):+,.0f} |")
    lines += [
        "",
        "| Datum | Naam | Reden | hit50 | PnL |",
        "|---|---|---|---|---:|",
    ]
    shown = list(sim.get("top_trades") or []) + list(sim.get("worst_trades") or [])
    if not shown:
        lines.append("| — | — | — | — | — |")
    for t in shown:
        lines.append(
            f"| {t['date']} | {t['base']} | {t['reason']} | {t['hit50']} | €{float(t['pnl']):+,.0f} |"
        )
    months = sim.get("months") or {}
    if months:
        lines += ["", "| Maand | PnL |", "|---|---:|"]
        for k, v in months.items():
            if abs(float(v)) >= 1.0:
                lines.append(f"| {k} | €{float(v):+,.0f} |")
    return lines


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="1-day +50% sleeve search")
    p.add_argument("--candles", default="data/ignition_expand_candles")
    p.add_argument("--book", type=float, default=1700.0)
    p.add_argument("--train-end", default="2025-12-31")
    p.add_argument("--out", default="artifacts/day50_sleeve.json")
    p.add_argument("--md", default="artifacts/day50_sleeve.md")
    args = p.parse_args()
    payload = run_day50(candle_dir=args.candles, book=args.book, train_end=args.train_end)
    md = to_markdown(payload)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    Path(args.md).write_text(md, encoding="utf-8")
    pkg = Path(__file__).resolve().parent
    (pkg / "DAY50.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "DAY50.md").write_text(md, encoding="utf-8")
    acc = payload.get("walkforward_accuracy") or {}
    money = payload.get("walkforward_pnl") or {}
    best = payload.get("best_test_pnl") or {}
    print(
        f"WF acc test €{float((acc.get('test') or {}).get('pnl') or 0):+.0f} "
        f"P50={float((acc.get('test') or {}).get('precision') or 0):.1%}",
        flush=True,
    )
    print(
        f"WF pnl test €{float((money.get('test') or {}).get('pnl') or 0):+.0f} "
        f"full €{float((money.get('full') or {}).get('pnl') or 0):+.0f}",
        flush=True,
    )
    print(
        f"best test €{float((best.get('test') or {}).get('pnl') or 0):+.0f} "
        f"full €{float((best.get('full') or {}).get('pnl') or 0):+.0f} "
        f"{best.get('name')}",
        flush=True,
    )
    print(f"wrote {pkg / 'DAY50.md'}", flush=True)


if __name__ == "__main__":
    main()
