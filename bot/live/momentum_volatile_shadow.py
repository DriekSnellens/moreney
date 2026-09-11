"""Paper shadow for volatile midcaps — AlphaI-first, separate from the core 16.

This is NOT the Momentum Desk logic. The live book keeps its RS/regime rules on
the core 16. Here we only consider a volatile pool *outside* that universe and
refuse to buy unless AlphaI is green, then time/size entries by conviction and
anti-chase filters. Exits adapt: AlphaI flip cuts, strong greens get room,
weak/neutral names trail tighter. Blind volatility chasing stays blocked.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import (
    BAR_MS,
    DEFAULT_CLUSTERS,
    DEFAULT_UNIVERSE,
    AlphaIView,
    BaseStats,
    Candle,
    DeskConfig,
    Position,
    RiskLedger,
    bar_stats,
    evaluate_exit,
    is_decision_time,
    net_pnl_eur,
    universe_stats,
)
from bot.research.momentum_backtest.engine import load_candles

# Midcaps / higher-beta names outside the live core 16.
VOLATILE_POOL: tuple[str, ...] = (
    "HYPE",
    "TAO",
    "WLD",
    "RAY",
    "ONDO",
    "XLM",
    "INJ",
    "HBAR",
    "JUP",
    "PEPE",
    "APT",
    "SEI",
    "ENA",
    "RENDER",
    "TIA",
    "TRX",
    "AAVE",
    "BCH",
)

_VOLATILE_CLUSTERS: dict[str, str] = {
    **dict(DEFAULT_CLUSTERS),
    "HYPE": "PERP",
    "TAO": "AI",
    "WLD": "AI",
    "RAY": "DEFI",
    "ONDO": "DEFI",
    "XLM": "L1",
    "INJ": "L1",
    "HBAR": "L1",
    "JUP": "DEFI",
    "PEPE": "MEME",
    "APT": "L1",
    "SEI": "L1",
    "ENA": "DEFI",
    "RENDER": "AI",
    "TIA": "L1",
    "TRX": "PAY",
    "AAVE": "DEFI",
    "BCH": "PAY",
}

_DEFAULT_ALPHAI_PATH = Path("data/alphai/volatile_recommendations.json")
_MIN_CLIP_EUR = 25.0


@dataclass(frozen=True)
class VolatileShadowConfig:
    """Entry/exit knobs for the AlphaI-first volatile paper book.

    Smart mode (default): AlphaI conviction drives size, entry quality filters
    avoid chase entries, and exits adapt to AlphaI flip / still-green winners.
    """

    decision_hours_utc: tuple[int, ...] = (7, 13, 16)
    clip_eur: float = 650.0
    alphai_clip_mult: float = 1.4
    macro_caution_clip_mult: float = 0.6
    # Concentration beats diversification on this sleeve (12w sweep).
    max_positions: int = 1
    top_n: int = 2
    # Softer than the core desk: AlphaI already did the "which name" work.
    min_volume_eur: float = 400_000.0
    min_ret_24h: float = 0.0  # need some upside print
    max_from_high: float = 0.05
    # Prefer a shallow pullback vs buying the exact high.
    prefer_pullback_from_high: float = 0.008
    # Skip exhaustion chase: big 24h pop already sitting on the high.
    max_chase_ret_24h: float = 0.10
    min_alphai_score: float = 30.0
    # With min_alphai_score=30, excess gate is optional (0 disables).
    weak_score_needs_excess: float = 0.0
    trail_pct: float = 0.04
    trail_tight_after: float = 0.06
    trail_tight_pct: float = 0.025
    # Strong AlphaI winners get more room.
    trail_wide_pct: float = 0.06
    trail_wide_after: float = 0.04
    strong_alphai_score: float = 60.0
    hard_stop_pct: float = 0.05
    time_exit_hours: float = 24.0
    # Still-green winners may hold longer before fee-aware time exit.
    time_exit_hours_green: float = 48.0
    fee_rt: float = 0.003
    day_loss_limit_eur: float = 80.0
    week_loss_limit_eur: float = 200.0
    pause_hours_after_week_limit: float = 48.0
    max_entries_per_base_per_day: int = 1
    skip_weekend_entries: bool = True
    # Hard rule: no AlphaI pick/watch ⇒ no buy (never blind).
    require_alphai_green: bool = True
    # Exit immediately when AlphaI flips a held name to avoid.
    alphai_flip_exits: bool = True
    # Scale clip by AlphaI score conviction.
    conviction_sizing: bool = True
    book_eur: float = 2000.0
    universe: tuple[str, ...] = field(default_factory=tuple)
    clusters: Mapping[str, str] = field(default_factory=dict)
    alphai_scores: Mapping[str, float] = field(default_factory=dict)


def volatile_universe() -> tuple[str, ...]:
    """Volatile pool only — never overlaps the live core 16."""
    core = set(DEFAULT_UNIVERSE)
    return tuple(b for b in VOLATILE_POOL if b not in core)


def _norm_base(raw: Any) -> str | None:
    if isinstance(raw, Mapping):
        raw = raw.get("base") or raw.get("symbol") or raw.get("ticker")
    text = str(raw or "").strip().upper().replace("-EUR", "").replace("EUR", "")
    return text or None


def load_shadow_alphai(
    path: str | Path | None = None,
) -> tuple[AlphaIView, dict[str, Any]]:
    """Load AlphaI picks/avoid/watch. Watch with score≥0 counts as soft pick."""
    p = Path(path) if path else _DEFAULT_ALPHAI_PATH
    meta: dict[str, Any] = {
        "path": str(p),
        "loaded": False,
        "generated_at": None,
        "picks": [],
        "avoid": [],
        "watch": [],
        "macro_caution": False,
        "scores": {},
        "note": "",
    }
    if not p.exists():
        meta["note"] = "AlphaI-bestand ontbreekt — zonder picks koopt deze shadow niets"
        return AlphaIView(), meta
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        meta["note"] = f"AlphaI onleesbaar: {exc}"
        return AlphaIView(), meta

    base_view = AlphaIView.from_recommendations(raw if isinstance(raw, Mapping) else None)
    picks: set[str] = set(base_view.picks)
    avoid: set[str] = set(base_view.avoid)
    scores: dict[str, float] = {}
    pick_rows: list[dict[str, Any]] = []
    avoid_rows: list[dict[str, Any]] = []
    watch_rows: list[dict[str, Any]] = []

    def _row(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, Mapping):
            b = _norm_base(item)
            return {"base": b, "score": None} if b else None
        b = _norm_base(item)
        if not b:
            return None
        try:
            score_f = float(item["score"]) if item.get("score") is not None else None
        except (TypeError, ValueError):
            score_f = None
        if score_f is not None:
            scores[b] = score_f
        return {
            "base": b,
            "score": score_f,
            "rank": item.get("rank"),
            "bullish": list(item.get("bullish_headlines") or [])[:2],
            "bearish": list(item.get("bearish_headlines") or [])[:2],
        }

    if isinstance(raw, Mapping):
        for item in raw.get("picks") or []:
            row = _row(item)
            if row:
                picks.add(row["base"])
                pick_rows.append(row)
        for item in raw.get("avoid") or []:
            row = _row(item)
            if row:
                avoid.add(row["base"])
                avoid_rows.append(row)
        for item in raw.get("watch") or []:
            row = _row(item)
            if not row:
                continue
            watch_rows.append(row)
            if row["score"] is None or row["score"] >= 0:
                picks.add(row["base"])

    picks -= avoid
    # Only keep AlphaI names that are in the volatile (non-core) universe.
    vol = set(volatile_universe())
    picks_vol = frozenset(b for b in picks if b in vol)
    avoid_vol = frozenset(b for b in avoid if b in vol)
    view = AlphaIView(
        picks=picks_vol,
        avoid=avoid_vol,
        macro_caution=bool(base_view.macro_caution),
    )
    meta.update(
        {
            "loaded": True,
            "generated_at": raw.get("generated_at") if isinstance(raw, Mapping) else None,
            "picks": [r for r in pick_rows if r["base"] in vol],
            "avoid": [r for r in avoid_rows if r["base"] in vol],
            "watch": [r for r in watch_rows if r["base"] in vol],
            "macro_caution": view.macro_caution,
            "effective_picks": sorted(view.picks),
            "effective_avoid": sorted(view.avoid),
            "scores": {k: v for k, v in scores.items() if k in vol},
            "core_alphai_ignored": sorted((picks | avoid) - vol),
            "note": (
                "Los van de core-16 desk. Alleen volatile namen met AlphaI ↑ "
                "(pick/watch) mogen binnen; AlphaI ↓ is hard veto. Core-AlphaI "
                f"namen genegeerd: {', '.join(sorted((picks | avoid) - vol)) or '—'}."
            ),
        }
    )
    return view, meta


def shadow_universe(alphai: AlphaIView | None = None) -> tuple[str, ...]:
    """Always the volatile pool; AlphaI only filters entries, not the scan list."""
    return volatile_universe()


def shadow_config(
    live_cfg: DeskConfig | None = None,
    *,
    alphai: AlphaIView | None = None,
    scores: Mapping[str, float] | None = None,
) -> VolatileShadowConfig:
    """Build volatile-shadow knobs. Intentionally not a DeskConfig clone."""
    uni = shadow_universe(alphai)
    clusters = {k: v for k, v in _VOLATILE_CLUSTERS.items() if k in uni}
    for b in uni:
        clusters.setdefault(b, "OTHER")
    clip = 650.0
    # Own schedule (more entry windows than core 7/13) — do not inherit core hours.
    hours = (7, 13, 16)
    if live_cfg is not None:
        # Borrow clip scale only; keep volatile smart decision hours.
        clip = min(650.0, float(live_cfg.clip_eur) * 0.5)
    return VolatileShadowConfig(
        decision_hours_utc=hours,
        clip_eur=clip,
        universe=uni,
        clusters=clusters,
        alphai_scores=dict(scores or {}),
    )


def _to_desk_exit_cfg(cfg: VolatileShadowConfig) -> DeskConfig:
    """Minimal DeskConfig so evaluate_exit can run with volatile trail/stop."""
    return DeskConfig(
        decision_hours_utc=cfg.decision_hours_utc,
        clip_eur=cfg.clip_eur,
        trail_pct=cfg.trail_pct,
        trail_tight_after=cfg.trail_tight_after,
        trail_tight_pct=cfg.trail_tight_pct,
        hard_stop_pct=cfg.hard_stop_pct,
        time_exit_hours=cfg.time_exit_hours,
        fee_rt=cfg.fee_rt,
        alphai_avoid_tightens_trail=True,
        universe=cfg.universe,
        clusters=dict(cfg.clusters),
        skip_weekend_entries=cfg.skip_weekend_entries,
    )


def _decision_cfg(cfg: VolatileShadowConfig) -> DeskConfig:
    return DeskConfig(
        decision_hours_utc=cfg.decision_hours_utc,
        skip_weekend_entries=cfg.skip_weekend_entries,
        decision_every_bar=False,
    )


@dataclass
class _VolCandidate:
    base: str
    excess: float
    ret_24h: float
    from_high: float
    volume_eur: float
    alphai_score: float
    score: float
    reasons: tuple[str, ...]


def _conviction_mult(score: float, cfg: VolatileShadowConfig) -> float:
    """Map AlphaI score → clip multiplier (weak / base / strong)."""
    if not cfg.conviction_sizing:
        return 1.0
    if score >= cfg.strong_alphai_score:
        return 1.25
    if score < cfg.min_alphai_score + 10:
        return 0.75
    return 1.0


def _rank_volatile(
    stats: Mapping[str, BaseStats],
    btc_ret: float,
    cfg: VolatileShadowConfig,
    alphai: AlphaIView,
) -> tuple[list[_VolCandidate], list[dict[str, Any]]]:
    """AlphaI-first ranking with anti-chase / quality filters."""
    rejected: list[dict[str, Any]] = []
    out: list[_VolCandidate] = []
    for base, st in stats.items():
        excess = st.ret_24h - btc_ret
        a_score = float(cfg.alphai_scores.get(base, 10.0 if base in alphai.picks else 0.0))
        why: list[str] = []
        if base in alphai.avoid:
            why.append("alphai_avoid")
        if cfg.require_alphai_green and base not in alphai.picks:
            why.append("no_alphai_green")
        if st.volume_eur < cfg.min_volume_eur:
            why.append("volume_low")
        if st.ret_24h < cfg.min_ret_24h:
            why.append("ret_flat_or_down")
        if st.from_high < -cfg.max_from_high:
            why.append("far_from_high")
        # Sitting on the high after a large pop → chase.
        if (
            st.ret_24h >= cfg.max_chase_ret_24h
            and st.from_high > -cfg.prefer_pullback_from_high
        ):
            why.append("chase_extended")
        if a_score < cfg.min_alphai_score and base in alphai.picks:
            why.append("alphai_score_weak")
        if (
            a_score < cfg.weak_score_needs_excess
            and excess <= 0
            and base in alphai.picks
        ):
            why.append("weak_score_no_excess")
        if why:
            rejected.append(
                {
                    "base": base,
                    "why": why,
                    "excess": round(excess, 4),
                    "ret_24h": round(st.ret_24h, 4),
                    "from_high": round(st.from_high, 4),
                    "alphai_score": cfg.alphai_scores.get(base),
                    "alphai_bias": (
                        "up"
                        if base in alphai.picks
                        else "down"
                        if base in alphai.avoid
                        else "neutral"
                    ),
                }
            )
            continue
        # Prefer slight pullback: reward being off the high a little.
        pullback_bonus = 0.0
        if -cfg.max_from_high <= st.from_high <= -cfg.prefer_pullback_from_high:
            pullback_bonus = 8.0
            pullback_tag = "pullback_entry"
        elif st.from_high > -cfg.prefer_pullback_from_high:
            pullback_bonus = -4.0
            pullback_tag = "near_high"
        else:
            pullback_tag = "deeper_pullback"
        # AlphaI score dominates; excess + pullback quality as secondary.
        score = a_score + 80.0 * excess + pullback_bonus
        reasons = [
            "alphai_green",
            f"alphai_score={a_score:.1f}",
            f"excess={excess:+.4f}",
            pullback_tag,
        ]
        out.append(
            _VolCandidate(
                base=base,
                excess=excess,
                ret_24h=st.ret_24h,
                from_high=st.from_high,
                volume_eur=st.volume_eur,
                alphai_score=a_score,
                score=score,
                reasons=tuple(reasons),
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    rejected.sort(key=lambda r: float(r.get("excess") or -9), reverse=True)
    return out, rejected


def _select_volatile(
    cands: Sequence[_VolCandidate],
    cfg: VolatileShadowConfig,
    *,
    held: set[str],
    blocked: set[str],
    alphai: AlphaIView,
) -> list[dict[str, Any]]:
    slots = max(0, cfg.max_positions - len(held))
    clusters_held = {cfg.clusters.get(b) for b in held}
    macro = cfg.macro_caution_clip_mult if alphai.macro_caution else 1.0
    planned: list[dict[str, Any]] = []
    for c in cands:
        if len(planned) >= min(slots, cfg.top_n):
            break
        if c.base in held or c.base in blocked:
            continue
        cluster = cfg.clusters.get(c.base)
        if cluster is not None and cluster in clusters_held:
            continue
        conv = _conviction_mult(c.alphai_score, cfg)
        clip = cfg.clip_eur * cfg.alphai_clip_mult * macro * conv
        reasons = list(c.reasons)
        if macro != 1.0:
            reasons.append("macro_reduce")
        if conv != 1.0:
            reasons.append(f"conviction_x{conv:.2f}")
        planned.append(
            {
                "base": c.base,
                "clip_eur": round(clip, 2),
                "score": c.score,
                "reasons": reasons,
                "alphai_score": c.alphai_score,
            }
        )
        if cluster is not None:
            clusters_held.add(cluster)
    return planned


def _evaluate_volatile_exit(
    pos: Position,
    bar: Candle,
    cfg: VolatileShadowConfig,
    alphai: AlphaIView,
) -> Any:
    """AlphaI-aware exit: flip-out, wider trail for strong greens, adaptive time exit."""
    from bot.live.momentum_desk import ExitDecision

    # 1) AlphaI flip → cut immediately on close (protect net PnL).
    if cfg.alphai_flip_exits and pos.base in alphai.avoid:
        close = float(bar[4])
        return ExitDecision(
            "alphai_flip",
            pos.gross_return(close),
            urgent=True,
            price=close,
        )

    a_score = float(cfg.alphai_scores.get(pos.base, 0.0))
    still_green = pos.base in alphai.picks
    strong = still_green and a_score >= cfg.strong_alphai_score

    # Per-position adaptive desk cfg.
    trail = cfg.trail_wide_pct if strong else cfg.trail_pct
    tight_after = cfg.trail_wide_after if strong else cfg.trail_tight_after
    time_hrs = cfg.time_exit_hours_green if still_green else cfg.time_exit_hours
    # Weak / no longer green → tighten stop & trail sooner.
    hard = cfg.hard_stop_pct
    tight_pct = cfg.trail_tight_pct
    if not still_green:
        trail = min(trail, cfg.trail_tight_pct)
        tight_after = min(tight_after, 0.03)
        hard = min(hard, 0.025)
        time_hrs = min(time_hrs, 12.0)

    exit_cfg = DeskConfig(
        decision_hours_utc=cfg.decision_hours_utc,
        clip_eur=cfg.clip_eur,
        trail_pct=trail,
        trail_tight_after=tight_after,
        trail_tight_pct=tight_pct,
        hard_stop_pct=hard,
        time_exit_hours=time_hrs,
        fee_rt=cfg.fee_rt,
        alphai_avoid_tightens_trail=True,
        universe=cfg.universe,
        clusters=dict(cfg.clusters),
        skip_weekend_entries=cfg.skip_weekend_entries,
    )
    return evaluate_exit(pos, bar, exit_cfg, alphai=alphai)


def simulate_volatile_alphai(
    candles_by_base: Mapping[str, Sequence[Candle]],
    cfg: VolatileShadowConfig,
    *,
    start_ms: int,
    end_ms: int,
    alphai: AlphaIView,
) -> dict[str, Any]:
    """Bar replay with AlphaI-gated entries (independent of core RS regime)."""
    exit_cfg = _to_desk_exit_cfg(cfg)
    decision_cfg = _decision_cfg(cfg)
    idx = {b: {int(r[0]): r for r in rows} for b, rows in candles_by_base.items()}
    ledger = RiskLedger(
        day_loss_limit_eur=cfg.day_loss_limit_eur,
        week_loss_limit_eur=cfg.week_loss_limit_eur,
        pause_hours=cfg.pause_hours_after_week_limit,
    )
    positions: list[Position] = []
    closed: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    t = start_ms // BAR_MS * BAR_MS
    while t <= end_ms:
        closed_ts = t - BAR_MS
        for pos in list(positions):
            bar = idx.get(pos.base, {}).get(closed_ts)
            if bar is None:
                continue
            decision = _evaluate_volatile_exit(pos, bar, cfg, alphai)
            if decision is None:
                continue
            exit_price = decision.price if decision.price is not None else float(bar[4])
            net = net_pnl_eur(pos, exit_price, None, exit_cfg)
            closed.append(
                {
                    "base": pos.base,
                    "opened_ms": pos.opened_ms,
                    "closed_ms": t,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "notional_eur": pos.notional_eur,
                    "gross_return": decision.gross_return,
                    "peak_return": pos.peak / pos.entry_price - 1.0,
                    "net_eur": net,
                    "reason": decision.reason,
                    "entry_reason": pos.entry_reason,
                }
            )
            ledger.note_close(net, t)
            positions.remove(pos)

        if is_decision_time(t, decision_cfg):
            stats = universe_stats(candles_by_base, t, exit_cfg)
            btc_rows = candles_by_base.get("BTC")
            btc = bar_stats("BTC", btc_rows, t) if btc_rows else None
            btc_ret = btc.ret_24h if btc is not None else 0.0
            cands, rejected = _rank_volatile(stats, btc_ret, cfg, alphai)
            allowed, why = ledger.entries_allowed(t)
            planned: list[dict[str, Any]] = []
            if allowed:
                if alphai.macro_caution and not alphai.picks:
                    why = "macro_caution_no_picks"
                elif cfg.require_alphai_green and not alphai.picks:
                    why = "no_alphai_volatile_picks"
                    allowed = False
                else:
                    planned = _select_volatile(
                        cands,
                        cfg,
                        held={p.base for p in positions},
                        blocked=ledger.blocked_bases(t, cfg.max_entries_per_base_per_day),
                        alphai=alphai,
                    )
            for e in planned:
                clip = float(e["clip_eur"])
                if cfg.book_eur > 0:
                    free = cfg.book_eur - sum(p.notional_eur for p in positions)
                    clip = min(clip, free)
                    if clip < _MIN_CLIP_EUR:
                        continue
                st = stats.get(e["base"])
                if st is None or st.price <= 0:
                    continue
                qty = clip / st.price
                positions.append(
                    Position(
                        base=e["base"],
                        entry_price=st.price,
                        quantity=qty,
                        notional_eur=clip,
                        opened_ms=t,
                        peak=st.price,
                        entry_reason=",".join(e["reasons"]),
                    )
                )
                ledger.note_entry(e["base"], t)
                e["clip_eur"] = round(clip, 2)
                e["price"] = st.price
            decisions.append(
                {
                    "t_ms": t,
                    "btc_ret": btc_ret,
                    "allowed": allowed,
                    "block_reason": "" if allowed else why,
                    "candidates": [c.base for c in cands[:6]],
                    "rejected": rejected[:8],
                    "entries": [
                        f"{e['base']}@{e['clip_eur']:.0f}" for e in planned if e.get("price")
                    ],
                    "planned": planned,
                }
            )
        t += BAR_MS

    open_mtm: list[dict[str, Any]] = []
    for pos in positions:
        rows = [r for r in (candles_by_base.get(pos.base) or []) if int(r[0]) < end_ms]
        last = rows[-1] if rows else None
        px = float(last[4]) if last else pos.entry_price
        open_mtm.append(
            {
                "base": pos.base,
                "opened": _fmt(pos.opened_ms),
                "entry_price": pos.entry_price,
                "mark": px,
                "gross_return": round(pos.gross_return(px), 4),
                "peak_return": round(pos.peak / pos.entry_price - 1.0, 4),
                "net_eur": round(net_pnl_eur(pos, px, None, exit_cfg), 2),
                "entry_reason": pos.entry_reason,
            }
        )
    wins = sum(1 for t in closed if t["net_eur"] > 0)
    n = len(closed)
    return {
        "closed": closed,
        "open_mtm": open_mtm,
        "decisions": decisions,
        "summary": {
            "trades": n,
            "open": len(open_mtm),
            "win_rate": round(wins / n, 3) if n else None,
            "realized_eur": round(sum(t["net_eur"] for t in closed), 2),
            "open_mtm_eur": round(sum(float(m["net_eur"]) for m in open_mtm), 2),
            "total_eur": round(
                sum(t["net_eur"] for t in closed) + sum(float(m["net_eur"]) for m in open_mtm),
                2,
            ),
            "fees_eur": round(sum(t["notional_eur"] for t in closed) * cfg.fee_rt, 2),
            "decision_points": len(decisions),
            "entries_attempted": sum(len(d.get("entries") or []) for d in decisions),
        },
    }


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _candle_cache_dir() -> Path:
    preferred = Path("./data/momentum_candles")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".shadow_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except OSError:
        fallback = Path("/tmp/moreney_volatile_candles")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback



def refresh_volatile_alphai(
    path: str | Path | None = None,
    *,
    force: bool = False,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Refresh AlphaI picks scoped to the volatile pool (per-symbol news).

    Writes a separate JSON from the core-16 daily recommendations so the live
    Momentum Desk majors feed stays untouched.
    """
    import os

    from bot.core.config import get_settings
    from bot.integrations.alphai.client import AlphaIClient
    from bot.integrations.alphai.daily_recommendations import (
        load_daily_recommendations,
        maybe_refresh_daily,
    )

    settings = get_settings()
    out_path = Path(path) if path else Path(
        str(
            getattr(settings, "alphai_volatile_recommendations_path", None)
            or _DEFAULT_ALPHAI_PATH
        )
    )
    if client is None:
        key = getattr(settings, "alphai_api_key", None)
        secret = (
            key.get_secret_value()
            if key is not None and hasattr(key, "get_secret_value")
            else (str(key) if key else os.environ.get("ALPHAI_API_KEY", ""))
        )
        if not secret:
            return load_daily_recommendations(out_path)
        client = AlphaIClient(str(secret))

    focus = set(volatile_universe())
    return maybe_refresh_daily(
        client,
        out_path,
        focus_bases=focus,
        enabled=True,
        min_relevance=int(
            getattr(settings, "alphai_daily_recommendations_min_relevance", 6) or 6
        ),
        top_n=int(getattr(settings, "alphai_daily_recommendations_top_n", 8) or 8),
        update_hour_local=int(
            getattr(settings, "alphai_daily_recommendations_hour", 12) or 12
        ),
        interval_minutes=int(
            getattr(settings, "alphai_recommendations_interval_minutes", 15) or 15
        ),
        interval_hours=int(
            getattr(settings, "alphai_recommendations_interval_hours", 1) or 1
        ),
        macro_caution=False,
        per_symbol_news=True,
        force=force,
    )


def build_volatile_shadow(
    *,
    days: int = 14,
    live_cfg: DeskConfig | None = None,
    end_ms: int | None = None,
    refresh: bool = False,
    alphai_path: str | Path | None = None,
    refresh_alphai: bool | None = None,
) -> dict[str, Any]:
    """Day-by-day AlphaI-first volatile paper report (no live orders)."""
    # Default: refresh AlphaI when candle refresh is requested; always soft-refresh
    # if the volatile feed is missing.
    do_alphai = refresh if refresh_alphai is None else bool(refresh_alphai)
    resolved_alphai = Path(alphai_path) if alphai_path else _DEFAULT_ALPHAI_PATH
    if do_alphai or not resolved_alphai.exists():
        with contextlib.suppress(Exception):
            refresh_volatile_alphai(resolved_alphai, force=bool(do_alphai))
    alphai, alphai_meta = load_shadow_alphai(resolved_alphai)
    cfg = shadow_config(live_cfg, alphai=alphai, scores=alphai_meta.get("scores") or {})
    uni = cfg.universe
    if not uni:
        return {
            "ok": False,
            "reason": "empty_volatile_universe",
            "universe": [],
            "alphai": alphai_meta,
        }
    end = end_ms if end_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    end = end // BAR_MS * BAR_MS
    start = end - max(1, int(days)) * 86_400_000
    candles = load_candles(
        ("BTC", *uni),
        days=max(int(days) + 2, 5),
        end_ms=end,
        refresh=refresh,
        cache_dir=_candle_cache_dir(),
    )
    res = simulate_volatile_alphai(candles, cfg, start_ms=start, end_ms=end, alphai=alphai)

    by_day: dict[str, dict[str, Any]] = {}

    def slot(day: str) -> dict[str, Any]:
        if day not in by_day:
            by_day[day] = {
                "day": day,
                "regime_on": 0,
                "regime_off": 0,
                "would_buy": [],
                "exits": [],
                "rejected": [],
                "day_net_eur": 0.0,
                "notes": [],
            }
        return by_day[day]

    for d in res["decisions"]:
        day = _day_key(int(d["t_ms"]))
        s = slot(day)
        if d.get("allowed") and d.get("entries"):
            s["regime_on"] += 1  # reused field: "active entry slots"
        else:
            s["regime_off"] += 1
            if d.get("block_reason"):
                s["notes"].append(str(d["block_reason"]))
        if d.get("rejected") and not s["rejected"]:
            s["rejected"] = list(d["rejected"])[:6]
        for raw in d.get("entries") or []:
            token = str(raw)
            base = token.split("@", 1)[0].upper()
            clip = None
            if "@" in token:
                try:
                    clip = float(token.split("@", 1)[1])
                except ValueError:
                    clip = None
            planned = next((p for p in (d.get("planned") or []) if p.get("base") == base), {})
            s["would_buy"].append(
                {
                    "base": base,
                    "at": _iso(int(d["t_ms"])),
                    "ts_ms": int(d["t_ms"]),
                    "notional_eur": clip,
                    "btc_ret": round(float(d.get("btc_ret") or 0), 4),
                    "alphai_pick": True,
                    "alphai_avoid": False,
                    "alphai_bias": "up",
                    "alphai_score": planned.get("alphai_score"),
                    "reasons": planned.get("reasons") or ["alphai_green"],
                }
            )

    trade_by_open: dict[tuple[str, int], dict[str, Any]] = {}
    for t in res["closed"]:
        trade_by_open[(t["base"], int(t["opened_ms"]))] = t
        day = _day_key(int(t["closed_ms"]))
        s = slot(day)
        s["exits"].append(
            {
                "base": t["base"],
                "opened": _iso(int(t["opened_ms"])),
                "closed": _iso(int(t["closed_ms"])),
                "hold_h": round((t["closed_ms"] - t["opened_ms"]) / 3_600_000, 2),
                "notional_eur": round(float(t["notional_eur"]), 2),
                "gross_return": round(float(t["gross_return"]), 4),
                "peak_return": round(float(t["peak_return"]), 4),
                "net_eur": round(float(t["net_eur"]), 2),
                "exit_reason": t["reason"],
                "entry_reason": t.get("entry_reason"),
            }
        )
        s["day_net_eur"] = round(float(s["day_net_eur"]) + float(t["net_eur"]), 2)

    for buy in (b for s in by_day.values() for b in s["would_buy"]):
        key = (buy["base"], int(buy["ts_ms"]))
        t = trade_by_open.get(key)
        if t is None:
            opened_label = _fmt(int(buy["ts_ms"]))
            for m in res["open_mtm"]:
                if m.get("base") == buy["base"] and str(m.get("opened")) == opened_label:
                    buy["status"] = "open"
                    buy["unrealized_net_eur"] = round(float(m.get("net_eur") or 0), 2)
                    break
            else:
                buy["status"] = "planned"
            continue
        buy["notional_eur"] = round(float(t["notional_eur"]), 2)
        buy["status"] = "closed"
        buy["net_eur"] = round(float(t["net_eur"]), 2)
        buy["exit_reason"] = t["reason"]
        buy["closed"] = _iso(int(t["closed_ms"]))
        buy["entry_reason"] = t.get("entry_reason")

    days_out = [by_day[k] for k in sorted(by_day.keys(), reverse=True)]
    summary = res["summary"]
    pick_net = round(sum(float(t["net_eur"]) for t in res["closed"]), 2)
    return {
        "ok": True,
        "shadow": True,
        "mode": "alphai_first_volatile",
        "label": "Volatile shadow · AlphaI-smart (los van core 16)",
        "universe": list(uni),
        "alphai": alphai_meta,
        "window": {"start": _iso(start), "end": _iso(end), "days": int(days)},
        "config": {
            "logic": "AlphaI-smart · conviction size · anti-chase · adaptive exits",
            "decision_hours_utc": list(cfg.decision_hours_utc),
            "clip_eur": cfg.clip_eur,
            "book_eur": cfg.book_eur,
            "min_volume_eur": cfg.min_volume_eur,
            "min_ret_24h": cfg.min_ret_24h,
            "max_from_high": cfg.max_from_high,
            "trail_pct": cfg.trail_pct,
            "hard_stop_pct": cfg.hard_stop_pct,
            "alphai_clip_mult": cfg.alphai_clip_mult,
            "require_alphai_green": cfg.require_alphai_green,
            "alphai_flip_exits": cfg.alphai_flip_exits,
            "conviction_sizing": cfg.conviction_sizing,
            "min_alphai_score": cfg.min_alphai_score,
            "max_chase_ret_24h": cfg.max_chase_ret_24h,
            "time_exit_hours_green": cfg.time_exit_hours_green,
        },
        "summary": {
            **summary,
            "volatile_net_eur": pick_net,
            "open_positions": len(res["open_mtm"]),
            "alphai_pick_trades": summary.get("trades") or 0,
            "alphai_pick_net_eur": pick_net,
        },
        "open": res["open_mtm"],
        "days": days_out,
        "trades": [
            {
                **t,
                "opened": _fmt(int(t["opened_ms"])),
                "closed": _fmt(int(t["closed_ms"])),
                "hold_h": round((t["closed_ms"] - t["opened_ms"]) / 3_600_000, 2),
            }
            for t in res["closed"]
        ],
        "now": _current_outlook(candles, cfg, alphai),
    }


def _current_outlook(
    candles: Mapping[str, Sequence[Candle]],
    cfg: VolatileShadowConfig,
    alphai: AlphaIView,
) -> dict[str, Any]:
    """What the AlphaI-first book would do on the latest closed bar."""
    end = int(datetime.now(UTC).timestamp() * 1000) // BAR_MS * BAR_MS
    exit_cfg = _to_desk_exit_cfg(cfg)
    stats = universe_stats(candles, end, exit_cfg)
    btc_rows = candles.get("BTC")
    btc = bar_stats("BTC", btc_rows, end) if btc_rows else None
    btc_ret = btc.ret_24h if btc is not None else 0.0
    cands, rejected = _rank_volatile(stats, btc_ret, cfg, alphai)
    planned = _select_volatile(cands, cfg, held=set(), blocked=set(), alphai=alphai)
    return {
        "at": _iso(end),
        "btc_ret": round(btc_ret, 4),
        "alphai_picks_in_pool": sorted(alphai.picks),
        "alphai_avoid_in_pool": sorted(alphai.avoid),
        "macro_caution": alphai.macro_caution,
        "would_buy": planned,
        "rejected_top": rejected[:8],
        "block": (
            None
            if planned
            else ("no_alphai_volatile_picks" if not alphai.picks else "no_name_passed_soft_filters")
        ),
    }


def render_volatile_shadow_html(payload: Mapping[str, Any]) -> str:
    from html import escape

    if not payload.get("ok"):
        return (
            f'<div class="hint bad">Shadow niet beschikbaar: '
            f"{escape(str(payload.get('reason') or 'onbekend'))}</div>"
        )

    summary = payload.get("summary") or {}
    window = payload.get("window") or {}
    cfg = payload.get("config") or {}
    alphai = payload.get("alphai") or {}
    now = payload.get("now") or {}
    uni = ", ".join(escape(str(b)) for b in (payload.get("universe") or []))

    def eur(v: Any, *, signed: bool = True) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "—"
        return f"{x:+,.2f} €" if signed else f"{x:,.2f} €"

    def cls(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        return "good" if x > 0 else "bad" if x < 0 else ""

    def bias_tag(b: Mapping[str, Any]) -> str:
        if b.get("alphai_pick") or str(b.get("alphai_bias")) == "up":
            return " <span class='good'>AlphaI ↑</span>"
        if b.get("alphai_avoid") or str(b.get("alphai_bias")) == "down":
            return " <span class='bad'>AlphaI ↓</span>"
        return ""

    now_buys = now.get("would_buy") or []
    now_lines = (
        "".join(
            f"<li><strong>{escape(str(b.get('base')))}</strong> "
            f"clip {eur(b.get('clip_eur'), signed=False)} · "
            f"{escape(', '.join(b.get('reasons') or []))}</li>"
            for b in now_buys
        )
        or f"<li class='muted'>{escape(str(now.get('block') or 'niets'))}</li>"
    )

    now_html = (
        '<div class="card section"><div class="card-head"><h2>Nu (AlphaI-first)</h2>'
        f'<span class="muted">{escape(str(now.get("at") or ""))}</span></div>'
        f"<p>Volatile AlphaI ↑: "
        f"<strong>{escape(', '.join(now.get('alphai_picks_in_pool') or []) or 'geen')}</strong>"
        f" · ↓ {escape(', '.join(now.get('alphai_avoid_in_pool') or []) or '—')}"
        f" · macro {'aan' if now.get('macro_caution') else 'uit'}</p>"
        f"<h3 style='font-size:.85rem;margin:.5rem 0 .3rem'>Zou nu kopen</h3>"
        f"<ul style='margin:0;padding-left:1.1rem'>{now_lines}</ul></div>"
    )

    alphai_html = (
        '<div class="card section"><div class="card-head"><h2>AlphaI (alleen volatile)</h2>'
        f'<span class="muted">{escape(str(alphai.get("generated_at") or "—"))}</span></div>'
        f"<p>Macro caution: <strong>{'ja' if alphai.get('macro_caution') else 'nee'}</strong>"
        f" · pick-clip ×{float(cfg.get('alphai_clip_mult') or 1.4):.1f}</p>"
        f'<p class="muted" style="font-size:.78rem">'
        f"{escape(str(alphai.get('note') or ''))}</p>"
        "<div class='rules' style='margin-top:.5rem'>"
        f"<div><span>Picks in pool (↑)</span>"
        f"{escape(', '.join(alphai.get('effective_picks') or []) or '—')}</div>"
        f"<div><span>Avoid in pool (↓)</span>"
        f"{escape(', '.join(alphai.get('effective_avoid') or []) or '—')}</div>"
        f"<div><span>Core-AlphaI genegeerd</span>"
        f"{escape(', '.join(alphai.get('core_alphai_ignored') or []) or '—')}</div>"
        "</div></div>"
    )

    rows: list[str] = []
    for day in payload.get("days") or []:
        buys = day.get("would_buy") or []
        exits = day.get("exits") or []
        if not buys and not exits:
            continue
        buy_lines = []
        for b in buys:
            net = b.get("net_eur")
            status = escape(str(b.get("status") or ""))
            extra = ""
            if net is not None:
                reason = escape(str(b.get("exit_reason") or ""))
                extra = f" → <span class='{cls(net)}'>{eur(net)}</span> ({reason})"
            elif b.get("unrealized_net_eur") is not None:
                u = b["unrealized_net_eur"]
                extra = f" → open <span class='{cls(u)}'>{eur(u)}</span>"
            buy_lines.append(
                f"<li><strong>{escape(str(b.get('base')))}</strong>{bias_tag(b)} "
                f"@ {escape(str(b.get('at')))} "
                f"· {eur(b.get('notional_eur'), signed=False)} · {status}{extra}</li>"
            )
        exit_lines = []
        for x in exits:
            exit_lines.append(
                f"<li><strong>{escape(str(x.get('base')))}</strong> "
                f"{escape(str(x.get('opened')))} → {escape(str(x.get('closed')))} "
                f"· <span class='{cls(x.get('net_eur'))}'>{eur(x.get('net_eur'))}</span> "
                f"· {escape(str(x.get('exit_reason') or ''))}</li>"
            )
        day_net = eur(day.get("day_net_eur"))
        note = ""
        if day.get("notes"):
            note = (
                f"<p class='muted' style='font-size:.75rem'>"
                f"{escape(', '.join(dict.fromkeys(day.get('notes') or [])))}</p>"
            )
        rows.append(
            '<div class="card section">'
            f'<div class="card-head"><h2>{escape(str(day.get("day")))}</h2>'
            f'<span class="{cls(day.get("day_net_eur"))}">{day_net}</span></div>'
            + note
            + (
                "<h3 style='font-size:.85rem;margin:.6rem 0 .3rem'>Zou kopen</h3>"
                f"<ul style='margin:0;padding-left:1.1rem'>{''.join(buy_lines)}</ul>"
                if buy_lines
                else '<p class="muted">Geen AlphaI-green entry deze dag.</p>'
            )
            + (
                "<h3 style='font-size:.85rem;margin:.8rem 0 .3rem'>Exits (P&amp;L)</h3>"
                f"<ul style='margin:0;padding-left:1.1rem'>{''.join(exit_lines)}</ul>"
                if exit_lines
                else ""
            )
            + "</div>"
        )

    open_rows = payload.get("open") or []
    open_html = ""
    if open_rows:
        items = "".join(
            f"<li><strong>{escape(str(o.get('base')))}</strong> "
            f"<span class='{cls(o.get('net_eur'))}'>{eur(o.get('net_eur'))}</span></li>"
            for o in open_rows
        )
        open_html = (
            '<div class="card section"><h2>Nog open (MTM)</h2>'
            f"<ul style='margin:0;padding-left:1.1rem'>{items}</ul></div>"
        )

    label = escape(str(payload.get("label")))
    w0 = escape(str(window.get("start")))
    w1 = escape(str(window.get("end")))
    hours = escape(",".join(str(h) for h in (cfg.get("decision_hours_utc") or [])))
    vol_m = float(cfg.get("min_volume_eur") or 0) / 1e6
    return (
        '<div class="hint warn">PAPER SHADOW · <strong>los van de core 16 én van live</strong>. '
        "Alleen volatile namen met AlphaI ↑; geen RS-regime van de live desk. "
        "Geen echte orders — research replay.</div>"
        f'<div class="card section"><div class="card-head"><h2>Paper · {label}</h2>'
        f'<span class="muted">{w0} → {w1}</span></div>'
        f"<p>Universe ({len(payload.get('universe') or [])}): {uni}</p>"
        f"<p>Logica: {escape(str(cfg.get('logic') or 'AlphaI-first'))}</p>"
        f"<p>Clip {eur(cfg.get('clip_eur'), signed=False)} · "
        f"book {eur(cfg.get('book_eur'), signed=False)} · "
        f"uren {hours} UTC · min vol {vol_m:.1f}M · "
        f"from_high ≤ {100 * float(cfg.get('max_from_high') or 0):.0f}%</p>"
        f"<p>Trades <strong>{summary.get('trades') or 0}</strong> · "
        f"win {summary.get('win_rate') or '—'} · "
        f"gerealiseerd <strong class='{cls(summary.get('realized_eur'))}'>"
        f"{eur(summary.get('realized_eur'))}</strong></p>"
        '<p style="margin-top:.6rem">'
        '<a class="muted" href="/live/momentum">← terug naar live desk</a> · '
        '<a class="muted" href="/live/momentum/volatile?format=json">JSON</a> · '
        '<a class="muted" href="/live/momentum/volatile?days=14">14d</a> · '
        '<a class="muted" href="/live/momentum/volatile?days=84">12w</a></p></div>'
        + now_html
        + alphai_html
        + open_html
        + (
            "".join(rows)
            if rows
            else '<p class="muted">Geen AlphaI-green trades in dit venster.</p>'
        )
    )


def render_volatile_live_html(live: Mapping[str, Any] | None) -> str:
    """LIVE volatile sleeve panel (operator dashboard body)."""
    from html import escape

    if not live:
        return (
            '<div class="hint warn">LIVE sleeve status niet beschikbaar. '
            'Check <code>/live/momentum/volatile/status</code>.</div>'
        )

    def eur(v: Any, *, signed: bool = True) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "—"
        return f"{x:+,.2f} €" if signed else f"{x:,.2f} €"

    def cls(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        return "good" if x > 0 else "bad" if x < 0 else ""

    running = bool(live.get("running"))
    dry = bool(live.get("dry_run"))
    enabled = bool(live.get("enabled_setting"))
    allow_live = bool(live.get("allow_live", False))
    if running and not dry:
        pill = '<span class="pill live"><span class="dot"></span>LIVE</span>'
        mode = "armed · echte orders"
    elif running and dry:
        pill = '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        mode = "paper/dry-run · geen echte orders"
    elif enabled:
        pill = '<span class="pill obs"><span class="dot"></span>PAPER / STOP</span>'
        mode = "enabled · start paper om data te verzamelen"
    else:
        pill = '<span class="pill obs"><span class="dot"></span>OFF</span>'
        mode = "MOMENTUM_VOLATILE_ENABLED=false"

    positions = live.get("positions") or []
    pos_html: list[str] = []
    for p in positions:
        hid = escape(str(p.get("holding_id") or ""))
        base = escape(str(p.get("base") or ""))
        net = p.get("unrealized_net_eur")
        gross = p.get("gross_return")
        try:
            gross_s = f"{float(gross)*100:+.2f}%"
        except (TypeError, ValueError):
            gross_s = "—"
        pos_html.append(
            "<li style='margin-bottom:.45rem'>"
            f"<strong>{base}</strong> · {escape(str(p.get('venue') or ''))} · "
            f"{eur(p.get('notional_eur'), signed=False)} · "
            f"<span class='{cls(net)}'>{eur(net)}</span> ({gross_s})"
            f'<form method="post" action="/live/momentum/volatile/sell" '
            'style="display:inline;margin-left:.4rem">'
            f'<input type="hidden" name="holding_id" value="{hid}">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn danger" style="padding:.15rem .45rem;'
            'font-size:.72rem">Verkoop</button></form></li>'
        )
    if not pos_html:
        pos_html.append("<li class='muted'>Geen open paper posities</li>")

    venues = ", ".join(escape(str(v)) for v in (live.get("venues") or [])) or "—"
    nxt = escape(str(live.get("next_decision") or "—"))
    err = live.get("last_error")
    err_html = (
        f'<p class="bad" style="font-size:.8rem">Error: {escape(str(err))}</p>'
        if err
        else ""
    )
    actions = (
        '<div style="display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.7rem">'
        '<form method="post" action="/live/momentum/volatile/decide">'
        '<input type="hidden" name="execute" value="0">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Decide (preview)</button></form>'
        '<form method="post" action="/live/momentum/volatile/decide">'
        '<input type="hidden" name="execute" value="1">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Decide + paper execute</button></form>'
        + (
            '<form method="post" action="/live/momentum/volatile/sell-all">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn danger">Verkoop alles</button></form>'
            if positions
            else ""
        )
        + '<form method="post" action="/live/momentum/volatile/stop">'
        '<input type="hidden" name="redirect" value="1">'
        '<button type="submit" class="btn">Stop sleeve</button></form>'
        "</div>"
    )
    if not running and enabled:
        start_bits = [
            '<div style="display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.7rem">',
            '<form method="post" action="/live/momentum/volatile/start">'
            '<input type="hidden" name="dry_run" value="true">'
            '<input type="hidden" name="redirect" value="1">'
            '<button type="submit" class="btn">Start PAPER</button></form>',
        ]
        if allow_live:
            start_bits.append(
                '<form method="post" action="/live/momentum/volatile/start">'
                '<input type="hidden" name="dry_run" value="false">'
                '<input type="hidden" name="redirect" value="1">'
                '<button type="submit" class="btn">Start LIVE</button></form>'
            )
        else:
            start_bits.append(
                '<span class="muted" style="font-size:.75rem;align-self:center">'
                "Live orders uit (ALLOW_LIVE=false)</span>"
            )
        start_bits.append("</div>")
        actions = "".join(start_bits)

    risk = live.get("risk") or {}
    risk_html = (
        f"<p>Day P&amp;L {eur(risk.get('day_realized_eur'))} · "
        f"week {eur(risk.get('week_realized_eur'))} · "
        f"entries {escape(str(risk.get('block_reason') or 'ok'))}</p>"
    )
    title = "PAPER volatile sleeve" if dry or not allow_live else "LIVE volatile sleeve"
    return (
        f'<div class="hint {"warn" if dry or not allow_live else "good"}">'
        f"<strong>{title}</strong> — apart van de core 16. "
        f"{escape(mode)}.</div>"
        '<div class="card section"><div class="card-head">'
        f"<h2>{'Paper' if dry or not allow_live else 'Live'} volatile</h2>{pill}</div>"
        f"<p>Book {eur(live.get('book_eur'), signed=False)} · "
        f"left {eur(live.get('book_left_eur'), signed=False)} · "
        f"deployed {eur(live.get('deployed_eur') or live.get('exposure_eur'), signed=False)}</p>"
        f"<p>Venues {venues} · next decision <strong>{nxt}</strong></p>"
        f"<p>Realized <strong class='{cls(live.get('realized_total_eur'))}'>"
        f"{eur(live.get('realized_total_eur'))}</strong> · "
        f"unrealized <strong class='{cls(live.get('unrealized_net_eur'))}'>"
        f"{eur(live.get('unrealized_net_eur'))}</strong> · "
        f"trades {int(live.get('trade_count') or 0)}</p>"
        f"{risk_html}"
        f"{err_html}"
        "<h3 style='font-size:.85rem;margin:.6rem 0 .3rem'>Open posities</h3>"
        f"<ul style='margin:0;padding-left:1.1rem'>{''.join(pos_html)}</ul>"
        f"{actions}"
        '<p class="muted" style="margin-top:.6rem;font-size:.75rem">'
        '<a href="/live/momentum/volatile/status">status JSON</a> · '
        '<a href="/live/momentum">core desk</a></p></div>'
    )


def render_volatile_live_page(
    live: Mapping[str, Any] | None,
    *,
    notice: str | None = None,
    shadow_html: str | None = None,
) -> str:
    """Operator page: paper/live sleeve + optional paper-shadow research."""
    from html import escape

    from bot.live.dashboard_v2 import dashboard_css
    from bot.live.momentum_dashboard import _CSS

    live_html = render_volatile_live_html(live)
    notice_html = f'<div class="hint">{escape(notice)}</div>' if notice else ""
    running = bool((live or {}).get("running"))
    dry = bool((live or {}).get("dry_run", True))
    allow_live = bool((live or {}).get("allow_live", False))
    if running and not dry and allow_live:
        top_pill = '<span class="pill live"><span class="dot"></span>LIVE</span>'
        sub = "Echte orders · los van core 16"
    elif running:
        top_pill = '<span class="pill obs"><span class="dot"></span>PAPER</span>'
        sub = "Paper sleeve · data verzamelen · los van core 16"
    else:
        top_pill = '<span class="pill obs"><span class="dot"></span>STOP</span>'
        sub = "Paper mode · sleeve gestopt · los van core 16"
    shadow_block = shadow_html or ""
    return f"""<!doctype html>
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Volatile · Moreney</title>
<style>{dashboard_css()}{_CSS}
  ul {{ font-size: .85rem; line-height: 1.45; }}
</style></head>
<body><div class="wrap">
  <div class="topbar"><div>
    <div class="brand">Volatile</div>
    <div class="sub">{sub}</div>
  </div>
  {top_pill}
  </div>
  {notice_html}
  {live_html}
  {shadow_block}
</div></body></html>"""


def render_volatile_shadow_page(
    payload: Mapping[str, Any],
    *,
    live: Mapping[str, Any] | None = None,
    notice: str | None = None,
) -> str:
    """Paper sleeve + research shadow replay on one page."""
    return render_volatile_live_page(
        live,
        notice=notice,
        shadow_html=render_volatile_shadow_html(payload),
    )


__all__ = [
    "VOLATILE_POOL",
    "VolatileShadowConfig",
    "build_volatile_shadow",
    "load_shadow_alphai",
    "refresh_volatile_alphai",
    "render_volatile_live_html",
    "render_volatile_live_page",
    "render_volatile_shadow_html",
    "render_volatile_shadow_page",
    "shadow_config",
    "shadow_universe",
    "simulate_volatile_alphai",
    "volatile_universe",
]
