"""Exhaustive €20k directional owner tournament on the wet 1d tape.

Families that share this tape: residual mix (lookback/skip/frac/trail/SMA/
rebalance/top-N/dual-SMA), live clip, Donchian sleeves, BTC hold.

Not on this tape (and therefore not ranked here): 15m WR-core, AlphaI,
HFT/arb/funding, paper shorts. Those cannot be Calmar-compared on daily
Bitvavo fills.

Research only. Does not arm live.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from bot.live.momentum_donchian import loop_sleeve_configs
from bot.research.btc_residual_mix.engine import run_btc_residual
from bot.research.clip_donch_mix.engine import run_donchian_sleeve
from bot.research.clip_exit_lab.engine import WET, FillModel, run_clip_exits
from bot.research.clip_exit_lab.policies import ExitPolicy

LOOKBACKS = (6, 8, 10, 12, 14, 16, 18, 20, 25, 30, 40)
SKIPS = (0, 1)
FRACS = (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75)
TRAILS = (0.0, 0.08, 0.10, 0.12, 0.15)


def _trail_policy(pct: float) -> ExitPolicy | None:
    if pct <= 0:
        return None
    tag = int(round(pct * 100))
    return ExitPolicy(name=f"alt_trail_{tag}", alt_trail_pct=float(pct))


def coarse_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for lb in LOOKBACKS:
        for skip in SKIPS:
            for frac in FRACS:
                for trail in TRAILS:
                    specs.append(
                        {
                            "family": "residual",
                            "name": (
                                f"f{int(frac * 100)}_lb{lb}_sk{skip}_"
                                f"tr{int(trail * 100)}_n1"
                            ),
                            "btc_frac": frac,
                            "lookback_days": lb,
                            "skip_days": skip,
                            "trail_pct": trail,
                            "flatten": "all",
                            "sma_n": 50,
                            "rebalance_days": 7,
                            "excess_floor": 0.0,
                            "n_alts": 1,
                            "require_alt_sma": False,
                        }
                    )
    return specs


def neighborhood_specs(seed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Fine knobs around a coarse winner, plus top-2 / dual-SMA / SMA-n / reb."""
    lb0 = int(seed.get("lookback_days") or 10)
    frac0 = float(seed.get("btc_frac") or 0.5)
    tr0 = float(seed.get("trail_pct") or 0.10)
    skip0 = int(seed.get("skip_days") or 1)
    lookbacks = sorted(
        {max(4, lb0 + d) for d in range(-3, 4)} | {lb0}
    )
    fracs = sorted(
        {round(min(0.9, max(0.0, frac0 + d)), 2) for d in (-0.15, -0.1, -0.05, 0.0, 0.05, 0.1)}
    )
    trails = sorted({round(max(0.0, tr0 + d), 2) for d in (-0.04, -0.02, 0.0, 0.02, 0.04)})
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(spec: dict[str, Any]) -> None:
        name = str(spec["name"])
        if name in seen:
            return
        seen.add(name)
        specs.append(spec)

    for lb in lookbacks:
        for frac in fracs:
            for trail in trails:
                add(
                    {
                        "family": "residual",
                        "name": (
                            f"fine_f{int(frac * 100)}_lb{lb}_sk{skip0}_"
                            f"tr{int(trail * 100)}_n1"
                        ),
                        "btc_frac": frac,
                        "lookback_days": lb,
                        "skip_days": skip0,
                        "trail_pct": trail,
                        "flatten": "all",
                        "sma_n": 50,
                        "rebalance_days": 7,
                        "excess_floor": 0.0,
                        "n_alts": 1,
                        "require_alt_sma": False,
                    }
                )
    for n_alts in (1, 2):
        for dual in (False, True):
            for sma_n in (20, 50, 100):
                add(
                    {
                        "family": "residual",
                        "name": (
                            f"nb_f{int(frac0 * 100)}_lb{lb0}_sk{skip0}_"
                            f"tr{int(tr0 * 100)}_n{n_alts}_sma{sma_n}"
                            f"{'_dual' if dual else ''}"
                        ),
                        "btc_frac": frac0,
                        "lookback_days": lb0,
                        "skip_days": skip0,
                        "trail_pct": tr0,
                        "flatten": "all",
                        "sma_n": sma_n,
                        "rebalance_days": 7,
                        "excess_floor": 0.0,
                        "n_alts": n_alts,
                        "require_alt_sma": dual,
                    }
                )
    for reb in (5, 7, 10, 14):
        add(
            {
                "family": "residual",
                "name": f"nb_reb{reb}_f{int(frac0 * 100)}_lb{lb0}_tr{int(tr0 * 100)}",
                "btc_frac": frac0,
                "lookback_days": lb0,
                "skip_days": skip0,
                "trail_pct": tr0,
                "flatten": "all",
                "sma_n": 50,
                "rebalance_days": reb,
                "excess_floor": 0.0,
                "n_alts": 1,
                "require_alt_sma": False,
            }
        )
    for floor in (0.0, 0.04, 0.08):
        add(
            {
                "family": "residual",
                "name": f"nb_fl{int(floor * 100)}_f{int(frac0 * 100)}_lb{lb0}",
                "btc_frac": frac0,
                "lookback_days": lb0,
                "skip_days": skip0,
                "trail_pct": tr0,
                "flatten": "all",
                "sma_n": 50,
                "rebalance_days": 7,
                "excess_floor": floor,
                "n_alts": 1,
                "require_alt_sma": False,
            }
        )
    return specs


def baseline_rows(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clip = run_clip_exits(
        ohlc, ExitPolicy(name="live"), start=start, end=end, book_eur=book_eur, model=model
    )
    clip["family"] = "clip"
    clip["name"] = "clip_live_75_25_floor8"
    rows.append(_slim(clip))
    for cfg in loop_sleeve_configs():
        don = run_donchian_sleeve(
            ohlc, cfg, start=start, end=end, book_eur=book_eur, model=model
        )
        don["family"] = "donchian"
        don["name"] = f"donch20k_{cfg.name}"
        rows.append(_slim(don))
    hold = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        btc_frac=1.0,
        flatten="none",
        model=model,
        strategy="btc_hold",
    )
    hold["family"] = "btc"
    hold["name"] = "btc_hold"
    rows.append(_slim(hold))
    sma = run_btc_residual(
        ohlc,
        start=start,
        end=end,
        book_eur=book_eur,
        btc_frac=1.0,
        flatten="all",
        sma_n=50,
        model=model,
        strategy="btc_sma50",
    )
    sma["family"] = "btc"
    sma["name"] = "btc_sma50"
    rows.append(_slim(sma))
    return rows


def eval_residual_spec(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    spec: Mapping[str, Any],
    *,
    start: str,
    end: str,
    book_eur: float,
    model: FillModel,
) -> dict[str, Any]:
    trail = float(spec.get("trail_pct") or 0.0)
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
        skip_days=int(spec.get("skip_days") or 1),
        n_alts=int(spec.get("n_alts") or 1),
        require_alt_sma=bool(spec.get("require_alt_sma")),
        model=model,
        policy=_trail_policy(trail),
        strategy=str(spec["name"]),
    )
    slim = _slim(row)
    slim.update(
        {
            "family": spec.get("family") or "residual",
            "name": spec["name"],
            "btc_frac": spec["btc_frac"],
            "lookback_days": spec["lookback_days"],
            "skip_days": spec["skip_days"],
            "trail_pct": trail,
            "n_alts": spec.get("n_alts") or 1,
            "require_alt_sma": bool(spec.get("require_alt_sma")),
            "sma_n": spec.get("sma_n") or 50,
            "rebalance_days": spec.get("rebalance_days") or 7,
            "excess_floor": spec.get("excess_floor") or 0.0,
        }
    )
    return slim


def _slim(row: Mapping[str, Any]) -> dict[str, Any]:
    drop = {"curve", "trades_tail", "weeks"}
    return {k: v for k, v in row.items() if k not in drop}


def year_ok(row: Mapping[str, Any], *, min_year: float = 0.0) -> bool:
    yp = row.get("year_pnl") or {}
    if not isinstance(yp, dict) or not yp:
        return False
    return all(float(v) >= min_year for v in yp.values())


def robust_ok(row: Mapping[str, Any], *, max_dd: float = 0.50, min_year: float = 0.0) -> bool:
    return year_ok(row, min_year=min_year) and float(row.get("max_dd_pct") or 1.0) <= max_dd


def pick_champion(ranked: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Best Calmar among year-positive, DD≤50% packs; else relax; else raw Calmar."""
    rows = list(ranked)
    for max_dd, min_year in ((0.50, 0.0), (0.55, 0.0), (0.55, -2000.0), (1.0, -1e12)):
        pool = [r for r in rows if robust_ok(r, max_dd=max_dd, min_year=min_year)]
        if pool:
            best = max(
                pool,
                key=lambda r: (float(r.get("calmar") or 0.0), float(r.get("pnl_eur") or 0.0)),
            )
            return {
                "name": best.get("name"),
                "rule": f"calmar | years>={min_year} | dd<={max_dd:.0%}",
                "calmar": best.get("calmar"),
                "pnl_eur": best.get("pnl_eur"),
                "max_dd_pct": best.get("max_dd_pct"),
                "year_pnl": best.get("year_pnl"),
                "pack": dict(best),
            }
    return {"name": None, "rule": "empty", "pack": {}}


_WORKER: dict[str, Any] = {}


def _init_worker(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    start: str,
    end: str,
    book_eur: float,
    model: FillModel,
) -> None:
    _WORKER["ohlc"] = ohlc
    _WORKER["start"] = start
    _WORKER["end"] = end
    _WORKER["book_eur"] = book_eur
    _WORKER["model"] = model


def _eval_job(spec: dict[str, Any]) -> dict[str, Any]:
    return eval_residual_spec(
        _WORKER["ohlc"],
        spec,
        start=_WORKER["start"],
        end=_WORKER["end"],
        book_eur=_WORKER["book_eur"],
        model=_WORKER["model"],
    )


def run_spec_grid(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    specs: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
    book_eur: float = 20_000.0,
    model: FillModel = WET,
    workers: int = 4,
) -> list[dict[str, Any]]:
    payload = [dict(spec) for spec in specs]
    if workers <= 1 or len(payload) <= 2:
        _init_worker(ohlc, start, end, book_eur, model)
        return [_eval_job(spec) for spec in payload]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(ohlc, start, end, book_eur, model),
    ) as pool:
        for row in pool.map(_eval_job, payload, chunksize=4):
            rows.append(row)
    return rows


def rank_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ranked = [dict(r) for r in rows]
    ranked.sort(key=lambda r: (-float(r.get("calmar") or 0.0), -float(r.get("pnl_eur") or 0.0)))
    return ranked
