"""Run the locked families on the live residual knobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from bot.research.better_book.bar import (
    ACCEL_WINDOWS,
    AGREE_DAYS,
    BETA_WINDOWS,
    BREAK_DAYS,
    BTC_EXT,
    BTC_VETO_GAPS,
    CROSS_DAYS,
    CROWD,
    EQUITY_SMA,
    FORM_DAYS,
    GAP_CAPS,
    GAP_DOWNS,
    GREEN_PAUSES,
    IR_WINDOWS,
    KEEP_RIDES,
    LEAD_EXITS,
    LEAD_WIDE,
    LIQ_WINDOWS,
    LOSS_BANS,
    MAX_WINDOWS,
    OWN_FLOORS,
    RANK_SLOTS,
    REARM_MODES,
    RECLAIM,
    RED_CUT_DAYS,
    RED_LEADER_DAYS,
    ROTATE_GAPS,
    RUNNER_ARMS,
    RUNNER_TRAILS,
    SCALE_ARMS,
    SCALE_STARTERS,
    STOP_PCTS,
    TIDE_FLOORS,
    TRAIL_CONFIRM,
    TRAIL_HALF_FRAC,
    TRAIL_WEEKDAYS,
    VOL_MULTS,
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
    keep_ride: str = "",
    loss_ban_days: int = 0,
    agree_days: int = 0,
    vol_mult: float = 0.0,
    cross_days: int = 0,
    lead_wide: float = 0.0,
    crowd_side: str = "",
    crowd_n: int = 0,
    gap_down: float | None = None,
    rotate_gap: float = 0.0,
    btc_ext_side: str = "",
    btc_ext: float = 0.0,
    own_floor: float | None = None,
    lookback_days: int = 0,
    equity_sma: int = 0,
    break_mode: str = "",
    break_days: int = 0,
    reclaim_side: str = "",
    reclaim_n: int = 0,
    keep_trades: bool = False,
) -> Cell:
    from bot.research.btc_residual_mix.engine import run_btc_residual

    knobs = dict(LIVE_KNOBS)
    if policy is not None:
        knobs["policy"] = policy
    if lookback_days > 0:
        knobs["lookback_days"] = int(lookback_days)
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
            keep_ride=keep_ride,
            loss_ban_days=loss_ban_days,
            agree_days=agree_days,
            vol_mult=vol_mult,
            cross_days=cross_days,
            lead_wide=lead_wide,
            crowd_side=crowd_side,
            crowd_n=crowd_n,
            gap_down=gap_down,
            rotate_gap=rotate_gap,
            btc_ext_side=btc_ext_side,
            btc_ext=btc_ext,
            own_floor=own_floor,
            equity_sma=equity_sma,
            break_mode=break_mode,
            break_days=break_days,
            reclaim_side=reclaim_side,
            reclaim_n=reclaim_n,
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
    kept = [{"name": f"keep_{mode}", "keep_ride": mode} for mode in KEEP_RIDES]
    agree = [{"name": f"agree_{days}", "agree_days": days} for days in AGREE_DAYS]
    banned = [{"name": f"ban_{days}", "loss_ban_days": days} for days in LOSS_BANS]
    vol = [
        {"name": f"vol_{int(round(mult * 100))}", "vol_mult": mult} for mult in VOL_MULTS
    ]
    fresh = [{"name": f"cross_{days}", "cross_days": days} for days in CROSS_DAYS]
    wide = [
        {"name": f"wide_{int(round(width * 100))}", "lead_wide": width} for width in LEAD_WIDE
    ]
    crowd = [
        {"name": f"crowd_{side}_{n}", "crowd_side": side, "crowd_n": n} for side, n in CROWD
    ]
    down = [{"name": f"gap_down_{int(round(cap * 100))}", "gap_down": cap} for cap in GAP_DOWNS]
    rotate = [
        {"name": f"rotate_{int(round(gap * 100))}", "rotate_gap": gap} for gap in ROTATE_GAPS
    ]
    ext = [
        {
            "name": f"ext_{side}_{int(round(bound * 100))}",
            "btc_ext_side": side,
            "btc_ext": bound,
        }
        for side, bound in BTC_EXT
    ]
    own = [
        {"name": f"own_{int(round(floor * 100))}", "own_floor": floor} for floor in OWN_FLOORS
    ]
    accel = [
        {
            "name": f"{mode}_{window}",
            "rank_mode": mode,
            "rank_window": window,
        }
        for mode in ("accel", "decel")
        for window in ACCEL_WINDOWS
    ]
    formed = [
        {"name": f"form_{days}", "lookback_days": days} for days in FORM_DAYS
    ]
    spiked = [
        {
            "name": f"{mode}_{window}",
            "rank_mode": mode,
            "rank_window": window,
        }
        for mode in ("max_high", "max_low")
        for window in MAX_WINDOWS
    ]
    liquid = [
        {
            "name": f"{mode}_{window}",
            "rank_mode": mode,
            "rank_window": window,
        }
        for mode in ("liq_high", "liq_low")
        for window in LIQ_WINDOWS
    ]
    curve = [{"name": f"eq_{n}", "equity_sma": n} for n in EQUITY_SMA]
    broken = [
        {
            "name": f"break_{mode}_{days}",
            "break_mode": mode,
            "break_days": days,
        }
        for mode in ("above", "inside")
        for days in BREAK_DAYS
    ]
    stopped = [
        {
            "name": f"stop_{int(round(pct * 100))}",
            "policy": replace(
                LIVE_POLICY,
                name=f"stop_{int(round(pct * 100))}",
                alt_stop_pct=pct,
            ),
        }
        for pct in STOP_PCTS
    ]
    reclaimed = [
        {"name": f"reclaim_{side}_{n}", "reclaim_side": side, "reclaim_n": n}
        for side, n in RECLAIM
    ]
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
        ("keep_ride", kept),
        ("agree", agree),
        ("loss_ban", banned),
        ("vol_expand", vol),
        ("fresh_cross", fresh),
        ("lead_wide", wide),
        ("crowd", crowd),
        ("gap_down", down),
        ("rotate_gap", rotate),
        ("btc_ext", ext),
        ("own_ret", own),
        ("accel_rank", accel),
        ("formation", formed),
        ("max_rank", spiked),
        ("liq_rank", liquid),
        ("equity_curve", curve),
        ("breakout", broken),
        ("hard_stop", stopped),
        ("reclaim", reclaimed),
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
