"""Dynamic price confirmation for AlphaI picks — no per-coin hardcoding.

Every base is scored from live tape:
* excess return vs BTC (benchmark)
* excess vs peer-median of the current pick set

Scales are continuous in ``[0, 1]``. Binary ``lagging`` is derived from the
scale, never from a fixed coin allow/deny list. Historical pick outcomes can
adapt the lag threshold and per-base reliability multipliers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import Any, Mapping


def excess_vs_benchmark(
    day_returns_pct: Mapping[str, float],
    bases: list[str] | tuple[str, ...] | set[str],
    *,
    benchmark: str = "BTC",
) -> dict[str, float]:
    """``base_day_pct - benchmark_day_pct`` for each requested base."""
    btc_key = str(benchmark or "BTC").upper()
    if btc_key not in day_returns_pct:
        return {}
    try:
        btc_ret = float(day_returns_pct[btc_key])
    except (TypeError, ValueError):
        return {}
    out: dict[str, float] = {}
    for raw in bases:
        base = str(raw or "").upper()
        if not base or base == btc_key or base not in day_returns_pct:
            continue
        try:
            out[base] = float(day_returns_pct[base]) - btc_ret
        except (TypeError, ValueError):
            continue
    return out


def excess_vs_peers(
    day_returns_pct: Mapping[str, float],
    bases: list[str] | tuple[str, ...] | set[str],
) -> dict[str, float]:
    """``base_day_pct - median(peer day pct)`` within the provided set."""
    cleaned: list[tuple[str, float]] = []
    for raw in bases:
        base = str(raw or "").upper()
        if not base or base not in day_returns_pct:
            continue
        try:
            cleaned.append((base, float(day_returns_pct[base])))
        except (TypeError, ValueError):
            continue
    if len(cleaned) < 2:
        return {}
    peer_med = float(median([r for _, r in cleaned]))
    return {base: ret - peer_med for base, ret in cleaned}


def confirm_scale_from_excess(
    vs_btc_pp: float | None,
    vs_peer_pp: float | None = None,
    *,
    lag_pp: float = 1.5,
    full_pp: float = 0.0,
    btc_weight: float = 0.7,
) -> float:
    """Map excess returns → continuous confirm scale in ``[0, 1]``.

    * excess ≤ −lag_pp → 0.0 (full demotion)
    * excess ≥ full_pp → 1.0 (fully confirmed)
    * linear in between

    When peer excess is available, blend with ``btc_weight`` on the BTC leg.
    """
    lag = max(0.25, float(lag_pp))
    full = float(full_pp)
    if full <= -lag:
        full = 0.0

    def _one(excess: float | None) -> float | None:
        if excess is None:
            return None
        if excess <= -lag:
            return 0.0
        if excess >= full:
            return 1.0
        # Map [-lag, full] → [0, 1]
        return (excess + lag) / (full + lag)

    btc_s = _one(None if vs_btc_pp is None else float(vs_btc_pp))
    peer_s = _one(None if vs_peer_pp is None else float(vs_peer_pp))
    if btc_s is None and peer_s is None:
        return 1.0  # no tape → neutral (do not invent a demotion)
    if btc_s is None:
        return max(0.0, min(1.0, float(peer_s)))
    if peer_s is None:
        return max(0.0, min(1.0, float(btc_s)))
    w = max(0.0, min(1.0, float(btc_weight)))
    return max(0.0, min(1.0, w * float(btc_s) + (1.0 - w) * float(peer_s)))


def adaptive_lag_threshold(
    historical_excess_pp: list[float],
    *,
    default_pp: float = 1.5,
    min_samples: int = 20,
    lo_pp: float = 0.75,
    hi_pp: float = 2.5,
) -> float:
    """Derive lag threshold from recent pick excess distribution.

    Uses the median of *negative* excesses (how bad laggards typically are).
    Falls back to ``default_pp`` until enough samples exist. No coin names.
    """
    default = float(default_pp)
    negatives = sorted(float(x) for x in historical_excess_pp if float(x) < 0)
    if len(historical_excess_pp) < int(min_samples) or len(negatives) < max(5, min_samples // 4):
        return default
    mid = negatives[len(negatives) // 2]
    # Median negative excess of −1.2 → threshold 1.2 (symmetric magnitude).
    derived = abs(mid)
    return max(float(lo_pp), min(float(hi_pp), derived if derived > 0 else default))


def classify_price_lags(
    day_returns_pct: Mapping[str, float],
    *,
    btc_base: str = "BTC",
    lag_vs_btc_pp: float = 1.5,
    scale_cutoff: float = 0.45,
    pick_bases: list[str] | tuple[str, ...] | set[str] | None = None,
) -> frozenset[str]:
    """Bases whose dynamic confirm scale falls below ``scale_cutoff``."""
    bases = list(pick_bases) if pick_bases is not None else [
        str(k).upper() for k in day_returns_pct.keys() if str(k).upper() != str(btc_base).upper()
    ]
    check = build_price_check(
        bases,
        day_returns_pct,
        btc_base=btc_base,
        lag_vs_btc_pp=lag_vs_btc_pp,
        scale_cutoff=scale_cutoff,
    )
    return frozenset(str(b).upper() for b in (check.get("lagging") or []))


def build_price_check(
    pick_bases: list[str] | tuple[str, ...] | set[str],
    day_returns_pct: Mapping[str, float],
    *,
    btc_base: str = "BTC",
    lag_vs_btc_pp: float = 1.5,
    full_pp: float = 0.0,
    scale_cutoff: float = 0.45,
    asof: datetime | None = None,
    base_reliability: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Structured, coin-agnostic price-check for daily reports / signals."""
    instant = asof or datetime.now(UTC)
    btc_key = str(btc_base or "BTC").upper()
    picks = [str(b).upper() for b in pick_bases if str(b or "").strip()]
    btc_ret = None
    if btc_key in day_returns_pct:
        try:
            btc_ret = round(float(day_returns_pct[btc_key]), 4)
        except (TypeError, ValueError):
            btc_ret = None

    vs_btc = excess_vs_benchmark(day_returns_pct, picks, benchmark=btc_key)
    vs_peer = excess_vs_peers(day_returns_pct, picks)
    reliability = {
        str(k).upper(): float(v)
        for k, v in (base_reliability or {}).items()
        if k and v is not None
    }

    rows: dict[str, Any] = {}
    scales: dict[str, float] = {}
    confirmed: list[str] = []
    lagged_picks: list[str] = []
    for base in picks:
        if base not in day_returns_pct:
            continue
        try:
            ret = float(day_returns_pct[base])
        except (TypeError, ValueError):
            continue
        excess_btc = vs_btc.get(base)
        excess_peer = vs_peer.get(base)
        scale = confirm_scale_from_excess(
            excess_btc,
            excess_peer,
            lag_pp=lag_vs_btc_pp,
            full_pp=full_pp,
        )
        # Historical reliability (any coin): soft multiplicative haircut.
        rel = reliability.get(base)
        if rel is not None:
            scale = max(0.0, min(1.0, scale * max(0.40, min(1.0, float(rel)))))
        scales[base] = round(scale, 4)
        is_lag = scale < float(scale_cutoff)
        rows[base] = {
            "day_pct": round(ret, 4),
            "vs_btc_pp": None if excess_btc is None else round(excess_btc, 4),
            "vs_peer_pp": None if excess_peer is None else round(excess_peer, 4),
            "confirm_scale": scales[base],
            "reliability": None if rel is None else round(float(rel), 4),
            "lagging": is_lag,
        }
        if is_lag:
            lagged_picks.append(base)
        else:
            confirmed.append(base)

    return {
        "asof": instant.astimezone(UTC).isoformat(),
        "btc_base": btc_key,
        "btc_day_pct": btc_ret,
        "lag_vs_btc_pp": float(lag_vs_btc_pp),
        "full_pp": float(full_pp),
        "scale_cutoff": float(scale_cutoff),
        "adaptive": False,
        "picks": rows,
        "confirm_scales": scales,
        "confirmed": confirmed,
        "lagging": lagged_picks,
    }


def enrich_daily_with_price_check(
    daily: dict[str, Any] | None,
    day_returns_pct: Mapping[str, float],
    *,
    lag_vs_btc_pp: float = 1.5,
    full_pp: float = 0.0,
    scale_cutoff: float = 0.45,
    btc_base: str = "BTC",
    asof: datetime | None = None,
    base_reliability: Mapping[str, float] | None = None,
    adaptive: bool = False,
) -> dict[str, Any] | None:
    """Return a copy of *daily* with dynamic ``price_check`` attached."""
    if not isinstance(daily, dict):
        return daily
    picks = [
        str(p.get("base") or "").upper()
        for p in (daily.get("picks") or [])
        if isinstance(p, dict) and p.get("base")
    ]
    needed = set(picks) | {str(btc_base or "BTC").upper()}
    filtered = {
        str(k).upper(): float(v)
        for k, v in day_returns_pct.items()
        if str(k).upper() in needed
    }
    check = build_price_check(
        picks,
        filtered,
        btc_base=btc_base,
        lag_vs_btc_pp=lag_vs_btc_pp,
        full_pp=full_pp,
        scale_cutoff=scale_cutoff,
        asof=asof,
        base_reliability=base_reliability,
    )
    check["adaptive"] = bool(adaptive)
    out = dict(daily)
    out["price_check"] = check
    out["price_lag_bases"] = list(check.get("lagging") or [])
    out["price_confirm_scales"] = dict(check.get("confirm_scales") or {})
    if base_reliability:
        out["base_reliability"] = {
            str(k).upper(): float(v) for k, v in base_reliability.items()
        }
    return out
