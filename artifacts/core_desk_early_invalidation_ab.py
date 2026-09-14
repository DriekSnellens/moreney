#!/usr/bin/env python3
"""A/B: winner core desk vs early-invalidation exit variants at 10/20/40k."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.live.momentum_desk import DeskConfig
from bot.research.momentum_backtest.engine import load_candles, simulate

OUT = Path(__file__).resolve().parent / "core_desk_early_invalidation_ab.json"
DESIGN_EUR = 4_000.0
CAPITALS = (10_000.0, 20_000.0, 40_000.0)

WINNER: dict[str, Any] = dict(
    decision_hours_utc=(7, 13),
    fee_rt=0.003,
    exit_on_touch=True,
    skip_weekend_entries=True,
    min_excess=0.025,
    entry_fee_buffer_mult=6.0,
    max_chase_ret_24h=0.0,
    midflat_hours=0.0,
    trail_pct=0.04,
    trail_tight_after=0.05,
    trail_tight_pct=0.025,
    hard_stop_pct=0.03,
    time_exit_hours=24.0,
    soft_regime_on_weak_tape=True,
    strong_clip_requires_quality=True,
    strong_clip_min_excess=0.04,
    max_positions=4,
    early_stop_pct=0.0,
    early_stop_until_peak=0.0,
    green_deadline_hours=0.0,
    green_min_peak=0.0,
)

VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("winner", {}),
    ("staged_1p5_until_0p5", {"early_stop_pct": 0.015, "early_stop_until_peak": 0.005}),
    ("staged_2p0_until_1p0", {"early_stop_pct": 0.02, "early_stop_until_peak": 0.01}),
    ("staged_2p0_until_0p5", {"early_stop_pct": 0.02, "early_stop_until_peak": 0.005}),
    ("green_4h_1pct", {"green_deadline_hours": 4.0, "green_min_peak": 0.01}),
    ("green_6h_1pct", {"green_deadline_hours": 6.0, "green_min_peak": 0.01}),
    ("green_4h_0p5", {"green_deadline_hours": 4.0, "green_min_peak": 0.005}),
    (
        "combo_staged_1p5_green_4h_1pct",
        {
            "early_stop_pct": 0.015,
            "early_stop_until_peak": 0.005,
            "green_deadline_hours": 4.0,
            "green_min_peak": 0.01,
        },
    ),
    (
        "combo_staged_2_green_6h_1pct",
        {
            "early_stop_pct": 0.02,
            "early_stop_until_peak": 0.01,
            "green_deadline_hours": 6.0,
            "green_min_peak": 0.01,
        },
    ),
]


def make_cfg(capital_eur: float, overrides: dict[str, Any]) -> DeskConfig:
    scale = capital_eur / DESIGN_EUR
    knobs = dict(WINNER)
    knobs.update(overrides)
    knobs.update(
        clip_eur=1_300.0 * scale,
        book_eur=capital_eur,
        day_loss_limit_eur=100.0 * scale,
        week_loss_limit_eur=250.0 * scale,
    )
    return DeskConfig().with_overrides(**knobs)


def summarize(res, capital_eur: float) -> dict[str, Any]:
    s = res.summary()
    total = float(s["total_eur"])
    dd = float(s["max_drawdown_eur"])
    trades = int(s["trades"] or 0)
    by_month: dict[str, dict[str, float]] = {}
    for t in res.closed:
        month = datetime.fromtimestamp(t.closed_ms / 1000, tz=UTC).strftime("%Y-%m")
        slot = by_month.setdefault(month, {"trades": 0.0, "wins": 0.0, "realized_eur": 0.0})
        slot["trades"] += 1
        slot["wins"] += 1 if t.net_eur > 0 else 0
        slot["realized_eur"] += float(t.net_eur)
    months = []
    for month, v in sorted(by_month.items()):
        n = int(v["trades"])
        months.append(
            {
                "month": month,
                "trades": n,
                "win_rate": round(v["wins"] / n, 3) if n else 0.0,
                "realized_eur": round(v["realized_eur"], 2),
                "return_pct": round(100.0 * v["realized_eur"] / capital_eur, 3),
            }
        )
    return {
        "capital_eur": capital_eur,
        "trades": trades,
        "win_rate": s["win_rate"],
        "total_eur": round(total, 2),
        "return_pct": round(100.0 * total / capital_eur, 3),
        "max_drawdown_eur": round(dd, 2),
        "max_drawdown_pct": round(100.0 * dd / capital_eur, 3),
        "fees_eur": s["fees_eur"],
        "avg_net_per_trade_eur": s.get("avg_net_per_trade_eur"),
        "by_reason": s["by_reason"],
        "by_month": months,
        "calmar_like": round(total / max(abs(dd), capital_eur * 0.01), 4),
    }


def main() -> None:
    end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    bases = ("BTC", *DeskConfig().universe)
    candles = load_candles(bases, days=135, end_ms=end_ms)
    # Align window to available BTC bars (same as prior winner sims).
    btc = candles.get("BTC") or []
    start_ms = int(btc[0][0]) if btc else 0
    end_bar = int(btc[-1][0]) if btc else end_ms

    results: dict[str, Any] = {
        "asof": datetime.now(tz=UTC).isoformat(),
        "method": (
            "Winner core desk vs early-invalidation exit variants "
            "(staged stop / time-to-green / combo). Same candles & "
            "linear book/clip/day/week scale from €4k design. AlphaI=None."
        ),
        "window": {
            "start_utc": datetime.fromtimestamp(start_ms / 1000, tz=UTC).isoformat(),
            "end_utc": datetime.fromtimestamp(end_bar / 1000, tz=UTC).isoformat(),
        },
        "variants": {},
    }

    for name, overrides in VARIANTS:
        print(f"=== {name} ===", flush=True)
        by_capital: dict[str, Any] = {}
        for capital in CAPITALS:
            cfg = make_cfg(capital, overrides)
            res = simulate(candles, cfg, start_ms=start_ms, end_ms=end_bar + 900_000, alphai=None)
            by_capital[str(int(capital))] = summarize(res, capital)
            row = by_capital[str(int(capital))]
            print(
                f"  €{int(capital)/1000:.0f}k: WR={row['win_rate']:.1%} "
                f"net={row['total_eur']:+.0f} ({row['return_pct']:+.1f}%) "
                f"DD={row['max_drawdown_eur']:.0f} ({row['max_drawdown_pct']:.1f}%) "
                f"n={row['trades']}",
                flush=True,
            )
        results["variants"][name] = {
            "overrides": overrides,
            "by_capital": by_capital,
        }

    # Rank on €40k: prefer higher calmar, then higher net, then higher WR.
    ranked = []
    for name, payload in results["variants"].items():
        r40 = payload["by_capital"]["40000"]
        ranked.append(
            {
                "name": name,
                "win_rate": r40["win_rate"],
                "total_eur": r40["total_eur"],
                "return_pct": r40["return_pct"],
                "max_drawdown_pct": r40["max_drawdown_pct"],
                "calmar_like": r40["calmar_like"],
                "trades": r40["trades"],
            }
        )
    ranked.sort(key=lambda r: (r["calmar_like"], r["total_eur"], r["win_rate"]), reverse=True)
    results["rank_40k"] = ranked
    winner_row = results["variants"]["winner"]["by_capital"]["40000"]
    best = ranked[0]
    beats = best["name"] != "winner" and (
        best["calmar_like"] > winner_row["calmar_like"] + 0.05
        or (
            best["total_eur"] >= winner_row["total_eur"]
            and best["max_drawdown_pct"] > winner_row["max_drawdown_pct"]  # less negative
            and best["win_rate"] > winner_row["win_rate"] + 0.02
        )
    )
    results["deploy_advice"] = {
        "best_variant": best["name"],
        "beats_winner": beats,
        "note": (
            "Deploy early-invalidation only if beats_winner is true; "
            "otherwise keep live winner knobs."
        ),
    }

    OUT.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {OUT}", flush=True)
    print("rank_40k:", json.dumps(ranked, indent=2), flush=True)
    print("deploy_advice:", json.dumps(results["deploy_advice"], indent=2), flush=True)


if __name__ == "__main__":
    main()
