"""Grid AlphaI allow-lists × selection modes on live residual_full."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.alphai_pack_compare.alphai_map import (
    build_alt_allow,
    coverage_report,
    load_merged_sessions,
    picks_asof_hour,
)
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, FillModel
from bot.research.clip_exit_lab.policies import ExitPolicy

TRAIL10 = ExitPolicy(name="alt_trail_10", alt_trail_pct=0.10)

# Live residual_full knobs.
RESIDUAL_FULL = {
    "btc_frac": 0.0,
    "excess_floor": 0.035,
    "lookback_days": 10,
    "skip_days": 1,
    "flatten": "all",
    "require_alt_sma": True,
    "cash_when_no_alt": True,
    "rebalance_days": 7,
}


def allow_specs() -> list[dict[str, Any]]:
    """How the daily AlphaI allow-set is built (still real sessions only)."""
    return [
        {"name": "asof07_all", "hour": 7, "top_k": None, "day_union": False},
        {"name": "asof13_all", "hour": 13, "top_k": None, "day_union": False},
        {"name": "asof16_all", "hour": 16, "top_k": None, "day_union": False},
        {"name": "asof07_top1", "hour": 7, "top_k": 1, "day_union": False},
        {"name": "asof07_top2", "hour": 7, "top_k": 2, "day_union": False},
        {"name": "asof07_top3", "hour": 7, "top_k": 3, "day_union": False},
        {"name": "day_union", "hour": 7, "top_k": None, "day_union": True},
        {"name": "day_union_top3", "hour": 7, "top_k": 3, "day_union": True},
    ]


def mode_specs() -> list[dict[str, Any]]:
    """How residual rank interacts with the allow-set."""
    return [
        {"name": "off", "mode": None, "override": None},
        {"name": "gate", "mode": "gate", "override": None},
        {"name": "intersect", "mode": "intersect", "override": None},
        {"name": "prefer", "mode": "prefer", "override": None},
        {"name": "overlap_or_rs", "mode": "overlap_or_rs", "override": None},
        {"name": "override_10", "mode": "override_gate", "override": 0.10},
        {"name": "override_15", "mode": "override_gate", "override": 0.15},
        {"name": "override_20", "mode": "override_gate", "override": 0.20},
        {"name": "override_25", "mode": "override_gate", "override": 0.25},
    ]


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if k
        in {
            "strategy",
            "pnl_eur",
            "pnl_pct",
            "max_dd_pct",
            "calmar",
            "n_trades",
            "end_eur",
            "end_hold",
            "weeks",
        }
    }


def run_gate_lab(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    dates: Sequence[str],
    sessions_path: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
) -> dict[str, Any]:
    sessions = load_merged_sessions(sessions_path)
    ranked: list[dict[str, Any]] = []

    # Baseline coverage at 07:00 for the report.
    base_daily = picks_asof_hour(sessions, hour_utc=7, universe=tuple(ohlc.keys()))
    cov = coverage_report(sessions, base_daily)

    for allow_spec in allow_specs():
        daily = picks_asof_hour(
            sessions,
            hour_utc=int(allow_spec["hour"]),
            universe=tuple(ohlc.keys()),
            top_k=allow_spec["top_k"],
            day_union=bool(allow_spec["day_union"]),
        )
        allow = build_alt_allow(
            start=start, end=end, ohlc_dates=dates, daily_picks=daily
        )
        for mode_spec in mode_specs():
            # off only once per allow-spec would duplicate; run off only with asof07_all.
            if mode_spec["mode"] is None and allow_spec["name"] != "asof07_all":
                continue
            name = (
                f"resfull__{allow_spec['name']}__{mode_spec['name']}"
                if mode_spec["mode"] is not None
                else "resfull__off"
            )
            alt_allow = None if mode_spec["mode"] is None else allow
            row = run_btc_residual(
                ohlc,
                start=start,
                end=end,
                book_eur=book_eur,
                model=model,
                policy=TRAIL10,
                keep_weeks=True,
                strategy=name,
                alt_allow=alt_allow,
                alt_allow_mode=str(mode_spec["mode"] or "gate"),
                alt_allow_excess_override=mode_spec["override"],
                **RESIDUAL_FULL,  # type: ignore[arg-type]
            )
            slim = _strip(row)
            pnl = float(slim.get("pnl_eur") or 0.0)
            dd = max(float(slim.get("max_dd_pct") or 0.0), 1e-6)
            slim.update(
                {
                    "name": name,
                    "allow": allow_spec["name"],
                    "mode": mode_spec["name"],
                    "hour": allow_spec["hour"],
                    "top_k": allow_spec["top_k"],
                    "day_union": allow_spec["day_union"],
                    "excess_override": mode_spec["override"],
                    "pnl_per_dd": pnl / dd,
                    "short_score": pnl - book_eur * dd,
                }
            )
            ranked.append(slim)

    ranked.sort(
        key=lambda r: (
            float(r.get("short_score") or 0.0),
            float(r.get("pnl_eur") or 0.0),
            -float(r.get("max_dd_pct") or 0.0),
        ),
        reverse=True,
    )
    off = next((r for r in ranked if r["mode"] == "off"), None)
    gated = [r for r in ranked if r["mode"] != "off"]
    best_gate = gated[0] if gated else None
    # Best gated that beats off on short_score, if any.
    beat_off = [
        r
        for r in gated
        if off is not None
        and float(r["short_score"]) >= float(off["short_score"])
        and float(r["max_dd_pct"]) <= float(off["max_dd_pct"]) + 1e-9
    ]
    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "pack": "live_residual_full",
        "alphai_coverage": cov,
        "n_rows": len(ranked),
        "off": off,
        "best_gate": best_gate,
        "best_beats_off": beat_off[0] if beat_off else None,
        "top": ranked[:20],
        "ranked": ranked,
    }
