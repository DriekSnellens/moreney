"""Paper short-weakest sleeve — bear-harvest shorts on weakest trends.

Balanced pack (bear_harvest_validated): trade only when BTC < SMA200; short
the single weakest 15d name (Asness skip-2d, mom≤−8%, bounce-block +4%);
rebalance every 30d; no trail/hard-stop/vol-spike; max_weight 0.5.
Idle-fill OFF. Paper-only; AlphaI avoid/picks gate; no per-coin hardcodes.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DEFAULT_UNIVERSE, AlphaIView

BITVAVO_PUBLIC = "https://api.bitvavo.com/v2"


@dataclass(frozen=True)
class ShortWeakestConfig:
    """Knobs for the paper short-weakest sleeve (bear-harvest balanced defaults)."""

    decision_hours_utc: tuple[int, ...] = (8, 16)
    book_eur: float = 20_000.0
    lookback_days: int = 15
    top_n: int = 1
    rebalance_days: int = 30
    mom_floor: float = -0.08
    # Asness-style: end lookback this many days before the latest close.
    skip_days: int = 2
    # Reject entry if prior daily return >= this (squeeze / bounce filter).
    bounce_block_pct: float = 0.04
    # 0 = disabled (bear-harvest runner lets the lag run).
    trail_pct: float = 0.0
    hard_stop_pct: float = 0.0  # adverse move vs entry (price up); 0 = off
    max_weight: float = 0.5
    deploy_frac: float = 1.0
    weight_mode: str = "equal"  # equal | magnitude
    vol_spike_mult: float = 3.0  # day ret >= mult * ATR14 → exit
    vol_spike_exit: bool = False
    require_btc_below_sma200: bool = True
    sma_days: int = 200
    fee_rt: float = 0.003
    day_loss_limit_eur: float = 600.0
    week_loss_limit_eur: float = 1_600.0
    # Bear sleeve is independent of the long momentum desk.
    only_when_core_idle: bool = False
    cover_when_core_active: bool = False
    # Idle-fill (excess vs BTC when core flat) — OFF; bled in 12w replay.
    idle_fill_enabled: bool = False
    idle_lookback_days: int = 14
    idle_excess_floor: float = -0.025
    idle_decide_every_sec: float = 900.0  # re-check while core idle + flat
    # AlphaI
    alphai_enabled: bool = True
    alphai_block_on_pick: bool = True  # do not short AlphaI long picks
    alphai_block_on_avoid: bool = True
    alphai_require_macro_or_bear: bool = False  # if True, need macro_caution
    alphai_bearish_size_boost: float = 1.25
    alphai_bullish_size_damp: float = 0.55
    alphai_stale_minutes: float = 90.0
    min_notional_eur: float = 50.0
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    tick_sec: float = 30.0


@dataclass
class ShortPosition:
    base: str
    entry_price: float
    notional_eur: float
    opened_ms: int
    peak_return: float = 0.0  # best short return so far
    holding_id: str = ""
    entry_reason: str = ""
    atr14: float = 0.02

    def __post_init__(self) -> None:
        if not self.holding_id:
            self.holding_id = f"sw-{self.base}-{uuid.uuid4().hex[:8]}"

    def short_return(self, mark: float) -> float:
        if self.entry_price <= 0 or mark <= 0:
            return 0.0
        return (self.entry_price - mark) / self.entry_price

    def unrealized_net(self, mark: float, fee_rt: float) -> float:
        """MTM short PnL minus half round-trip exit fee estimate."""
        ret = self.short_return(mark)
        return self.notional_eur * ret - self.notional_eur * (fee_rt / 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ShortPosition:
        return cls(
            base=str(raw["base"]),
            entry_price=float(raw["entry_price"]),
            notional_eur=float(raw["notional_eur"]),
            opened_ms=int(raw["opened_ms"]),
            peak_return=float(raw.get("peak_return") or 0.0),
            holding_id=str(raw.get("holding_id") or ""),
            entry_reason=str(raw.get("entry_reason") or ""),
            atr14=float(raw.get("atr14") or 0.02),
        )


def fetch_daily_closes(base: str, *, days: int = 260) -> list[tuple[int, float]]:
    """Public Bitvavo daily closes (ts_ms, close)."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    url = (
        f"{BITVAVO_PUBLIC}/{base}-EUR/candles"
        f"?interval=1d&start={start_ms}&end={end_ms}&limit=1000"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        rows = json.load(resp)
    out = sorted(
        ((int(r[0]), float(r[4])) for r in rows),
        key=lambda x: x[0],
    )
    return out


def fetch_daily_ohlc(base: str, *, days: int = 40) -> list[list[float]]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    url = (
        f"{BITVAVO_PUBLIC}/{base}-EUR/candles"
        f"?interval=1d&start={start_ms}&end={end_ms}&limit=200"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        rows = json.load(resp)
    return sorted(
        (
            [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
            for r in rows
        ),
        key=lambda r: r[0],
    )


def _sma(closes: Sequence[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _atr14(rows: Sequence[Sequence[float]]) -> float:
    if len(rows) < 15:
        return 0.02
    rets = []
    for i in range(-14, 0):
        prev = float(rows[i - 1][4])
        cur = float(rows[i][4])
        if prev > 0:
            rets.append(abs(cur / prev - 1.0))
    return sum(rets) / len(rets) if rets else 0.02


def btc_bear_ok(btc_closes: Sequence[float], cfg: ShortWeakestConfig) -> tuple[bool, dict[str, Any]]:
    sma = _sma(btc_closes, cfg.sma_days)
    last = float(btc_closes[-1]) if btc_closes else 0.0
    ok = (not cfg.require_btc_below_sma200) or (sma is not None and last < sma)
    return ok, {"btc": last, "sma200": sma, "bear_ok": ok}


def rank_weakest(
    closes_by_base: Mapping[str, Sequence[float]],
    cfg: ShortWeakestConfig,
    *,
    alphai: AlphaIView | None = None,
    mode: str = "absolute",
    btc_closes: Sequence[float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rank universe by weakness.

    ``absolute``: most negative lookback return (hard-bear mode).
    ``excess``: most negative excess vs BTC (idle-fill when core is flat).
    """
    use_excess = str(mode).lower() == "excess"
    lb = int(cfg.idle_lookback_days if use_excess else cfg.lookback_days)
    floor = float(cfg.idle_excess_floor if use_excess else cfg.mom_floor)
    skip = 0 if use_excess else max(0, int(cfg.skip_days))
    bounce_thr = float(cfg.bounce_block_pct) if not use_excess else 0.0
    btc = list(btc_closes or closes_by_base.get("BTC") or [])
    cands: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    alphai = alphai or AlphaIView()
    for base in cfg.universe:
        series = closes_by_base.get(base) or []
        need = lb + skip + 1
        if len(series) < need:
            rejected.append({"base": base, "reason": "short_history"})
            continue
        end_i = -1 - skip
        start_i = end_i - lb
        mom = float(series[end_i]) / float(series[start_i]) - 1.0
        score_val = mom
        if use_excess:
            if len(btc) < need:
                rejected.append({"base": base, "reason": "btc_short_history"})
                continue
            btc_mom = float(btc[end_i]) / float(btc[start_i]) - 1.0
            score_val = mom - btc_mom
        if score_val > floor:
            rejected.append(
                {
                    "base": base,
                    "reason": "mom_above_floor",
                    "mom": round(mom, 4),
                    "score": round(score_val, 4),
                    "mode": mode,
                }
            )
            continue
        if bounce_thr > 0 and len(series) >= 2:
            day_ret = float(series[-1]) / float(series[-2]) - 1.0
            if day_ret >= bounce_thr:
                rejected.append(
                    {
                        "base": base,
                        "reason": "bounce_block",
                        "day_ret": round(day_ret, 4),
                        "mom": round(mom, 4),
                    }
                )
                continue
        reasons: list[str] = [f"mode={mode}"]
        if skip:
            reasons.append(f"skip={skip}")
        if cfg.alphai_enabled and cfg.alphai_block_on_avoid and base in alphai.avoid:
            rejected.append({"base": base, "reason": "alphai_avoid", "mom": round(mom, 4)})
            continue
        if cfg.alphai_enabled and cfg.alphai_block_on_pick and base in alphai.picks:
            rejected.append({"base": base, "reason": "alphai_long_pick", "mom": round(mom, 4)})
            continue
        size_mult = 1.0
        bear_n = int((alphai.bearish_headline_counts or {}).get(base, 0) or 0)
        bull_n = int((alphai.bullish_headline_counts or {}).get(base, 0) or 0)
        if bear_n > bull_n:
            size_mult *= float(cfg.alphai_bearish_size_boost)
            reasons.append("alphai_bearish_boost")
        elif bull_n > bear_n:
            size_mult *= float(cfg.alphai_bullish_size_damp)
            reasons.append("alphai_bullish_damp")
        pick_score = alphai.pick_scores.get(base) if alphai.pick_scores else None
        if pick_score is not None and float(pick_score) >= 60:
            size_mult *= float(cfg.alphai_bullish_size_damp)
            reasons.append("alphai_high_score_damp")
        cands.append(
            {
                "base": base,
                "mom": mom,
                "score": score_val,
                "size_mult": size_mult,
                "reasons": reasons,
            }
        )
    cands.sort(key=lambda r: float(r["score"]))
    return cands, rejected


def select_shorts(
    cands: Sequence[Mapping[str, Any]],
    cfg: ShortWeakestConfig,
    *,
    cash_eur: float,
    held: set[str],
) -> list[dict[str, Any]]:
    picks = [c for c in cands if c["base"] not in held][: int(cfg.top_n)]
    if not picks:
        return []
    deploy = max(0.0, cash_eur) * float(cfg.deploy_frac)
    if deploy < cfg.min_notional_eur:
        return []
    if cfg.weight_mode == "magnitude":
        mag = sum(abs(float(c.get("score", c["mom"]))) for c in picks) or 1.0
        raw_w = [abs(float(c.get("score", c["mom"]))) / mag for c in picks]
    else:
        raw_w = [1.0 / len(picks)] * len(picks)
    out: list[dict[str, Any]] = []
    for w, c in zip(raw_w, picks):
        w = min(float(w), float(cfg.max_weight))
        notional = deploy * w * float(c.get("size_mult") or 1.0)
        notional = min(notional, deploy * float(cfg.max_weight))
        if notional < cfg.min_notional_eur:
            continue
        out.append(
            {
                "base": c["base"],
                "notional_eur": round(notional, 2),
                "mom": round(float(c["mom"]), 4),
                "reasons": list(c.get("reasons") or []) + [f"mom={float(c['mom']):.3f}"],
            }
        )
    return out


def evaluate_short_exit(
    pos: ShortPosition,
    *,
    mark: float,
    day_ret: float | None,
    cfg: ShortWeakestConfig,
) -> dict[str, Any] | None:
    ret = pos.short_return(mark)
    peak = max(pos.peak_return, ret)
    # adverse: price rose → short return negative
    if float(cfg.hard_stop_pct) > 0 and ret <= -float(cfg.hard_stop_pct):
        return {"reason": "hard_stop", "short_return": ret, "peak_return": peak}
    if float(cfg.trail_pct) > 0 and peak - ret >= float(cfg.trail_pct):
        return {"reason": "trail", "short_return": ret, "peak_return": peak}
    if cfg.vol_spike_exit and day_ret is not None:
        thr = float(cfg.vol_spike_mult) * max(float(pos.atr14), 0.01)
        if day_ret >= thr:
            return {"reason": "vol_spike", "short_return": ret, "peak_return": peak, "day_ret": day_ret}
    return None


def load_alphai_view(path: str | Path | None) -> tuple[AlphaIView, dict[str, Any]]:
    meta: dict[str, Any] = {"path": str(path or ""), "ok": False}
    if not path:
        return AlphaIView(), meta
    p = Path(path)
    if not p.exists():
        meta["reason"] = "missing"
        return AlphaIView(), meta
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        meta["reason"] = f"read_error:{exc}"
        return AlphaIView(), meta
    view = AlphaIView.from_recommendations(payload if isinstance(payload, Mapping) else None)
    meta["ok"] = True
    meta["macro_caution"] = view.macro_caution
    meta["picks"] = sorted(view.picks)
    meta["avoid"] = sorted(view.avoid)
    meta["generated_at_ms"] = view.generated_at_ms
    return view, meta


def alphai_is_stale(view: AlphaIView, cfg: ShortWeakestConfig, *, now_ms: int | None = None) -> bool:
    if not cfg.alphai_enabled or cfg.alphai_stale_minutes <= 0:
        return False
    if view.generated_at_ms is None:
        return False  # unknown age — don't block paper on missing stamp
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    age_min = (now - int(view.generated_at_ms)) / 60_000.0
    return age_min > float(cfg.alphai_stale_minutes)


def default_config() -> ShortWeakestConfig:
    return ShortWeakestConfig()
