"""Grid residual_full knobs on full tape vs pre-NEAR-outlier."""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from bot.live.momentum_desk import DEFAULT_UNIVERSE
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_exit_lab.engine import WET
from bot.research.clip_exit_lab.policies import ExitPolicy

BOOK = 20_000.0
START = "2024-03-16"
CACHE = Path("data/residual_wet_candles")
_OHLC: dict | None = None


def _load() -> dict:
    global _OHLC
    if _OHLC is None:
        ohlc = {}
        for b in ("BTC", *DEFAULT_UNIVERSE):
            p = CACHE / f"{b}.json"
            if p.exists():
                ohlc[b] = json.loads(p.read_text())
        _OHLC = ohlc
    return _OHLC


def eval_one(spec: tuple) -> dict:
    start, end, trail, skip, lb, floor = spec
    ohlc = _load()
    row = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=BOOK,
        btc_frac=0.0,
        excess_floor=floor,
        flatten="all",
        sma_n=50,
        rebalance_days=7,
        lookback_days=lb,
        skip_days=skip,
        require_alt_sma=True,
        cash_when_no_alt=True,
        model=WET,
        policy=ExitPolicy(name=f"tr{int(round(trail * 100))}", alt_trail_pct=trail),
        keep_weeks=True,
        strategy=f"sk{skip}_lb{lb}_tr{int(round(trail * 100))}_fl{floor:.3f}",
        rebalance_weekday=None,
    )
    weeks = row.get("weeks") or []
    green = sum(1 for w in weeks if float(w.get("pnl_eur") or 0) > 0)
    worst = min((float(w.get("pnl_eur") or 0) for w in weeks), default=0.0)
    return {
        "name": row["strategy"],
        "start": start,
        "end": end,
        "trail_pct": trail,
        "skip_days": skip,
        "lookback_days": lb,
        "excess_floor": floor,
        "pnl_eur": row["pnl_eur"],
        "max_dd_pct": row["max_dd_pct"],
        "ann_pct": row.get("ann_pct"),
        "calmar": row.get("calmar"),
        "n_trades": row["n_trades"],
        "n_weeks": len(weeks),
        "pct_weeks_green": green / (len(weeks) or 1),
        "worst_week": worst,
        "end_hold": row.get("end_hold"),
    }


def _init() -> None:
    _load()


def main() -> None:
    ohlc = _load()
    full_end = datetime.fromtimestamp(int(ohlc["BTC"][-1][0]) / 1000, UTC).strftime(
        "%Y-%m-%d"
    )
    windows = {
        "full": (START, full_end),
        "pre_near": (START, "2026-09-15"),
        "pre_sep": (START, "2026-08-31"),
        "pre_may_near": (START, "2026-05-10"),
    }
    trails = (0.05, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20)
    skips = (0, 1)
    lbs = (8, 10, 12, 20)
    floors = (0.035, 0.04)
    jobs: list[tuple[str, tuple]] = []
    for wname, (s, e) in windows.items():
        for trail in trails:
            for skip in skips:
                for lb in lbs:
                    for floor in floors:
                        jobs.append((wname, (s, e, trail, skip, lb, floor)))
    print(f"jobs={len(jobs)}", flush=True)
    out: dict[str, list] = {w: [] for w in windows}
    with ProcessPoolExecutor(max_workers=6, initializer=_init) as ex:
        specs = [j[1] for j in jobs]
        labels = [j[0] for j in jobs]
        for lab, row in zip(labels, ex.map(eval_one, specs, chunksize=4), strict=True):
            out[lab].append(row)
    payload = {
        "asof": datetime.now(UTC).isoformat(),
        "book_eur": BOOK,
        "windows": {k: {"start": v[0], "end": v[1]} for k, v in windows.items()},
        "note": (
            "residual_full family: btc=0, sma50 flatten, require_alt_sma, "
            "cash_when_no_alt, reb7 any-day, compound. "
            "pre_near = last bar before 2026-09-16 NEAR buy."
        ),
        "results": out,
    }
    pkg = Path(__file__).resolve().parent
    slim_md: list[str] = [
        "# residual_full — winst zonder NEAR-uitschieter",
        "",
        f"asof `{payload['asof']}`  book €{BOOK:,.0f}  wet compound  any-day reb7",
        "",
        "Uitschieter = NEAR 2026-09-16→29 (koop ~€209k / verkoop ~€432k; "
        "W38–W39 ≈ +€275k). `pre_near` stopt 2026-09-15.",
        "",
    ]
    for wname, rows in out.items():
        rows_s = sorted(rows, key=lambda r: -r["pnl_eur"])
        liveish = [
            r
            for r in rows_s
            if r["trail_pct"] == 0.10
            and r["skip_days"] == 1
            and r["lookback_days"] == 10
            and abs(r["excess_floor"] - 0.035) < 1e-9
        ]
        s, e = windows[wname]
        slim_md += [
            f"## `{wname}`  `{s}` → `{e}`",
            "",
            "| Rank | Pack | PnL | maxDD | Calmar | greenW | worst week |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
        for i, r in enumerate(rows_s[:8], 1):
            slim_md.append(
                f"| {i} | `{r['name']}` | {r['pnl_eur']:+,.0f} | "
                f"{100 * r['max_dd_pct']:.1f}% | {float(r.get('calmar') or 0):.2f} | "
                f"{100 * r['pct_weeks_green']:.0f}% | {r['worst_week']:+,.0f} |"
            )
        if liveish:
            r = liveish[0]
            rank = next(i for i, x in enumerate(rows_s) if x["name"] == r["name"]) + 1
            slim_md.append(
                f"| live | `{r['name']}` (#{rank}/{len(rows_s)}) | {r['pnl_eur']:+,.0f} | "
                f"{100 * r['max_dd_pct']:.1f}% | {float(r.get('calmar') or 0):.2f} | "
                f"{100 * r['pct_weeks_green']:.0f}% | {r['worst_week']:+,.0f} |"
            )
        slim_md += [
            "",
            "Trail-sweep live knobs (`sk1 lb10 fl3.5%`):",
            "",
            "| Trail | PnL | maxDD |",
            "|---:|---:|---:|",
        ]
        subset = [
            r
            for r in rows_s
            if r["skip_days"] == 1
            and r["lookback_days"] == 10
            and abs(r["excess_floor"] - 0.035) < 1e-9
        ]
        subset.sort(key=lambda r: r["trail_pct"])
        for r in subset:
            slim_md.append(
                f"| {int(round(r['trail_pct'] * 100))}% | {r['pnl_eur']:+,.0f} | "
                f"{100 * r['max_dd_pct']:.1f}% |"
            )
        slim_md.append("")
        print(f"== {wname} winner {rows_s[0]['name']} {rows_s[0]['pnl_eur']:+.0f}", flush=True)

    md = "\n".join(slim_md)
    (pkg / "TRAIL_EX_OUTLIER.json").write_text(json.dumps(payload, indent=2))
    (pkg / "TRAIL_EX_OUTLIER.md").write_text(md)
    Path("artifacts/residual_trail_ex_outlier.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
