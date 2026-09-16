#!/usr/bin/env python3
"""A/B: AlphaI binary clip×1.3 vs conviction size overlay on live WR desk.

Uses the merged pick_outcomes timeline (scores + macro). No harder entry
gates — only clip sizing differs. Writes artifacts/alphai_size_overlay_ab.json.
"""
from __future__ import annotations

import bisect
import json
import statistics as st
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from bot.live.momentum_desk import AlphaIView, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "alphai_size_overlay_ab.json"
MERGED = Path(__file__).resolve().parent / "alphai_pick_outcomes_merged.json"
SNAP = Path(__file__).resolve().parent / "alphai_pick_outcomes_snapshot.json"

BOOK = 4_036.0
CLIP = 1_300.0


def live_wr(**overrides: Any) -> DeskConfig:
    cfg: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        clip_eur=CLIP,
        book_eur=BOOK,
        max_positions=4,
        day_loss_limit_eur=100.0,
        week_loss_limit_eur=250.0,
        fee_rt=0.003,
        exit_on_touch=False,
        skip_weekend_entries=True,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        max_chase_ret_24h=0.0,
        midflat_hours=0.0,
        soft_regime_on_weak_tape=True,
        strong_clip_requires_quality=True,
        strong_clip_min_excess=0.04,
        soft_regime_fee_buffer_mult=6.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        time_exit_hours=36.0,
        green_deadline_hours=0.0,
        green_min_peak=0.01,
        btc_min_ret=-0.01,
        min_breadth=0.5,
        decision_every_bar=False,
        decision_interval_sec=0.0,
        fade_eta_sec=0.0,
        alphai_rank_boost=0.01,
        alphai_clip_mult=1.3,
        alphai_clip_mult_min=1.0,
        alphai_size_mode="binary",
        alphai_stale_minutes=0.0,  # timeline already aligned to session time
        alphai_price_confirm_sizing=False,  # no historical confirm scales in outcomes
        alphai_reliability_sizing=False,
        macro_caution_requires_alphai_pick=True,
    )
    cfg.update(overrides)
    return DeskConfig().with_overrides(**cfg)


def _parse_ms(raw: str) -> int:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def load_timeline(path: Path) -> tuple[list[int], list[AlphaIView], dict[str, Any]]:
    data = json.loads(path.read_text())
    sessions = list(data.get("sessions") or [])
    sessions.sort(key=lambda s: s.get("generated_at") or "")
    times: list[int] = []
    views: list[AlphaIView] = []
    for s in sessions:
        gen = s.get("generated_at")
        if not gen:
            continue
        payload = {
            "generated_at": gen,
            "macro_caution": bool(s.get("macro_caution")),
            "picks": s.get("picks") or [],
            "avoid": s.get("avoid") or [],
        }
        times.append(_parse_ms(gen))
        views.append(AlphaIView.from_recommendations(payload))
    meta = {
        "source": str(path),
        "sessions": len(views),
        "start": datetime.fromtimestamp(times[0] / 1000, UTC).isoformat() if times else None,
        "end": datetime.fromtimestamp(times[-1] / 1000, UTC).isoformat() if times else None,
    }
    return times, views, meta


def make_at(times: list[int], views: list[AlphaIView]) -> Callable[[int], AlphaIView | None]:
    def alphai_at(t_ms: int) -> AlphaIView | None:
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        return views[i] if i >= 0 else None

    return alphai_at


def summarize(res) -> dict[str, Any]:
    s = res.summary()
    n = int(s.get("trades") or 0)
    pick_n = sum(1 for t in res.closed if "alphai_pick" in (t.entry_reason or ""))
    conv_tags = sum(1 for t in res.closed if "alphai_conv=" in (t.entry_reason or ""))
    by_base: dict[str, dict[str, Any]] = {}
    for t in res.closed:
        row = by_base.setdefault(t.base, {"n": 0, "pnl": 0.0, "wins": 0, "clips": []})
        row["n"] += 1
        row["pnl"] += float(t.net_eur)
        row["clips"].append(float(t.notional_eur))
        if t.net_eur > 0:
            row["wins"] += 1
    return {
        "total_eur": round(float(s.get("total_eur") or 0.0), 2),
        "max_dd_eur": round(float(s.get("max_drawdown_eur") or 0.0), 2),
        "trades": n,
        "win_rate": round(float(s.get("win_rate") or 0.0), 4) if n else 0.0,
        "alphai_pick_entries": pick_n,
        "conviction_tagged_entries": conv_tags,
        "avg_clip_eur": round(
            st.mean([float(t.notional_eur) for t in res.closed]), 2
        )
        if res.closed
        else 0.0,
        "by_base": {
            b: {
                "n": v["n"],
                "pnl": round(v["pnl"], 2),
                "wr": round(v["wins"] / v["n"], 3) if v["n"] else 0.0,
                "avg_clip": round(st.mean(v["clips"]), 2) if v["clips"] else 0.0,
            }
            for b, v in sorted(by_base.items(), key=lambda kv: -kv[1]["pnl"])
        },
        "entries": [
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "net_eur": round(float(t.net_eur), 2),
                "clip_eur": round(float(t.notional_eur), 2),
                "entry_reason": t.entry_reason,
                "exit_reason": t.reason,
            }
            for t in res.closed
        ],
    }


def main() -> None:
    path = MERGED if MERGED.exists() else SNAP
    if not path.exists():
        raise SystemExit(f"missing AlphaI timeline: {path}")
    times, views, meta = load_timeline(path)
    if not times:
        raise SystemExit("empty timeline")
    start_ms, end_ms = times[0], times[-1]
    days = max(14, int((end_ms - start_ms) / 86_400_000) + 5)
    candles = load_candles(("BTC", *DeskConfig().universe), days=days, end_ms=end_ms + 86_400_000)
    alphai_at = make_at(times, views)

    tape = simulate(
        candles, live_wr(), start_ms=start_ms, end_ms=end_ms, alphai=None
    )
    binary = simulate(
        candles,
        live_wr(alphai_size_mode="binary"),
        start_ms=start_ms,
        end_ms=end_ms,
        alphai_at=alphai_at,
    )
    conviction = simulate(
        candles,
        live_wr(alphai_size_mode="conviction"),
        start_ms=start_ms,
        end_ms=end_ms,
        alphai_at=alphai_at,
    )

    tape_s, bin_s, conv_s = summarize(tape), summarize(binary), summarize(conviction)
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": meta,
        "config": "live WR hours 7/13/16; trail 3%/4%→2%; green off; fade off in replay",
        "arms": {
            "tape_only": tape_s,
            "alphai_binary_clip": bin_s,
            "alphai_conviction_size": conv_s,
        },
        "delta": {
            "conviction_minus_binary_eur": round(conv_s["total_eur"] - bin_s["total_eur"], 2),
            "conviction_minus_tape_eur": round(conv_s["total_eur"] - tape_s["total_eur"], 2),
            "binary_minus_tape_eur": round(bin_s["total_eur"] - tape_s["total_eur"], 2),
            "avg_clip_conviction_vs_binary": round(
                conv_s["avg_clip_eur"] - bin_s["avg_clip_eur"], 2
            ),
        },
        "verdict": (
            f"Conviction sizing {conv_s['total_eur'] - bin_s['total_eur']:+.2f} EUR vs binary "
            f"({conv_s['total_eur']} vs {bin_s['total_eur']}); "
            f"avg clip {conv_s['avg_clip_eur']:.0f} vs {bin_s['avg_clip_eur']:.0f}; "
            f"trades {conv_s['trades']} vs {bin_s['trades']}."
        ),
        "caveats": [
            "Bar-close replay; live fade/BBO not modeled.",
            "pick_outcomes has scores/macro but usually no avoid/headlines/price_confirm.",
            "Membership gates unchanged — size overlay only.",
        ],
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"window": meta, "delta": out["delta"], "verdict": out["verdict"]}, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
