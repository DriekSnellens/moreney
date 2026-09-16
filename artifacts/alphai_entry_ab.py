#!/usr/bin/env python3
"""A/B: momentum desk with vs without historical AlphaI (picks + macro).

Uses pick_outcomes.json sessions as a time-varying AlphaIView timeline
(picks + macro_caution; avoid is usually empty in that file). Live ledger
attribution is reported separately from real fills.
"""
from __future__ import annotations

import bisect
import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from bot.live.momentum_desk import AlphaIView, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "alphai_entry_ab.json"
PICK_SRC = ROOT / "data" / "alphai" / "pick_outcomes.json"
PICK_COPY = Path(__file__).resolve().parent / "alphai_pick_outcomes_snapshot.json"
LEDGER = ROOT / "data" / "momentum_desk_ledger.jsonl"

BOOK = 4_036.0
CLIP = 1_300.0


def live_wr_cfg(**overrides: Any) -> DeskConfig:
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
        macro_caution_requires_alphai_pick=True,
    )
    cfg.update(overrides)
    return DeskConfig().with_overrides(**cfg)


def _parse_ts(raw: str) -> int:
    s = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def load_pick_timeline(path: Path) -> tuple[list[int], list[AlphaIView], dict[str, Any]]:
    data = json.loads(path.read_text())
    sessions = list(data.get("sessions") or [])
    sessions.sort(key=lambda s: s.get("generated_at") or s.get("session_id") or "")
    times: list[int] = []
    views: list[AlphaIView] = []
    macro_n = 0
    pick_counts: Counter[str] = Counter()
    for s in sessions:
        gen = s.get("generated_at") or ""
        if not gen:
            continue
        picks = frozenset(
            str(p.get("base") or "").upper()
            for p in (s.get("picks") or [])
            if isinstance(p, dict) and p.get("base")
        )
        macro = bool(s.get("macro_caution"))
        if macro:
            macro_n += 1
        for b in picks:
            pick_counts[b] += 1
        # pick_outcomes has no avoid list; live desk may still set avoid separately.
        times.append(_parse_ts(gen))
        views.append(AlphaIView(picks=picks, avoid=frozenset(), macro_caution=macro))
    meta = {
        "sessions": len(views),
        "first_ms": times[0] if times else None,
        "last_ms": times[-1] if times else None,
        "macro_caution_sessions": macro_n,
        "macro_caution_rate": round(macro_n / max(1, len(views)), 3),
        "top_picks": pick_counts.most_common(12),
        "has_avoid": False,
        "source": str(path),
    }
    return times, views, meta


def make_alphai_at(times: list[int], views: list[AlphaIView]) -> Callable[[int], AlphaIView | None]:
    def alphai_at(t_ms: int) -> AlphaIView | None:
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        if i < 0:
            return None
        return views[i]

    return alphai_at


def summarize(res) -> dict[str, Any]:
    s = res.summary()
    n = int(s["trades"] or 0)
    wr = float(s["win_rate"] or 0.0) if n else 0.0
    pick_n = sum(1 for t in res.closed if "alphai_pick" in (t.entry_reason or ""))
    macro_n = sum(1 for t in res.closed if "macro_reduce" in (t.entry_reason or ""))
    by_base: dict[str, dict[str, Any]] = {}
    for t in res.closed:
        row = by_base.setdefault(t.base, {"n": 0, "pnl": 0.0, "wins": 0})
        row["n"] += 1
        row["pnl"] += float(t.net_eur)
        if t.net_eur > 0:
            row["wins"] += 1
    return {
        "total_eur": round(float(s["total_eur"]), 2),
        "max_drawdown_eur": round(float(s["max_drawdown_eur"]), 2),
        "trades": n,
        "win_rate": round(wr, 4),
        "avg_net_per_trade_eur": s.get("avg_net_per_trade_eur"),
        "by_reason": s.get("by_reason"),
        "alphai_pick_entries": pick_n,
        "macro_reduce_entries": macro_n,
        "open_mtm_eur": round(sum(float(x.get("net_eur") or 0) for x in res.open_mtm), 2),
        "by_base": {
            b: {
                "n": v["n"],
                "pnl": round(v["pnl"], 2),
                "wr": round(v["wins"] / v["n"], 3) if v["n"] else 0.0,
            }
            for b, v in sorted(by_base.items(), key=lambda kv: -kv[1]["pnl"])
        },
        "entries_sample": [
            {
                "base": t.base,
                "opened": datetime.fromtimestamp(t.opened_ms / 1000, UTC).isoformat(),
                "net_eur": round(float(t.net_eur), 2),
                "reason": t.reason,
                "entry_reason": t.entry_reason,
            }
            for t in res.closed
        ],
    }


def entry_set_diff(with_s, without_s) -> dict[str, Any]:
    def keys(res):
        return {(t.base, t.opened_ms) for t in res.closed}

    w, o = keys(with_s), keys(without_s)
    only_with = sorted(w - o)
    only_without = sorted(o - w)
    both = sorted(w & o)

    def pnl_map(res):
        return {(t.base, t.opened_ms): float(t.net_eur) for t in res.closed}

    pw, po = pnl_map(with_s), pnl_map(without_s)
    return {
        "shared_entries": len(both),
        "only_with_alphai": [
            {
                "base": b,
                "opened": datetime.fromtimestamp(ms / 1000, UTC).isoformat(),
                "net_eur": round(pw[(b, ms)], 2),
            }
            for b, ms in only_with
        ],
        "only_without_alphai": [
            {
                "base": b,
                "opened": datetime.fromtimestamp(ms / 1000, UTC).isoformat(),
                "net_eur": round(po[(b, ms)], 2),
            }
            for b, ms in only_without
        ],
        "only_with_pnl": round(sum(pw[k] for k in only_with), 2),
        "only_without_pnl": round(sum(po[k] for k in only_without), 2),
        "shared_pnl_with": round(sum(pw[k] for k in both), 2),
        "shared_pnl_without": round(sum(po[k] for k in both), 2),
    }


def live_ledger_attribution(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"error": "ledger_missing"}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    exits = [r for r in rows if r.get("event") == "exit"]
    buckets: dict[str, list[float]] = defaultdict(list)
    detail = []
    for x in exits:
        reason = str(x.get("entry_reason") or "")
        has_pick = "alphai_pick" in reason
        has_macro = "macro_reduce" in reason
        if has_pick and has_macro:
            key = "alphai_pick+macro_reduce"
        elif has_pick:
            key = "alphai_pick"
        elif has_macro:
            key = "macro_reduce"
        else:
            key = "tape_only"
        pnl = float(x.get("net_eur") or 0.0)
        buckets[key].append(pnl)
        # Also roll up any alphai-touched vs pure tape.
        buckets["any_alphai_tag" if (has_pick or has_macro) else "no_alphai_tag"].append(pnl)
        detail.append(
            {
                "ts": x.get("ts"),
                "base": x.get("base"),
                "net_eur": round(pnl, 2),
                "exit_reason": x.get("reason"),
                "bucket": key,
                "entry_reason": reason,
            }
        )
    summary = {}
    for k, pnls in sorted(buckets.items()):
        summary[k] = {
            "n": len(pnls),
            "pnl": round(sum(pnls), 2),
            "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3) if pnls else 0.0,
            "avg": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    # decisions: how often soft-regime / picks shaped planned entries
    decisions = [r for r in rows if r.get("event") == "decision"]
    planned_tags = Counter()
    for d in decisions:
        for p in d.get("planned") or []:
            rs = ",".join(p.get("reasons") or [])
            if "alphai_pick" in rs:
                planned_tags["alphai_pick"] += 1
            elif "macro_reduce" in rs:
                planned_tags["macro_reduce_only"] += 1
            else:
                planned_tags["tape_only"] += 1
    return {
        "exits": len(exits),
        "by_bucket": summary,
        "detail": detail,
        "planned_entry_tags": dict(planned_tags),
        "realized_total_eur": round(sum(float(x.get("net_eur") or 0) for x in exits), 2),
    }


def main() -> None:
    # Prefer a writable snapshot so re-runs don't need sudo.
    if PICK_SRC.exists():
        try:
            shutil.copy2(PICK_SRC, PICK_COPY)
        except PermissionError:
            pass
    pick_path = PICK_COPY if PICK_COPY.exists() else PICK_SRC
    if not pick_path.exists():
        raise SystemExit(f"missing AlphaI pick outcomes: {pick_path}")
    try:
        pick_path.read_text()[:1]
    except PermissionError as exc:
        raise SystemExit(
            f"cannot read {pick_path}; copy {PICK_SRC} → {PICK_COPY} first"
        ) from exc

    times, views, meta = load_pick_timeline(pick_path)
    if not times:
        raise SystemExit("empty AlphaI timeline")

    start_ms = times[0]
    end_ms = times[-1]
    # Warm-up: need lookback for ret/high stats inside the window.
    days = max(14, int((end_ms - start_ms) / 86_400_000) + 5)
    bases = ("BTC", *DeskConfig().universe)
    candles = load_candles(bases, days=days, end_ms=end_ms + 86_400_000)

    cfg = live_wr_cfg()
    alphai_at = make_alphai_at(times, views)

    off = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai=None)
    on = simulate(candles, cfg, start_ms=start_ms, end_ms=end_ms, alphai_at=alphai_at)
    # Isolate rank/soft-regime vs clip-size boost.
    cfg_rank = live_wr_cfg(alphai_clip_mult=1.0)
    rank_only = simulate(
        candles, cfg_rank, start_ms=start_ms, end_ms=end_ms, alphai_at=alphai_at
    )

    off_s = summarize(off)
    on_s = summarize(on)
    rank_s = summarize(rank_only)
    delta = {
        "total_eur": round(on_s["total_eur"] - off_s["total_eur"], 2),
        "trades": on_s["trades"] - off_s["trades"],
        "win_rate_pp": round((on_s["win_rate"] - off_s["win_rate"]) * 100, 2),
        "max_drawdown_eur": round(on_s["max_drawdown_eur"] - off_s["max_drawdown_eur"], 2),
        "open_mtm_eur": round(on_s["open_mtm_eur"] - off_s["open_mtm_eur"], 2),
        "vs_rank_only_eur": round(on_s["total_eur"] - rank_s["total_eur"], 2),
        "rank_only_vs_tape_eur": round(rank_s["total_eur"] - off_s["total_eur"], 2),
    }
    diff = entry_set_diff(on, off)
    if not diff["only_with_alphai"] and not diff["only_without_alphai"]:
        delta["entry_set_unchanged"] = True
        delta["effect"] = "sizing_and_or_shared_trade_pnl"
    else:
        delta["entry_set_unchanged"] = False
        delta["effect"] = "entry_selection_changed"

    live = live_ledger_attribution(LEDGER)

    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
            "days": round((end_ms - start_ms) / 86_400_000, 2),
        },
        "config": "live_wr_hybrid (hours 7/13/16, trail 3%/4%→2%, green off, 36h, fade off in replay)",
        "alphai_timeline": meta,
        "caveats": [
            "Bar-close replay; live fade/BBO marks not modeled.",
            "pick_outcomes has picks+macro_caution but no avoid list.",
            "Short window only (AlphaI history starts ~2026-09-10).",
            "Path-dependent: AlphaI changes which slots fill first.",
            "rank_only arm keeps picks/macro filters but alphai_clip_mult=1.0.",
        ],
        "without_alphai": off_s,
        "with_alphai": on_s,
        "alphai_rank_only_no_clip_boost": rank_s,
        "delta_with_minus_without": delta,
        "entry_diff": diff,
        "live_ledger_attribution": live,
        "verdict": _verdict(delta, on_s, off_s, live, diff),
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("window", "delta_with_minus_without", "verdict")}, indent=2))
    print(f"wrote {OUT}")


def _verdict(
    delta: dict, on_s: dict, off_s: dict, live: dict, diff: dict | None = None
) -> str:
    parts = [
        f"Sim window: AlphaI {'+' if delta['total_eur'] >= 0 else ''}{delta['total_eur']} EUR "
        f"vs tape-only ({on_s['total_eur']} vs {off_s['total_eur']}), "
        f"WR {on_s['win_rate']:.0%} vs {off_s['win_rate']:.0%} "
        f"({on_s['trades']} vs {off_s['trades']} trades)."
    ]
    if delta.get("entry_set_unchanged"):
        parts.append(
            f"Same entry set; clip-boost vs rank-only = "
            f"{delta.get('vs_rank_only_eur', 0):+.2f} EUR, "
            f"rank/macro vs tape = {delta.get('rank_only_vs_tape_eur', 0):+.2f} EUR."
        )
    elif diff is not None:
        parts.append(
            f"Entry set changed: +{len(diff['only_with_alphai'])} only-with / "
            f"+{len(diff['only_without_alphai'])} only-without."
        )
    lb = live.get("by_bucket") or {}
    any_a = lb.get("any_alphai_tag")
    tape = lb.get("no_alphai_tag") or lb.get("tape_only")
    bits = []
    if any_a:
        bits.append(
            f"live AlphaI-tagged n={any_a['n']} pnl={any_a['pnl']} WR={any_a['wr']:.0%}"
        )
    if tape:
        bits.append(f"live tape-only n={tape['n']} pnl={tape['pnl']} WR={tape['wr']:.0%}")
    if bits:
        parts.append("Live fills: " + "; ".join(bits) + ".")
    return " ".join(parts)


if __name__ == "__main__":
    main()
