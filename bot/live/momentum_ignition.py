"""Paper ignition sleeve — desk-universe early-signal + 15% trail.

Research (``artifacts/early_signal_book.json``, ``explosive_factor_scan.json``):
quiet + 20d breakout + day ≥+6% + volume ≥2× on liquid desk names, with a
15% trail, was the only early-signal book that stayed profitable when
restricted to the desk universe. Pure "days before" quiet/SMA filters had
zero out-of-sample lift.

Paper-only. No per-coin hardcodes.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from bot.live.momentum_btc_rs_clip import completed_ohlc, fill_px, sma
from bot.live.momentum_desk import DEFAULT_UNIVERSE

FEE_RT = 0.003
SLIP = 0.001


@dataclass(frozen=True)
class IgnitionConfig:
    book_eur: float = 2_000.0
    max_positions: int = 1
    deploy_frac: float = 1.0
    trail_pct: float = 0.15
    # Early-signal gates (desk-only winner from early_signal_book).
    quiet_max: float = 0.12
    day_ret_min: float = 0.06
    vol_mult_min: float = 2.0
    breakout_days: int = 20
    quiet_lookback: int = 10
    vol_lookback: int = 20
    min_median_qvol_eur: float = 100_000.0
    min_day_qvol_eur: float = 50_000.0
    # Optional BTC regime gate (long ignition only in uptrend).
    require_btc_sma: bool = True
    btc_sma_n: int = 50
    # Score atoms for ranking / diagnostics (entry still needs early_signal).
    min_points: int = 3
    fee_rt: float = FEE_RT
    slip: float = SLIP
    min_notional_eur: float = 50.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    decision_hours_utc: tuple[int, ...] = (0,)
    tick_sec: float = 30.0
    ohlc_days: int = 120


@dataclass
class IgnitionPosition:
    base: str
    entry_price: float
    notional_eur: float
    qty: float
    opened_ms: int
    holding_id: str = ""
    venue: str = "paper"
    entry_reason: str = ""
    peak_px: float = 0.0
    points: int = 0

    def __post_init__(self) -> None:
        if not self.holding_id:
            self.holding_id = f"ign-{self.base}-{uuid.uuid4().hex[:8]}"
        if self.peak_px <= 0:
            self.peak_px = self.entry_price

    def gross_return(self, mark: float) -> float:
        if self.entry_price <= 0 or mark <= 0:
            return 0.0
        return mark / self.entry_price - 1.0

    def unrealized_net(self, mark: float, fee_rt: float) -> float:
        ret = self.gross_return(mark)
        return self.notional_eur * ret - self.notional_eur * (fee_rt / 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IgnitionPosition:
        return cls(
            base=str(raw["base"]),
            entry_price=float(raw["entry_price"]),
            notional_eur=float(raw["notional_eur"]),
            qty=float(raw.get("qty") or 0.0),
            opened_ms=int(raw["opened_ms"]),
            holding_id=str(raw.get("holding_id") or ""),
            venue=str(raw.get("venue") or "paper"),
            entry_reason=str(raw.get("entry_reason") or ""),
            peak_px=float(raw["peak_px"]) if raw.get("peak_px") not in (None, "") else 0.0,
            points=int(raw.get("points") or 0),
        )


def default_config() -> IgnitionConfig:
    return IgnitionConfig()


def _qvol(row: Sequence[float]) -> float:
    return max(0.0, float(row[5]) * float(row[4]))


def _median(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(float(x) for x in xs)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def score_ignition_day(
    rows: Sequence[Sequence[float]],
    btc_rows: Sequence[Sequence[float]],
    cfg: IgnitionConfig,
) -> dict[str, Any] | None:
    """Score the latest *completed* daily bar for ignition atoms."""
    need = max(cfg.breakout_days, cfg.vol_lookback, cfg.quiet_lookback, 3) + 2
    if len(rows) < need or len(btc_rows) < need:
        return None
    day = rows[-1]
    prior = rows[:-1]
    o, h, l, c = float(day[1]), float(day[2]), float(day[3]), float(day[4])
    if o <= 0 or c <= 0:
        return None
    day_ret = c / o - 1.0
    quiet_anchor = float(prior[-cfg.quiet_lookback][4])
    if quiet_anchor <= 0:
        return None
    quiet_ret = c / quiet_anchor - 1.0
    highs = [float(r[2]) for r in prior[-cfg.breakout_days :]]
    brk20 = bool(highs) and c >= max(highs)
    vol_window = [_qvol(r) for r in prior[-cfg.vol_lookback :]]
    med_vol = _median(vol_window)
    day_qvol = _qvol(day)
    vol_x = (day_qvol / med_vol) if med_vol > 0 else 0.0
    liquid = med_vol >= cfg.min_median_qvol_eur and day_qvol >= cfg.min_day_qvol_eur
    quiet = quiet_ret < cfg.quiet_max

    # 3d momentum / RS atoms (diagnostics + ranking).
    c3 = float(prior[-3][4]) if len(prior) >= 3 else 0.0
    r3 = (c / c3 - 1.0) if c3 > 0 else 0.0
    btc_c = [float(r[4]) for r in btc_rows if float(r[4]) > 0]
    alt_c = [float(r[4]) for r in rows if float(r[4]) > 0]
    xs10 = None
    if len(alt_c) >= 11 and len(btc_c) >= 11 and alt_c[-11] > 0 and btc_c[-11] > 0:
        xs10 = (alt_c[-1] / alt_c[-11] - 1.0) - (btc_c[-1] / btc_c[-11] - 1.0)
    s20 = sma(alt_c, 20)
    trend = s20 is not None and c > s20
    ranges = [
        (float(r[2]) / float(r[3]) - 1.0)
        for r in prior[-cfg.vol_lookback :]
        if float(r[3]) > 0
    ]
    med_rng = _median(ranges)
    day_rng = (h / l - 1.0) if l > 0 else 0.0
    expand = med_rng > 0 and day_rng >= 1.5 * med_rng

    atoms: list[str] = []
    if brk20:
        atoms.append("brk20")
    if day_ret >= cfg.day_ret_min:
        atoms.append("r1_6")
    if vol_x >= cfg.vol_mult_min:
        atoms.append("vol2")
    if vol_x >= 3.0:
        atoms.append("vol3")
    if r3 >= 0.15:
        atoms.append("r3_15")
    if xs10 is not None and xs10 >= 0.15:
        atoms.append("xs15")
    if xs10 is not None and xs10 >= 0.25:
        atoms.append("xs25")
    if trend:
        atoms.append("trend")
    if expand:
        atoms.append("expand")

    early_signal = (
        quiet
        and liquid
        and brk20
        and day_ret >= cfg.day_ret_min
        and vol_x >= cfg.vol_mult_min
    )
    points = len(atoms)
    return {
        "day_ret": round(day_ret, 4),
        "quiet_ret": round(quiet_ret, 4),
        "quiet": quiet,
        "liquid": liquid,
        "brk20": brk20,
        "vol_x": round(vol_x, 3),
        "day_qvol": round(day_qvol, 0),
        "med_qvol": round(med_vol, 0),
        "r3": round(r3, 4),
        "xs10": None if xs10 is None else round(xs10, 4),
        "trend": trend,
        "expand": expand,
        "atoms": atoms,
        "points": points,
        "early_signal": early_signal,
        "close": c,
    }


def evaluate_ignition(
    ohlc_by_base: Mapping[str, Sequence[Sequence[float]]],
    cfg: IgnitionConfig,
    *,
    held: Sequence[str],
    cash_eur: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Paper decision: enter top early-signal name; exits are trail-managed live."""
    now = now or datetime.now(UTC)
    btc = completed_ohlc(ohlc_by_base.get("BTC") or [], now=now)
    btc_c = [float(r[4]) for r in btc if float(r[4]) > 0]
    s50 = sma(btc_c, cfg.btc_sma_n)
    last_btc = float(btc_c[-1]) if btc_c else 0.0
    risk_on = (not cfg.require_btc_sma) or (s50 is not None and last_btc > s50)

    ranked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for base in cfg.universe:
        rows = completed_ohlc(ohlc_by_base.get(base) or [], now=now)
        scored = score_ignition_day(rows, btc, cfg)
        if scored is None:
            rejected.append({"base": base, "reason": "short_history"})
            continue
        row = {"base": base, **scored}
        if not scored["early_signal"]:
            why = []
            if not scored["quiet"]:
                why.append("not_quiet")
            if not scored["liquid"]:
                why.append("thin")
            if not scored["brk20"]:
                why.append("no_brk20")
            if scored["day_ret"] < cfg.day_ret_min:
                why.append("day_ret")
            if scored["vol_x"] < cfg.vol_mult_min:
                why.append("vol")
            rejected.append({"base": base, "reason": ",".join(why) or "no_signal", **scored})
            continue
        if scored["points"] < cfg.min_points:
            rejected.append({"base": base, "reason": "low_points", **scored})
            continue
        ranked.append(row)
    ranked.sort(
        key=lambda r: (int(r["points"]), float(r["vol_x"]), float(r["day_ret"])),
        reverse=True,
    )

    held_set = {str(b).upper() for b in held}
    entries: list[dict[str, Any]] = []
    risk_block = ""
    if not risk_on:
        risk_block = "btc_below_sma50"
    elif len(held_set) >= int(cfg.max_positions):
        risk_block = "slots_full"
    elif ranked:
        top = ranked[0]
        if top["base"] not in held_set:
            notional = min(float(cash_eur), float(cfg.book_eur)) * float(cfg.deploy_frac)
            if notional >= cfg.min_notional_eur:
                entries.append(
                    {
                        "base": top["base"],
                        "notional_eur": round(notional, 2),
                        "reasons": [
                            "early_signal",
                            f"points={top['points']}",
                            f"day={top['day_ret']:+.1%}",
                            f"volx={top['vol_x']:.1f}",
                            *list(top["atoms"]),
                        ],
                        "score": top,
                    }
                )

    want = ranked[0]["base"] if ranked else None
    caption = (
        f"Ignition PAPER: desk-universe early-signal + trail {cfg.trail_pct:.0%}. "
        + (
            f"Candidate {want} ({ranked[0]['points']} pts)."
            if want
            else "Geen early-signal vandaag."
        )
        + (f" Block: {risk_block}." if risk_block else "")
    )
    return {
        "ok": True,
        "risk_on": risk_on,
        "risk_block": risk_block,
        "sma50": s50,
        "btc": last_btc,
        "entries": entries,
        "exits": [],
        "ranked": ranked[:8],
        "rejected": rejected[:12],
        "want": want,
        "caption": caption,
        "signal": "quiet+brk20+r1_6+vol2",
        "trail_pct": cfg.trail_pct,
    }


def trail_exit(
    pos: IgnitionPosition,
    mark: float,
    cfg: IgnitionConfig,
) -> dict[str, Any] | None:
    if mark <= 0 or pos.entry_price <= 0:
        return None
    peak = max(float(pos.peak_px or pos.entry_price), mark)
    trail = float(cfg.trail_pct)
    if trail <= 0:
        return None
    if mark <= peak * (1.0 - trail):
        return {
            "base": pos.base,
            "reason": "ignition_trail",
            "mark": mark,
            "peak": peak,
            "gross_return": pos.gross_return(mark),
        }
    return None


__all__ = [
    "IgnitionConfig",
    "IgnitionPosition",
    "default_config",
    "evaluate_ignition",
    "fill_px",
    "score_ignition_day",
    "trail_exit",
    "replace",
]
