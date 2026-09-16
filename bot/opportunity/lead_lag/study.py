"""Lead-lag study: audit → discovery → shadow → OOS protocol → verdict."""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from bot.core.exchange_types import OrderBookLevel
from bot.opportunity.lead_lag.horizons import HORIZON_MS_GRID, LATENCY_MS_GRID, horizon_support_table
from bot.opportunity.lead_lag.models_a_d import MODEL_REGISTRY
from bot.opportunity.lead_lag.observer import synthetic_lead_lag_tape
from bot.opportunity.lead_lag.pairs import directed_pairs, empty_pair_report, pair_id
from bot.opportunity.lead_lag.timestamps import audit_timestamps
from bot.opportunity.lead_lag.types import LeadLagObservation
from bot.opportunity.lead_lag.walkforward import run_latency_sensitivity, walk_forward_lead_lag

_ZERO = Decimal("0")


VERDICTS = (
    "NO_STABLE_PREDICTIVE_RELATIONSHIP",
    "PREDICTIVE_BUT_NOT_EXECUTABLE",
    "EXECUTABLE_IN_SAMPLE_ONLY",
    "PROMISING_OOS_RESEARCH_SIGNAL",
    "INSUFFICIENT_DATA",
)


def _percentile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    ordered = sorted(vals)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def pair_discovery(
    observations: Sequence[LeadLagObservation],
    *,
    horizon_ms: int,
    model_version: str = "A_SIGNED_LEADER_v1",
) -> list[dict[str, Any]]:
    """Evaluate each directed pair present in observations at a fixed horizon."""
    by_pair: dict[str, list[LeadLagObservation]] = {}
    for o in observations:
        by_pair.setdefault(pair_id(o.leader_venue, o.follower_venue, o.symbol), []).append(o)

    reports: list[dict[str, Any]] = []
    if not by_pair:
        for lead, foll in directed_pairs():
            reports.append(empty_pair_report(lead, foll, horizon_ms=horizon_ms))
        return reports

    for key, series in sorted(by_pair.items()):
        series = sorted(series, key=lambda x: x.timestamp_ms)
        wf = walk_forward_lead_lag(
            series, model_version=model_version, horizon_ms=horizon_ms
        )
        responses = [
            float(o["follower_move_bps"][str(horizon_ms)])
            for o in wf.outcomes
            if str(horizon_ms) in o.get("follower_move_bps", {})
        ]
        lead = series[0].leader_venue
        foll = series[0].follower_venue
        reports.append(
            {
                "pair": key,
                "leader": lead,
                "follower": foll,
                "symbol": series[0].symbol,
                "horizon_ms": horizon_ms,
                "sample_count": len(series),
                "n_outcomes": wf.n_outcomes,
                "mean_follower_response_bps": (
                    sum(responses) / len(responses) if responses else None
                ),
                "median_follower_response_bps": _percentile(responses, 50),
                "directional_hit_rate": wf.hit_rate,
                "effect_size": (
                    (sum(responses) / len(responses)) / (math.sqrt(sum(x * x for x in responses) / len(responses)) + 1e-9)
                    if len(responses) > 1
                    else None
                ),
                "uncertainty": (
                    math.sqrt(sum((x - sum(responses) / len(responses)) ** 2 for x in responses) / (len(responses) - 1))
                    if len(responses) > 1
                    else None
                ),
                "stability": "hypothesis_only" if wf.n_outcomes < 30 else "descriptive",
                "status": "OK" if wf.n_outcomes >= 10 else "INSUFFICIENT_DATA",
                "label": "CAUSAL_REPLAY",
                "shadow_summary": wf.summary(),
            }
        )
    return reports


def freeze_candidates(
    *,
    pairs: list[tuple[str, str]],
    horizons: list[int],
    models: list[str],
) -> dict[str, Any]:
    """Freeze pair/horizon/model definitions before OOS — no post-hoc selection."""
    return {
        "frozen": True,
        "pairs": [{"leader": a, "follower": b} for a, b in pairs],
        "horizons_ms": list(horizons),
        "models": list(models),
        "latency_ms_grid": list(LATENCY_MS_GRID),
        "note": "Frozen before untouched OOS. Report every candidate.",
        "label": "DEVELOPMENT",
    }


def split_observations(
    observations: Sequence[LeadLagObservation],
    *,
    train_frac: float = 0.6,
) -> tuple[list[LeadLagObservation], list[LeadLagObservation]]:
    if not observations:
        return [], []
    ordered = sorted(observations, key=lambda o: o.timestamp_ms)
    cut = max(1, int(len(ordered) * train_frac))
    cut = min(cut, len(ordered) - 1) if len(ordered) > 1 else len(ordered)
    return list(ordered[:cut]), list(ordered[cut:])


def run_oos(
    dev: Sequence[LeadLagObservation],
    oos: Sequence[LeadLagObservation],
    frozen: dict[str, Any],
) -> dict[str, Any]:
    """Run every predeclared candidate on untouched OOS — hide nothing."""
    results = []
    for pair in frozen["pairs"]:
        for horizon in frozen["horizons_ms"]:
            for model in frozen["models"]:
                series = [
                    o
                    for o in oos
                    if o.leader_venue == pair["leader"] and o.follower_venue == pair["follower"]
                ]
                if not series:
                    results.append(
                        {
                            "pair": pair,
                            "horizon_ms": horizon,
                            "model": model,
                            "status": "INSUFFICIENT_DATA",
                            "label": "UNTOUCHED_OOS",
                            "summary": None,
                        }
                    )
                    continue
                wf = walk_forward_lead_lag(
                    series, model_version=model, horizon_ms=int(horizon)
                )
                results.append(
                    {
                        "pair": pair,
                        "horizon_ms": horizon,
                        "model": model,
                        "status": "OK" if wf.n_outcomes else "INSUFFICIENT_DATA",
                        "label": "UNTOUCHED_OOS",
                        "summary": wf.summary(),
                    }
                )
    return {
        "n_candidates": len(results),
        "results": results,
        "dev_n": len(dev),
        "oos_n": len(oos),
        "note": "No winner selection. Non-participation is not alpha.",
    }


def choose_verdict(
    *,
    audit: dict[str, Any],
    horizon_table: list[dict[str, Any]],
    discovery: list[dict[str, Any]],
    oos: dict[str, Any] | None,
) -> str:
    if not audit.get("has_synchronized_tape"):
        return "INSUFFICIENT_DATA"
    supported = [h for h in horizon_table if h["support"] != "UNSUPPORTED_BY_DATA"]
    if not supported:
        return "INSUFFICIENT_DATA"
    usable = [d for d in discovery if (d.get("n_outcomes") or 0) >= 10]
    if not usable:
        return "INSUFFICIENT_DATA"
    hits = [d["directional_hit_rate"] for d in usable if d.get("directional_hit_rate") is not None]
    if not hits or max(hits) < 0.55:
        return "NO_STABLE_PREDICTIVE_RELATIONSHIP"
    # Executable check: any shadow admissions with positive net on OOS?
    if oos:
        oos_ok = [
            r
            for r in oos.get("results") or []
            if r.get("summary") and (r["summary"].get("n_admitted") or 0) > 0
        ]
        if not oos_ok:
            # Predictive hit rate but no executable admissions
            return "PREDICTIVE_BUT_NOT_EXECUTABLE"
        # Compare rough in-sample vs oos hit rates
        oos_hits = [
            r["summary"]["hit_rate"]
            for r in oos_ok
            if r["summary"].get("hit_rate") is not None
        ]
        if oos_hits and max(oos_hits) < 0.55:
            return "EXECUTABLE_IN_SAMPLE_ONLY"
        return "PROMISING_OOS_RESEARCH_SIGNAL"
    return "PREDICTIVE_BUT_NOT_EXECUTABLE"


def build_study(
    *,
    observations: Sequence[LeadLagObservation] | None = None,
    market_data_dir: str | Path | None = "data/market_data",
    use_synthetic_if_empty: bool = False,
) -> dict[str, Any]:
    audit = audit_timestamps(market_data_dir=market_data_dir)
    horizon_table = horizon_support_table(
        data_quality=audit["overall_quality"],
        min_resolution_ms=audit.get("min_resolution_ms"),
        has_synchronized_tape=bool(audit["has_synchronized_tape"]),
    )

    obs: list[LeadLagObservation] = list(observations or [])
    synthetic_used = False
    if not obs and use_synthetic_if_empty:
        obs = synthetic_lead_lag_tape()
        synthetic_used = True

    # Development freeze (predeclared — even when empty)
    frozen = freeze_candidates(
        pairs=directed_pairs(),
        horizons=[h for h in HORIZON_MS_GRID if h >= 250],  # skip clearly unsupported without tape
        models=list(MODEL_REGISTRY.keys()),
    )

    discovery: list[dict[str, Any]] = []
    latency: dict[str, Any] = {}
    oos_block: dict[str, Any] | None = None
    development: dict[str, Any] = {"label": "DEVELOPMENT", "n": 0}

    if obs and (audit["has_synchronized_tape"] or synthetic_used):
        # Only run causal discovery when we have a tape (real or synthetic test)
        for h in frozen["horizons_ms"]:
            if not synthetic_used:
                # Real tape path: still respect unsupported horizons
                row = next((r for r in horizon_table if r["horizon_ms"] == h), None)
                if row and row["support"] == "UNSUPPORTED_BY_DATA":
                    continue
            discovery.extend(pair_discovery(obs, horizon_ms=h))
        # Latency on first available series
        by_pair: dict[str, list[LeadLagObservation]] = {}
        for o in obs:
            by_pair.setdefault(pair_id(o.leader_venue, o.follower_venue, o.symbol), []).append(o)
        if by_pair:
            key = sorted(by_pair.keys())[0]
            series = sorted(by_pair[key], key=lambda x: x.timestamp_ms)
            latency = run_latency_sensitivity(
                series, model_version="A_SIGNED_LEADER_v1", horizon_ms=500
            )
        dev, oos = split_observations(obs)
        development = {"label": "DEVELOPMENT", "n": len(dev)}
        oos_block = run_oos(dev, oos, frozen)
    else:
        # Honest empty discovery for all directed pairs / horizons
        for lead, foll in directed_pairs():
            for h in HORIZON_MS_GRID:
                discovery.append(empty_pair_report(lead, foll, horizon_ms=h))

    verdict = choose_verdict(
        audit=audit if not synthetic_used else {**audit, "has_synchronized_tape": True},
        horizon_table=horizon_table if not synthetic_used else [
            {**h, "support": "SUPPORTED"} for h in horizon_table
        ],
        discovery=discovery,
        oos=oos_block,
    )
    if not audit["has_synchronized_tape"] and not synthetic_used:
        verdict = "INSUFFICIENT_DATA"

    panel = []
    for lead, foll in directed_pairs():
        rows = [d for d in discovery if d.get("leader") == lead and d.get("follower") == foll]
        best = rows[0] if rows else empty_pair_report(lead, foll, horizon_ms=500)
        panel.append(
            {
                "pair": f"{lead}->{foll}",
                "status": best.get("status"),
                "data_quality": audit["overall_quality"],
                "sample_count": best.get("sample_count") or best.get("n_outcomes") or 0,
                "horizon_ms": best.get("horizon_ms"),
                "directional_hit_rate": best.get("directional_hit_rate"),
                "median_follower_response": best.get("median_follower_response_bps"),
                "estimated_prediction_error": best.get("uncertainty"),
                "shadow_opportunities": (best.get("shadow_summary") or {}).get("n_predictions"),
                "conservative_admissions": (best.get("shadow_summary") or {}).get("n_admitted"),
                "expected_net": None,
                "counterfactual_shadow_net": (best.get("shadow_summary") or {}).get("shadow_net_sum"),
                "latency": latency,
                "label": "RESEARCH_ONLY",
            }
        )

    return {
        "A_hypothesis": (
            "Leader venue/instrument price discovery predicts follower executable "
            "movement after a lag, surviving full costs and latency."
        ),
        "B_motivation": (
            "Maker inventory underperformed (~−€62 / 17 RTs); trade-through adverse "
            "dominated; toxicity not predictive; fill lab requires better data. "
            "Lead-lag is a separate research thesis."
        ),
        "C_timestamp_audit": audit,
        "D_causal_ordering": (
            "release_due → update from known outcomes only → predict at t → "
            "immutable shadow decision → wait horizon → observe → then train."
        ),
        "E_pair_universe": [{"leader": a, "follower": b} for a, b in directed_pairs()],
        "F_horizons": horizon_table,
        "G_signal_models": list(MODEL_REGISTRY.keys()),
        "H_executable_cost_model": {
            "entry": "follower depth VWAP (never mid when depth exists)",
            "hedge": "FULLY_HEDGED default via leader depth VWAP",
            "costs": "fees + slippage/buffer + latency haircut + uncertainty allowance",
            "admission": "conservative_net > 0",
            "aligns_with": "NetProfitCalculator structure",
        },
        "I_latency_sensitivity": latency,
        "J_hedge_assumptions": {
            "default": "FULLY_HEDGED",
            "missing_hedge": "HEDGE_UNAVAILABLE reject",
            "label": "SHADOW_COUNTERFACTUAL",
        },
        "K_development": development,
        "L_frozen_candidates": frozen,
        "M_untouched_oos": oos_block,
        "N_failure_analysis": {
            "no_tape": not audit["has_synchronized_tape"],
            "bitvavo_local_clock": True,
            "redis_receive_skew_lost": True,
            "synthetic_used": synthetic_used,
        },
        "O_final_verdict": verdict,
        "allowed_verdicts": list(VERDICTS),
        "lead_lag_lab_panel": panel,
        "discovery": discovery,
        "production_safety": {
            "alters_execution": False,
            "execution_enabled_default": False,
            "affects_production_pnl": False,
            "affects_route_calibration": False,
            "affects_maker_fills": False,
            "label": "RESEARCH_ONLY",
        },
        "observation_count": len(obs),
    }


def levels(price: Decimal, amount: Decimal = Decimal("5")) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=price, amount=amount)]
