#!/usr/bin/env python3
"""Search DeskConfig variants for highest win-rate on available Bitvavo 15m data."""
from __future__ import annotations

import itertools
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "wr_config_search.json"
LOG = Path(__file__).resolve().parent / "wr_config_search.log"
BOOK = 4_036.0
CLIP = 1_300.0
MIN_TRADES = 15
N_WORKERS = 8
DAYS = 135

_CANDLES = None
_START_MS = 0
_END_MS = 0


def init_worker(end_ms: int, start_ms: int) -> None:
    global _CANDLES, _START_MS, _END_MS
    _START_MS, _END_MS = start_ms, end_ms
    bases = ("BTC", *DeskConfig().universe)
    _CANDLES = load_candles(bases, days=DAYS, end_ms=end_ms)


def live_base(**overrides: Any) -> DeskConfig:
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
        trail_pct=0.04,
        trail_tight_after=0.05,
        trail_tight_pct=0.025,
        hard_stop_pct=0.03,
        time_exit_hours=24.0,
        green_deadline_hours=4.0,
        green_min_peak=0.01,
        btc_min_ret=-0.01,
        min_breadth=0.5,
        decision_every_bar=False,
        decision_interval_sec=0.0,
        # fade_fast is live-mark only; disable in bar replay
        fade_eta_sec=0.0,
    )
    cfg.update(overrides)
    return DeskConfig().with_overrides(**cfg)


def summarize(res) -> dict[str, Any]:
    s = res.summary()
    n = int(s["trades"] or 0)
    wr = float(s["win_rate"] or 0.0) if n else 0.0
    return {
        "total_eur": float(s["total_eur"]),
        "max_drawdown_eur": float(s["max_drawdown_eur"]),
        "trades": n,
        "win_rate": wr,
        "avg_net_per_trade_eur": s.get("avg_net_per_trade_eur"),
        "by_reason": s.get("by_reason"),
    }


def eval_one(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload["name"]
    overrides = payload["overrides"]
    cfg = live_base(**overrides)
    res = simulate(_CANDLES, cfg, start_ms=_START_MS, end_ms=_END_MS)
    out = summarize(res)
    out["name"] = name
    out["overrides"] = overrides
    return out


def grid() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(name: str, **ov: Any) -> None:
        rows.append({"name": name, "overrides": ov})

    # Baselines
    add("live_current")
    add(
        "prior_40k_winner_scaled",
        min_excess=0.025,
        trail_pct=0.04,
        trail_tight_after=0.05,
        trail_tight_pct=0.025,
        hard_stop_pct=0.03,
        midflat_hours=24.0,
        max_chase_ret_24h=0.0,
        soft_regime_on_weak_tape=True,
        strong_clip_requires_quality=True,
        green_deadline_hours=0.0,
        decision_hours_utc=(7, 13),
    )
    add("hours_071316", decision_hours_utc=(7, 13, 16))
    add("hours_0713", decision_hours_utc=(7, 13))
    add("hours_0716", decision_hours_utc=(7, 16))
    add("hours_0814", decision_hours_utc=(8, 14))
    add("every_bar", decision_every_bar=True)

    # Coarse WR grid (~250). Trail packs keep trigger ≥ base trail.
    trail_packs = (
        (0.03, 0.04, 0.02),
        (0.04, 0.05, 0.025),
        (0.05, 0.06, 0.025),
        (0.06, 0.08, 0.03),
    )
    for me, (trail, tta, ttp), hs, te, gd, hours in itertools.product(
        [0.025, 0.03, 0.035, 0.04],
        trail_packs,
        [0.03, 0.04],
        [24.0, 36.0],
        [0.0, 4.0],
        [(7, 13, 16), (7, 13), (7, 16)],
    ):
        name = (
            f"me{me}_tr{trail}_tta{tta}_ttp{ttp}_hs{hs}_te{te}_gd{gd}"
            f"_h{'-'.join(map(str, hours))}"
        )
        add(
            name,
            min_excess=me,
            trail_pct=trail,
            trail_tight_after=tta,
            trail_tight_pct=ttp,
            hard_stop_pct=hs,
            time_exit_hours=te,
            green_deadline_hours=gd,
            soft_regime_on_weak_tape=True,
            strong_clip_requires_quality=True,
            decision_hours_utc=hours,
        )

    # Extra high-selectivity / schedule probes
    for me in (0.045, 0.05):
        for hours in ((7, 13, 16), (7, 16)):
            add(
                f"strict_me{me}_h{'-'.join(map(str, hours))}",
                min_excess=me,
                decision_hours_utc=hours,
                green_deadline_hours=4.0,
            )

    # Dedup by override frozenset
    seen: set[tuple] = set()
    uniq: list[dict[str, Any]] = []
    for r in rows:
        key = tuple(
            sorted(
                (k, tuple(v) if isinstance(v, (tuple, list)) else v)
                for k, v in r["overrides"].items()
            )
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def main() -> None:
    end_ms = int(time.time() * 1000) // (15 * 60_000) * (15 * 60_000)
    start_ms = end_ms - DAYS * 86_400_000
    configs = grid()
    print(
        f"configs={len(configs)} days={DAYS} book={BOOK} clip={CLIP} workers={N_WORKERS}",
        flush=True,
    )

    # Smoke on main process (also warms cache)
    init_worker(end_ms, start_ms)
    t0 = time.time()
    smoke = eval_one(configs[0])
    print(
        f"smoke {smoke['name']}: WR={smoke['win_rate']:.1%} n={smoke['trades']} "
        f"pnl={smoke['total_eur']:+.1f} ({time.time()-t0:.1f}s)",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    t0 = time.time()
    with ProcessPoolExecutor(
        max_workers=N_WORKERS,
        initializer=init_worker,
        initargs=(end_ms, start_ms),
    ) as ex:
        futs = {ex.submit(eval_one, c): c["name"] for c in configs}
        done = 0
        for fut in as_completed(futs):
            done += 1
            name = futs[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "name": name,
                        "error": str(exc),
                        "trades": 0,
                        "win_rate": 0.0,
                        "total_eur": 0.0,
                        "max_drawdown_eur": 0.0,
                        "overrides": {},
                    }
                )
            if done % 25 == 0 or done == len(configs):
                msg = f"progress {done}/{len(configs)} elapsed={time.time()-t0:.0f}s"
                print(msg, flush=True)
                LOG.write_text(msg + "\n")

    valid = [r for r in results if r.get("trades", 0) >= MIN_TRADES and "error" not in r]
    by_wr = sorted(valid, key=lambda r: (r["win_rate"], r["total_eur"]), reverse=True)
    by_wr30 = sorted(
        [r for r in valid if r["trades"] >= 30],
        key=lambda r: (r["win_rate"], r["total_eur"]),
        reverse=True,
    )
    by_pnl = sorted(valid, key=lambda r: r["total_eur"], reverse=True)
    live_row = next((r for r in results if r.get("name") == "live_current"), None)

    out = {
        "asof": datetime.now(tz=UTC).isoformat(),
        "meta": {
            "days": DAYS,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "book_eur": BOOK,
            "clip_eur": CLIP,
            "min_trades": MIN_TRADES,
            "n_configs": len(configs),
            "n_valid": len(valid),
            "elapsed_sec": round(time.time() - t0, 1),
            "note": (
                "Bar-close Bitvavo 15m replay; fade_fast/AlphaI/BBO not modeled. "
                "Entries at decision hours (unless decision_every_bar)."
            ),
        },
        "live_current": live_row,
        "top_wr": by_wr[:25],
        "top_wr_n30": by_wr30[:25],
        "top_pnl": by_pnl[:15],
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}", flush=True)
    print("=== LIVE ===", flush=True)
    print(json.dumps(live_row, indent=2), flush=True)
    print("=== TOP WR (n>=15) ===", flush=True)
    for r in by_wr[:12]:
        print(
            f"WR={r['win_rate']:.1%} n={r['trades']:3d} pnl={r['total_eur']:+8.1f} "
            f"dd={r['max_drawdown_eur']:7.1f} {r['name'][:100]}",
            flush=True,
        )
    print("=== TOP WR (n>=30) ===", flush=True)
    for r in by_wr30[:12]:
        print(
            f"WR={r['win_rate']:.1%} n={r['trades']:3d} pnl={r['total_eur']:+8.1f} "
            f"dd={r['max_drawdown_eur']:7.1f} {r['name'][:100]}",
            flush=True,
        )


if __name__ == "__main__":
    main()
