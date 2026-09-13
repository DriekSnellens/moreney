#!/usr/bin/env python3
"""Realistic 8-week dual-sleeve replay at €40k total capital.

Splits capital in live design proportions (core €4k clip-book + volatile €650
soft book → ~86% / ~14%), scales risk caps, uses fee_rt=0.003 and core
exit_on_touch. Volatile entries require AlphaI green (current board snapshot —
no historical AlphaI path available).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import BAR_MS, DEFAULT_UNIVERSE, DeskConfig
from bot.live.momentum_runner import desk_config_from_settings
from bot.live.momentum_volatile_shadow import (
    _candle_cache_dir,
    load_shadow_alphai,
    shadow_config,
    simulate_volatile_alphai,
)
from bot.core.config import get_settings
from bot.research.momentum_backtest.engine import load_candles, simulate, walk_forward

DESIGN_CORE_BOOK = 4_000.0
DESIGN_CORE_CLIP = 1_300.0
DESIGN_CORE_DAY = 100.0
DESIGN_CORE_WEEK = 250.0
DESIGN_VOL_BOOK = 650.0
DESIGN_VOL_CLIP = 650.0
DESIGN_VOL_DAY = 80.0
DESIGN_VOL_WEEK = 200.0
DESIGN_TOTAL = DESIGN_CORE_BOOK + DESIGN_VOL_BOOK

CAPITAL_EUR = 40_000.0
DAYS = 56
WINDOW_DAYS = 14
OUT = Path("artifacts/dual_sleeve_40k_8w.json")


def _cfg_public(cfg: Any) -> dict[str, Any]:
    raw = asdict(cfg) if hasattr(cfg, "__dataclass_fields__") else dict(cfg.__dict__)
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "clusters":
            continue
        if isinstance(v, tuple):
            out[k] = list(v)
        elif isinstance(v, dict):
            out[k] = {str(a): b for a, b in v.items()}
        else:
            out[k] = v
    return out


def _by_week(closed: list[dict[str, Any]], *, capital: float) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in closed:
        ms = int(row.get("closed_ms") or 0)
        if not ms:
            continue
        dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
        # ISO week label (Amsterdam-ish operator view ≈ UTC date is fine for weekly buckets)
        label = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
        buckets.setdefault(label, []).append(row)
    out = []
    for label in sorted(buckets):
        rows = buckets[label]
        nets = [float(r.get("net_eur") or 0.0) for r in rows]
        wins = sum(1 for n in nets if n > 0)
        realized = sum(nets)
        out.append(
            {
                "week": label,
                "trades": len(rows),
                "win_rate": round(wins / len(rows), 3) if rows else 0.0,
                "realized_eur": round(realized, 2),
                "return_pct": round(100.0 * realized / capital, 3) if capital else 0.0,
                "bases": sorted({str(r.get("base")) for r in rows}),
            }
        )
    return out


def main() -> None:
    scale = CAPITAL_EUR / DESIGN_TOTAL
    core_book = DESIGN_CORE_BOOK * scale
    core_clip = DESIGN_CORE_CLIP * scale
    vol_book = DESIGN_VOL_BOOK * scale
    vol_clip = DESIGN_VOL_CLIP * scale

    settings = get_settings()
    live_core = desk_config_from_settings(settings)
    core_cfg = live_core.with_overrides(
        book_eur=core_book,
        clip_eur=core_clip,
        day_loss_limit_eur=DESIGN_CORE_DAY * scale,
        week_loss_limit_eur=DESIGN_CORE_WEEK * scale,
        fee_rt=0.003,
        exit_on_touch=True,
        universe=DEFAULT_UNIVERSE,
    )

    alphai, alphai_meta = load_shadow_alphai()
    vol_base = shadow_config(alphai=alphai, scores=alphai_meta.get("scores") or {})
    vol_cfg = replace(
        vol_base,
        book_eur=vol_book,
        clip_eur=vol_clip,
        day_loss_limit_eur=DESIGN_VOL_DAY * scale,
        week_loss_limit_eur=DESIGN_VOL_WEEK * scale,
        fee_rt=0.003,
        # Historical board: disable wall-clock stale gate for replay.
        alphai_max_age_hours=0.0,
    )

    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - DAYS * 86_400_000
    cache = _candle_cache_dir()

    core_bases = ("BTC", *core_cfg.universe)
    vol_bases = ("BTC", *vol_cfg.universe)
    print(f"loading candles core={len(core_bases)} vol={len(vol_bases)} days={DAYS+2}…")
    core_candles = load_candles(core_bases, days=DAYS + 2, end_ms=end_ms, cache_dir=cache)
    vol_candles = load_candles(vol_bases, days=DAYS + 2, end_ms=end_ms, cache_dir=cache)

    print("simulating core…")
    core_full = simulate(core_candles, core_cfg, start_ms=start_ms, end_ms=end_ms)
    core_windows = walk_forward(
        core_candles, core_cfg, start_ms=start_ms, end_ms=end_ms, window_days=WINDOW_DAYS
    )
    core_closed = [t.as_row() for t in core_full.closed]

    print("simulating volatile…")
    vol_res = simulate_volatile_alphai(
        vol_candles, vol_cfg, start_ms=start_ms, end_ms=end_ms, alphai=alphai
    )
    vol_summary = vol_res["summary"]
    vol_closed = list(vol_res.get("closed") or [])

    core_total = float(core_full.summary()["total_eur"])
    vol_total = float(vol_summary.get("total_eur") or 0.0)
    combined = core_total + vol_total

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "method": (
            "Independent bar replays of core (momentum_backtest.simulate) and "
            "volatile (simulate_volatile_alphai) on Bitvavo 15m candles. Capital "
            f"€{CAPITAL_EUR:.0f} split in live design ratio "
            f"(core €{DESIGN_CORE_BOOK:.0f} + vol €{DESIGN_VOL_BOOK:.0f}). "
            "fee_rt=0.003; core exit_on_touch=True. Volatile AlphaI = current "
            "board snapshot held flat over the window (no historical AlphaI). "
            "No shared cash coupling, no slippage beyond fee_rt."
        ),
        "capital_eur": CAPITAL_EUR,
        "days": DAYS,
        "scale_from_design": round(scale, 6),
        "allocation": {
            "core_book_eur": round(core_book, 2),
            "core_clip_eur": round(core_clip, 2),
            "volatile_book_eur": round(vol_book, 2),
            "volatile_clip_eur": round(vol_clip, 2),
        },
        "window": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_utc": datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat(),
        },
        "combined": {
            "total_eur": round(combined, 2),
            "return_on_40k_pct": round(100.0 * combined / CAPITAL_EUR, 3),
            "core_total_eur": round(core_total, 2),
            "volatile_total_eur": round(vol_total, 2),
            "core_share_pct": round(100.0 * core_total / combined, 1) if combined else None,
        },
        "core": {
            "config": _cfg_public(core_cfg),
            "full": core_full.summary(),
            "windows_14d": [w.summary() for w in core_windows],
            "by_week": _by_week(core_closed, capital=core_book),
            "trades": core_closed,
        },
        "volatile": {
            "config": _cfg_public(vol_cfg),
            "alphai": {
                "loaded": alphai_meta.get("loaded"),
                "generated_at": alphai_meta.get("generated_at"),
                "age_hours": alphai_meta.get("age_hours"),
                "effective_picks": alphai_meta.get("effective_picks"),
                "effective_avoid": alphai_meta.get("effective_avoid"),
                "macro_caution": alphai_meta.get("macro_caution"),
                "note": alphai_meta.get("note"),
            },
            "summary": vol_summary,
            "by_week": _by_week(vol_closed, capital=vol_book),
            "trades": vol_closed,
            "open_mtm": vol_res.get("open_mtm"),
        },
        "caveats": [
            "AlphaI board is a single snapshot for the whole 8 weeks (volatile gated on it).",
            "Replay fills at bar close / touch — no latency, impact, or partial fills.",
            "Core and volatile books are independent (no shared cash).",
            "Bitvavo candles only (live core may also use OKX).",
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload["combined"], indent=2))
    print("core", payload["core"]["full"])
    print("vol", payload["volatile"]["summary"])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
