"""Run the locked families on the live residual knobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from bot.research.better_book.bar import (
    BETA_WINDOWS,
    BTC_VETO_GAPS,
    GAP_CAPS,
    GREEN_PAUSES,
    IR_WINDOWS,
    LEAD_EXITS,
    RANK_SLOTS,
    REARM_MODES,
    RED_CUT_DAYS,
    RED_LEADER_DAYS,
    RUNNER_ARMS,
    RUNNER_TRAILS,
    SCALE_ARMS,
    SCALE_STARTERS,
    TIDE_FLOORS,
    TRAIL_CONFIRM,
    TRAIL_HALF_FRAC,
    TRAIL_WEEKDAYS,
)
from bot.research.clip_exit_lab.policies import ExitPolicy
from bot.research.factor_scoreboard.engine import LIVE_KNOBS, LIVE_POLICY

Window = tuple[str, str, str]
Cell = dict[str, Any]


def _run(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    name: str,
    windows: Sequence[Window],
    book_eur: float,
    policy: ExitPolicy | None = None,
    starter_frac: float = 1.0,
    add_arm_pct: float = 0.0,
    rank_mode: str = "excess",
    rank_window: int = 20,
    rearm_mode: str = "",
    btc_tide_floor: float | None = None,
    green_pause_days: int = 0,
    universe_top_n: int = 0,
    btc_veto_gap: float | None = None,
    rank_slot: str = "",
    gap_cap: float | None = None,
    lead_exit: str = "",
    keep_trades: bool = False,
) -> Cell:
    from bot.research.btc_residual_mix.engine import run_btc_residual

    knobs = dict(LIVE_KNOBS)
    if policy is not None:
        knobs["policy"] = policy
    out: Cell = {"name": name, "family": ""}
    for tag, start, end in windows:
        row = run_btc_residual(
            ohlc,
            start=start,
            end=end,
            book_eur=book_eur,
            strategy=name,
            keep_trades=keep_trades,
            starter_frac=starter_frac,
            add_arm_pct=add_arm_pct,
            rank_mode=rank_mode,
            rank_window=rank_window,
            rearm_mode=rearm_mode,
            btc_tide_floor=btc_tide_floor,
            green_pause_days=green_pause_days,
            universe_top_n=universe_top_n,
            btc_veto_gap=btc_veto_gap,
            rank_slot=rank_slot,
            gap_cap=gap_cap,
            lead_exit=lead_exit,
            **knobs,
        )
        out[tag] = {
            "pnl_eur": round(float(row["pnl_eur"]), 2),
            "max_dd_pct": round(float(row["max_dd_pct"]), 4),
            "n_trades": int(row["n_trades"]),
            "year_pnl": row.get("year_pnl") or {},
        }
        if keep_trades:
            out[f"{tag}_trades"] = list(row.get("trades") or [])
    return out


def families() -> list[tuple[str, list[dict[str, Any]]]]:
    """Name plus the kwargs for each cell. Baseline is not in here."""
    scale: list[dict[str, Any]] = []
    for starter in SCALE_STARTERS:
        for arm in SCALE_ARMS:
            scale.append(
                {
                    "name": f"scale_s{int(starter * 100)}_a{int(arm * 100)}",
                    "starter_frac": starter,
                    "add_arm_pct": arm,
                }
            )
    runner: list[dict[str, Any]] = []
    for arm in RUNNER_ARMS:
        for trail in RUNNER_TRAILS:
            runner.append(
                {
                    "name": f"runner_a{int(arm * 100)}_t{int(trail * 100)}",
                    "policy": replace(
                        LIVE_POLICY,
                        name=f"runner_a{int(arm * 100)}_t{int(trail * 100)}",
                        alt_runner_arm_pct=arm,
                        alt_runner_trail_pct=trail,
                    ),
                }
            )
    weekly: list[dict[str, Any]] = []
    for wd in TRAIL_WEEKDAYS:
        label = "mon" if wd == 0 else "fri"
        weekly.append(
            {
                "name": f"trail_{label}",
                "policy": replace(
                    LIVE_POLICY,
                    name=f"trail_{label}",
                    alt_trail_weekdays=(wd,),
                ),
            }
        )
    confirm: list[dict[str, Any]] = []
    for n in TRAIL_CONFIRM:
        confirm.append(
            {
                "name": f"confirm_{n}",
                "policy": replace(
                    LIVE_POLICY,
                    name=f"confirm_{n}",
                    alt_trail_confirm=n,
                ),
            }
        )
    confirm.append(
        {
            "name": "half_pierce",
            "policy": replace(
                LIVE_POLICY,
                name="half_pierce",
                alt_trail_confirm=2,
                alt_trail_first_frac=TRAIL_HALF_FRAC,
            ),
        }
    )
    touch = [
        {
            "name": "touch_trail",
            "policy": replace(LIVE_POLICY, name="touch_trail", alt_trail_on_touch=True),
        },
        {
            "name": "touch_lock",
            "policy": replace(LIVE_POLICY, name="touch_lock", alt_lock_on_touch=True),
        },
        {
            "name": "touch_both",
            "policy": replace(
                LIVE_POLICY,
                name="touch_both",
                alt_trail_on_touch=True,
                alt_lock_on_touch=True,
            ),
        },
    ]
    beta = [
        {
            "name": f"beta_{n}",
            "rank_mode": "beta",
            "rank_window": n,
        }
        for n in BETA_WINDOWS
    ]
    ir = [
        {
            "name": f"ir_{n}",
            "rank_mode": "ir",
            "rank_window": n,
        }
        for n in IR_WINDOWS
    ]
    rearm = [{"name": f"rearm_{mode}", "rearm_mode": mode} for mode in REARM_MODES]
    pause = [
        {"name": f"pause_{days}", "green_pause_days": days} for days in GREEN_PAUSES
    ]
    tide = [
        {
            "name": f"tide_{int(floor * 100)}",
            "btc_tide_floor": floor,
        }
        for floor in TIDE_FLOORS
    ]
    veto = [{"name": name, "btc_veto_gap": gap} for name, gap in BTC_VETO_GAPS]
    slot = [{"name": f"slot_{name}", "rank_slot": name} for name in RANK_SLOTS]
    red = [
        {
            "name": f"red_{days}",
            "policy": replace(
                LIVE_POLICY,
                name=f"red_{days}",
                alt_red_cut_days=days,
            ),
        }
        for days in RED_CUT_DAYS
    ]
    red_lead = [
        {
            "name": f"red_lead_{days}",
            "policy": replace(
                LIVE_POLICY,
                name=f"red_lead_{days}",
                alt_red_cut_days=days,
                alt_red_cut_if_leader=True,
            ),
        }
        for days in RED_LEADER_DAYS
    ]
    gap = [
        {"name": f"gap_{int(round(cap * 100))}", "gap_cap": cap} for cap in GAP_CAPS
    ]
    exited = [{"name": f"lead_{mode}", "lead_exit": mode} for mode in LEAD_EXITS]
    return [
        ("scale_in", scale),
        ("runner", runner),
        ("weekday_trail", weekly),
        ("trail_confirm", confirm),
        ("trail_touch", touch),
        ("beta_rank", beta),
        ("ir_rank", ir),
        ("rearm", rearm),
        ("btc_tide", tide),
        ("green_pause", pause),
        ("btc_veto", veto),
        ("rank_slot", slot),
        ("red_cut", red),
        ("red_leader", red_lead),
        ("gap_cap", gap),
        ("lead_exit", exited),
    ]


def run_search(
    ohlc: Mapping[str, Sequence[Sequence[float]]],
    *,
    windows: Sequence[Window],
    book_eur: float = 20_000.0,
    log: Any = None,
) -> dict[str, Any]:
    say = log or (lambda *_a, **_k: None)
    base = _run(ohlc, name="baseline", windows=windows, book_eur=book_eur)
    base["family"] = "baseline"
    say(f"baseline full {base['full']['pnl_eur']:+.0f} oos {base['oos']['pnl_eur']:+.0f}")
    grouped: dict[str, list[Cell]] = {}
    for family, cells in families():
        grouped[family] = []
        for spec in cells:
            cell = _run(ohlc, windows=windows, book_eur=book_eur, **spec)
            cell["family"] = family
            grouped[family].append(cell)
            say(
                f"{cell['name']} full {cell['full']['pnl_eur']:+.0f} "
                f"oos {cell['oos']['pnl_eur']:+.0f}"
            )
    return {"baseline": base, "families": grouped}


def round_trips(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Gross round trips from fill rows. Fees sit inside the fill prices."""
    cost: dict[str, float] = {}
    qty: dict[str, float] = {}
    out: list[dict[str, Any]] = []
    for fill in trades:
        base = str(fill.get("base") or "")
        px = float(fill.get("px") or 0.0)
        eur = float(fill.get("eur") or 0.0)
        if px <= 0 or eur <= 0 or not base:
            continue
        if fill.get("side") == "buy":
            cost[base] = cost.get(base, 0.0) + eur
            qty[base] = qty.get(base, 0.0) + eur / px
            continue
        if fill.get("side") != "sell" or qty.get(base, 0.0) <= 0:
            continue
        take = min(qty[base], eur / px)
        frac = take / qty[base]
        basis = cost[base] * frac
        out.append(
            {
                "base": base,
                "date": str(fill.get("date") or ""),
                "pnl": eur - basis,
                "reason": str(fill.get("reason") or ""),
            }
        )
        cost[base] -= basis
        qty[base] -= take
    return out


def largest_new_trip(
    variant: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
) -> float:
    """Largest variant round trip that the baseline did not also book.

    A twin is the same base, a sell date within five days, and a pnl within
    20% (or €500 when the trip is small).
    """
    from datetime import datetime

    def near(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        if a["base"] != b["base"]:
            return False
        da = datetime.strptime(str(a["date"]), "%Y-%m-%d")
        db = datetime.strptime(str(b["date"]), "%Y-%m-%d")
        if abs((da - db).days) > 5:
            return False
        pa, pb = float(a["pnl"]), float(b["pnl"])
        if abs(pa - pb) <= 500.0:
            return True
        denom = max(abs(pb), 1.0)
        return abs(pa - pb) / denom <= 0.20

    base_trips = round_trips(baseline)
    best = 0.0
    for trip in round_trips(variant):
        if any(near(trip, other) for other in base_trips):
            continue
        if trip["pnl"] > best:
            best = float(trip["pnl"])
    return best
