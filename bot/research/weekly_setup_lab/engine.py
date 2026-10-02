"""Parameter grid for forward-week residual entries ±AlphaI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, FillModel
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.weekly_setup_lab.alphai_map import allow_mode_name, build_alt_allow


def entry_variants() -> list[dict[str, Any]]:
    """Coin-agnostic entry filters aimed at the *coming* week, not RS chase."""
    return [
        {"name": "rs_excess", "rank_by": "excess"},
        {"name": "setup_score", "rank_by": "setup"},
        {
            "name": "pullback_5",
            "rank_by": "setup",
            "min_pullback_from_high": 0.05,
        },
        {
            "name": "pullback_8",
            "rank_by": "setup",
            "min_pullback_from_high": 0.08,
        },
        {
            "name": "pullback_12",
            "rank_by": "setup",
            "min_pullback_from_high": 0.12,
        },
        {
            "name": "not_ath_95",
            "rank_by": "setup",
            "max_close_over_high": 0.95,
        },
        {
            "name": "not_ath_90",
            "rank_by": "setup",
            "max_close_over_high": 0.90,
        },
        {"name": "rsi_70", "rank_by": "setup", "max_rsi": 70.0},
        {"name": "rsi_65", "rank_by": "setup", "max_rsi": 65.0},
        {
            "name": "ext_sma_8",
            "rank_by": "setup",
            "max_ext_above_sma": 0.08,
        },
        {
            "name": "ext_sma_12",
            "rank_by": "setup",
            "max_ext_above_sma": 0.12,
        },
        {
            "name": "setup_pb5_rsi70",
            "rank_by": "setup",
            "min_pullback_from_high": 0.05,
            "max_rsi": 70.0,
        },
        {
            "name": "setup_pb8_rsi65_ext10",
            "rank_by": "setup",
            "min_pullback_from_high": 0.08,
            "max_rsi": 65.0,
            "max_ext_above_sma": 0.10,
        },
        {
            "name": "setup_pb5_notath95",
            "rank_by": "setup",
            "min_pullback_from_high": 0.05,
            "max_close_over_high": 0.95,
        },
        {
            "name": "setup_pb8_notath90_rsi70",
            "rank_by": "setup",
            "min_pullback_from_high": 0.08,
            "max_close_over_high": 0.90,
            "max_rsi": 70.0,
        },
    ]


def book_variants() -> list[dict[str, Any]]:
    """Book / exit sleeves that host the weekly residual pick."""
    return [
        {
            "name": "res100_hold",
            "btc_frac": 0.0,
            "flatten": "none",
            "trail": 0.0,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "require_alt_sma": False,
            "cash_when_no_alt": False,
        },
        {
            "name": "res100_trail10",
            "btc_frac": 0.0,
            "flatten": "none",
            "trail": 0.10,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "require_alt_sma": False,
            "cash_when_no_alt": False,
        },
        {
            "name": "res100_sma_trail10",
            "btc_frac": 0.0,
            "flatten": "all",
            "trail": 0.10,
            "excess_floor": 0.035,
            "lookback_days": 10,
            "require_alt_sma": True,
            "cash_when_no_alt": True,
        },
        {
            "name": "clip20_80_trail10",
            "btc_frac": 0.20,
            "flatten": "all",
            "trail": 0.10,
            "excess_floor": 0.04,
            "lookback_days": 10,
            "require_alt_sma": False,
            "cash_when_no_alt": False,
        },
        {
            "name": "btc50_flat_trail10",
            "btc_frac": 0.50,
            "flatten": "all",
            "trail": 0.10,
            "excess_floor": 0.0,
            "lookback_days": 20,
            "require_alt_sma": False,
            "cash_when_no_alt": False,
        },
    ]


def alphai_modes_for_window(window: str) -> list[str]:
    base = ["off", "proxy_gate", "proxy_intersect"]
    if window == "alphai_overlap":
        return base + ["real_gate", "real_intersect"]
    return base


def _pick_kwargs(entry: Mapping[str, Any]) -> dict[str, Any]:
    skip = {"name"}
    return {k: v for k, v in entry.items() if k not in skip}


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"curve", "trades", "trades_tail", "weeks"}}


def run_one(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    entry: Mapping[str, Any],
    book: Mapping[str, Any],
    alphai_mode: str,
    alt_allow: Mapping[str, set[str]] | None,
    model: FillModel = WET,
) -> dict[str, Any]:
    trail = float(book.get("trail") or 0.0)
    policy = (
        ExitPolicy(name=f"alt_trail_{int(round(trail * 100))}", alt_trail_pct=trail)
        if trail > 0
        else None
    )
    name = f"{book['name']}__{entry['name']}__ai_{alphai_mode}"
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        btc_frac=float(book["btc_frac"]),
        excess_floor=float(book["excess_floor"]),
        flatten=str(book["flatten"]),  # type: ignore[arg-type]
        lookback_days=int(book["lookback_days"]),
        skip_days=1,
        rebalance_days=7,
        require_alt_sma=bool(book.get("require_alt_sma")),
        cash_when_no_alt=bool(book.get("cash_when_no_alt")),
        model=model,
        policy=policy,
        strategy=name,
        alt_allow=alt_allow,
        alt_allow_mode=allow_mode_name(alphai_mode),
        pick_kwargs=_pick_kwargs(entry),
        keep_weeks=False,
    )
    slim = _strip(row)
    slim["entry"] = entry["name"]
    slim["book"] = book["name"]
    slim["alphai"] = alphai_mode
    slim["name"] = name
    return slim


def run_weekly_setup_grid(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    window: str = "custom",
    sessions: Sequence[Mapping[str, Any]] | None = None,
    model: FillModel = WET,
    entries: Sequence[Mapping[str, Any]] | None = None,
    books: Sequence[Mapping[str, Any]] | None = None,
    alphai_modes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Full factor grid. Ranked by Calmar then PnL."""
    ents = list(entries or entry_variants())
    bks = list(books or book_variants())
    modes = list(alphai_modes or alphai_modes_for_window(window))
    allow_cache: dict[str, Mapping[str, set[str]] | None] = {}
    ranked: list[dict[str, Any]] = []
    for mode in modes:
        if mode not in allow_cache:
            allow_cache[mode] = build_alt_allow(
                ohlc,
                start=start,
                end=end,
                mode=mode,
                sessions=sessions,
            )
        alt_allow = allow_cache[mode]
        for book in bks:
            for entry in ents:
                ranked.append(
                    run_one(
                        ohlc,
                        start=start,
                        end=end,
                        book_eur=book_eur,
                        entry=entry,
                        book=book,
                        alphai_mode=mode,
                        alt_allow=alt_allow,
                        model=model,
                    )
                )
    ranked.sort(
        key=lambda r: (-float(r.get("calmar") or 0.0), -float(r.get("pnl_eur") or 0.0))
    )
    best = ranked[0] if ranked else {}
    by_alphai: dict[str, list[dict[str, Any]]] = {}
    for row in ranked:
        by_alphai.setdefault(str(row["alphai"]), []).append(row)
    alphai_best = {
        mode: {
            "name": rows[0].get("name"),
            "pnl_eur": rows[0].get("pnl_eur"),
            "calmar": rows[0].get("calmar"),
            "max_dd_pct": rows[0].get("max_dd_pct"),
            "ann_pct": rows[0].get("ann_pct"),
            "entry": rows[0].get("entry"),
            "book": rows[0].get("book"),
        }
        for mode, rows in by_alphai.items()
        if rows
    }
    # Paired off vs AlphaI for the same entry×book.
    paired: list[dict[str, Any]] = []
    off_map = {
        (r["book"], r["entry"]): r for r in ranked if r.get("alphai") == "off"
    }
    for row in ranked:
        if row.get("alphai") == "off":
            continue
        base = off_map.get((row["book"], row["entry"]))
        if base is None:
            continue
        paired.append(
            {
                "name": row["name"],
                "book": row["book"],
                "entry": row["entry"],
                "alphai": row["alphai"],
                "pnl_eur": row["pnl_eur"],
                "calmar": row["calmar"],
                "off_pnl_eur": base["pnl_eur"],
                "off_calmar": base["calmar"],
                "delta_pnl": round(float(row["pnl_eur"]) - float(base["pnl_eur"]), 2),
                "delta_calmar": round(
                    float(row["calmar"] or 0.0) - float(base["calmar"] or 0.0), 3
                ),
            }
        )
    paired.sort(key=lambda r: (-float(r["delta_calmar"]), -float(r["delta_pnl"])))
    return {
        "window": window,
        "start": start,
        "end": end,
        "book_eur": book_eur,
        "model": model.name,
        "n_packs": len(ranked),
        "best": best.get("name"),
        "best_calmar": best.get("calmar"),
        "best_pnl_eur": best.get("pnl_eur"),
        "alphai_best": alphai_best,
        "top": ranked[:25],
        "paired_alphai_lift": paired[:25],
        "ranked": ranked,
    }
