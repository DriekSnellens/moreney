"""Daily weight-based replay for bear-market sleeves.

A strategy maps the history through day ``t-1`` to target weights for day ``t``
(fraction of the book, negative = short). Positions are set at the open of day
``t`` and earn open(t) -> open(t+1). Costs:

* spot legs (weight > 0): Bitvavo taker ``SPOT_FEE`` + ``SPOT_SLIP`` per side
* perp legs (weight < 0): ``PERP_FEE`` + ``PERP_SLIP`` per side, plus funding
  (positive funding is received by the short, negative is paid), three
  settlements a day from the daily mean rate

Stops are evaluated on the close of day ``t`` against the entry open and act
at the next open. No per-coin branches.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SPOT_FEE = 0.0025
SPOT_SLIP = 0.0010
PERP_FEE = 0.0005
PERP_SLIP = 0.0005
DAY_MS = 86_400_000

Ohlc = Mapping[str, Sequence[Sequence[float]]]
Funding = Mapping[str, Mapping[str, float]]


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")


@dataclass
class Frame:
    """Aligned daily arrays per base over the BTC calendar."""

    dates: list[str]
    open: dict[str, list[float | None]]
    close: dict[str, list[float | None]]
    high: dict[str, list[float | None]]
    low: dict[str, list[float | None]]

    @classmethod
    def build(cls, ohlc: Ohlc) -> Frame:
        btc = ohlc["BTC"]
        dates = [day_of(r[0]) for r in btc]
        idx = {d: i for i, d in enumerate(dates)}
        n = len(dates)
        o: dict[str, list[float | None]] = {}
        c: dict[str, list[float | None]] = {}
        h: dict[str, list[float | None]] = {}
        lo: dict[str, list[float | None]] = {}
        for base, rows in ohlc.items():
            o[base] = [None] * n
            c[base] = [None] * n
            h[base] = [None] * n
            lo[base] = [None] * n
            for r in rows:
                i = idx.get(day_of(r[0]))
                if i is None:
                    continue
                o[base][i] = float(r[1])
                h[base][i] = float(r[2])
                lo[base][i] = float(r[3])
                c[base][i] = float(r[4])
        return cls(dates, o, c, h, lo)

    def closes(self, base: str, i: int, n: int) -> list[float]:
        """Last ``n`` closes through index ``i`` (inclusive), skipping gaps."""
        xs = [x for x in self.close.get(base, [])[max(0, i - n + 1) : i + 1] if x]
        return xs

    def ret(self, base: str, i: int, lookback: int, skip: int = 0) -> float | None:
        c = self.close.get(base)
        if not c:
            return None
        j = i - skip
        k = j - lookback
        if k < 0 or j < 0 or not c[j] or not c[k]:
            return None
        return c[j] / c[k] - 1.0

    def sma(self, base: str, i: int, n: int) -> float | None:
        xs = self.closes(base, i, n)
        return statistics.fmean(xs) if len(xs) >= n else None


@dataclass(frozen=True)
class BearSpec:
    name: str
    kind: str  # short_weakest | short_btc | pair | capitulation | none
    gate_sma: int = 50  # BTC below this SMA = bear regime (0 = always on)
    gate_mode: str = "below"  # below | above | always
    lookback: int = 15
    skip: int = 1
    top_n: int = 1
    mom_floor: float = -0.03  # short only names with lookback return <= floor
    require_below_own_sma: int = 50  # 0 = off
    rebalance_days: int = 7
    hard_stop: float = 0.10  # adverse move vs entry; 0 = off
    trail: float = 0.0  # give-back from peak favourable move; 0 = off
    gross: float = 1.0  # total gross exposure as fraction of book
    # capitulation (long spot) knobs
    drop_days: int = 3
    drop_pct: float = -0.12
    hold_days: int = 3
    take_profit: float = 0.06
    cap_base: str = "BTC"  # BTC | strongest (least-fallen desk name)
    cap_trigger: str = "BTC"  # BTC | median (median desk-alt drop over drop_days)
    universe: tuple[str, ...] = ()


@dataclass
class Leg:
    base: str
    weight: float
    entry_px: float
    entry_i: int
    peak_fav: float = 0.0


@dataclass
class Result:
    name: str
    pnl_eur: float
    max_dd_eur: float
    max_dd_pct: float
    n_trades: int
    days_active: int
    days_total: int
    funding_eur: float
    fees_eur: float
    by_year: dict[str, dict[str, float]] = field(default_factory=dict)
    curve: list[tuple[str, float]] = field(default_factory=list)


def _bear(frame: Frame, i: int, spec: BearSpec) -> bool:
    if spec.gate_mode == "always" or spec.gate_sma <= 0:
        return True
    sma = frame.sma("BTC", i, spec.gate_sma)
    c = frame.close["BTC"][i]
    if sma is None or not c:
        return False
    return c < sma if spec.gate_mode == "below" else c > sma


def _weakest(
    frame: Frame, i: int, spec: BearSpec, universe: Sequence[str]
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for base in universe:
        r = frame.ret(base, i, spec.lookback, spec.skip)
        if r is None or r > spec.mom_floor:
            continue
        if spec.require_below_own_sma > 0:
            sma = frame.sma(base, i, spec.require_below_own_sma)
            c = frame.close[base][i]
            if sma is None or not c or c >= sma:
                continue
        rows.append((base, r))
    rows.sort(key=lambda x: x[1])
    return rows[: spec.top_n]


def _strongest(frame: Frame, i: int, spec: BearSpec, universe: Sequence[str]) -> str | None:
    best: tuple[str, float] | None = None
    for base in universe:
        r = frame.ret(base, i, spec.drop_days, 0)
        if r is None:
            continue
        if best is None or r > best[1]:
            best = (base, r)
    return best[0] if best else None


def _cap_trigger(frame: Frame, i: int, spec: BearSpec, universe: Sequence[str]) -> float | None:
    if spec.cap_trigger != "median":
        return frame.ret("BTC", i, spec.drop_days, 0)
    rs = [r for b in universe if (r := frame.ret(b, i, spec.drop_days, 0)) is not None]
    return statistics.median(rs) if rs else None


def targets(
    frame: Frame, i: int, spec: BearSpec, universe: Sequence[str], state: dict[str, Any]
) -> dict[str, float]:
    """Target weights for the open of day ``i+1`` using data through close ``i``."""
    if spec.kind == "none":
        return {}
    bear = _bear(frame, i, spec)
    if spec.kind == "capitulation":
        if state.get("cap_hold", 0) > 0:
            return dict(state["cap_targets"])
        r = _cap_trigger(frame, i, spec, universe)
        if r is None or r > spec.drop_pct:
            return {}
        base = "BTC" if spec.cap_base == "BTC" else (_strongest(frame, i, spec, universe) or "BTC")
        return {base: spec.gross}
    if not bear:
        return {}
    due = state.get("last_reb", -(10**9)) <= i - spec.rebalance_days or not state.get("held")
    if not due:
        return dict(state.get("held") or {})
    out: dict[str, float] = {}
    if spec.kind == "short_btc":
        out["BTC"] = -spec.gross
    elif spec.kind == "short_weakest":
        picks = _weakest(frame, i, spec, universe)
        for base, _ in picks:
            out[base] = -spec.gross / max(1, len(picks))
    elif spec.kind == "pair":
        picks = _weakest(frame, i, spec, universe)
        if picks:
            half = spec.gross / 2.0
            out["BTC"] = half
            for base, _ in picks:
                out[base] = out.get(base, 0.0) - half / len(picks)
    if out:
        state["last_reb"] = i
    return out


def run(
    ohlc: Ohlc,
    spec: BearSpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    funding: Funding | None = None,
    universe: Sequence[str] = (),
) -> Result:
    frame = Frame.build(ohlc)
    uni = list(universe or spec.universe or [b for b in ohlc if b != "BTC"])
    legs: dict[str, Leg] = {}
    state: dict[str, Any] = {"held": {}}
    equity = book_eur
    peak = equity
    max_dd = 0.0
    n_trades = 0
    days_active = 0
    days_total = 0
    fund_total = 0.0
    fee_total = 0.0
    curve: list[tuple[str, float]] = []
    year_start: dict[str, float] = {}
    year_dd: dict[str, float] = {}
    year_peak: dict[str, float] = {}
    n = len(frame.dates)
    for i in range(1, n - 1):
        date = frame.dates[i]
        if date < start or date > end:
            continue
        y = date[:4]
        if y not in year_start:
            year_start[y] = equity
            year_peak[y] = equity
            year_dd[y] = 0.0
        days_total += 1
        # 1) stops on yesterday's close -> flatten at today's open
        want = dict(state.get("want") or {})
        for base, leg in list(legs.items()):
            c = frame.close[base][i - 1]
            if not c:
                continue
            fav = (c / leg.entry_px - 1.0) * (1 if leg.weight > 0 else -1)
            leg.peak_fav = max(leg.peak_fav, fav)
            stop = (spec.hard_stop > 0 and fav <= -spec.hard_stop) or (
                spec.trail > 0 and leg.peak_fav - fav >= spec.trail
            )
            if spec.kind == "capitulation":
                held_days = i - leg.entry_i
                if fav >= spec.take_profit or held_days >= spec.hold_days:
                    stop = True
                    state["cap_hold"] = 0
            if stop:
                want[base] = 0.0
        # 2) trade to targets at today's open
        for base in set(list(legs) + list(want)):
            tw = float(want.get(base, 0.0))
            cur = legs.get(base)
            cw = cur.weight if cur else 0.0
            if abs(tw - cw) < 1e-9:
                continue
            px = frame.open[base][i]
            if not px:
                continue
            fee = (SPOT_FEE + SPOT_SLIP) if (tw > 0 or cw > 0) else (PERP_FEE + PERP_SLIP)
            cost = abs(tw - cw) * equity * fee
            equity -= cost
            fee_total += cost
            n_trades += 1
            if abs(tw) < 1e-9:
                legs.pop(base, None)
            else:
                legs[base] = Leg(base, tw, px, i, 0.0)
        state["held"] = {b: leg.weight for b, leg in legs.items()}
        if spec.kind == "capitulation" and legs:
            state["cap_hold"] = 1
            state["cap_targets"] = dict(state["held"])
        # 3) earn open(i) -> open(i+1), plus funding on perp legs
        day_pnl = 0.0
        for base, leg in legs.items():
            o0 = frame.open[base][i]
            o1 = frame.open[base][i + 1]
            if not o0 or not o1:
                continue
            day_pnl += leg.weight * equity * (o1 / o0 - 1.0)
            if leg.weight < 0 and funding is not None:
                rate = float((funding.get(base) or {}).get(date, 0.0))
                f = abs(leg.weight) * equity * rate * 3.0
                day_pnl += f
                fund_total += f
        if legs:
            days_active += 1
        equity += day_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        year_peak[y] = max(year_peak[y], equity)
        year_dd[y] = max(year_dd[y], year_peak[y] - equity)
        curve.append((date, equity))
        # 4) decide tomorrow's targets from today's close
        state["want"] = targets(frame, i, spec, uni, state)
        if spec.kind == "capitulation" and not legs:
            state["cap_hold"] = 0
    by_year = {
        y: {
            "pnl_eur": round((next((e for d, e in reversed(curve) if d.startswith(y)), s) - s), 0),
            "max_dd_eur": round(year_dd[y], 0),
        }
        for y, s in year_start.items()
    }
    return Result(
        name=spec.name,
        pnl_eur=round(equity - book_eur, 2),
        max_dd_eur=round(max_dd, 2),
        max_dd_pct=round(max_dd / max(peak, 1e-9), 4),
        n_trades=n_trades,
        days_active=days_active,
        days_total=days_total,
        funding_eur=round(fund_total, 2),
        fees_eur=round(fee_total, 2),
        by_year=by_year,
        curve=curve,
    )


def phase_average(
    ohlc: Ohlc,
    spec: BearSpec,
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    funding: Funding | None = None,
    universe: Sequence[str] = (),
) -> Result:
    """Book split into ``rebalance_days`` equal tranches, each on its own clock phase.

    Approximated by averaging the equity curves of phase-shifted runs, which removes
    the start-day luck that a single clock carries.
    """
    frame = Frame.build(ohlc)
    first = next(i for i, d in enumerate(frame.dates) if d >= start)
    phases = max(1, spec.rebalance_days)
    runs = [
        run(
            ohlc,
            spec,
            start=frame.dates[first + k],
            end=end,
            book_eur=book_eur,
            funding=funding,
            universe=universe,
        )
        for k in range(phases)
    ]
    common = frame.dates[first + phases - 1]
    curves = [{d: e for d, e in r.curve if d >= common} for r in runs]
    dates = sorted(set.intersection(*(set(c) for c in curves)))
    curve = [(d, statistics.fmean(c[d] for c in curves)) for d in dates]
    peak = book_eur
    max_dd = 0.0
    year_start: dict[str, float] = {}
    year_peak: dict[str, float] = {}
    year_dd: dict[str, float] = {}
    prev = book_eur
    for d, e in curve:
        y = d[:4]
        if y not in year_start:
            year_start[y] = prev
            year_peak[y] = prev
            year_dd[y] = 0.0
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
        year_peak[y] = max(year_peak[y], e)
        year_dd[y] = max(year_dd[y], year_peak[y] - e)
        prev = e
    by_year = {
        y: {
            "pnl_eur": round(next(e for d, e in reversed(curve) if d.startswith(y)) - s, 0),
            "max_dd_eur": round(year_dd[y], 0),
        }
        for y, s in year_start.items()
    }
    return Result(
        name=f"{spec.name}@staggered{phases}",
        pnl_eur=round(curve[-1][1] - book_eur, 2) if curve else 0.0,
        max_dd_eur=round(max_dd, 2),
        max_dd_pct=round(max_dd / max(peak, 1e-9), 4),
        n_trades=round(statistics.fmean(r.n_trades for r in runs)),
        days_active=round(statistics.fmean(r.days_active for r in runs)),
        days_total=round(statistics.fmean(r.days_total for r in runs)),
        funding_eur=round(statistics.fmean(r.funding_eur for r in runs), 2),
        fees_eur=round(statistics.fmean(r.fees_eur for r in runs), 2),
        by_year=by_year,
        curve=curve,
    )


def pair(name: str, **kw: Any) -> BearSpec:
    """Dollar-neutral long BTC spot / short weakest desk alts (perps), no regime gate."""
    base: dict[str, Any] = {
        "kind": "pair",
        "gate_mode": "always",
        "mom_floor": 1.0,
        "require_below_own_sma": 0,
    }
    base.update(kw)
    return BearSpec(name=name, **base)


def sw(name: str, **kw: Any) -> BearSpec:
    """Outright short of the weakest desk alts, on only when BTC is below its SMA."""
    return BearSpec(name=name, kind="short_weakest", **kw)


def cap(name: str, **kw: Any) -> BearSpec:
    """Long spot after a capitulation drop; exits on take-profit or max hold."""
    return BearSpec(name=name, kind="capitulation", gate_mode="always", **kw)


def single_clock_specs() -> list[BearSpec]:
    """One clock, one phase: the naive grid."""
    out: list[BearSpec] = [
        BearSpec(name="short_btc_sma50", kind="short_btc"),
        BearSpec(name="short_btc_sma20", kind="short_btc", gate_sma=20),
        sw("sw1_sma20_L15", gate_sma=20),
        sw("sw1_sma20_L10", gate_sma=20, lookback=10),
        sw("sw2_sma20_L15", gate_sma=20, top_n=2),
        sw("sw2_sma20_L15_half", gate_sma=20, top_n=2, gross=0.5),
        sw("sw2_sma50_L15", gate_sma=50, top_n=2),
        sw("sw2_sma100_L15", gate_sma=100, top_n=2),
        sw("sw3_always_L30_reb14", gate_mode="always", top_n=3, lookback=30, rebalance_days=14),
    ]
    for reb in (7, 10, 14, 21):
        for n in (3, 5):
            for lb in (20, 30, 45):
                out.append(
                    pair(f"pair{n}_L{lb}_reb{reb}", top_n=n, lookback=lb, rebalance_days=reb)
                )
    out += [
        pair(
            "pair3_L30_reb14_bull",
            gate_mode="above",
            gate_sma=50,
            top_n=3,
            lookback=30,
            rebalance_days=14,
        ),
        pair(
            "pair3_L30_reb14_bear",
            gate_mode="below",
            gate_sma=50,
            top_n=3,
            lookback=30,
            rebalance_days=14,
        ),
        pair("pair3_L30_reb14_half", top_n=3, lookback=30, rebalance_days=14, gross=0.5),
        cap("cap_btc_3d12_hold3_tp6"),
        cap("cap_btc_3d12_hold5_tp8", hold_days=5, take_profit=0.08),
        cap("cap_btc_3d12_hold10_tp10", hold_days=10, take_profit=0.10),
        cap("cap_btc_3d10_hold5_tp8", drop_pct=-0.10, hold_days=5, take_profit=0.08),
        cap("cap_btc_5d12_hold7_tp10", drop_days=5, hold_days=7, take_profit=0.10),
        cap("cap_btc_1d7_hold3_tp5", drop_days=1, drop_pct=-0.07, hold_days=3, take_profit=0.05),
        cap("cap_strongest_3d12_hold3", cap_base="strongest"),
        cap("cap_med_3d12_hold5_tp8", cap_trigger="median", hold_days=5, take_profit=0.08),
        cap(
            "cap_med_3d15_hold5_tp8",
            cap_trigger="median",
            drop_pct=-0.15,
            hold_days=5,
            take_profit=0.08,
        ),
        cap(
            "cap_med_5d20_hold7_tp10",
            cap_trigger="median",
            drop_days=5,
            drop_pct=-0.20,
            hold_days=7,
            take_profit=0.10,
        ),
    ]
    return out


def staggered_specs() -> list[BearSpec]:
    """Candidates worth measuring without clock-phase luck."""
    out: list[BearSpec] = []
    for reb in (7, 10, 14, 21):
        for n in (3, 5):
            for lb in (20, 30):
                out.append(
                    pair(f"pair{n}_L{lb}_reb{reb}", top_n=n, lookback=lb, rebalance_days=reb)
                )
    out += [
        pair(
            "pair3_L30_reb14_bull",
            gate_mode="above",
            gate_sma=50,
            top_n=3,
            lookback=30,
            rebalance_days=14,
        ),
        pair(
            "pair3_L30_reb14_bear",
            gate_mode="below",
            gate_sma=50,
            top_n=3,
            lookback=30,
            rebalance_days=14,
        ),
        sw("sw2_sma20_L15", gate_sma=20, top_n=2),
        sw("sw1_sma20_L10", gate_sma=20, lookback=10),
        sw("sw2_sma100_L15", gate_sma=100, top_n=2),
    ]
    return out


PHASE_PROBE = pair("pair3_L30_reb14", top_n=3, lookback=30, rebalance_days=14)
