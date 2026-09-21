#!/usr/bin/env python3
"""Does the loop-winner mix scale linearly at €50k / €100k?

The published 1y figure (+€27.9k on a €20k book) used unit-return sleeves,
15 bps/side, and no Bitvavo depth. Live Donchian is a taker on Bitvavo EUR
alts with max_pos=2 — clips stack, ADV does not.

This replay keeps the same mix (sma20_50, Donchian 10/5 ± Friday, paper
short-weakest) and adds venue-realistic costs:

  - Bitvavo taker fee schedule (30d volume tiers)
  - 20 bps live taker-cross, wider on thin ADV
  - square-root temporary impact vs that day's Bitvavo quote ADV
  - 12% ADV participation cap (TWAP); leftover inventory retries next day
  - same-name stacking across Donchian sleeves shares the cap + impact
  - Friday flatten pays an extra weekend-spread haircut
  - shorts are a perp proxy (spot cannot short): 8× Bitvavo ADV, funding

Does not touch live. Writes artifacts/loop_mix_scale_capacity.json + .svg
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from artifacts.bear_harvest_hunter import pick_weakest
from artifacts.bear_market_strategy_sim import (
    CACHE_DIR,
    SHORT_FUNDING_PER_DAY,
    _align,
    _max_drawdown,
    _sma,
    load_daily,
)
from artifacts.expert_20k_desk import apply_expert, path_from_eq
from artifacts.missed_capacity_five_strats import ALL, ALT, _mom
from artifacts.multi_strat_20k_allocator import _date, _idx, path_from_book, summarize, to_returns
from bot.live.desk_allocator import REGIME_MAP, classify_sma20_50

OUT = Path(__file__).resolve().parent / "loop_mix_scale_capacity.json"
SVG = Path(__file__).resolve().parent / "loop_mix_scale_capacity.svg"

W0 = "2025-09-20"
W1 = "2026-09-19"
PUBLISHED_20K_PNL = 27_929.76
PUBLISHED_20K_DD = -6.5

# Live Donchian taker-cross (bot/live/momentum_donchian_runner.py).
LIVE_TAKER_CROSS = 0.002
# Original sleeve sim: 30 bps round-trip = 15 bps/side.
ORIG_FEE_SIDE = 0.0015
# Daily TWAP participation cap on that venue's quote ADV.
MAX_PART_SPOT = 0.12
MAX_PART_PERP = 0.12
# Perp books are deeper than Bitvavo EUR; still not Binance-optimistic.
PERP_ADV_MULT = 8.0
# Almgren-style temporary impact: k * sigma * sqrt(Q/ADV).
IMPACT_K = 0.75
FRIDAY_EXTRA_SPREAD = 0.0015
MIN_NOTIONAL = 50.0
WEIGHT = 0.4
LOOKBACK_SIGMA = 20

# Bitvavo Category A taker (https://www.bitvavo.com/en/fees), 30d crypto volume.
BITVAVO_TAKER: tuple[tuple[float, float], ...] = (
    (0.0, 0.0025),
    (100_000.0, 0.0020),
    (250_000.0, 0.0018),
    (500_000.0, 0.0016),
    (1_000_000.0, 0.0014),
    (2_500_000.0, 0.0012),
    (5_000_000.0, 0.0010),
    (10_000_000.0, 0.0008),
    (25_000_000.0, 0.0004),
    (100_000_000.0, 0.0002),
)

DONCH = {
    "donch_fri10": {"ch": 10, "exit_n": 5, "friday": True},
    "donch10": {"ch": 10, "exit_n": 5, "friday": False},
    "donch_fri": {"ch": 20, "exit_n": 10, "friday": True},
}

SHORT_CFG = dict(
    lookback=15,
    top_n=1,
    floor=-0.03,
    skip=1,
    bounce=0.02,
    reb=7,
    stop=0.10,
    max_weight=0.5,
)


Mode = Literal["naive", "original", "realistic"]


def bitvavo_taker_fee(volume_30d: float) -> float:
    fee = BITVAVO_TAKER[0][1]
    for thresh, f in BITVAVO_TAKER:
        if volume_30d >= thresh:
            fee = f
    return fee


def half_spread(adv_eur: float, *, floor: float = LIVE_TAKER_CROSS) -> float:
    """Live 20 bps cross on liquid names; widens as 1/sqrt(ADV) on thin alts."""
    if adv_eur <= 1.0:
        return 0.008
    wide = 0.002 * math.sqrt(1_000_000.0 / adv_eur)
    return min(0.008, max(floor, wide))


def daily_sigma(closes: list[float], i: int, n: int = LOOKBACK_SIGMA) -> float:
    if i < 2:
        return 0.04
    start = max(1, i - n + 1)
    rets = [
        closes[j] / closes[j - 1] - 1.0
        for j in range(start, i + 1)
        if closes[j - 1] > 0
    ]
    if len(rets) < 5:
        return 0.04
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return max(0.012, math.sqrt(var))


def impact_frac(clip_eur: float, adv_eur: float, sigma: float, *, k: float = IMPACT_K) -> float:
    part = clip_eur / max(adv_eur, 1.0)
    return k * sigma * math.sqrt(part)


def capped_fill(want_eur: float, adv_eur: float, *, max_part: float = MAX_PART_SPOT) -> float:
    if want_eur <= 0:
        return 0.0
    cap = max_part * max(adv_eur, 1.0)
    return min(want_eur, cap)


def exec_cost(
    fill_eur: float,
    adv_eur: float,
    sigma: float,
    fee: float,
    *,
    friday: bool = False,
    venue: Literal["spot", "perp"] = "spot",
) -> dict[str, float]:
    """One-way fractional cost on the filled notional."""
    depth = adv_eur * (PERP_ADV_MULT if venue == "perp" else 1.0)
    spread = half_spread(depth, floor=0.0008 if venue == "perp" else LIVE_TAKER_CROSS)
    if friday:
        spread += FRIDAY_EXTRA_SPREAD
    imp = impact_frac(fill_eur, depth, sigma)
    part = fill_eur / max(depth, 1.0)
    return {
        "fee": fee,
        "spread": spread,
        "impact": imp,
        "total": fee + spread + imp,
        "part": part,
        "depth": depth,
    }


@dataclass
class Lot:
    sleeve: str
    base: str
    side: Literal["long", "short"]
    qty: float
    entry: float
    opened_i: int


@dataclass
class FillStats:
    intended_eur: float = 0.0
    filled_eur: float = 0.0
    fees_eur: float = 0.0
    spread_eur: float = 0.0
    impact_eur: float = 0.0
    cap_hits: int = 0
    fills: int = 0
    stacked_days: int = 0
    funding_eur: float = 0.0
    cap_by_base: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    part_samples: list[float] = field(default_factory=list)
    vol_30d_peak: float = 0.0
    taker_fee_end: float = 0.0


def _weekday(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).weekday()


class CostBook:
    """Fee/impact-aware copy of the unit-sleeve Book used in the +€28k search."""

    def __init__(
        self,
        cash: float,
        *,
        mode: Mode,
        vol: dict[str, list[float]],
        closes: dict[str, list[float]],
        venue: Literal["spot", "perp"] = "spot",
        stack: float = 1.0,
        fee_side: float | None = None,
    ) -> None:
        self.cash = float(cash)
        self.pos: dict[str, tuple[float, float, float]] = {}
        self.eq: list[float] = []
        self.mode = mode
        self.vol = vol
        self.closes = closes
        self.venue = venue
        self.stack = stack
        self.i = 0
        self.friday = False
        self.fee_side = fee_side
        self.intended = 0.0
        self.filled = 0.0
        self.cost_eur = 0.0
        self.cap_hits = 0
        self.trades = 0
        self.wins = 0

    def mark(self, px: dict[str, float]) -> float:
        u = nsum = 0.0
        for b, (n, e, _) in self.pos.items():
            p = px.get(b, e)
            u += n * (p / e - 1.0)
            nsum += n
        return self.cash + nsum + u

    def snapshot(self, px: dict[str, float]) -> None:
        self.eq.append(self.mark(px))

    def bump_peaks(self, highs: dict[str, float]) -> None:
        for b in list(self.pos):
            n, e, peak = self.pos[b]
            self.pos[b] = (n, e, max(peak, highs.get(b, peak)))

    def _hair(self, base: str, notional: float) -> tuple[float, float]:
        want = max(notional, 0.0)
        adv = max(float(self.vol[base][self.i]), 1.0)
        if self.mode == "naive":
            return want, 0.0
        if self.mode == "original":
            return want, float(self.fee_side if self.fee_side is not None else ORIG_FEE_SIDE)
        stacked = want * self.stack
        depth = adv * (PERP_ADV_MULT if self.venue == "perp" else 1.0)
        stacked_fill = capped_fill(stacked, depth, max_part=MAX_PART_SPOT if self.venue == "spot" else MAX_PART_PERP)
        if stacked_fill + 1e-9 < stacked:
            self.cap_hits += 1
        fill = stacked_fill / self.stack if self.stack else stacked_fill
        sigma = daily_sigma(self.closes[base], self.i)
        fee = float(self.fee_side if self.fee_side is not None else 0.0025)
        cost = exec_cost(stacked_fill, adv, sigma, fee, friday=self.friday, venue=self.venue)
        return fill, float(cost["total"])

    def open(self, b: str, price: float, notional: float) -> bool:
        if b in self.pos or notional < MIN_NOTIONAL or notional > self.cash or price <= 0:
            return False
        self.intended += notional
        fill, hair = self._hair(b, notional)
        if fill < MIN_NOTIONAL:
            return False
        spent = fill + fill * hair
        if spent > self.cash:
            fill = self.cash / (1.0 + hair) if hair > -0.5 else 0.0
            if fill < MIN_NOTIONAL:
                return False
            spent = self.cash
        self.filled += fill
        self.cost_eur += fill * hair
        self.cash -= spent
        self.pos[b] = (fill * (1.0 - hair), price, price)
        return True

    def close(self, b: str, price: float) -> float:
        n, e, _ = self.pos.pop(b)
        self.intended += n
        fill, hair = self._hair(b, n)
        frac = fill / n if n else 1.0
        ret = price / e - 1.0
        net = n * frac * ret - n * frac * (2.0 * hair)
        # If capped, keep leftover
        if frac < 0.999 and n * (1.0 - frac) >= MIN_NOTIONAL:
            self.pos[b] = (n * (1.0 - frac), e, e)
        self.cash += n * frac + net
        self.filled += n * frac
        self.cost_eur += n * frac * (2.0 * hair) / 2.0
        self.trades += 1
        self.wins += int(net > 0)
        return net


def _fee_side_for_book(book: float, mode: Mode) -> float:
    if mode == "original":
        return ORIG_FEE_SIDE
    if mode == "naive":
        return 0.0
    # Friday Donchian turnover: ~€90k / 30d at €20k, ~€450k at €100k.
    if book >= 100_000:
        return 0.0018
    if book >= 50_000:
        return 0.0020
    return 0.0025


def sim_donch_cost(
    data: dict[str, Any],
    i0: int,
    i1: int,
    *,
    book: float,
    mode: Mode,
    ch: int,
    exit_n: int,
    friday_flat: bool,
    stack: float,
    max_pos: int = 2,
    w: float = WEIGHT,
    btc_sma: int = 50,
) -> CostBook:
    ts, closes, highs, lows, vol = data["ts"], data["closes"], data["highs"], data["lows"], data["vol"]
    sma_btc = _sma(closes["BTC"], btc_sma)
    book_obj = CostBook(
        book,
        mode=mode,
        vol=vol,
        closes=closes,
        venue="spot",
        stack=stack,
        fee_side=_fee_side_for_book(book, mode),
    )
    for i in range(i0, i1 + 1):
        book_obj.i = i
        book_obj.friday = friday_flat and _weekday(ts[i]) >= 4
        px = {b: closes[b][i] for b in ALT}
        book_obj.bump_peaks({b: highs[b][i] for b in book_obj.pos if b in highs})
        btc_ok = sma_btc[i] is not None and closes["BTC"][i] > sma_btc[i]
        if book_obj.friday and book_obj.pos:
            for b in list(book_obj.pos):
                book_obj.close(b, px[b])
            book_obj.snapshot(px)
            continue
        for b in list(book_obj.pos):
            if i >= exit_n:
                ll = min(lows[b][i - exit_n : i])
                if lows[b][i] <= ll:
                    book_obj.close(b, px[b])
        if btc_ok and len(book_obj.pos) < max_pos and i >= ch:
            cands = []
            for b in ALT:
                if b in book_obj.pos:
                    continue
                hh = max(highs[b][i - ch : i])
                if highs[b][i] > hh:
                    mom = _mom(closes[b], i, ch, 0) or 0.0
                    cands.append((mom, b))
            cands.sort(reverse=True)
            eq = book_obj.mark(px)
            for _, b in cands:
                if len(book_obj.pos) >= max_pos:
                    break
                book_obj.open(b, px[b], min(eq * w, book_obj.cash * 0.95))
        book_obj.snapshot(px)
    for b in list(book_obj.pos):
        book_obj.close(b, closes[b][i1])
    if book_obj.eq:
        book_obj.eq[-1] = book_obj.cash
    return book_obj


def sim_short_cost(
    data: dict[str, Any],
    i0: int,
    i1: int,
    *,
    book: float,
    mode: Mode,
) -> list[float]:
    closes = data["closes"]
    vol = data["vol"]
    btc = closes["BTC"]
    sma20 = _sma(btc, 20)
    cash = float(book)
    shorts: dict[str, tuple[float, float, float]] = {}
    eq_path: list[float] = []
    last_reb = -10**9
    fee_side = _fee_side_for_book(book, mode)
    dummy = CostBook(book, mode=mode, vol=vol, closes=closes, venue="perp", stack=1.0, fee_side=fee_side)

    def equity_now(i: int) -> float:
        u = sum(n * (e - closes[b][i]) / e for b, (n, e, _) in shorts.items())
        return cash + sum(n for n, _, _ in shorts.values()) + u

    def close_one(b: str, i: int) -> None:
        nonlocal cash
        n, e, _ = shorts.pop(b)
        dummy.i = i
        dummy.friday = False
        fill, hair = dummy._hair(b, n)
        frac = fill / n if n else 1.0
        ret = (e - closes[b][i]) / e
        net = n * frac * ret - n * frac * (2.0 * hair)
        cash += n * frac + net
        if frac < 0.999 and n * (1.0 - frac) >= MIN_NOTIONAL:
            shorts[b] = (n * (1.0 - frac), e, 0.0)

    for i in range(i0, i1 + 1):
        dummy.i = i
        if mode == "realistic":
            for b, (n, e, _) in list(shorts.items()):
                cash += n * SHORT_FUNDING_PER_DAY
        for b in list(shorts):
            n, e, peak = shorts[b]
            ret = (e - closes[b][i]) / e
            peak = max(peak, ret)
            shorts[b] = (n, e, peak)
            if ret <= -float(SHORT_CFG["stop"]):
                close_one(b, i)
        gate_ok = sma20[i] is not None and btc[i] < sma20[i]
        if shorts and not gate_ok:
            for b in list(shorts):
                close_one(b, i)
            last_reb = i
        due = i - last_reb >= int(SHORT_CFG["reb"]) or last_reb < 0
        if due:
            for b in list(shorts):
                close_one(b, i)
            if gate_ok and cash > MIN_NOTIONAL:
                picks = pick_weakest(
                    closes,
                    btc,
                    i,
                    lookback=int(SHORT_CFG["lookback"]),
                    top_n=int(SHORT_CFG["top_n"]),
                    floor=float(SHORT_CFG["floor"]),
                    skip=int(SHORT_CFG["skip"]),
                    lookback2=0,
                    floor2=0.0,
                    mode="mom",
                    bounce=float(SHORT_CFG["bounce"]),
                    below_sma=0,
                    weight_mode="equal",
                )
                if picks:
                    deploy_n = cash
                    wsum = sum(w for _, w in picks) or 1.0
                    for b, w in picks:
                        ww = min(w / wsum, float(SHORT_CFG["max_weight"]))
                        notional = deploy_n * ww
                        dummy.i = i
                        fill, hair = dummy._hair(b, notional)
                        if fill < MIN_NOTIONAL or fill + fill * hair > cash:
                            continue
                        cash -= fill + fill * hair
                        shorts[b] = (fill * (1.0 - hair), closes[b][i], 0.0)
            last_reb = i
        eq_path.append(equity_now(i))
    if shorts:
        for b in list(shorts):
            close_one(b, i1)
        eq_path[-1] = cash
    return eq_path


def simulate_unit_mix(
    data: dict[str, Any],
    *,
    book: float,
    i0: int,
    i1: int,
    mode: Mode,
    max_pos: int = 2,
) -> dict[str, Any]:
    """Same accounting as the +€28k search, clips scaled with book, costs optional."""
    ts = data["ts"]
    dates = [_date(ts[i]) for i in range(i0, i1 + 1)]
    # Two 10d Donchians often buy the same breakout; share Bitvavo ADV.
    stack = 1.8 if mode == "realistic" else 1.0
    d10 = sim_donch_cost(
        data, i0, i1, book=book, mode=mode, ch=10, exit_n=5, friday_flat=False, stack=stack, max_pos=max_pos
    )
    d10f = sim_donch_cost(
        data, i0, i1, book=book, mode=mode, ch=10, exit_n=5, friday_flat=True, stack=stack, max_pos=max_pos
    )
    d20f = sim_donch_cost(
        data, i0, i1, book=book, mode=mode, ch=20, exit_n=10, friday_flat=True, stack=1.0, max_pos=max_pos
    )
    sh = sim_short_cost(data, i0, i1, book=book, mode=mode)
    paths = {
        "donch10": path_from_book(ts, i0, i1, d10),
        "donch_fri10": path_from_book(ts, i0, i1, d10f),
        "donch_fri": path_from_book(ts, i0, i1, d20f),
        "short_weakest": path_from_eq(ts, i0, i1, sh),
        "cash": {d: book for d in dates},
    }
    rets = {k: to_returns(v, book=book) for k, v in paths.items()}
    regime_of = {
        _date(ts[i]): classify_sma20_50(data["closes"]["BTC"][: i + 1])["label"] for i in range(i0, i1 + 1)
    }
    path, rows = apply_expert(rets, dates, regime_w=REGIME_MAP, regime_of=regime_of, book=book)
    st = summarize(path, rows, book=book)
    intended = d10.intended + d10f.intended + d20f.intended
    filled = d10.filled + d10f.filled + d20f.filled
    cap = d10.cap_hits + d10f.cap_hits + d20f.cap_hits
    costs = d10.cost_eur + d10f.cost_eur + d20f.cost_eur
    return {
        "book_eur": book,
        "mode": mode,
        "compound": False,
        "max_pos": max_pos,
        "pnl_eur": st["pnl_eur"],
        "end_eur": st.get("end_eur", book + st["pnl_eur"]),
        "return_pct": round(100.0 * st["pnl_eur"] / book, 2),
        "max_dd_eur": st.get("max_dd_eur"),
        "max_dd_pct": st["max_dd_pct"],
        "calmar": st["calmar"],
        "worst_month": st.get("worst_month"),
        "months": st.get("months"),
        "path": [{"date": r["date"], "equity_eur": r["equity_eur"], "regime": r.get("regime")} for r in rows],
        "fill_ratio": round(filled / intended, 4) if intended else 1.0,
        "cap_hits": cap,
        "sleeve_cost_eur": round(costs, 2),
        "unit_ends": {
            "donch10": round(d10.eq[-1], 2) if d10.eq else None,
            "donch_fri10": round(d10f.eq[-1], 2) if d10f.eq else None,
            "donch_fri": round(d20f.eq[-1], 2) if d20f.eq else None,
            "short_weakest": round(sh[-1], 2) if sh else None,
        },
        "variant": "unit_mix",
    }


def load_aligned(days: int = 430) -> dict[str, Any]:
    series = load_daily(ALL, days=days)
    ts, closes = _align(series, ALL)
    highs: dict[str, list[float]] = {}
    lows: dict[str, list[float]] = {}
    vol: dict[str, list[float]] = {}
    for b in ALL:
        idx = {t: i for i, t in enumerate(series[b].ts)}
        highs[b] = [series[b].h[idx[t]] for t in ts]
        lows[b] = [series[b].l[idx[t]] for t in ts]
        cache = CACHE_DIR / f"{b}-EUR-1d.json"
        by_t: dict[int, float] = {}
        if cache.exists():
            rows = json.loads(cache.read_text())
            for r in rows:
                by_t[int(r[0])] = float(r[5]) * float(r[4])
        vol[b] = [float(by_t.get(t, 0.0)) for t in ts]
    return {"ts": ts, "closes": closes, "highs": highs, "lows": lows, "vol": vol}


def _mark(lots: list[Lot], px: dict[str, float]) -> float:
    u = 0.0
    for lot in lots:
        p = px.get(lot.base, lot.entry)
        if lot.side == "long":
            u += lot.qty * p
        else:
            u += lot.qty * (2.0 * lot.entry - p)
    return u


def _deployed(lots: list[Lot], sleeve: str, px: dict[str, float]) -> float:
    s = 0.0
    for lot in lots:
        if lot.sleeve != sleeve:
            continue
        p = px.get(lot.base, lot.entry)
        s += lot.qty * p
    return s


def simulate_mix(
    data: dict[str, Any],
    *,
    book: float,
    i0: int,
    i1: int,
    mode: Mode = "realistic",
    compound: bool = False,
    max_pos: int | None = None,
) -> dict[str, Any]:
    ts = data["ts"]
    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    vol = data["vol"]
    slots = int(max_pos) if max_pos is not None else 2

    cash = float(book)
    lots: list[Lot] = []
    stats = FillStats()
    vol_30d: deque[tuple[int, float]] = deque()
    rolled = 0.0
    last_short_reb = -10**9
    path: list[dict[str, Any]] = []
    sma50_btc = _sma(closes["BTC"], 50)

    def px_at(i: int) -> dict[str, float]:
        return {b: closes[b][i] for b in ALL}

    def rolling_fee() -> float:
        if mode == "original":
            return ORIG_FEE_SIDE
        if mode == "naive":
            return 0.0
        return bitvavo_taker_fee(rolled)

    def note_volume(i: int, eur: float) -> None:
        nonlocal rolled
        if eur <= 0:
            return
        vol_30d.append((i, eur))
        rolled += eur
        cutoff = i - 30
        while vol_30d and vol_30d[0][0] < cutoff:
            _, old = vol_30d.popleft()
            rolled -= old
        stats.vol_30d_peak = max(stats.vol_30d_peak, rolled)

    def venue_for(side: str) -> Literal["spot", "perp"]:
        if mode != "realistic":
            return "spot"
        return "perp" if side == "short" else "spot"

    def max_part_for(side: str) -> float:
        if mode != "realistic":
            return 1.0
        return MAX_PART_PERP if side == "short" else MAX_PART_SPOT

    def apply_fills(i: int, orders: list[dict[str, Any]], *, friday: bool) -> None:
        """orders: sleeve, base, side long/short, action open/close, lot?, want_eur."""
        nonlocal cash
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for od in orders:
            groups[(od["base"], od["action"], od["side"])].append(od)

        stacked = {
            base
            for (base, act, _side), grp in groups.items()
            if act == "open" and len({g["sleeve"] for g in grp}) > 1
        }
        if stacked:
            stats.stacked_days += 1

        for (base, action, side), grp in groups.items():
            px = closes[base][i]
            if px <= 0:
                continue
            want = 0.0
            for od in grp:
                if action == "close":
                    want += od["lot"].qty * px
                else:
                    want += float(od["want_eur"])
            if want < 1.0:
                continue
            adv = max(float(vol[base][i]), 1.0)
            depth = adv * (PERP_ADV_MULT if venue_for(side) == "perp" else 1.0)
            fill_eur = capped_fill(want, depth, max_part=max_part_for(side)) if mode == "realistic" else want
            if fill_eur + 1e-9 < want:
                stats.cap_hits += 1
                stats.cap_by_base[base] += 1
            if fill_eur < MIN_NOTIONAL and action == "open":
                continue
            frac = fill_eur / want if want else 0.0
            sigma = daily_sigma(closes[base], i)
            fee = rolling_fee()
            if mode == "naive":
                cost = {"fee": 0.0, "spread": 0.0, "impact": 0.0, "total": 0.0, "part": fill_eur / max(depth, 1.0)}
            elif mode == "original":
                cost = {"fee": fee, "spread": 0.0, "impact": 0.0, "total": fee, "part": fill_eur / max(depth, 1.0)}
            else:
                cost = exec_cost(fill_eur, adv, sigma, fee, friday=friday, venue=venue_for(side))
            hair = float(cost["total"])
            stats.intended_eur += want
            stats.filled_eur += fill_eur
            stats.fees_eur += fill_eur * cost["fee"]
            stats.spread_eur += fill_eur * cost["spread"]
            stats.impact_eur += fill_eur * cost["impact"]
            stats.fills += 1
            stats.part_samples.append(cost["part"])
            note_volume(i, fill_eur)

            if action == "open":
                spent = fill_eur * (1.0 + hair) if side == "short" else fill_eur
                if spent > cash:
                    if cash < MIN_NOTIONAL:
                        continue
                    scale = cash / spent
                    fill_eur *= scale
                    spent = cash
                    frac *= scale
                cash -= spent
                if side == "long":
                    fill_px = px * (1.0 + hair)
                    qty_total = fill_eur / fill_px if fill_px > 0 else 0.0
                    entry = fill_px
                else:
                    qty_total = fill_eur / px
                    entry = px
                for od in grp:
                    q = qty_total * (float(od["want_eur"]) / want) if want else 0.0
                    if q * px < MIN_NOTIONAL * 0.5:
                        continue
                    lots.append(
                        Lot(
                            sleeve=od["sleeve"],
                            base=base,
                            side=side,
                            qty=q,
                            entry=entry,
                            opened_i=i,
                        )
                    )
            else:
                # Long sell / short cover both pay the haircut against you.
                fill_px = px * (1.0 - hair) if side == "long" else px * (1.0 + hair)
                for od in grp:
                    lot: Lot = od["lot"]
                    q = lot.qty * frac
                    if q <= 0:
                        continue
                    if lot.side == "long":
                        cash += q * fill_px
                    else:
                        cash += q * lot.entry + q * (lot.entry - fill_px)
                    lot.qty -= q
            lots[:] = [lt for lt in lots if lt.qty > 1e-12]

        leftover = []
        for lt in lots:
            notion = lt.qty * closes[lt.base][i]
            if notion >= MIN_NOTIONAL * 0.25:
                leftover.append(lt)
                continue
            p = closes[lt.base][i]
            if lt.side == "long":
                cash += lt.qty * p
            else:
                cash += lt.qty * lt.entry + lt.qty * (lt.entry - p)
        lots[:] = leftover

    for i in range(i0, i1 + 1):
        px = px_at(i)
        wd = _weekday(ts[i])
        friday = wd >= 4
        btc_hist = closes["BTC"][: i + 1]
        label = classify_sma20_50(btc_hist)["label"]
        equity = cash + _mark(lots, px)
        base_eq = equity if compound else book
        wanted = {k: base_eq * v for k, v in (REGIME_MAP.get(label) or {"cash": 1.0}).items() if k != "cash" and v > 0}

        # Funding on shorts (perp proxy). Original sleeve sim omitted this.
        if mode == "realistic":
            for lot in lots:
                if lot.side != "short":
                    continue
                notion = lot.qty * px.get(lot.base, lot.entry)
                pay = notion * SHORT_FUNDING_PER_DAY
                cash += pay
                stats.funding_eur += pay

        orders: list[dict[str, Any]] = []

        # Flatten sleeves that are off.
        active = set(wanted)
        for lot in list(lots):
            if lot.sleeve not in active:
                orders.append({"sleeve": lot.sleeve, "base": lot.base, "side": lot.side, "action": "close", "lot": lot})

        # Short hard stop.
        for lot in list(lots):
            if lot.side != "short":
                continue
            p = px.get(lot.base, lot.entry)
            ret = (lot.entry - p) / lot.entry if lot.entry else 0.0
            if ret <= -float(SHORT_CFG["stop"]):
                orders.append({"sleeve": lot.sleeve, "base": lot.base, "side": "short", "action": "close", "lot": lot})

        # Donchian channel-low / Friday flatten.
        for lot in list(lots):
            if lot.side != "long" or lot.sleeve not in DONCH:
                continue
            cfg = DONCH[lot.sleeve]
            if cfg["friday"] and friday:
                orders.append({"sleeve": lot.sleeve, "base": lot.base, "side": "long", "action": "close", "lot": lot})
                continue
            n = int(cfg["exit_n"])
            if i >= n:
                ll = min(lows[lot.base][i - n : i])
                if lows[lot.base][i] <= ll:
                    orders.append({"sleeve": lot.sleeve, "base": lot.base, "side": "long", "action": "close", "lot": lot})

        apply_fills(i, orders, friday=friday)
        px = px_at(i)
        equity = cash + _mark(lots, px)
        base_eq = equity if compound else book
        wanted = {k: base_eq * v for k, v in (REGIME_MAP.get(label) or {}).items() if k != "cash" and v > 0}

        opens: list[dict[str, Any]] = []
        sma50 = sma50_btc[i]
        btc_ok = sma50 is not None and closes["BTC"][i] > float(sma50)
        reserved: dict[str, float] = defaultdict(float)

        for sleeve, target in wanted.items():
            if sleeve not in DONCH:
                continue
            cfg = DONCH[sleeve]
            if cfg["friday"] and friday:
                continue
            if not btc_ok:
                continue
            held = {lt.base for lt in lots if lt.sleeve == sleeve and lt.side == "long"}
            free = slots - len(held)
            if free <= 0:
                continue
            ch = int(cfg["ch"])
            if i < ch:
                continue
            cands: list[tuple[float, str]] = []
            for b in ALT:
                if b in held:
                    continue
                hh = max(highs[b][i - ch : i])
                if highs[b][i] > hh:
                    mom = _mom(closes[b], i, ch, 0) or 0.0
                    cands.append((mom, b))
            cands.sort(reverse=True)
            clip_src = max(target, 0.0)
            for _, b in cands:
                if free <= 0:
                    break
                deployed = _deployed(lots, sleeve, px) + reserved[sleeve]
                room = max(0.0, clip_src - deployed)
                want_n = min(clip_src * WEIGHT, room, cash * 0.95)
                if want_n < MIN_NOTIONAL:
                    continue
                opens.append({"sleeve": sleeve, "base": b, "side": "long", "action": "open", "want_eur": want_n})
                reserved[sleeve] += want_n
                free -= 1

        if "short_weakest" in wanted:
            due = i - last_short_reb >= int(SHORT_CFG["reb"]) or last_short_reb < 0
            held_s = [lt for lt in lots if lt.sleeve == "short_weakest"]
            if due:
                for lt in held_s:
                    opens.append({"sleeve": "short_weakest", "base": lt.base, "side": "short", "action": "close", "lot": lt})
                last_short_reb = i
            elif not held_s:
                due = True
            if due or not held_s:
                # close orders go first
                pass

        apply_fills(i, [od for od in opens if od["action"] == "close"], friday=friday)
        px = px_at(i)

        short_opens: list[dict[str, Any]] = []
        if "short_weakest" in wanted:
            held_s = [lt for lt in lots if lt.sleeve == "short_weakest" and lt.qty > 0]
            due = i - last_short_reb >= int(SHORT_CFG["reb"]) or last_short_reb < 0 or not held_s
            if due and not held_s:
                picks = pick_weakest(
                    closes,
                    closes["BTC"],
                    i,
                    lookback=int(SHORT_CFG["lookback"]),
                    top_n=int(SHORT_CFG["top_n"]),
                    floor=float(SHORT_CFG["floor"]),
                    skip=int(SHORT_CFG["skip"]),
                    lookback2=0,
                    floor2=0.0,
                    mode="mom",
                    bounce=float(SHORT_CFG["bounce"]),
                    below_sma=0,
                    weight_mode="equal",
                )
                target = wanted["short_weakest"]
                if picks:
                    deploy_n = min(target, cash * 0.95)
                    wsum = sum(w for _, w in picks) or 1.0
                    for b, w in picks:
                        ww = min(w / wsum, float(SHORT_CFG["max_weight"]))
                        want_n = deploy_n * ww
                        if want_n >= MIN_NOTIONAL:
                            short_opens.append(
                                {
                                    "sleeve": "short_weakest",
                                    "base": b,
                                    "side": "short",
                                    "action": "open",
                                    "want_eur": want_n,
                                }
                            )
                last_short_reb = i

        apply_fills(i, [od for od in opens if od["action"] == "open"] + short_opens, friday=friday)

        px = px_at(i)
        equity = cash + _mark(lots, px)
        path.append(
            {
                "date": _date(ts[i]),
                "equity_eur": round(equity, 2),
                "cash_eur": round(cash, 2),
                "regime": label,
                "n_lots": len(lots),
            }
        )

    # Flatten remainder at last close, realistic costs.
    if lots:
        last = i1
        rest = [
            {"sleeve": lt.sleeve, "base": lt.base, "side": lt.side, "action": "close", "lot": lt}
            for lt in list(lots)
        ]
        apply_fills(last, rest, friday=_weekday(ts[last]) >= 4)
        if path:
            path[-1]["equity_eur"] = round(cash, 2)
            path[-1]["cash_eur"] = round(cash, 2)
            path[-1]["n_lots"] = 0

    eq = [book] + [float(r["equity_eur"]) for r in path]
    pnl = eq[-1] - book
    mdd = _max_drawdown(eq)
    by_m: dict[str, list[float]] = defaultdict(list)
    for row in path:
        by_m[row["date"][:7]].append(row["equity_eur"])
    months = []
    m_prev = book
    for m in sorted(by_m):
        end = by_m[m][-1]
        months.append({"month": m, "pnl_eur": round(end - m_prev, 2), "end_eur": round(end, 2)})
        m_prev = end
    worst = min(months, key=lambda x: x["pnl_eur"]) if months else None
    fill_ratio = stats.filled_eur / stats.intended_eur if stats.intended_eur else 1.0
    med_part = 0.0
    if stats.part_samples:
        srt = sorted(stats.part_samples)
        med_part = srt[len(srt) // 2]
    stats.taker_fee_end = rolling_fee()
    calmar = (pnl / abs(mdd)) if abs(mdd) > 1 else (99.0 if pnl > 0 else 0.0)
    return {
        "book_eur": book,
        "mode": mode,
        "compound": compound,
        "max_pos": slots,
        "pnl_eur": round(pnl, 2),
        "end_eur": round(eq[-1], 2),
        "return_pct": round(100.0 * pnl / book, 2),
        "max_dd_eur": round(mdd, 2),
        "max_dd_pct": round(100.0 * mdd / book, 2),
        "calmar": round(calmar, 3),
        "worst_month": worst,
        "months": months,
        "path": path,
        "fill_ratio": round(fill_ratio, 4),
        "cap_hits": stats.cap_hits,
        "stacked_days": stats.stacked_days,
        "fills": stats.fills,
        "fees_eur": round(stats.fees_eur, 2),
        "spread_eur": round(stats.spread_eur, 2),
        "impact_eur": round(stats.impact_eur, 2),
        "funding_eur": round(stats.funding_eur, 2),
        "cost_eur": round(stats.fees_eur + stats.spread_eur + stats.impact_eur - min(0.0, stats.funding_eur), 2),
        "vol_30d_peak_eur": round(stats.vol_30d_peak, 2),
        "taker_fee_end": stats.taker_fee_end,
        "median_participation": round(med_part, 4),
        "cap_by_base": dict(sorted(stats.cap_by_base.items(), key=lambda kv: -kv[1])[:8]),
        "scale_vs_linear_pct": None,
    }


def _scale_efficiency(pnl: float, book: float, pnl20: float) -> float | None:
    if book <= 0 or abs(pnl20) < 1:
        return None
    linear = pnl20 * (book / 20_000.0)
    return round(100.0 * pnl / linear, 1) if linear else None


def write_svg(rows_by_key: dict[str, list[dict[str, Any]]], summary: list[dict[str, Any]], path: Path) -> None:
    w, h = 960, 620
    pad_l, pad_r, pad_t, pad_b = 58, 20, 36, 40
    chart_h = 320
    colors = {
        "20k realistic": "#3dff9a",
        "50k realistic": "#6ea8ff",
        "100k realistic": "#ffb020",
        "50k naive linear": "#6ea8ff66",
        "100k naive linear": "#ffb02066",
    }
    # Panel 1: indexed equity (start=100) so scale shows up as curve gap.
    series = []
    for name, rows in rows_by_key.items():
        if not rows:
            continue
        start = float(rows[0]["equity_eur"])
        book = start
        # naive series starts at scaled book; index still 100
        series.append((name, colors.get(name, "#e8ecf7"), rows, book))
    n = max((len(rows) for _, _, rows, _ in series), default=1) - 1
    n = max(n, 1)
    idxs: list[float] = []
    for _, _, rows, book in series:
        for r in rows:
            idxs.append(100.0 * float(r["equity_eur"]) / book)
    vmin, vmax = min(idxs + [100.0]), max(idxs + [100.0])
    span = max(vmax - vmin, 1.0)

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad_l + i / n * (w - pad_l - pad_r)
        y = pad_t + (1.0 - (v - vmin) / span) * (chart_h - 10)
        return x, y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        '<rect width="100%" height="100%" fill="#0b1020"/>',
        f'<text x="{pad_l}" y="22" fill="#e8ecf7" font-size="15" font-family="ui-sans-serif,system-ui">'
        "Loop-mix 1y · indexed equity (start=100) · Bitvavo depth</text>",
    ]
    x0, y100 = xy(0, 100.0)
    x1, _ = xy(n, 100.0)
    parts.append(f'<line x1="{x0:.1f}" y1="{y100:.1f}" x2="{x1:.1f}" y2="{y100:.1f}" stroke="#2a3348" stroke-dasharray="4 4"/>')
    for li, (name, color, rows, book) in enumerate(series):
        d = []
        for j, r in enumerate(rows):
            x, y = xy(j, 100.0 * float(r["equity_eur"]) / book)
            d.append(("M" if j == 0 else "L") + f"{x:.1f},{y:.1f}")
        dash = ' stroke-dasharray="5 4"' if "naive" in name else ""
        width = "1.6" if "naive" in name else "2.2"
        parts.append(f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="{width}"{dash}/>')
        parts.append(
            f'<text x="{w - 250}" y="{pad_t + 8 + li * 15}" fill="{color}" font-size="12" '
            f'font-family="ui-sans-serif">{name}</text>'
        )
    ticks = [0, n // 3, (2 * n) // 3, n]
    sample = next(rows for _, _, rows, _ in series)
    for ti in ticks:
        lab = sample[min(ti, len(sample) - 1)]["date"]
        parts.append(
            f'<text x="{xy(min(ti, n), vmin)[0]:.1f}" y="{chart_h + pad_t + 18}" fill="#8b93a7" font-size="11" '
            f'text-anchor="middle" font-family="ui-sans-serif">{lab}</text>'
        )

    # Panel 2: PnL bars + return%
    bar_top = chart_h + pad_t + 48
    parts.append(
        f'<text x="{pad_l}" y="{bar_top - 8}" fill="#e8ecf7" font-size="14" font-family="ui-sans-serif">'
        "1y PnL (€) vs naive linear · same knobs, realistic fills</text>"
    )
    realistic = [s for s in summary if s.get("variant") == "unit_realistic"]
    if realistic:
        bw = 70
        gap = 160
        origin_x = pad_l + 40
        max_pnl = max(max(s["pnl_eur"] for s in realistic), max(s.get("naive_pnl_eur") or 0 for s in realistic), 1)
        axis_y = h - pad_b - 24
        bar_h = axis_y - bar_top - 10
        for i, s in enumerate(realistic):
            x = origin_x + i * gap
            naive = float(s.get("naive_pnl_eur") or 0)
            real = float(s["pnl_eur"])
            hn = bar_h * naive / max_pnl
            hr = bar_h * real / max_pnl
            parts.append(
                f'<rect x="{x}" y="{axis_y - hn:.1f}" width="{bw}" height="{hn:.1f}" fill="#2a3348"/>'
            )
            parts.append(
                f'<rect x="{x + 18}" y="{axis_y - hr:.1f}" width="{bw}" height="{hr:.1f}" fill="{list(colors.values())[i]}"/>'
            )
            parts.append(
                f'<text x="{x + bw/2:.1f}" y="{axis_y + 14}" fill="#8b93a7" font-size="11" text-anchor="middle" '
                f'font-family="ui-sans-serif">€{int(s["book_eur"]/1000)}k</text>'
            )
            parts.append(
                f'<text x="{x + 18 + bw/2:.1f}" y="{axis_y - hr - 6:.1f}" fill="#e8ecf7" font-size="11" '
                f'text-anchor="middle" font-family="ui-sans-serif">€{real:,.0f}</text>'
            )
            parts.append(
                f'<text x="{x + bw/2:.1f}" y="{axis_y - hn - 6:.1f}" fill="#8b93a7" font-size="10" '
                f'text-anchor="middle" font-family="ui-sans-serif">lin €{naive:,.0f}</text>'
            )
        parts.append(
            f'<text x="{pad_l}" y="{h - 10}" fill="#8b93a7" font-size="11" font-family="ui-sans-serif">'
            "Grey = linear €27.9k × book/20k. Colour = replay with Bitvavo ADV cap, impact, taker+cross.</text>"
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _slim(st: dict[str, Any]) -> dict[str, Any]:
    skip = {"path", "months"}
    out = {k: v for k, v in st.items() if k not in skip}
    out["months"] = st.get("months")
    return out


def main() -> None:
    print("loading daily OHLC+volume…", flush=True)
    data = load_aligned(days=430)
    ts = data["ts"]
    i0 = _idx(ts, W0)
    i1 = _idx(ts, W1)
    print(f"window {_date(ts[i0])}→{_date(ts[i1])} n={i1 - i0 + 1}", flush=True)

    books = (20_000.0, 50_000.0, 100_000.0)
    results: list[dict[str, Any]] = []
    paths: dict[str, list[dict[str, Any]]] = {}

    print("unit-mix original (same accounting as +€28k search)…", flush=True)
    orig_unit = simulate_unit_mix(data, book=20_000.0, i0=i0, i1=i1, mode="original")
    orig_unit["variant"] = "unit_original"
    results.append(orig_unit)
    print(
        f"  20k original unit-mix pnl={orig_unit['pnl_eur']:+.0f} dd={orig_unit['max_dd_pct']:.2f}% "
        f"ends={orig_unit['unit_ends']}",
        flush=True,
    )

    for book in books:
        print(f"unit-mix realistic €{book:.0f}…", flush=True)
        st = simulate_unit_mix(data, book=book, i0=i0, i1=i1, mode="realistic")
        naive = PUBLISHED_20K_PNL * (book / 20_000.0)
        st["naive_pnl_eur"] = round(naive, 2)
        st["naive_end_eur"] = round(book + naive, 2)
        st["scale_vs_published_linear_pct"] = round(100.0 * st["pnl_eur"] / naive, 1) if naive else None
        st["variant"] = "unit_realistic"
        results.append(st)
        paths[f"{int(book/1000)}k realistic"] = st["path"]
        print(
            f"  pnl={st['pnl_eur']:+.0f} ({st['return_pct']:+.1f}%) dd={st['max_dd_pct']:.2f}% "
            f"fill={st['fill_ratio']:.2%} cap_hits={st['cap_hits']} vs_linear={st['scale_vs_published_linear_pct']}%",
            flush=True,
        )

    pnl20 = next(s["pnl_eur"] for s in results if s["variant"] == "unit_realistic" and s["book_eur"] == 20_000.0)
    for st in results:
        if st.get("variant") == "unit_realistic":
            st["scale_vs_20k_realistic_pct"] = _scale_efficiency(st["pnl_eur"], st["book_eur"], pnl20)

    print("live combined-book realistic (allocator clip sizing)…", flush=True)
    for book in books:
        st = simulate_mix(data, book=book, i0=i0, i1=i1, mode="realistic", compound=False)
        st["variant"] = "live_combined"
        st["naive_pnl_eur"] = round(PUBLISHED_20K_PNL * (book / 20_000.0), 2)
        results.append(st)
        print(f"  {int(book)} combined pnl={st['pnl_eur']:+.0f} ({st['return_pct']:+.1f}%) fill={st['fill_ratio']:.2%}", flush=True)

    print("spread-slots unit-mix (more names, smaller clips)…", flush=True)
    for book, slots in ((20_000.0, 2), (50_000.0, 5), (100_000.0, 10)):
        st = simulate_unit_mix(data, book=book, i0=i0, i1=i1, mode="realistic", max_pos=slots)
        st["variant"] = "unit_spread_slots"
        st["naive_pnl_eur"] = round(PUBLISHED_20K_PNL * (book / 20_000.0), 2)
        results.append(st)
        print(f"  {int(book)} max_pos={slots} pnl={st['pnl_eur']:+.0f} ({st['return_pct']:+.1f}%)", flush=True)

    p20 = paths["20k realistic"]
    for book, name in ((50_000.0, "50k naive linear"), (100_000.0, "100k naive linear")):
        scale = book / 20_000.0
        paths[name] = [
            {
                "date": r["date"],
                "equity_eur": round(book + (float(r["equity_eur"]) - 20_000.0) * scale, 2),
                "regime": r.get("regime"),
            }
            for r in p20
        ]

    write_svg(paths, results, SVG)

    unit = [s for s in results if s.get("variant") == "unit_realistic"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"start": _date(ts[i0]), "end": _date(ts[i1]), "days": i1 - i0 + 1},
        "question": "Does the loop-winner mix scale linearly to €50k and €100k?",
        "answer": (
            "Percent returns are almost linear in the search accounting (unit sleeves, 15 bps/side). "
            "On Bitvavo they are not: taker+cross+impact and max_pos=2 clips eat a growing share at €50k/€100k."
        ),
        "published_20k": {
            "pnl_eur": PUBLISHED_20K_PNL,
            "max_dd_pct": PUBLISHED_20K_DD,
            "replayed_original_unit_mix_pnl_eur": orig_unit["pnl_eur"],
            "note": "In-sample unit-return mix, 15 bps/side, no ADV cap. Book €20k, not profit €20k.",
        },
        "naive_linear": {
            "20000": round(PUBLISHED_20K_PNL, 2),
            "50000": round(PUBLISHED_20K_PNL * 2.5, 2),
            "100000": round(PUBLISHED_20K_PNL * 5.0, 2),
        },
        "assumptions": {
            "fee": "Bitvavo Category A taker (0.25% / 0.20% / 0.18% at €20k/€50k/€100k turnover)",
            "spread": "20 bps live taker-cross, widens as 1/sqrt(ADV) below €1m",
            "impact": f"{IMPACT_K} × 20d sigma × sqrt(Q/ADV)",
            "participation_cap": f"{MAX_PART_SPOT:.0%} of venue daily quote ADV (TWAP)",
            "stacking": "donch_fri10 + donch10 share ADV with 1.8× stack on impact/cap",
            "shorts": f"perp proxy, {PERP_ADV_MULT:.0f}× Bitvavo ADV, funding {SHORT_FUNDING_PER_DAY}/day (spot cannot short)",
            "alpha_i": "not simulated",
            "in_sample": True,
        },
        "runs": [_slim(s) for s in results],
        "headline": {
            str(int(s["book_eur"])): {
                "pnl_eur": s["pnl_eur"],
                "return_pct": s["return_pct"],
                "max_dd_pct": s["max_dd_pct"],
                "naive_pnl_eur": s["naive_pnl_eur"],
                "efficiency_vs_published_linear_pct": s.get("scale_vs_published_linear_pct"),
                "efficiency_vs_20k_realistic_pct": s.get("scale_vs_20k_realistic_pct"),
                "fill_ratio": s["fill_ratio"],
                "end_eur": s["end_eur"],
            }
            for s in unit
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT} and {SVG}", flush=True)


if __name__ == "__main__":
    main()
