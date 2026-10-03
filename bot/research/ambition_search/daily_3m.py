"""Daily PnL for the recommended capital stack over the last ~90 calendar days.

Usage::

    .venv/bin/python -m bot.research.ambition_search.daily_3m
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bot.research.alphai_pack_compare.alphai_map import (
    build_alt_allow,
    load_merged_sessions,
    picks_asof_hour,
)
from bot.research.alphai_pack_compare.candles import load_cached, ohlc_dates
from bot.research.ambition_search.engine import _trail
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET

BOOK = 20_000.0

# Recommended packs from CROSS_ENGINE_BEST.md
PACKS: dict[str, dict[str, Any]] = {
    "btc50_primary": {
        "btc_frac": 0.50,
        "lookback_days": 20,
        "skip_days": 1,
        "trail_pct": 0.10,
        "excess_floor": 0.035,
        "flatten": "all",
        "require_alt_sma": False,
        "cash_when_no_alt": False,
        "rebalance_days": 14,
        "sma_n": 20,
        "n_alts": 1,
    },
    "btc50_prefer07": {
        "btc_frac": 0.50,
        "lookback_days": 20,
        "skip_days": 1,
        "trail_pct": 0.10,
        "excess_floor": 0.035,
        "flatten": "all",
        "require_alt_sma": False,
        "cash_when_no_alt": False,
        "rebalance_days": 14,
        "sma_n": 20,
        "n_alts": 1,
        "ai_mode": "prefer",
        "ai_hour": 7,
    },
    "residual_full_satellite": {
        "btc_frac": 0.0,
        "lookback_days": 10,
        "skip_days": 0,
        "trail_pct": 0.10,
        "excess_floor": 0.035,
        "flatten": "all",
        "require_alt_sma": True,
        "cash_when_no_alt": True,
        "rebalance_days": 7,
        "sma_n": 50,
        "n_alts": 1,
    },
    "btc20_calmar": {
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
        "n_alts": 1,
    },
}

# Indicative capital weights for the "best stack" owner book (Bitvavo daily).
# Ignition/WR are separate sleeves; see note in markdown.
STACK_WEIGHTS = {
    "btc50_primary": 0.70,
    "residual_full_satellite": 0.15,
    # remaining 0.15 reserved for ignition+WR outside this daily residual sim
}


def _curve_to_daily(curve: list[list[Any]], book: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prev = float(book)
    for i, (date, eq) in enumerate(curve):
        eq_f = float(eq)
        day_pnl = eq_f - prev
        rows.append(
            {
                "date": str(date),
                "equity": round(eq_f, 2),
                "day_pnl": round(day_pnl, 2),
                "day_ret_pct": round(day_pnl / prev * 100.0, 3) if prev else 0.0,
                "cum_pnl": round(eq_f - book, 2),
            }
        )
        prev = eq_f
        _ = i
    return rows


def _summarize(daily: list[dict[str, Any]], book: float) -> dict[str, Any]:
    if not daily:
        return {}
    pnls = [float(r["day_pnl"]) for r in daily]
    pos = sum(1 for p in pnls if p > 0)
    neg = sum(1 for p in pnls if p < 0)
    flat = sum(1 for p in pnls if p == 0)
    # Week aggregates (ISO weeks)
    weeks: dict[str, float] = {}
    for r in daily:
        d = datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=UTC)
        key = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
        weeks[key] = weeks.get(key, 0.0) + float(r["day_pnl"])
    w_pos = sum(1 for v in weeks.values() if v > 0)
    peak = book
    max_dd = 0.0
    for r in daily:
        eq = float(r["equity"])
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return {
        "start": daily[0]["date"],
        "end": daily[-1]["date"],
        "n_days": len(daily),
        "total_pnl": round(daily[-1]["cum_pnl"], 2),
        "total_ret_pct": round(daily[-1]["cum_pnl"] / book * 100.0, 2),
        "avg_day_pnl": round(sum(pnls) / len(pnls), 2),
        "median_day_pnl": round(sorted(pnls)[len(pnls) // 2], 2),
        "best_day": round(max(pnls), 2),
        "worst_day": round(min(pnls), 2),
        "pct_days_green": round(pos / len(pnls), 4),
        "n_days_green": pos,
        "n_days_red": neg,
        "n_days_flat": flat,
        "n_weeks": len(weeks),
        "pct_weeks_green": round(w_pos / len(weeks), 4) if weeks else 0.0,
        "n_weeks_green": w_pos,
        "worst_week": round(min(weeks.values()), 2) if weeks else 0.0,
        "best_week": round(max(weeks.values()), 2) if weeks else 0.0,
        "max_dd_pct": round(max_dd * 100.0, 2),
        "weeks": {k: round(v, 2) for k, v in sorted(weeks.items())},
    }


def _run_pack(
    ohlc: Any,
    name: str,
    spec: dict[str, Any],
    *,
    start: str,
    end: str,
    book: float,
    alt_allow: dict[str, set[str]] | None,
) -> dict[str, Any]:
    ai_mode = spec.get("ai_mode")
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book,
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
        model=WET,
        policy=_trail(float(spec.get("trail_pct") or 0.0)),
        keep_curve=True,
        keep_weeks=True,
        strategy=name,
        alt_allow=alt_allow if ai_mode else None,
        alt_allow_mode=str(ai_mode or "gate"),
    )
    curve = list(row.get("curve") or [])
    daily = _curve_to_daily(curve, book)
    summary = _summarize(daily, book)
    return {
        "name": name,
        "spec": {k: v for k, v in spec.items()},
        "engine_pnl": row.get("pnl_eur"),
        "engine_max_dd_pct": row.get("max_dd_pct"),
        "summary": summary,
        "daily": daily,
    }


def _weighted_stack(
    pack_results: dict[str, dict[str, Any]],
    weights: dict[str, float],
    *,
    book: float,
) -> dict[str, Any]:
    # Align by date; equity = sum(weight_i * equity_i / book * book) = sum(w_i * eq_i)
    # Each pack ran at full `book`; scale each curve by weight so total seed = book * sum(w).
    wsum = sum(weights.values())
    seed = book * wsum
    dates: list[str] | None = None
    for name in weights:
        d = [r["date"] for r in pack_results[name]["daily"]]
        if dates is None:
            dates = d
        elif d != dates:
            # intersect
            common = sorted(set(dates) & set(d))
            dates = common
    assert dates is not None
    by_date = {
        name: {r["date"]: r for r in pack_results[name]["daily"]} for name in weights
    }
    daily: list[dict[str, Any]] = []
    prev = seed
    for date in dates:
        eq = 0.0
        for name, w in weights.items():
            eq += w * float(by_date[name][date]["equity"])
        day_pnl = eq - prev
        daily.append(
            {
                "date": date,
                "equity": round(eq, 2),
                "day_pnl": round(day_pnl, 2),
                "day_ret_pct": round(day_pnl / prev * 100.0, 3) if prev else 0.0,
                "cum_pnl": round(eq - seed, 2),
            }
        )
        prev = eq
    summary = _summarize(daily, seed)
    summary["seed_eur"] = round(seed, 2)
    summary["weights"] = weights
    return {
        "name": "stack_70btc50_15residual",
        "summary": summary,
        "daily": daily,
    }


def _md(payload: dict[str, Any]) -> str:
    lines = [
        "# Daily PnL — recommended stack, last ~3 months",
        "",
        f"asof `{payload['asof']}`  window `{payload['start']}` → `{payload['end']}`  "
        f"fill wet Bitvavo next-open  book €{payload['book_eur']:,.0f} per pack",
        "",
        "Primary recommendation = **btc50** (trail10 / lb20 / reb14 / sma20). "
        "Stack = 70% btc50 + 15% residual_full (seed €16k on €20k reference; "
        "remaining ~15% is ignition+WR outside this residual tape).",
        "",
        "## Summary",
        "",
        "| Pack | Total PnL | Ret% | Avg day | Green days | Green weeks | Worst day | Worst week | maxDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in (
        "btc50_primary",
        "btc50_prefer07",
        "residual_full_satellite",
        "btc20_calmar",
        "stack_70btc50_15residual",
    ):
        block = payload["packs"].get(name) or {}
        s = block.get("summary") or {}
        if not s:
            continue
        lines.append(
            f"| `{name}` | {s.get('total_pnl'):+.0f} | {s.get('total_ret_pct'):+.1f}% | "
            f"{s.get('avg_day_pnl'):+.0f} | "
            f"{100 * float(s.get('pct_days_green') or 0):.0f}% "
            f"({s.get('n_days_green')}/{s.get('n_days')}) | "
            f"{100 * float(s.get('pct_weeks_green') or 0):.0f}% "
            f"({s.get('n_weeks_green')}/{s.get('n_weeks')}) | "
            f"{s.get('worst_day'):+.0f} | {s.get('worst_week'):+.0f} | "
            f"{s.get('max_dd_pct'):.1f}% |"
        )
    lines += [
        "",
        "### Notes",
        "",
        "- `btc50_prefer07` only differs on days with real AlphaI pick history "
        "(~Sep 19–Oct 3); earlier days behave like ungated residual.",
        "- Ignition (OKX top_day) and 15m WR desk are **not** in these daily rows — "
        "different venues/tapes; residual owns most capital.",
        "- Day PnL = mark-to-market equity change (wet next-open fills), not only realized.",
        "",
    ]

    # Primary daily table
    primary = payload["packs"]["btc50_primary"]["daily"]
    lines += [
        "## Daily table — `btc50_primary` (recommended owner)",
        "",
        "| Date | Day PnL | Cum PnL | Equity | Ret% |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in primary:
        lines.append(
            f"| {r['date']} | {r['day_pnl']:+.2f} | {r['cum_pnl']:+.2f} | "
            f"{r['equity']:,.2f} | {r['day_ret_pct']:+.2f}% |"
        )

    stack = payload["packs"].get("stack_70btc50_15residual")
    if stack:
        lines += [
            "",
            "## Daily table — weighted stack (70% btc50 + 15% residual_full)",
            "",
            f"Seed €{stack['summary'].get('seed_eur'):,.0f}",
            "",
            "| Date | Day PnL | Cum PnL | Equity | Ret% |",
            "|---|---:|---:|---:|---:|",
        ]
        for r in stack["daily"]:
            lines.append(
                f"| {r['date']} | {r['day_pnl']:+.2f} | {r['cum_pnl']:+.2f} | "
                f"{r['equity']:,.2f} | {r['day_ret_pct']:+.2f}% |"
            )

    # Monthly + weekly for primary
    primary_daily = payload["packs"]["btc50_primary"]["daily"]
    monthly: dict[str, float] = {}
    for r in primary_daily:
        monthly[r["date"][:7]] = monthly.get(r["date"][:7], 0.0) + float(r["day_pnl"])
    if monthly:
        lines += [
            "",
            "## Monthly PnL — `btc50_primary`",
            "",
            "| Month | PnL |",
            "|---|---:|",
        ]
        for k, v in sorted(monthly.items()):
            lines.append(f"| {k} | {v:+.2f} |")

    weeks = (payload["packs"]["btc50_primary"].get("summary") or {}).get("weeks") or {}
    if weeks:
        lines += [
            "",
            "## Weekly PnL — `btc50_primary`",
            "",
            "| Week | PnL |",
            "|---|---:|",
        ]
        for k, v in weeks.items():
            lines.append(f"| {k} | {v:+.2f} |")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    cache = Path("data/residual_wet_candles")
    ohlc = load_cached(cache)
    dates = ohlc_dates(ohlc)
    end = dates[-1]
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    start = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    # snap start to first available bar on/after
    start = next((d for d in dates if d >= start), start)

    alphai_path = Path("data/research/alphai_sessions_merged.json")
    alt_allow_07: dict[str, set[str]] | None = None
    if alphai_path.exists():
        sessions = load_merged_sessions(alphai_path)
        picks = picks_asof_hour(
            sessions, hour_utc=7, universe=tuple(ohlc.keys())
        )
        alt_allow_07 = build_alt_allow(
            start=start,
            end=end,
            ohlc_dates=dates,
            daily_picks=picks,
        )

    packs_out: dict[str, Any] = {}
    for name, spec in PACKS.items():
        allow = alt_allow_07 if spec.get("ai_mode") else None
        print(f"running {name}…", flush=True)
        packs_out[name] = _run_pack(
            ohlc, name, spec, start=start, end=end, book=BOOK, alt_allow=allow
        )
        s = packs_out[name]["summary"]
        print(
            f"  pnl={s.get('total_pnl'):+.0f} green_days="
            f"{100 * float(s.get('pct_days_green') or 0):.0f}% "
            f"green_weeks={100 * float(s.get('pct_weeks_green') or 0):.0f}%",
            flush=True,
        )

    packs_out["stack_70btc50_15residual"] = _weighted_stack(
        packs_out, STACK_WEIGHTS, book=BOOK
    )
    ss = packs_out["stack_70btc50_15residual"]["summary"]
    print(
        f"stack pnl={ss.get('total_pnl'):+.0f} "
        f"green_days={100 * float(ss.get('pct_days_green') or 0):.0f}% "
        f"green_weeks={100 * float(ss.get('pct_weeks_green') or 0):.0f}%",
        flush=True,
    )

    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "start": start,
        "end": end,
        "book_eur": BOOK,
        "fill": "wet_next_open_taker",
        "packs": packs_out,
    }
    pkg = Path(__file__).resolve().parent
    (pkg / "DAILY_3M.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (pkg / "DAILY_3M.md").write_text(_md(payload), encoding="utf-8")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/ambition_daily_3m.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    Path("artifacts/ambition_daily_3m.md").write_text(_md(payload), encoding="utf-8")
    print(f"wrote {pkg / 'DAILY_3M.md'}", flush=True)


if __name__ == "__main__":
    main()
