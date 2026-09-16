#!/usr/bin/env python3
"""12w A/B: proposed WR tactics A–F vs current WR+survival €20k desk.

Research-only — does not change live bot code. Monkeypatches entry helpers
inside the backtest engine for gates that have no DeskConfig knob yet.

Tactics (from prior WR research):
  A pick-gate     — only AlphaI picks may enter (firm/strong included)
  B confidence    — AlphaI picks with score ≥ threshold only (implies pick-gate)
  C macro block   — macro_caution_mode=block
  D chase         — max_chase_ret_24h=0.09 near-high reject
  E volume spike  — require 24h EUR volume ≥ 1.5× median of tradeable alts
  F strong-breadth — min_breadth=strong_breadth (0.85), soft regime off

Writes ``artifacts/wr_tactics_12w_ab.json``.
"""
from __future__ import annotations

import bisect
import json
import statistics as st
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bot.research.momentum_backtest.engine as eng
from bot.live.momentum_desk import (
    AlphaIView,
    BaseStats,
    Candidate,
    DeskConfig,
    Entry,
    RegimeDecision,
    rank_candidates,
    select_entries,
)
from bot.research.momentum_backtest.engine import load_candles, simulate

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "wr_tactics_12w_ab.json"
MERGED = Path(__file__).resolve().parent / "alphai_pick_outcomes_merged.json"
SNAP = Path(__file__).resolve().parent / "alphai_pick_outcomes_snapshot.json"

BOOK = 20_000.0
CLIP = 10_000.0
WEEKS = 12
# Score floors from pick_outcomes distribution (median≈24, p75≈39).
SCORE_MED = 24.0
SCORE_P75 = 39.0
VOL_SPIKE_MULT = 1.5
CHASE_RET = 0.09


def live_cfg(**extra: Any) -> DeskConfig:
    knobs: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        decision_interval_sec=0.0,
        clip_eur=CLIP,
        max_positions=3,
        book_eur=BOOK,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        green_min_peak=0.01,
        fade_eta_sec=0.0,  # bar replay: fade path needs dense marks
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        soft_regime_fee_buffer_mult=6.0,
        strong_clip_mult=1.3,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        weak_clip_mult=0.7,
        skip_weekend_entries=True,
        refill_on_exit=True,
        macro_caution_mode="reduce",
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
        alphai_stale_minutes=0.0,
        alphai_price_confirm_sizing=False,
        alphai_reliability_sizing=False,
        outcome_size_enabled=False,
    )
    knobs.update(extra)
    return DeskConfig().with_overrides(**knobs)


def _parse_ms(raw: str) -> int:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def load_timeline(
    path: Path, *, min_score: float | None = None
) -> tuple[list[int], list[AlphaIView], dict[str, Any]]:
    data = json.loads(path.read_text())
    sessions = list(data.get("sessions") or [])
    sessions.sort(key=lambda s: s.get("generated_at") or "")
    times: list[int] = []
    views: list[AlphaIView] = []
    kept_picks = 0
    raw_picks = 0
    macro_n = 0
    for s in sessions:
        gen = s.get("generated_at")
        if not gen:
            continue
        picks_in = list(s.get("picks") or [])
        raw_picks += len(picks_in)
        if min_score is not None:
            picks_in = [
                p
                for p in picks_in
                if isinstance(p, dict)
                and p.get("score") is not None
                and float(p["score"]) >= float(min_score)
            ]
        kept_picks += len(picks_in)
        if s.get("macro_caution"):
            macro_n += 1
        payload = {
            "generated_at": gen,
            "macro_caution": bool(s.get("macro_caution")),
            "picks": picks_in,
            "avoid": s.get("avoid") or [],
        }
        times.append(_parse_ms(gen))
        views.append(AlphaIView.from_recommendations(payload))
    meta = {
        "source": str(path),
        "sessions": len(views),
        "first": datetime.fromtimestamp(times[0] / 1000, UTC).isoformat() if times else None,
        "last": datetime.fromtimestamp(times[-1] / 1000, UTC).isoformat() if times else None,
        "macro_caution_sessions": macro_n,
        "macro_caution_rate": round(macro_n / max(1, len(views)), 3),
        "min_score": min_score,
        "raw_picks": raw_picks,
        "kept_picks": kept_picks,
    }
    return times, views, meta


def make_alphai_at(
    times: list[int], views: list[AlphaIView]
) -> Callable[[int], AlphaIView | None]:
    def alphai_at(t_ms: int) -> AlphaIView | None:
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        return views[i] if i >= 0 else None

    return alphai_at


def _select_pick_only(
    cands: Sequence[Candidate],
    regime: RegimeDecision,
    cfg: DeskConfig,
    *,
    held_bases,
    blocked_bases=(),
    alphai: AlphaIView | None = None,
    now_ms: int | None = None,
    outcome_store: Any | None = None,
) -> list[Entry]:
    entries = select_entries(
        cands,
        regime,
        cfg,
        held_bases=held_bases,
        blocked_bases=blocked_bases,
        alphai=alphai,
        now_ms=now_ms,
        outcome_store=outcome_store,
    )
    return [e for e in entries if "alphai_pick" in (e.reasons or ())]


def _rank_with_vol_spike(
    alts: Mapping[str, BaseStats],
    btc_ret: float,
    cfg: DeskConfig,
    *,
    alphai: AlphaIView | None = None,
) -> list[Candidate]:
    cands = rank_candidates(alts, btc_ret, cfg, alphai=alphai)
    if len(alts) < 3:
        return cands
    vols = [float(s.volume_eur) for s in alts.values()]
    med = st.median(vols)
    floor = med * VOL_SPIKE_MULT
    return [c for c in cands if float(c.volume_eur) >= floor]


def install_patches(*, pick_only: bool, vol_spike: bool) -> None:
    eng.select_entries = _select_pick_only if pick_only else select_entries
    eng.rank_candidates = _rank_with_vol_spike if vol_spike else rank_candidates


def weekly_rows(res) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for t in res.closed:
        week = datetime.fromtimestamp(t.opened_ms / 1000, UTC).date()
        week = week - timedelta(days=week.weekday())  # Monday
        key = week.isoformat()
        b = buckets.setdefault(key, {"trades": 0, "wins": 0, "pnl_eur": 0.0})
        b["trades"] += 1
        b["pnl_eur"] += float(t.net_eur)
        if t.net_eur > 0:
            b["wins"] += 1
    out = []
    for k in sorted(buckets):
        b = buckets[k]
        n = b["trades"]
        out.append(
            {
                "week_start": k,
                "trades": n,
                "wins": b["wins"],
                "win_rate": round(b["wins"] / n, 3) if n else None,
                "pnl_eur": round(b["pnl_eur"], 2),
            }
        )
    return out


def pack(res, *, label: str, cfg: DeskConfig, note: str = "") -> dict[str, Any]:
    s = res.summary()
    total = float(s.get("total_eur") or 0.0)
    n = int(s.get("trades") or 0)
    pick_n = sum(1 for t in res.closed if "alphai_pick" in (t.entry_reason or ""))
    return {
        "label": label,
        "note": note,
        "summary": s,
        "return_on_book_pct": round(100.0 * total / BOOK, 3),
        "alphai_pick_entries": pick_n,
        "non_pick_entries": n - pick_n,
        "weeks": weekly_rows(res),
        "config_delta": {
            k: getattr(cfg, k)
            for k in (
                "macro_caution_mode",
                "max_chase_ret_24h",
                "min_breadth",
                "soft_regime_on_weak_tape",
                "min_volume_eur",
            )
        },
    }


def run_arm(
    *,
    label: str,
    candles,
    start_ms: int,
    end_ms: int,
    alphai_at,
    cfg: DeskConfig,
    pick_only: bool = False,
    vol_spike: bool = False,
    note: str = "",
) -> dict[str, Any]:
    install_patches(pick_only=pick_only, vol_spike=vol_spike)
    try:
        res = simulate(
            candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai_at=alphai_at
        )
    finally:
        install_patches(pick_only=False, vol_spike=False)
    return pack(res, label=label, cfg=cfg, note=note)


def delta_vs(base: dict[str, Any], arm: dict[str, Any]) -> dict[str, Any]:
    b = float(base["summary"].get("total_eur") or 0)
    a = float(arm["summary"].get("total_eur") or 0)
    bw = base["summary"].get("win_rate")
    aw = arm["summary"].get("win_rate")
    return {
        "pnl_delta_eur": round(a - b, 2),
        "pnl_delta_pct_pts": round(arm["return_on_book_pct"] - base["return_on_book_pct"], 3),
        "wr_delta": None
        if bw is None or aw is None
        else round(float(aw) - float(bw), 3),
        "trades_delta": int(arm["summary"].get("trades") or 0)
        - int(base["summary"].get("trades") or 0),
        "verdict": "RAISE" if a > b + 1 else ("LOWER" if a < b - 1 else "FLAT"),
    }


def main() -> None:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(weeks=WEEKS)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)

    path = MERGED if MERGED.exists() else SNAP
    if not path.exists():
        raise SystemExit(f"missing AlphaI timeline: {path}")

    times_all, views_all, meta_all = load_timeline(path)
    times_med, views_med, meta_med = load_timeline(path, min_score=SCORE_MED)
    times_hi, views_hi, meta_hi = load_timeline(path, min_score=SCORE_P75)
    alphai_all = make_alphai_at(times_all, views_all)
    alphai_med = make_alphai_at(times_med, views_med)
    alphai_hi = make_alphai_at(times_hi, views_hi)

    candles = load_candles(
        ("BTC", *DeskConfig().universe), days=WEEKS * 7 + 10, end_ms=end_ms
    )

    # --- full 12w ---
    baseline = run_arm(
        label="baseline_survival",
        candles=candles,
        start_ms=start_ms,
        end_ms=end_ms,
        alphai_at=alphai_all,
        cfg=live_cfg(),
        note="Current WR+survival €20k×€10k×3",
    )

    arms_full: dict[str, dict[str, Any]] = {"baseline_survival": baseline}

    specs: list[tuple[str, dict[str, Any], bool, bool, Any, str]] = [
        (
            "A_pick_gate",
            {},
            True,
            False,
            alphai_all,
            "Only AlphaI picks enter (all regimes)",
        ),
        (
            "B_confidence_med24",
            {},
            True,
            False,
            alphai_med,
            f"Pick-gate + AlphaI score ≥ {SCORE_MED:.0f} (median)",
        ),
        (
            "B_confidence_p75_39",
            {},
            True,
            False,
            alphai_hi,
            f"Pick-gate + AlphaI score ≥ {SCORE_P75:.0f} (p75)",
        ),
        (
            "C_macro_block",
            {"macro_caution_mode": "block"},
            False,
            False,
            alphai_all,
            "Idle all new entries while AlphaI macro_caution",
        ),
        (
            "D_chase_reject",
            {"max_chase_ret_24h": CHASE_RET},
            False,
            False,
            alphai_all,
            f"Reject ret_24h≥{CHASE_RET:.0%} glued to 24h high",
        ),
        (
            "E_volume_spike",
            {},
            False,
            True,
            alphai_all,
            f"Require volume ≥ {VOL_SPIKE_MULT}× median alt 24h EUR vol",
        ),
        (
            "F_strong_breadth",
            {"min_breadth": 0.85, "soft_regime_on_weak_tape": False},
            False,
            False,
            alphai_all,
            "Entries only when breadth≥0.85; soft regime off",
        ),
        (
            "pack_alphai_ABC_med",
            {"macro_caution_mode": "block"},
            True,
            False,
            alphai_med,
            "A+B(med)+C",
        ),
        (
            "pack_tape_DEF",
            {
                "max_chase_ret_24h": CHASE_RET,
                "min_breadth": 0.85,
                "soft_regime_on_weak_tape": False,
            },
            False,
            True,
            alphai_all,
            "D+E+F",
        ),
        (
            "pack_all_med",
            {
                "macro_caution_mode": "block",
                "max_chase_ret_24h": CHASE_RET,
                "min_breadth": 0.85,
                "soft_regime_on_weak_tape": False,
            },
            True,
            True,
            alphai_med,
            "A+B(med)+C+D+E+F",
        ),
    ]

    for name, overrides, pick_only, vol_spike, at, note in specs:
        arms_full[name] = run_arm(
            label=name,
            candles=candles,
            start_ms=start_ms,
            end_ms=end_ms,
            alphai_at=at,
            cfg=live_cfg(**overrides),
            pick_only=pick_only,
            vol_spike=vol_spike,
            note=note,
        )
        print(
            f"[12w] {name}: pnl={arms_full[name]['summary'].get('total_eur')} "
            f"WR={arms_full[name]['summary'].get('win_rate')} "
            f"n={arms_full[name]['summary'].get('trades')}"
        )

    deltas_full = {
        k: delta_vs(baseline, v) for k, v in arms_full.items() if k != "baseline_survival"
    }

    # --- AlphaI-covered subwindow (fair test for A/B/C) ---
    ai_start = times_all[0]
    ai_end = min(end_ms, times_all[-1] + 3_600_000)
    baseline_ai = run_arm(
        label="baseline_survival",
        candles=candles,
        start_ms=ai_start,
        end_ms=ai_end,
        alphai_at=alphai_all,
        cfg=live_cfg(),
        note="Baseline on AlphaI timeline window only",
    )
    arms_ai: dict[str, dict[str, Any]] = {"baseline_survival": baseline_ai}
    for name, overrides, pick_only, vol_spike, at, note in specs:
        arms_ai[name] = run_arm(
            label=name,
            candles=candles,
            start_ms=ai_start,
            end_ms=ai_end,
            alphai_at=at,
            cfg=live_cfg(**overrides),
            pick_only=pick_only,
            vol_spike=vol_spike,
            note=note,
        )
        print(
            f"[AI] {name}: pnl={arms_ai[name]['summary'].get('total_eur')} "
            f"WR={arms_ai[name]['summary'].get('win_rate')} "
            f"n={arms_ai[name]['summary'].get('trades')}"
        )
    deltas_ai = {
        k: delta_vs(baseline_ai, v) for k, v in arms_ai.items() if k != "baseline_survival"
    }

    # Verdict helper: for A/B/C prefer AI window; for D/E/F prefer full 12w.
    prefer_ai = {
        "A_pick_gate",
        "B_confidence_med24",
        "B_confidence_p75_39",
        "C_macro_block",
        "pack_alphai_ABC_med",
        "pack_all_med",
    }
    recommendations: dict[str, Any] = {}
    for name in deltas_full:
        src = "alphai_window" if name in prefer_ai else "full_12w"
        d = deltas_ai[name] if src == "alphai_window" else deltas_full[name]
        recommendations[name] = {
            "use_window": src,
            **d,
            "full_12w": deltas_full[name],
            "alphai_window": deltas_ai[name],
        }

    out = {
        "asof": end.isoformat(),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "weeks": WEEKS,
        },
        "alphai_window": {
            "start": datetime.fromtimestamp(ai_start / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(ai_end / 1000, UTC).isoformat(),
            "meta": meta_all,
            "score_med_meta": meta_med,
            "score_p75_meta": meta_hi,
        },
        "book_eur": BOOK,
        "clip_eur": CLIP,
        "caveats": [
            "15m bar replay; fills at bar close (no live maker/taker path).",
            "AlphaI timeline only covers ~last 8 days of the 12w window; A/B/C "
            "are judged on that subwindow. Earlier weeks have no picks → pick-gate "
            "would idle the desk if applied blindly over sparse history.",
            "Fade-ETA exits disabled in replay (need dense marks).",
            "No bot code changes — research monkeypatches only inside this script.",
            "Volume spike = relative to median alt volume at decision time "
            f"(×{VOL_SPIKE_MULT}), coin-agnostic.",
        ],
        "full_12w": {
            "baseline_pnl_eur": baseline["summary"].get("total_eur"),
            "baseline_wr": baseline["summary"].get("win_rate"),
            "baseline_trades": baseline["summary"].get("trades"),
            "deltas": deltas_full,
            "arms": {k: {kk: vv for kk, vv in v.items() if kk != "weeks"} | {"weeks": v["weeks"]} for k, v in arms_full.items()},
        },
        "alphai_covered": {
            "baseline_pnl_eur": baseline_ai["summary"].get("total_eur"),
            "baseline_wr": baseline_ai["summary"].get("win_rate"),
            "baseline_trades": baseline_ai["summary"].get("trades"),
            "deltas": deltas_ai,
            "arms": {
                k: {kk: vv for kk, vv in v.items() if kk != "weeks"}
                for k, v in arms_ai.items()
            },
        },
        "recommendations": recommendations,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    print("--- verdicts (preferred window) ---")
    for name, rec in recommendations.items():
        print(
            f"{name}: {rec['verdict']} Δ€{rec['pnl_delta_eur']} "
            f"WRΔ{rec['wr_delta']} ({rec['use_window']})"
        )


if __name__ == "__main__":
    main()
