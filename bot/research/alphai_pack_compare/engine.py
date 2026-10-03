"""Compare live-relevant residual/clip packs on the real AlphaI overlap only."""

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


def pack_specs() -> list[dict[str, Any]]:
    """Coin-agnostic packs: live residual_full, clip 20/80, and DD-aware variants."""
    return [
        {
            "name": "live_residual_full",
            "btc_frac": 0.0,
            "excess_floor": 0.035,
            "lookback_days": 10,
            "flatten": "all",
            "require_alt_sma": True,
            "cash_when_no_alt": True,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "live_clip_20_80",
            "btc_frac": 0.20,
            "excess_floor": 0.04,
            "lookback_days": 10,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "res100_trail10",
            "btc_frac": 0.0,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "none",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "res100_trail8",
            "btc_frac": 0.0,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "none",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.08,
            "rebalance_days": 7,
        },
        {
            "name": "res100_trail12",
            "btc_frac": 0.0,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "none",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.12,
            "rebalance_days": 7,
        },
        {
            "name": "res100_sma_trail10",
            "btc_frac": 0.0,
            "excess_floor": 0.035,
            "lookback_days": 10,
            "flatten": "all",
            "require_alt_sma": True,
            "cash_when_no_alt": True,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "btc50_res50_trail10",
            "btc_frac": 0.50,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "btc75_res25_trail10",
            "btc_frac": 0.75,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.10,
            "rebalance_days": 7,
        },
        {
            "name": "btc_only_sma50",
            "btc_frac": 1.0,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.0,
            "rebalance_days": 7,
        },
        # Daily residual hunt on the short AlphaI window (more decisions).
        {
            "name": "res100_trail10_reb1",
            "btc_frac": 0.0,
            "excess_floor": 0.035,
            "lookback_days": 10,
            "flatten": "all",
            "require_alt_sma": True,
            "cash_when_no_alt": True,
            "trail": 0.10,
            "rebalance_days": 1,
        },
        {
            "name": "live_residual_full_reb1",
            "btc_frac": 0.0,
            "excess_floor": 0.035,
            "lookback_days": 10,
            "flatten": "all",
            "require_alt_sma": True,
            "cash_when_no_alt": True,
            "trail": 0.10,
            "rebalance_days": 1,
        },
        {
            "name": "btc50_res50_trail10_reb1",
            "btc_frac": 0.50,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "trail": 0.10,
            "rebalance_days": 1,
        },
    ]


def alphai_modes() -> list[tuple[str, str | None]]:
    """(label, alt_allow_mode or None for off)."""
    return [
        ("off", None),
        ("gate", "gate"),
        ("intersect", "intersect"),
    ]


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    keep = {
        k: v
        for k, v in row.items()
        if k
        in {
            "strategy",
            "pnl_eur",
            "pnl_pct",
            "max_dd_pct",
            "calmar",
            "ann_pct",
            "n_trades",
            "end_eur",
            "end_hold",
            "start_eur",
            "year_pnl",
            "weeks",
            "n_overlay_exits",
        }
    }
    return keep


def _score_row(row: Mapping[str, Any]) -> tuple[float, float, float]:
    """Sort key: calmar desc, pnl desc, dd asc."""
    return (
        float(row.get("calmar") or 0.0),
        float(row.get("pnl_eur") or 0.0),
        -float(row.get("max_dd_pct") or 0.0),
    )


def run_one(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    pack: Mapping[str, Any],
    alphai_label: str,
    alt_allow: Mapping[str, set[str]] | None,
    alt_allow_mode: str,
    model: FillModel = WET,
) -> dict[str, Any]:
    trail = float(pack.get("trail") or 0.0)
    policy = (
        ExitPolicy(name=f"alt_trail_{int(round(trail * 100))}", alt_trail_pct=trail)
        if trail > 0
        else None
    )
    name = f"{pack['name']}__ai_{alphai_label}"
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        btc_frac=float(pack["btc_frac"]),
        excess_floor=float(pack["excess_floor"]),
        flatten=str(pack["flatten"]),  # type: ignore[arg-type]
        lookback_days=int(pack["lookback_days"]),
        rebalance_days=int(pack["rebalance_days"]),
        require_alt_sma=bool(pack["require_alt_sma"]),
        cash_when_no_alt=bool(pack["cash_when_no_alt"]),
        model=model,
        policy=policy,
        keep_weeks=True,
        strategy=name,
        alt_allow=alt_allow,
        alt_allow_mode=alt_allow_mode or "gate",
    )
    slim = _strip(row)
    slim.update(
        {
            "name": name,
            "pack": str(pack["name"]),
            "alphai": alphai_label,
            "trail": trail,
            "btc_frac": float(pack["btc_frac"]),
            "excess_floor": float(pack["excess_floor"]),
            "rebalance_days": int(pack["rebalance_days"]),
            "require_alt_sma": bool(pack["require_alt_sma"]),
            "cash_when_no_alt": bool(pack["cash_when_no_alt"]),
            "flatten": str(pack["flatten"]),
        }
    )
    return slim


def pareto_front(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Non-dominated on maximize PnL and minimize DD."""
    front: list[dict[str, Any]] = []
    for row in rows:
        pnl = float(row.get("pnl_eur") or 0.0)
        dd = float(row.get("max_dd_pct") or 0.0)
        dominated = False
        for other in rows:
            if other is row:
                continue
            op = float(other.get("pnl_eur") or 0.0)
            od = float(other.get("max_dd_pct") or 0.0)
            if (op >= pnl and od <= dd) and (op > pnl or od < dd):
                dominated = True
                break
        if not dominated:
            front.append(dict(row))
    front.sort(key=lambda r: (-float(r.get("pnl_eur") or 0.0), float(r.get("max_dd_pct") or 0.0)))
    return front


def run_alphai_pack_compare(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    sessions_path: str,
    decision_hour_utc: int = 7,
    dates: Sequence[str],
    model: FillModel = WET,
) -> dict[str, Any]:
    sessions = load_merged_sessions(sessions_path)
    daily = picks_asof_hour(
        sessions, hour_utc=decision_hour_utc, universe=tuple(ohlc.keys())
    )
    allow = build_alt_allow(
        start=start, end=end, ohlc_dates=dates, daily_picks=daily
    )
    cov = coverage_report(sessions, daily)

    ranked: list[dict[str, Any]] = []
    for pack in pack_specs():
        for ai_label, ai_mode in alphai_modes():
            alt_allow = None if ai_mode is None else allow
            row = run_one(
                ohlc,
                start=start,
                end=end,
                book_eur=book_eur,
                pack=pack,
                alphai_label=ai_label,
                alt_allow=alt_allow,
                alt_allow_mode=ai_mode or "gate",
                model=model,
            )
            ranked.append(row)

    ranked.sort(key=_score_row, reverse=True)
    by_ai: dict[str, list[dict[str, Any]]] = {"off": [], "gate": [], "intersect": []}
    for row in ranked:
        by_ai.setdefault(str(row["alphai"]), []).append(row)

    best_pnl = max(ranked, key=lambda r: float(r.get("pnl_eur") or 0.0))
    best_dd = min(ranked, key=lambda r: float(r.get("max_dd_pct") or 1.0))
    best_calmar = ranked[0]
    front = pareto_front(ranked)

    # Paired AlphaI lift vs same pack off.
    off_by_pack = {r["pack"]: r for r in ranked if r["alphai"] == "off"}
    lifts: list[dict[str, Any]] = []
    for row in ranked:
        if row["alphai"] == "off":
            continue
        base = off_by_pack.get(row["pack"])
        if not base:
            continue
        lifts.append(
            {
                "name": row["name"],
                "pack": row["pack"],
                "alphai": row["alphai"],
                "delta_pnl": float(row["pnl_eur"]) - float(base["pnl_eur"]),
                "delta_dd": float(row["max_dd_pct"]) - float(base["max_dd_pct"]),
                "delta_calmar": float(row["calmar"]) - float(base["calmar"]),
                "pnl_eur": row["pnl_eur"],
                "max_dd_pct": row["max_dd_pct"],
                "calmar": row["calmar"],
            }
        )
    lifts.sort(key=lambda r: (-float(r["delta_calmar"]), -float(r["delta_pnl"])))

    return {
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "decision_hour_utc": decision_hour_utc,
        "alphai_coverage": cov,
        "n_rows": len(ranked),
        "best_calmar": best_calmar,
        "best_pnl": best_pnl,
        "lowest_dd": best_dd,
        "pareto": front,
        "alphai_best": {
            mode: (rows[0] if rows else None) for mode, rows in by_ai.items()
        },
        "paired_alphai_lift": lifts[:12],
        "top": ranked[:15],
        "ranked": ranked,
    }
