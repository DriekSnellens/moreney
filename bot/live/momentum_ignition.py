"""Ignition sleeve — desk classic + coil hybrid + trailing exit.

Research (``artifacts/early_signal_book.json``, ``ignition_lab/HYBRID_ENTRY``):
**Classic** — quiet + 20d breakout + day ≥+6% + volume ≥2× on liquid desk
names. Looser classic gates add trades but destroy PnL.
**Coil** — compress + 5d breakout + milder day/vol/quiet/close_loc, with a
wider path trail (25%). Catches explosive legs that miss classic same-day.
Classic ranks above coil when both fire. Exit: path trail from peak,
tightening to 10% once the lot is ≥+30% above entry (ratchet).

Live OKX when armed; paper otherwise. No per-coin hardcodes.
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
    # Seed sleeve. Ambition target is banking ~€2–3k in spike weeks (not every
    # quiet week). Wet scan: €10k desk coil + compound ≈ 4× more €2k bank-weeks
    # than a fixed €2k book; most calendar weeks still stay near zero.
    book_eur: float = 10_000.0
    max_positions: int = 2
    deploy_frac: float = 1.0
    # When True, size from available cash so winners grow firepower (book_eur
    # is the seed, not a permanent clip ceiling). max_book_eur>0 hard-caps.
    compound_sizing: bool = True
    max_book_eur: float = 0.0
    # Base trail from peak. Ablation: 12% beats 15% on desk-only tape.
    trail_pct: float = 0.12
    # Once peak gain ≥ arm, use the tighter trail (0 disables ratchet).
    trail_ratchet_arm_pct: float = 0.30
    trail_ratchet_pct: float = 0.10
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
    # Coil path: catch compression→breakouts earlier than classic early_signal.
    # Wet desk ablation: classic|coil hybrid + 25% coil trail ≈ +€6.8k vs +€2.7k
    # classic-only on €2k (full window); desk +80% leg catch ~21% vs ~2%.
    coil_entry_enabled: bool = True
    coil_breakout_days: int = 5
    coil_day_ret_min: float = 0.025
    coil_vol_mult_min: float = 1.2
    coil_quiet_max: float = 0.10
    coil_min_close_loc: float = 0.65
    coil_compress_ratio: float = 0.5
    coil_trail_pct: float = 0.25
    fee_rt: float = FEE_RT
    slip: float = SLIP
    min_notional_eur: float = 50.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    # Sparse hour slots when ``decision_interval_sec`` is 0.
    decision_hours_utc: tuple[int, ...] = (0,)
    # When > 0, scan for entries this often (UTC), not only at decision hours.
    # Daily OHLC early-signals evolve through the day — 15m catches breakouts.
    decision_interval_sec: float = 900.0
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
    # 0 → use cfg trail_pct; coil entries set a wider path trail.
    trail_pct: float = 0.0
    entry_path: str = "classic"

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
            trail_pct=float(raw.get("trail_pct") or 0.0),
            entry_path=str(raw.get("entry_path") or "classic"),
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
    coil_n = max(3, int(cfg.coil_breakout_days))
    highs_coil = [float(r[2]) for r in prior[-coil_n :]]
    brk_coil = bool(highs_coil) and c >= max(highs_coil)
    vol_window = [_qvol(r) for r in prior[-cfg.vol_lookback :]]
    med_vol = _median(vol_window)
    day_qvol = _qvol(day)
    vol_x = (day_qvol / med_vol) if med_vol > 0 else 0.0
    liquid = med_vol >= cfg.min_median_qvol_eur and day_qvol >= cfg.min_day_qvol_eur
    quiet = quiet_ret < cfg.quiet_max
    close_loc = ((c - l) / (h - l)) if h > l else 0.5

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
    # Compression: recent 5d range much tighter than 20d range.
    def _span(rs: Sequence[Sequence[float]]) -> float:
        hs = [float(r[2]) for r in rs if float(r[2]) > 0]
        ls = [float(r[3]) for r in rs if float(r[3]) > 0]
        if not hs or not ls or min(ls) <= 0:
            return 0.0
        return max(hs) / min(ls) - 1.0

    span20 = _span(prior[-cfg.vol_lookback :])
    span5 = _span(prior[-5:])
    compress = span20 > 0 and span5 < float(cfg.coil_compress_ratio) * span20

    atoms: list[str] = []
    if brk20:
        atoms.append("brk20")
    if brk_coil:
        atoms.append(f"brk{coil_n}")
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
    if compress:
        atoms.append("compress")

    early_signal = (
        quiet
        and liquid
        and brk20
        and day_ret >= cfg.day_ret_min
        and vol_x >= cfg.vol_mult_min
    )
    coil_signal = (
        bool(cfg.coil_entry_enabled)
        and liquid
        and quiet_ret < float(cfg.coil_quiet_max)
        and compress
        and brk_coil
        and day_ret >= float(cfg.coil_day_ret_min)
        and vol_x >= float(cfg.coil_vol_mult_min)
        and close_loc >= float(cfg.coil_min_close_loc)
    )
    points = len(atoms)
    if early_signal:
        entry_path = "classic"
        trail_for_entry = float(cfg.trail_pct)
    elif coil_signal:
        entry_path = "coil"
        trail_for_entry = float(cfg.coil_trail_pct)
    else:
        entry_path = ""
        trail_for_entry = float(cfg.trail_pct)
    return {
        "day_ret": round(day_ret, 4),
        "quiet_ret": round(quiet_ret, 4),
        "quiet": quiet,
        "liquid": liquid,
        "brk20": brk20,
        "brk_coil": brk_coil,
        "vol_x": round(vol_x, 3),
        "day_qvol": round(day_qvol, 0),
        "med_qvol": round(med_vol, 0),
        "r3": round(r3, 4),
        "xs10": None if xs10 is None else round(xs10, 4),
        "trend": trend,
        "expand": expand,
        "compress": compress,
        "close_loc": round(close_loc, 3),
        "atoms": atoms,
        "points": points,
        "early_signal": early_signal,
        "coil_signal": coil_signal,
        "entry_path": entry_path,
        "trail_pct": trail_for_entry,
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
        path = str(scored.get("entry_path") or "")
        if not path:
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
            if cfg.coil_entry_enabled and not scored.get("coil_signal"):
                why.append("no_coil")
            rejected.append({"base": base, "reason": ",".join(why) or "no_signal", **scored})
            continue
        # Classic keeps min_points; coil is allowed with fewer atoms (compress+brk).
        if path == "classic" and scored["points"] < cfg.min_points:
            rejected.append({"base": base, "reason": "low_points", **scored})
            continue
        ranked.append(row)
    # Prefer classic over coil, then points / vol / day ret.
    ranked.sort(
        key=lambda r: (
            0 if r.get("entry_path") == "classic" else 1,
            -int(r["points"]),
            -float(r["vol_x"]),
            -float(r["day_ret"]),
        )
    )

    held_set = {str(b).upper() for b in held}
    entries: list[dict[str, Any]] = []
    risk_block = ""
    if not risk_on:
        risk_block = "btc_below_sma50"
    elif len(held_set) >= int(cfg.max_positions):
        risk_block = "slots_full"
    elif ranked:
        free = max(0, int(cfg.max_positions) - len(held_set))
        take = [r for r in ranked if r["base"] not in held_set][:free]
        if take:
            powder = float(cash_eur)
            if not cfg.compound_sizing:
                powder = min(powder, float(cfg.book_eur))
            if float(cfg.max_book_eur or 0.0) > 0:
                powder = min(powder, float(cfg.max_book_eur))
            per = (powder * float(cfg.deploy_frac)) / len(take)
            for top in take:
                if per < cfg.min_notional_eur:
                    break
                path = str(top.get("entry_path") or "classic")
                tag = "early_signal" if path == "classic" else "coil_signal"
                entries.append(
                    {
                        "base": top["base"],
                        "notional_eur": round(per, 2),
                        "entry_path": path,
                        "trail_pct": float(top.get("trail_pct") or cfg.trail_pct),
                        "reasons": [
                            tag,
                            f"path={path}",
                            f"points={top['points']}",
                            f"day={top['day_ret']:+.1%}",
                            f"volx={top['vol_x']:.1f}",
                            *list(top["atoms"]),
                        ],
                        "score": top,
                    }
                )

    want = ranked[0]["base"] if ranked else None
    near = _near_misses(rejected, cfg, limit=3)
    trail_txt = f"trail {cfg.trail_pct:.0%}"
    if cfg.trail_ratchet_arm_pct > 0 and cfg.trail_ratchet_pct > 0:
        trail_txt += (
            f" (→{cfg.trail_ratchet_pct:.0%} na +{cfg.trail_ratchet_arm_pct:.0%})"
        )
    if cfg.coil_entry_enabled:
        trail_txt += f"; coil-trail {cfg.coil_trail_pct:.0%}"
    if want:
        path = str(ranked[0].get("entry_path") or "classic")
        body = f"Candidate {want} [{path}] ({ranked[0]['points']} pts)."
    elif near:
        n0 = near[0]
        body = (
            f"Geen signal; dichtbij {n0['base']} "
            f"(mist {n0['missing']})."
        )
    else:
        body = "Geen ignition-signal vandaag."
    size_txt = (
        f"compound €{cfg.book_eur:,.0f} seed"
        if cfg.compound_sizing
        else f"fixed €{cfg.book_eur:,.0f}"
    )
    caption = (
        f"Ignition: desk classic+coil + {trail_txt}; {size_txt}, "
        f"{cfg.max_positions} slot(s). Spike-week target €2–3k. "
        + body
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
        "near_miss": near,
        "want": want,
        "caption": caption,
        "signal": "classic(quiet+brk20+r1_6+vol2)|coil(compress+brk5)",
        "trail_pct": cfg.trail_pct,
        "coil_trail_pct": cfg.coil_trail_pct,
        "coil_entry_enabled": cfg.coil_entry_enabled,
        "trail_ratchet_arm_pct": cfg.trail_ratchet_arm_pct,
        "trail_ratchet_pct": cfg.trail_ratchet_pct,
        "compound_sizing": cfg.compound_sizing,
        "book_eur": cfg.book_eur,
        "max_positions": cfg.max_positions,
        "ambition_week_eur": [2_000.0, 3_000.0],
    }


def _near_misses(
    rejected: Sequence[Mapping[str, Any]],
    cfg: IgnitionConfig,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Names that fail exactly one early-signal gate (operator watchlist)."""
    out: list[dict[str, Any]] = []
    for row in rejected:
        if "day_ret" not in row:
            continue
        missing: list[str] = []
        if not row.get("quiet"):
            missing.append("quiet")
        if not row.get("liquid"):
            missing.append("liquid")
        if not row.get("brk20"):
            missing.append("brk20")
        if float(row.get("day_ret") or 0.0) < cfg.day_ret_min:
            missing.append("day_ret")
        if float(row.get("vol_x") or 0.0) < cfg.vol_mult_min:
            missing.append("vol")
        if len(missing) != 1:
            continue
        out.append(
            {
                "base": row["base"],
                "missing": missing[0],
                "day_ret": row.get("day_ret"),
                "vol_x": row.get("vol_x"),
                "brk20": row.get("brk20"),
                "points": row.get("points"),
            }
        )
    out.sort(
        key=lambda r: (
            -int(r.get("points") or 0),
            -float(r.get("vol_x") or 0.0),
            -float(r.get("day_ret") or 0.0),
        )
    )
    return out[:limit]


def effective_trail_pct(pos: IgnitionPosition, cfg: IgnitionConfig) -> float:
    """Path trail (coil/classic), then ratchet once peak gain clears the arm."""
    base = float(pos.trail_pct or 0.0) or float(cfg.trail_pct or 0.0)
    arm = float(cfg.trail_ratchet_arm_pct or 0.0)
    tight = float(cfg.trail_ratchet_pct or 0.0)
    if base <= 0 and tight <= 0:
        return 0.0
    if arm <= 0 or tight <= 0 or pos.entry_price <= 0:
        return base
    peak = max(float(pos.peak_px or pos.entry_price), float(pos.entry_price))
    if (peak / float(pos.entry_price) - 1.0) >= arm:
        return tight if base <= 0 else min(base, tight)
    return base


def trail_exit(
    pos: IgnitionPosition,
    mark: float,
    cfg: IgnitionConfig,
) -> dict[str, Any] | None:
    if mark <= 0 or pos.entry_price <= 0:
        return None
    peak = max(float(pos.peak_px or pos.entry_price), mark)
    trail = effective_trail_pct(pos, cfg)
    if trail <= 0:
        return None
    path_base = float(pos.trail_pct or 0.0) or float(cfg.trail_pct or trail)
    if mark <= peak * (1.0 - trail):
        armed = trail < path_base - 1e-12
        return {
            "base": pos.base,
            "reason": "ignition_trail_ratchet" if armed else "ignition_trail",
            "mark": mark,
            "peak": peak,
            "trail_pct": trail,
            "entry_path": pos.entry_path,
            "gross_return": pos.gross_return(mark),
        }
    return None


__all__ = [
    "IgnitionConfig",
    "IgnitionPosition",
    "default_config",
    "effective_trail_pct",
    "evaluate_ignition",
    "fill_px",
    "score_ignition_day",
    "trail_exit",
    "replace",
]
