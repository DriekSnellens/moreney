"""Main execution realism engine — orchestrates scenarios across the walk-forward tape."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from bot.research.accounting.protocol import REPLAY_VERSION, SCHEMA_VERSION
from bot.research.execution_realism.accounting import audit_scenario
from bot.research.execution_realism.breakeven import compute_breakeven_surface
from bot.research.execution_realism.config import (
    CONCENTRATION_CAP,
    EXECUTION_REALISM_PRODUCTION_ENABLED,
    FILL_RATE,
    MIN_INDEPENDENT_WINDOWS,
    MIN_POSITIVE_SCENARIO_FRACTION,
    NOTIONAL_EUR,
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    STRIDE_DEFAULT,
    build_manifest,
)
from bot.research.execution_realism.execution_simulator import simulate_signal
from bot.research.execution_realism.models import (
    ExecutionWaterfall,
    FillStatus,
    ScenarioResult,
    SignalOutcome,
    Verdict,
)
from bot.research.execution_realism.scenario import reference_scenarios, stage1_screen
from bot.research.regime_lab.families import FreshnessCVDFamily
from bot.research.regime_lab.features import views_for
from bot.research.regime_lab.metrics import attach_event_economics
from bot.research.robustness.protocol import (
    FIRST_LAB_OOS_START_NS,
    FROZEN_H0005_PARAMS,
    LOOKBACK_BUFFER_NS,
)
from bot.research.robustness.windows import sequential_windows
from bot.research.tournament.tape_index import build_tape_index

_ZERO = Decimal("0")


def _signal_side(event: dict[str, Any]) -> str:
    dis = event.get("dislocation")
    if dis is None:
        return "BUY"
    return "BUY" if float(dis) > 0 else "SELL"


def _entry_price(event: dict[str, Any]) -> Decimal:
    mid = event.get("mid") or event.get("entry_mid")
    if mid is not None:
        return Decimal(str(mid))
    bid = event.get("bid")
    ask = event.get("ask")
    if bid is not None and ask is not None:
        return Decimal(str((float(bid) + float(ask)) / 2.0))
    return Decimal("1")


def run_execution_realism(
    *,
    mode: str = "screen",
    strategies: list[str] | None = None,
    research_path: str = "data/research_marketdata",
    max_events: int | None = None,
    stride: int = STRIDE_DEFAULT,
    out_dir: str = "data/research",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    strategies = strategies or ["H-0005"]

    if not Path(research_path).exists():
        return {"STATUS": "DATA_NOT_READY", "VERDICT": Verdict.DATA_NOT_READY.value}

    min_ts = int(FIRST_LAB_OOS_START_NS) - int(LOOKBACK_BUFFER_NS)
    index = build_tape_index(
        Path(research_path),
        max_events=max_events,
        stride=stride,
        min_ts_ns=min_ts,
        parse_inventory_events=False,
    )
    plan = sequential_windows(index)
    complete_windows = [w for w in (plan.get("windows") or []) if w.get("complete")]

    if len(complete_windows) < 3:
        return {"STATUS": "DATA_NOT_READY", "VERDICT": Verdict.DATA_NOT_READY.value}

    scenarios = stage1_screen() if mode == "screen" else reference_scenarios()
    views = views_for(index)
    fam = FreshnessCVDFamily()
    params = dict(FROZEN_H0005_PARAMS)
    venue = str(params["venue_a"])
    venue_exit = str(params["venue_b"])
    h = int(params.get("horizon_ms") or 5000)

    all_waterfalls: dict[str, list[ExecutionWaterfall]] = {s["scenario_id"]: [] for s in scenarios}
    canonical_net = _ZERO
    n_signals = 0
    window_nets: dict[str, list[Decimal]] = {s["scenario_id"]: [] for s in scenarios}

    for w in complete_windows:
        part = fam.partition_window(
            index,
            start_ns=int(w["start_ts_ns"]),
            end_ns_exclusive=None,
            end_ns_inclusive=int(w["end_ts_ns_inclusive"]),
            params=params,
            horizons=[h],
        )
        parent_events = attach_event_economics(
            list(part["parent_events"]), venue=venue, venue_exit=venue_exit, horizon_ms=h
        )
        # Points for the window
        window_points = []
        for key, pts in index.series.items():
            if key[0] == venue:
                for p in pts:
                    if int(w["start_ts_ns"]) <= p.ts_ns <= int(w["end_ts_ns_inclusive"]):
                        window_points.append(p)
        window_points.sort(key=lambda p: p.ts_ns)

        scen_window_net: dict[str, Decimal] = {s["scenario_id"]: _ZERO for s in scenarios}

        for event in parent_events:
            n_signals += 1
            fwd = float(event.get("forward") or 0.0)
            canonical_net += Decimal(str(event.get("net") or 0))
            ts = int(event.get("ts_ns") or 0)
            sym = str(event.get("symbol") or "")
            side = _signal_side(event)
            entry = _entry_price(event)
            has_exchange_ts = event.get("exchange_ts_ns") is not None

            # Filter points relevant to this signal's horizon window
            sig_points = [p for p in window_points if p.ts_ns >= ts and p.ts_ns <= ts + h * 1_000_000]

            for scen in scenarios:
                wf = simulate_signal(
                    signal_id=f"{w['WINDOW_ID']}_{sym}_{ts}",
                    strategy_id="H-0005",
                    symbol=sym,
                    route=f"{venue}|{venue_exit}",
                    venue=venue,
                    venue_exit=venue_exit,
                    side=side,
                    forward=fwd,
                    observed_at_ns=ts,
                    entry_price=entry,
                    points=sig_points,
                    fill_model=scen["fill_model"],
                    latency_scenario=scen["latency_scenario"],
                    hedge_scenario=scen["hedge_scenario"],
                    cancel_scenario=scen["cancel_scenario"],
                    exchange_ts_available=has_exchange_ts,
                    notional=NOTIONAL_EUR,
                )
                all_waterfalls[scen["scenario_id"]].append(wf)
                scen_window_net[scen["scenario_id"]] += wf.execution_net

        for sid, net in scen_window_net.items():
            window_nets[sid].append(net)

    # Build scenario results
    scenario_results: list[dict[str, Any]] = []
    for scen in scenarios:
        sid = scen["scenario_id"]
        wfs = all_waterfalls[sid]
        fills = sum(1 for w in wfs if w.fill_status in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL))
        partials = sum(1 for w in wfs if w.fill_status == FillStatus.PARTIAL_FILL)
        no_fills = sum(1 for w in wfs if w.fill_status == FillStatus.NO_FILL)
        exec_net = sum((w.execution_net for w in wfs), _ZERO)
        wnets = window_nets[sid]
        pos_w = sum(1 for n in wnets if n > 0)
        neg_w = sum(1 for n in wnets if n < 0)
        med_w = Decimal(str(median(float(n) for n in wnets))) if wnets else None

        outcomes: dict[str, int] = {}
        for w in wfs:
            outcomes[w.outcome.value] = outcomes.get(w.outcome.value, 0) + 1

        sr = ScenarioResult(
            scenario_id=sid,
            fill_model=scen["fill_model"],
            latency_scenario=scen["latency_scenario"],
            hedge_scenario=scen["hedge_scenario"],
            cancel_scenario=scen["cancel_scenario"],
            n_signals=len(wfs),
            n_fills=fills,
            n_partial=partials,
            n_no_fill=no_fills,
            fill_rate=fills / len(wfs) if wfs else 0.0,
            partial_fill_rate=partials / len(wfs) if wfs else 0.0,
            execution_net_eur=exec_net,
            execution_net_per_signal=exec_net / Decimal(len(wfs)) if wfs else None,
            execution_net_per_fill=exec_net / Decimal(fills) if fills else None,
            canonical_replay_net_eur=canonical_net,
            delta_eur=exec_net - canonical_net,
            positive_windows=pos_w,
            negative_windows=neg_w,
            median_window_net=med_w,
            outcome_counts=outcomes,
        )
        scenario_results.append(sr.to_dict())

    # Determine verdict
    normal_scen = next(
        (r for r in scenario_results
         if r["latency_scenario"] == "NORMAL" and r["fill_model"] == "POST_ONLY_SURVIVAL" and r["hedge_scenario"] == "NORMAL"),
        None,
    )
    positive_scenarios = sum(1 for r in scenario_results if Decimal(str(r["execution_net_eur"])) > 0)
    total_scenarios = len(scenario_results) or 1
    pos_fraction = positive_scenarios / total_scenarios

    if not complete_windows:
        verdict = Verdict.DATA_NOT_READY
    elif normal_scen is None:
        verdict = Verdict.EXECUTION_FRAGILE
    elif Decimal(str(normal_scen["execution_net_eur"])) <= 0:
        verdict = Verdict.EXECUTION_FAILED
    elif pos_fraction < MIN_POSITIVE_SCENARIO_FRACTION:
        verdict = Verdict.EXECUTION_FRAGILE
    elif len(complete_windows) < MIN_INDEPENDENT_WINDOWS:
        verdict = Verdict.PROMISING_EXECUTION
    else:
        verdict = Verdict.ROBUST_EXECUTION_CANDIDATE

    # Break-even surface
    canonical_per_signal = canonical_net / Decimal(n_signals) if n_signals else _ZERO
    fee_per_signal = NOTIONAL_EUR * Decimal("0.002")  # approximate for breakeven calc
    adverse_per_signal = NOTIONAL_EUR * Decimal("0.0008")
    slip_per_signal = NOTIONAL_EUR * Decimal("0.0002")
    breakeven = compute_breakeven_surface(
        canonical_net_per_signal=canonical_per_signal,
        n_signals=n_signals,
        fill_rate_baseline=FILL_RATE,
        fee_baseline_per_signal=fee_per_signal,
        adverse_baseline_per_signal=adverse_per_signal,
        slippage_baseline_per_signal=slip_per_signal,
    )

    # Alpha loss attribution
    ref_normal = normal_scen or (scenario_results[0] if scenario_results else {})
    oc = ref_normal.get("outcome_counts") or {}
    total_oc = sum(oc.values()) or 1

    elapsed = time.perf_counter() - t0
    result: dict[str, Any] = {
        "STATUS": "COMPLETE",
        "PACKAGE": PACKAGE_LABEL,
        "VERDICT": verdict.value,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": build_manifest(extra={
            "n_signals": n_signals,
            "n_windows": len(complete_windows),
            "n_scenarios": len(scenarios),
            "mode": mode,
            "strategies": strategies,
        }),
        "CANONICAL_REPLAY_NET": str(canonical_net),
        "REALISTIC_EXECUTION_NET": str(ref_normal.get("execution_net_eur") or _ZERO),
        "DELTA": str(Decimal(str(ref_normal.get("execution_net_eur") or 0)) - canonical_net),
        "FILL_SURVIVAL_PCT": round(float(ref_normal.get("fill_rate") or 0) * 100, 1),
        "PARTIAL_FILL_PCT": round(float(ref_normal.get("partial_fill_rate") or 0) * 100, 1),
        "NO_FILL_PCT": round(float(ref_normal.get("n_no_fill") or 0) / max(1, int(ref_normal.get("n_signals") or 1)) * 100, 1),
        "alpha_loss_attribution": {
            k: round(v / total_oc * 100, 1) for k, v in oc.items()
        },
        "n_signals": n_signals,
        "n_windows": len(complete_windows),
        "positive_scenario_fraction": round(pos_fraction, 3),
        "scenario_results": scenario_results,
        "breakeven_surface": breakeven,
        "PRODUCTION_EXECUTION": "DISABLED",
        "execution_enabled": EXECUTION_REALISM_PRODUCTION_ENABLED,
        "NEW_STRATEGIES_CREATED": [],
        "NO_NEW_ALPHA_CLAIMED": True,
        "PERFORMANCE": {
            "execution_realism_seconds": elapsed,
            "n_signals": n_signals,
            "n_scenarios": len(scenarios),
            "signals_per_second": n_signals / elapsed if elapsed > 0 else 0,
        },
    }

    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
    (dest / "execution_realism_results.json").write_text(payload, encoding="utf-8")

    return result
