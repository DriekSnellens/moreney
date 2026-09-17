#!/usr/bin/env python3
"""12w A/B: ignore AlphaI macro_caution for entries vs live macro entry gates."""

from __future__ import annotations

import bisect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from bot.live.momentum_desk import AlphaIView, DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path("artifacts/macro_caution_entry_ignore_12w.json")
END = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
END_MS = int(END.timestamp() * 1000)
START = END - timedelta(weeks=12)
START_MS = int(START.timestamp() * 1000)


def cfg(**extra: Any) -> DeskConfig:
    knobs: dict[str, Any] = dict(
        decision_hours_utc=(7, 13, 16),
        clip_eur=20_000.0,
        max_positions=1,
        book_eur=20_000.0,
        min_excess=0.025,
        entry_fee_buffer_mult=6.0,
        trail_pct=0.03,
        trail_tight_after=0.04,
        trail_tight_pct=0.02,
        hard_stop_pct=0.03,
        early_stop_pct=0.02,
        early_stop_until_peak=0.015,
        time_exit_hours=36.0,
        midflat_hours=0.0,
        green_deadline_hours=0.0,
        fade_eta_sec=0.0,
        soft_regime_on_weak_tape=True,
        soft_regime_clip_mult=0.5,
        weak_tape_idle_on_double=True,
        soft_regime_idle_on_macro_caution=True,
        day_loss_limit_eur=750.0,
        week_loss_limit_eur=2000.0,
        skip_weekend_entries=True,
        refill_on_exit=True,
        strong_clip_mult=1.3,
        weak_clip_mult=0.7,
        max_chase_ret_24h=0.0,
        max_from_high=0.02,
        alphai_rank_boost=0.01,
        alphai_clip_mult=1.3,
        alphai_size_mode="conviction",
        macro_caution_mode="reduce",
        macro_caution_requires_alphai_pick=True,
        macro_caution_clip_mult=0.7,
        macro_caution_fee_buffer_mult=7.0,
    )
    knobs.update(extra)
    return DeskConfig().with_overrides(**knobs)


def _parse_ts(raw: str) -> int:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def load_timeline() -> tuple[list[int], list[AlphaIView], dict[str, Any]]:
    by_ms: dict[int, AlphaIView] = {}
    sources: list[dict[str, Any]] = []
    merged = Path("artifacts/alphai_pick_outcomes_merged.json")
    if merged.exists():
        n = 0
        for s in json.loads(merged.read_text()).get("sessions") or []:
            gen = s.get("generated_at") or ""
            if not gen:
                continue
            picks = frozenset(
                str(p.get("base") or "").upper()
                for p in (s.get("picks") or [])
                if isinstance(p, dict) and p.get("base")
            )
            by_ms[_parse_ts(gen)] = AlphaIView(
                picks=picks, avoid=frozenset(), macro_caution=bool(s.get("macro_caution"))
            )
            n += 1
        sources.append({"file": str(merged), "sessions": n})
    ledger = Path("data/momentum_desk_ledger.jsonl")
    if ledger.exists():
        n = 0
        for line in ledger.read_text().splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("event") != "decision" or not isinstance(o.get("alphai"), dict):
                continue
            ai = o["alphai"]
            at = o.get("at") or o.get("ts")
            if not at:
                continue
            by_ms[_parse_ts(at)] = AlphaIView(
                picks=frozenset(str(x).upper() for x in (ai.get("picks") or [])),
                avoid=frozenset(str(x).upper() for x in (ai.get("avoid") or [])),
                macro_caution=bool(ai.get("macro_caution")),
            )
            n += 1
        sources.append({"file": str(ledger), "sessions": n})
    times = sorted(by_ms)
    views = [by_ms[t] for t in times]
    macro_n = sum(1 for v in views if v.macro_caution)
    meta = {
        "points": len(views),
        "first": datetime.fromtimestamp(times[0] / 1000, UTC).isoformat() if times else None,
        "last": datetime.fromtimestamp(times[-1] / 1000, UTC).isoformat() if times else None,
        "macro_caution_points": macro_n,
        "macro_caution_rate": round(macro_n / max(1, len(views)), 3),
        "sources": sources,
        "caveat": "AlphaI coverage starts ~2026-09-07; earlier weeks tape-only for both arms.",
    }
    return times, views, meta


def make_at(
    times: list[int], views: list[AlphaIView], *, ignore_macro: bool
) -> Callable[[int], AlphaIView | None]:
    def alphai_at(t_ms: int) -> AlphaIView | None:
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        if i < 0:
            return None
        v = views[i]
        if ignore_macro and v.macro_caution:
            return AlphaIView(picks=v.picks, avoid=v.avoid, macro_caution=False)
        return v

    return alphai_at


def summarize(label: str, res) -> dict[str, Any]:
    sm = res.summary()
    return {
        "label": label,
        "total_eur": sm["total_eur"],
        "realized_eur": sm["realized_eur"],
        "open_mtm_eur": sm["open_mtm_eur"],
        "max_dd_eur": sm["max_drawdown_eur"],
        "win_rate": sm["win_rate"],
        "trades": sm["trades"],
        "open_mtm": res.open_mtm,
        "by_reason": sm["by_reason"],
    }


def main() -> None:
    times, views, meta = load_timeline()
    candles = load_candles(("BTC", *DeskConfig().universe), days=120, end_ms=END_MS, refresh=False)
    base = cfg()
    cfg_ignore = cfg(
        macro_caution_requires_alphai_pick=False,
        soft_regime_idle_on_macro_caution=False,
        macro_caution_clip_mult=1.0,
        macro_caution_fee_buffer_mult=6.0,
    )
    arms = [
        ("tape_only_no_alphai", base, None),
        ("with_macro_entry_gates", base, make_at(times, views, ignore_macro=False)),
        ("ignore_macro_for_entries", cfg_ignore, make_at(times, views, ignore_macro=True)),
    ]
    rows = []
    for label, c, at in arms:
        print("run", label, flush=True)
        res = simulate(candles, c, start_ms=START_MS, end_ms=END_MS, alphai_at=at)
        rows.append(summarize(label, res))
        print(rows[-1], flush=True)

    with_m = next(r for r in rows if r["label"] == "with_macro_entry_gates")
    ign = next(r for r in rows if r["label"] == "ignore_macro_for_entries")
    out = {
        "asof": datetime.now(UTC).isoformat(),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "alphai_timeline": meta,
        "full_12w": {
            "rows": rows,
            "delta_ignore_minus_with_macro": {
                "delta_total_pnl": round(ign["total_eur"] - with_m["total_eur"], 2),
                "delta_realized": round(ign["realized_eur"] - with_m["realized_eur"], 2),
                "delta_open_mtm": round(ign["open_mtm_eur"] - with_m["open_mtm_eur"], 2),
            },
        },
        "clarification": {
            "realized_12w_identical": ign["realized_eur"] == with_m["realized_eur"],
            "note": "Any total_eur gap is open MTM (e.g. NEAR entered only when ignoring macro).",
        },
        "verdict_nl": (
            f"Gesloten 12w: met/zonder macro identiek (€{with_m['realized_eur']:.0f}). "
            f"Total Δ€{ign['total_eur'] - with_m['total_eur']:+.0f} = open MTM only."
        ),
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(out["verdict_nl"], flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
