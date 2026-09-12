#!/usr/bin/env python3
"""A/B: current AlphaI volatile sleeve vs pure max-mover chase (research).

Question: if the volatile sleeve bought the pool names that move the most
(24h return), instead of AlphaI-gated anti-chase entries, what would PnL be?

Long-only desk — "dalen" cannot be shorted; mover mode buys the strongest
upside prints in the volatile pool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bot.live.momentum_desk import (
    BAR_MS,
    AlphaIView,
    DeskConfig,
    Position,
    RiskLedger,
    bar_stats,
    evaluate_exit,
    is_decision_time,
    net_pnl_eur,
    universe_stats,
)
from bot.live.momentum_volatile_shadow import (
    VolatileShadowConfig,
    _MIN_CLIP_EUR,
    _candle_cache_dir,
    _decision_cfg,
    _evaluate_volatile_exit,
    _fmt,
    _rank_volatile,
    _select_volatile,
    _to_desk_exit_cfg,
    load_shadow_alphai,
    shadow_config,
    volatile_universe,
)
from bot.research.momentum_backtest.engine import load_candles

Mode = Literal["current", "max_mover_hold", "max_mover_rotate"]

MIN_AGE_H = 3.0
MOVE_GAP = 0.02  # challenger needs +2pp 24h ret vs held to rotate
FLAT_GROSS = 0.003
VOLUME_FLOOR = 200_000.0  # liquidity only; lower than current sleeve


@dataclass(frozen=True)
class _MoverCand:
    base: str
    ret_24h: float
    from_high: float
    volume_eur: float
    excess: float
    score: float
    reasons: tuple[str, ...]


def _rank_max_mover(
    stats: dict[str, Any],
    btc_ret: float,
) -> tuple[list[_MoverCand], list[dict[str, Any]]]:
    """Rank volatile pool by raw 24h return — chase the biggest upside movers."""
    rejected: list[dict[str, Any]] = []
    out: list[_MoverCand] = []
    for base, st in stats.items():
        why: list[str] = []
        if st.volume_eur < VOLUME_FLOOR:
            why.append("volume_low")
        if st.ret_24h <= 0:
            why.append("ret_flat_or_down")
        if why:
            rejected.append(
                {
                    "base": base,
                    "why": why,
                    "ret_24h": round(st.ret_24h, 4),
                    "from_high": round(st.from_high, 4),
                    "volume_eur": round(st.volume_eur, 0),
                }
            )
            continue
        excess = st.ret_24h - btc_ret
        # Score = 24h return in bps so ranking is pure mover magnitude.
        score = st.ret_24h * 10_000.0
        out.append(
            _MoverCand(
                base=base,
                ret_24h=st.ret_24h,
                from_high=st.from_high,
                volume_eur=st.volume_eur,
                excess=excess,
                score=score,
                reasons=(
                    "max_mover",
                    f"ret_24h={st.ret_24h:+.4f}",
                    f"from_high={st.from_high:+.4f}",
                ),
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    rejected.sort(key=lambda r: float(r.get("ret_24h") or -9), reverse=True)
    return out, rejected


def _select_mover(
    cands: list[_MoverCand],
    cfg: VolatileShadowConfig,
    *,
    held: set[str],
) -> list[dict[str, Any]]:
    slots = max(0, cfg.max_positions - len(held))
    planned: list[dict[str, Any]] = []
    for c in cands:
        if len(planned) >= min(slots, cfg.top_n):
            break
        if c.base in held:
            continue
        planned.append(
            {
                "base": c.base,
                "clip_eur": round(cfg.clip_eur, 2),
                "score": c.score,
                "reasons": list(c.reasons),
                "ret_24h": c.ret_24h,
            }
        )
    return planned


def _mover_exit(pos: Position, bar: Any, cfg: VolatileShadowConfig) -> Any:
    """Plain trail/stop/time exits — no AlphaI flip (mover mode ignores AlphaI)."""
    exit_cfg = DeskConfig(
        decision_hours_utc=cfg.decision_hours_utc,
        clip_eur=cfg.clip_eur,
        trail_pct=cfg.trail_pct,
        trail_tight_after=cfg.trail_tight_after,
        trail_tight_pct=cfg.trail_tight_pct,
        hard_stop_pct=cfg.hard_stop_pct,
        time_exit_hours=cfg.time_exit_hours,
        fee_rt=cfg.fee_rt,
        alphai_avoid_tightens_trail=False,
        universe=cfg.universe,
        clusters=dict(cfg.clusters),
        skip_weekend_entries=cfg.skip_weekend_entries,
    )
    return evaluate_exit(pos, bar, exit_cfg, alphai=None)


def simulate(
    candles: dict[str, Any],
    cfg: VolatileShadowConfig,
    *,
    start_ms: int,
    end_ms: int,
    alphai: AlphaIView,
    mode: Mode,
) -> dict[str, Any]:
    exit_cfg = _to_desk_exit_cfg(cfg)
    decision_cfg = _decision_cfg(cfg)
    idx = {b: {int(r[0]): r for r in rows} for b, rows in candles.items()}
    ledger = RiskLedger(
        day_loss_limit_eur=cfg.day_loss_limit_eur,
        week_loss_limit_eur=cfg.week_loss_limit_eur,
        pause_hours=cfg.pause_hours_after_week_limit,
    )
    positions: list[Position] = []
    closed: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    rotates = 0

    def close_pos(
        pos: Position, t: int, exit_price: float, reason: str, gross: float
    ) -> None:
        nonlocal positions
        net = net_pnl_eur(pos, exit_price, None, exit_cfg)
        closed.append(
            {
                "base": pos.base,
                "opened_ms": pos.opened_ms,
                "closed_ms": t,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "notional_eur": pos.notional_eur,
                "gross_return": gross,
                "peak_return": pos.peak / pos.entry_price - 1.0,
                "net_eur": net,
                "reason": reason,
                "entry_reason": pos.entry_reason,
                "opened": _fmt(pos.opened_ms),
                "closed": _fmt(t),
            }
        )
        ledger.note_close(net, t)
        positions = [p for p in positions if p is not pos]

    t = start_ms // BAR_MS * BAR_MS
    while t <= end_ms:
        closed_ts = t - BAR_MS
        for pos in list(positions):
            bar = idx.get(pos.base, {}).get(closed_ts)
            if bar is None:
                continue
            px = float(bar[4])
            if px > pos.peak:
                pos.peak = px
            if mode == "current":
                decision = _evaluate_volatile_exit(pos, bar, cfg, alphai)
            else:
                decision = _mover_exit(pos, bar, cfg)
            if decision is None:
                continue
            exit_price = (
                decision.price if decision.price is not None else float(bar[4])
            )
            close_pos(pos, t, exit_price, decision.reason, decision.gross_return)

        if is_decision_time(t, decision_cfg):
            stats = universe_stats(candles, t, exit_cfg)
            btc_rows = candles.get("BTC")
            btc = bar_stats("BTC", btc_rows, t) if btc_rows else None
            btc_ret = btc.ret_24h if btc is not None else 0.0
            allowed, why = ledger.entries_allowed(t)
            planned: list[dict[str, Any]] = []
            rotate_note = ""
            cand_labels: list[str] = []

            if mode == "current":
                cands, _rej = _rank_volatile(stats, btc_ret, cfg, alphai)
                cand_labels = [f"{c.base}:{c.ret_24h:+.1%}" for c in cands[:4]]
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
                            blocked=ledger.blocked_bases(
                                t, cfg.max_entries_per_base_per_day
                            ),
                            alphai=alphai,
                        )
            else:
                movers, _rej = _rank_max_mover(stats, btc_ret)
                cand_labels = [f"{c.base}:{c.ret_24h:+.1%}" for c in movers[:4]]
                if allowed:
                    if positions and mode == "max_mover_rotate" and movers:
                        pos = positions[0]
                        age_h = (t - pos.opened_ms) / 3_600_000
                        top = movers[0]
                        held_m = next(
                            (c for c in movers if c.base == pos.base), None
                        )
                        mark = (
                            float(stats[pos.base].price)
                            if pos.base in stats
                            else pos.entry_price
                        )
                        gross = pos.gross_return(mark)
                        held_ret = held_m.ret_24h if held_m else -1.0
                        gap = top.ret_24h - held_ret
                        if (
                            age_h >= MIN_AGE_H
                            and top.base != pos.base
                            and gap >= MOVE_GAP
                            and (gross <= FLAT_GROSS or gap >= MOVE_GAP * 2)
                            and pos.base in stats
                        ):
                            close_pos(
                                pos,
                                t,
                                float(stats[pos.base].price),
                                "rotate_max_mover",
                                gross,
                            )
                            rotates += 1
                            rotate_note = (
                                f"rotate_max_mover:{pos.base}->{top.base}"
                                f"(gap={gap:+.1%})"
                            )
                    planned = _select_mover(
                        movers, cfg, held={p.base for p in positions}
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
                # Same per-base day cap as live sleeve.
                blocked = ledger.blocked_bases(t, cfg.max_entries_per_base_per_day)
                if e["base"] in blocked:
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
                    "t": _fmt(t),
                    "mode": mode,
                    "allowed": allowed,
                    "block": "" if allowed else why,
                    "cands": cand_labels,
                    "entries": [
                        f"{e['base']}@{e['clip_eur']:.0f}"
                        for e in planned
                        if e.get("price")
                    ],
                    "rotate": rotate_note,
                }
            )
        t += BAR_MS

    open_mtm = []
    for pos in positions:
        rows = [r for r in (candles.get(pos.base) or []) if int(r[0]) < end_ms]
        last = rows[-1] if rows else None
        px = float(last[4]) if last else pos.entry_price
        open_mtm.append(
            {
                "base": pos.base,
                "opened": _fmt(pos.opened_ms),
                "entry": pos.entry_price,
                "mark": px,
                "gross": round(pos.gross_return(px), 4),
                "net_eur": round(net_pnl_eur(pos, px, None, exit_cfg), 2),
            }
        )
    n = len(closed)
    wins = sum(1 for x in closed if x["net_eur"] > 0)
    realized = sum(x["net_eur"] for x in closed)
    open_e = sum(float(m["net_eur"]) for m in open_mtm)
    by_base: dict[str, float] = {}
    for x in closed:
        by_base[x["base"]] = by_base.get(x["base"], 0.0) + float(x["net_eur"])
    for m in open_mtm:
        by_base[m["base"]] = by_base.get(m["base"], 0.0) + float(m["net_eur"])

    return {
        "mode": mode,
        "summary": {
            "trades": n,
            "rotates": rotates,
            "win_rate": round(wins / n, 3) if n else None,
            "realized_eur": round(realized, 2),
            "open_mtm_eur": round(open_e, 2),
            "total_eur": round(realized + open_e, 2),
            "fees_on_closed_eur": round(
                sum(t["notional_eur"] for t in closed) * cfg.fee_rt, 2
            ),
            "unique_bases_traded": len(
                {x["base"] for x in closed} | {m["base"] for m in open_mtm}
            ),
        },
        "by_base_eur": {k: round(v, 2) for k, v in sorted(by_base.items(), key=lambda kv: -kv[1])},
        "closed": closed,
        "open_mtm": open_mtm,
        "actions": [
            d for d in decisions if d.get("entries") or d.get("rotate")
        ],
    }


def main() -> None:
    alphai, meta = load_shadow_alphai()
    cfg = replace(
        shadow_config(alphai=alphai, scores=meta.get("scores") or {}),
        book_eur=650.0,
        clip_eur=650.0,
        max_positions=1,
        universe=volatile_universe(),
    )
    end = int(datetime.now(UTC).timestamp() * 1000) // BAR_MS * BAR_MS
    windows = {
        "today": int(datetime(2026, 9, 12, 0, 0, tzinfo=UTC).timestamp() * 1000),
        "week7d": end - 7 * 86_400_000,
        "week14d": end - 14 * 86_400_000,
    }
    cache = _candle_cache_dir()
    candles = load_candles(
        ("BTC", *cfg.universe),
        days=16,
        end_ms=end,
        refresh=False,
        cache_dir=cache,
    )

    out: dict[str, Any] = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": cfg.book_eur,
        "clip_eur": cfg.clip_eur,
        "universe": list(cfg.universe),
        "alphai_generated_at": meta.get("generated_at"),
        "alphai_picks": sorted(alphai.picks),
        "alphai_avoid": sorted(alphai.avoid),
        "modes": {
            "current": (
                "AlphaI-green required, anti-chase, pullback bonus, "
                "conviction sizing, adaptive AlphaI exits (live sleeve)."
            ),
            "max_mover_hold": (
                "No AlphaI. Buy highest 24h return in pool (volume floor only). "
                "Hold until trail/stop/time. Same book/clip/1-slot."
            ),
            "max_mover_rotate": (
                "Same entry as max_mover_hold, but rotate into a stronger "
                "24h mover when flat/weak and gap ≥ 2pp after 3h."
            ),
        },
        "caveat": (
            "Long-only: cannot short the biggest down-movers. AlphaI board for "
            "'current' is one snapshot projected over the window. Mover modes "
            "use live candle returns each decision bar."
        ),
        "windows": {},
    }

    for label, start in windows.items():
        out["windows"][label] = {}
        for mode in ("current", "max_mover_hold", "max_mover_rotate"):
            res = simulate(
                candles,
                cfg,
                start_ms=start,
                end_ms=end,
                alphai=alphai,
                mode=mode,  # type: ignore[arg-type]
            )
            by_day: dict[str, float] = {}
            for tr in res["closed"]:
                d = tr["closed"][:10]
                by_day[d] = by_day.get(d, 0.0) + float(tr["net_eur"])
            out["windows"][label][mode] = {
                "summary": res["summary"],
                "by_base_eur": res["by_base_eur"],
                "open_mtm": res["open_mtm"],
                "closed_tail": res["closed"][-12:],
                "actions_tail": res["actions"][-20:],
                "by_day_realized": {
                    k: round(v, 2) for k, v in sorted(by_day.items())
                },
            }

    # Compact comparison table
    cmp: dict[str, Any] = {}
    for label, modes in out["windows"].items():
        cmp[label] = {
            m: modes[m]["summary"]["total_eur"]
            for m in ("current", "max_mover_hold", "max_mover_rotate")
        }
    out["comparison_total_eur"] = cmp

    dest = Path("artifacts/volatile_max_mover_ab_2026-09-12.json")
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(dest), "comparison_total_eur": cmp}, indent=2))


if __name__ == "__main__":
    main()
