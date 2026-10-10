"""Ambition search: maximize green weeks / structural PnL across daily packs.

AlphaI-conditioned rows are evaluated only on the real pick overlap.
Longer windows are wet residual/clip/BTC (no AlphaI claim).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from bot.research.alphai_pack_compare.alphai_map import (
    build_alt_allow,
    load_merged_sessions,
    picks_asof_hour,
)
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET, FillModel
from bot.research.clip_exit_lab.policies import ExitPolicy


def _trail(pct: float) -> ExitPolicy | None:
    if pct <= 0:
        return None
    return ExitPolicy(name=f"alt_trail_{int(round(pct * 100))}", alt_trail_pct=float(pct))


def week_stats(weeks: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    rows = list(weeks or [])
    pnls = [float(w.get("pnl_eur") or 0.0) for w in rows]
    n = len(pnls)
    if n <= 0:
        return {
            "n_weeks": 0,
            "pct_weeks_pos": 0.0,
            "n_weeks_pos": 0,
            "n_weeks_neg": 0,
            "worst_week": 0.0,
            "best_week": 0.0,
            "median_week": 0.0,
            "all_weeks_green": False,
        }
    pos = sum(1 for p in pnls if p > 0)
    neg = sum(1 for p in pnls if p < 0)
    return {
        "n_weeks": n,
        "pct_weeks_pos": round(pos / n, 4),
        "n_weeks_pos": pos,
        "n_weeks_neg": neg,
        "worst_week": round(min(pnls), 2),
        "best_week": round(max(pnls), 2),
        "median_week": round(statistics.median(pnls), 2),
        "all_weeks_green": neg == 0 and pos == n,
    }


def ambition_specs() -> list[dict[str, Any]]:
    """Broad coin-agnostic daily-owner grid (residual / clip / BTC).

    Sized to finish in one research pass (~1–2k packs × 3 windows).
    """
    specs: list[dict[str, Any]] = []

    def add(**kw: Any) -> None:
        specs.append({"family": kw.get("family") or "daily_owner", "n_alts": 1, **kw})

    # BTC baselines.
    add(
        name="btc_hold",
        btc_frac=1.0,
        lookback_days=10,
        skip_days=1,
        trail_pct=0.0,
        excess_floor=0.0,
        flatten="none",
        require_alt_sma=False,
        cash_when_no_alt=False,
        rebalance_days=7,
        sma_n=50,
    )
    add(
        name="btc_sma50",
        btc_frac=1.0,
        lookback_days=10,
        skip_days=1,
        trail_pct=0.0,
        excess_floor=0.0,
        flatten="all",
        require_alt_sma=False,
        cash_when_no_alt=False,
        rebalance_days=7,
        sma_n=50,
    )

    # Core mix grid.
    for btc_frac in (0.0, 0.20, 0.50, 0.75):
        for lb in (8, 10, 12, 16, 20):
            for skip in (0, 1):
                for trail in (0.08, 0.10, 0.12, 0.15):
                    for floor in (0.0, 0.035, 0.04, 0.08):
                        flats = ("all", "none") if btc_frac <= 0.20 else ("all",)
                        # residual_full-style (rq+cash) + raw residual; mixes: raw only.
                        styles = (
                            ((False, False), (True, True))
                            if btc_frac == 0.0
                            else ((False, False),)
                        )
                        for flatten in flats:
                            for req_sma, cash_empty in styles:
                                name = (
                                    f"f{int(btc_frac * 100)}_lb{lb}_sk{skip}_"
                                    f"tr{int(trail * 100)}_fl{floor:.3f}_"
                                    f"{flatten}_rq{int(req_sma)}_c{int(cash_empty)}"
                                )
                                add(
                                    name=name,
                                    btc_frac=btc_frac,
                                    lookback_days=lb,
                                    skip_days=skip,
                                    trail_pct=trail,
                                    excess_floor=floor,
                                    flatten=flatten,
                                    require_alt_sma=req_sma,
                                    cash_when_no_alt=cash_empty,
                                    rebalance_days=7,
                                    sma_n=50,
                                )

    # Focus knobs around live residual / clip / 50-50.
    for btc_frac, floor, lb, trail, req, cash in (
        (0.0, 0.035, 10, 0.10, True, True),
        (0.20, 0.04, 10, 0.10, False, False),
        (0.50, 0.0, 20, 0.10, False, False),
        (0.0, 0.0, 20, 0.10, False, False),
        (0.0, 0.035, 10, 0.12, True, True),
        (0.20, 0.04, 12, 0.10, False, False),
    ):
        for reb in (1, 3, 7, 14):
            for sma_n in (20, 50, 100):
                name = (
                    f"focus_f{int(btc_frac * 100)}_lb{lb}_tr{int(trail * 100)}_"
                    f"fl{floor:.3f}_reb{reb}_sma{sma_n}_rq{int(req)}_c{int(cash)}"
                )
                add(
                    family="daily_owner_focus",
                    name=name,
                    btc_frac=btc_frac,
                    lookback_days=lb,
                    skip_days=1,
                    trail_pct=trail,
                    excess_floor=floor,
                    flatten="all",
                    require_alt_sma=req,
                    cash_when_no_alt=cash,
                    rebalance_days=reb,
                    sma_n=sma_n,
                )

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for s in specs:
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        out.append(s)
    return out


def alphai_overlay_specs() -> list[dict[str, Any]]:
    """Gate overlays on the strongest structural candidates (AlphaI window only)."""
    bases = [
        {
            "pack": "residual_full",
            "btc_frac": 0.0,
            "lookback_days": 10,
            "skip_days": 1,
            "trail_pct": 0.10,
            "excess_floor": 0.035,
            "flatten": "all",
            "require_alt_sma": True,
            "cash_when_no_alt": True,
            "rebalance_days": 7,
            "sma_n": 50,
        },
        {
            "pack": "clip_20_80",
            "btc_frac": 0.20,
            "lookback_days": 10,
            "skip_days": 1,
            "trail_pct": 0.10,
            "excess_floor": 0.04,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "rebalance_days": 7,
            "sma_n": 50,
        },
        {
            "pack": "btc50",
            "btc_frac": 0.50,
            "lookback_days": 20,
            "skip_days": 1,
            "trail_pct": 0.10,
            "excess_floor": 0.0,
            "flatten": "all",
            "require_alt_sma": False,
            "cash_when_no_alt": False,
            "rebalance_days": 7,
            "sma_n": 50,
        },
    ]
    overlays = [
        {"ai": "off", "mode": None, "hour": 7, "override": None},
        {"ai": "gate07", "mode": "gate", "hour": 7, "override": None},
        {"ai": "intersect07", "mode": "intersect", "hour": 7, "override": None},
        {"ai": "intersect16", "mode": "intersect", "hour": 16, "override": None},
        {"ai": "prefer07", "mode": "prefer", "hour": 7, "override": None},
        {"ai": "overlap07", "mode": "overlap_or_rs", "hour": 7, "override": None},
        {"ai": "override15", "mode": "override_gate", "hour": 7, "override": 0.15},
        {"ai": "override25", "mode": "override_gate", "hour": 7, "override": 0.25},
    ]
    out: list[dict[str, Any]] = []
    for b in bases:
        for o in overlays:
            spec = {
                **b,
                "family": "alphai_overlay",
                "name": f"{b['pack']}__{o['ai']}",
                "ai_mode": o["mode"],
                "ai_hour": o["hour"],
                "ai_override": o["override"],
                "n_alts": 1,
            }
            out.append(spec)
    return out


def _annotate(row: dict[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    ws = week_stats(row.get("weeks"))
    pnl = float(row.get("pnl_eur") or 0.0)
    dd = max(float(row.get("max_dd_pct") or 0.0), 1e-6)
    out = {k: v for k, v in row.items() if k not in {"curve", "trades", "trades_tail"}}
    out.update(ws)
    out.update(
        {
            "name": spec.get("name") or row.get("strategy"),
            "family": spec.get("family"),
            "btc_frac": spec.get("btc_frac"),
            "lookback_days": spec.get("lookback_days"),
            "skip_days": spec.get("skip_days"),
            "trail_pct": spec.get("trail_pct"),
            "excess_floor": spec.get("excess_floor"),
            "flatten": spec.get("flatten"),
            "require_alt_sma": spec.get("require_alt_sma"),
            "cash_when_no_alt": spec.get("cash_when_no_alt"),
            "rebalance_days": spec.get("rebalance_days"),
            "sma_n": spec.get("sma_n"),
            "ai_mode": spec.get("ai_mode"),
            "ai_hour": spec.get("ai_hour"),
            "ai_override": spec.get("ai_override"),
            "pnl_per_dd": round(pnl / dd, 2),
            "short_score": round(pnl - 20_000.0 * dd, 2),
            # Ambition score: green weeks first, then less-bad worst week, then PnL.
            "ambition_score": round(
                1000.0 * float(ws["pct_weeks_pos"])
                + 0.001 * float(ws["worst_week"])
                + 0.00001 * pnl
                - 0.5 * float(row.get("max_dd_pct") or 0.0),
                6,
            ),
        }
    )
    # Drop bulky weeks from ranked payload later; keep for filtering now.
    return out


def eval_spec(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: Mapping[str, Any],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel,
    alt_allow: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        btc_frac=float(spec["btc_frac"]),
        excess_floor=float(spec.get("excess_floor") or 0.0),
        flatten=str(spec.get("flatten") or "all"),  # type: ignore[arg-type]
        sma_n=int(spec.get("sma_n") or 50),
        rebalance_days=int(spec.get("rebalance_days") or 7),
        lookback_days=int(spec.get("lookback_days") or 20),
        skip_days=int(spec["skip_days"]) if spec.get("skip_days") is not None else 1,
        n_alts=int(spec.get("n_alts") or 1),
        require_alt_sma=bool(spec.get("require_alt_sma")),
        cash_when_no_alt=bool(spec.get("cash_when_no_alt")),
        model=model,
        policy=_trail(float(spec.get("trail_pct") or 0.0)),
        keep_weeks=True,
        strategy=str(spec["name"]),
        alt_allow=alt_allow,
        alt_allow_mode=str(spec.get("ai_mode") or "gate"),
        alt_allow_excess_override=spec.get("ai_override"),
    )
    return _annotate(row, spec)


_WORKER: dict[str, Any] = {}


def _init_worker(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    start: str,
    end: str,
    book_eur: float,
    model: FillModel,
) -> None:
    _WORKER.update(
        ohlc=ohlc, start=start, end=end, book_eur=book_eur, model=model, alt_allow=None
    )


def _job(spec: dict[str, Any]) -> dict[str, Any]:
    return eval_spec(
        _WORKER["ohlc"],
        spec,
        start=_WORKER["start"],
        end=_WORKER["end"],
        book_eur=_WORKER["book_eur"],
        model=_WORKER["model"],
        alt_allow=_WORKER.get("alt_allow"),
    )


def run_grid(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    specs: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
    book_eur: float,
    workers: int = 4,
    alt_allow: Mapping[str, set[str]] | None = None,
    model: FillModel = WET,
) -> list[dict[str, Any]]:
    payload = [dict(s) for s in specs]
    if workers <= 1 or len(payload) <= 2:
        _init_worker(ohlc, start, end, book_eur, model)
        _WORKER["alt_allow"] = alt_allow
        return [_job(s) for s in payload]
    # Process pool cannot pickle alt_allow easily with initializer extras —
    # for AlphaI overlays use serial; for large no-AI grids use workers.
    if alt_allow is not None:
        _init_worker(ohlc, start, end, book_eur, model)
        _WORKER["alt_allow"] = alt_allow
        return [_job(s) for s in payload]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(ohlc, start, end, book_eur, model),
    ) as pool:
        for row in pool.map(_job, payload, chunksize=8):
            rows.append(row)
    return rows


def rank_ambition(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = [dict(r) for r in rows]
    ranked.sort(
        key=lambda r: (
            float(r.get("pct_weeks_pos") or 0.0),
            float(r.get("worst_week") or 0.0),
            float(r.get("pnl_eur") or 0.0),
            -float(r.get("max_dd_pct") or 0.0),
        ),
        reverse=True,
    )
    return ranked


def pick_ambition_champions(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ranked = rank_ambition(rows)
    profitable = [r for r in ranked if float(r.get("pnl_eur") or 0.0) > 0]
    greenish = [r for r in profitable if float(r.get("pct_weeks_pos") or 0.0) >= 0.70]
    low_dd = [r for r in profitable if float(r.get("max_dd_pct") or 1.0) <= 0.20]
    all_green = [r for r in profitable if r.get("all_weeks_green")]
    best_green_rate = profitable[0] if profitable else (ranked[0] if ranked else None)
    best_pnl = max(ranked, key=lambda r: float(r.get("pnl_eur") or 0.0)) if ranked else None
    best_dd = (
        min(profitable, key=lambda r: float(r.get("max_dd_pct") or 1.0)) if profitable else None
    )
    best_balanced = None
    if greenish:
        best_balanced = max(
            greenish,
            key=lambda r: (
                float(r.get("pct_weeks_pos") or 0.0),
                float(r.get("pnl_eur") or 0.0),
                -float(r.get("max_dd_pct") or 0.0),
            ),
        )
    elif low_dd:
        best_balanced = max(
            low_dd,
            key=lambda r: (
                float(r.get("pct_weeks_pos") or 0.0),
                float(r.get("pnl_eur") or 0.0),
            ),
        )
    elif profitable:
        best_balanced = best_green_rate

    def slim(r: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not r:
            return None
        return {k: v for k, v in r.items() if k != "weeks"}

    return {
        "all_weeks_green_count": len(all_green),
        "all_weeks_green": slim(all_green[0]) if all_green else None,
        "best_green_rate": slim(best_green_rate),
        "best_pnl": slim(best_pnl),
        "best_low_dd_profit": slim(best_dd),
        "best_balanced": slim(best_balanced),
        "top_green": [slim(r) for r in ranked[:15]],
    }


def run_ambition_search(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    dates: Sequence[str],
    book_eur: float = 20_000.0,
    workers: int = 4,
    alphai_path: str | None = None,
    windows: Sequence[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    last = dates[-1]
    wins = list(windows or [])
    if not wins:
        # Default windows.
        from datetime import UTC, datetime, timedelta

        last_dt = datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=UTC)
        wins = [
            ("fair_2y", "2024-03-16"),
            ("last_12w", (last_dt - timedelta(days=84)).strftime("%Y-%m-%d")),
            ("last_90d", (last_dt - timedelta(days=90)).strftime("%Y-%m-%d")),
        ]

    specs = ambition_specs()
    out_windows: dict[str, Any] = {}
    for wname, start in wins:
        if start > last:
            continue
        print(f"  grid {wname} {start}→{last} n={len(specs)}", flush=True)
        rows = run_grid(
            ohlc,
            specs,
            start=start,
            end=last,
            book_eur=book_eur,
            workers=workers,
        )
        champs = pick_ambition_champions(rows)
        out_windows[wname] = {
            "start": start,
            "end": last,
            "n_specs": len(specs),
            "n_rows": len(rows),
            **champs,
            # Keep only slim ranked top + note full count.
            "ranked_top": champs["top_green"],
        }

    # AlphaI overlay window.
    alphai_block = None
    if alphai_path:
        sessions = load_merged_sessions(alphai_path)
        daily7 = picks_asof_hour(sessions, hour_utc=7, universe=tuple(ohlc.keys()))
        if daily7:
            a_start, a_end = min(daily7), min(max(daily7), last)
            print(
                f"  alphai overlays {a_start}→{a_end}",
                flush=True,
            )
            overlay_rows: list[dict[str, Any]] = []
            for spec in alphai_overlay_specs():
                mode = spec.get("ai_mode")
                if mode is None:
                    allow = None
                else:
                    daily = picks_asof_hour(
                        sessions,
                        hour_utc=int(spec.get("ai_hour") or 7),
                        universe=tuple(ohlc.keys()),
                    )
                    allow = build_alt_allow(
                        start=a_start, end=a_end, ohlc_dates=dates, daily_picks=daily
                    )
                overlay_rows.append(
                    eval_spec(
                        ohlc,
                        spec,
                        start=a_start,
                        end=a_end,
                        book_eur=book_eur,
                        model=WET,
                        alt_allow=allow,
                    )
                )
            alphai_block = {
                "start": a_start,
                "end": a_end,
                "n_rows": len(overlay_rows),
                **pick_ambition_champions(overlay_rows),
                "ranked_top": [
                    {k: v for k, v in r.items() if k != "weeks"}
                    for r in rank_ambition(overlay_rows)[:20]
                ],
            }

    return {
        "book_eur": book_eur,
        "n_specs_daily": len(specs),
        "windows": out_windows,
        "alphai_overlays": alphai_block,
        "note": (
            "Ambition objective = maximize % green weeks, then worst-week, then PnL, "
            "then lower DD. 'All weeks green' is reported when achieved; on long "
            "windows it is usually impossible. AlphaI overlays use real picks only."
        ),
    }
