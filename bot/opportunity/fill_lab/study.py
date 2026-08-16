"""Fill-mechanism sensitivity study: distributions, lock time, OOS shells."""

from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import Any, Sequence

from bot.opportunity.fill_lab.audit import audit_dataset
from bot.opportunity.fill_lab.baseline import (
    baseline_fingerprint,
    extract_baseline_fills,
    extract_quotes,
    load_paper,
)
from bot.opportunity.fill_lab.events import FillEvent
from bot.opportunity.fill_lab.models import (
    PERSISTENCE_MS_GRID,
    FillModelId,
    FillModelResult,
    run_depth_consumption,
    run_touch_only,
    run_touch_persistence,
    run_trade_through_baseline,
)

_ZERO = Decimal("0")


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def summarize_numeric(values: Sequence[float | Decimal | None]) -> dict[str, Any]:
    nums = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not nums:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    ordered = sorted(nums)
    return {
        "n": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "median": _percentile(ordered, 50),
        "p25": _percentile(ordered, 25),
        "p75": _percentile(ordered, 75),
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> dict[str, Any]:
    nums = [float(v) for v in values]
    if len(nums) < 2:
        return {"n": len(nums), "mean": (sum(nums) / len(nums) if nums else None), "ci_low": None, "ci_high": None}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        sample = [nums[rng.randrange(len(nums))] for _ in range(len(nums))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int((alpha / 2) * (n_boot - 1))]
    hi = means[int((1 - alpha / 2) * (n_boot - 1))]
    return {
        "n": len(nums),
        "mean": sum(nums) / len(nums),
        "ci_low": lo,
        "ci_high": hi,
        "note": "Bootstrap percentile CI; tiny-n — descriptive only.",
    }


def markout_distribution_from_export(data: dict[str, Any]) -> dict[str, Any]:
    """Horizon markouts from export (all observed samples — TT-dominated)."""
    by_h = (data.get("markout") or {}).get("by_horizon") or {}
    out: dict[str, Any] = {}
    for h, vals in by_h.items():
        floats = [float(x) for x in (vals or [])]
        out[str(h)] = {
            **summarize_numeric(floats),
            "bootstrap_mean_ci": bootstrap_mean_ci(floats),
        }
    return out


def capital_lock_analysis(fills: Sequence[FillEvent]) -> dict[str, Any]:
    locks = [f.capital_lock_ms for f in fills if f.capital_lock_ms is not None]
    ages = [f.quote_age_ms for f in fills]
    return {
        "quote_age_ms": summarize_numeric(ages),
        "capital_lock_ms": summarize_numeric(locks),
        "capital_lock_p95_ms": (
            _percentile(sorted(float(x) for x in locks), 95) if locks else None
        ),
        "note": (
            "Lock ≈ placed_ms → completed round-trip timestamp when joinable; "
            "fill created_at often missing in dump."
        ),
    }


def breakdown(fills: Sequence[FillEvent], trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Venue / symbol / side breakdown with adverse from trades when joinable."""
    trade_by_opp = {str(t.get("opportunity_id")): t for t in trades}
    buckets: dict[str, list[float]] = {}
    for f in fills:
        trade = trade_by_opp.get(f.opportunity_id) or {}
        adv = trade.get("realized_adverse")
        if adv is None:
            continue
        for key in (
            f"venue:{f.venue}",
            f"symbol:{f.symbol}",
            f"side:{f.side}",
            f"venue_side:{f.venue}|{f.side}",
        ):
            buckets.setdefault(key, []).append(float(adv))
    return {
        k: {**summarize_numeric(v), "hypothesis_only": len(v) < 10}
        for k, v in sorted(buckets.items())
    }


def run_all_models(path: str) -> dict[str, FillModelResult]:
    from pathlib import Path

    path = Path(path)
    data = load_paper(path)
    audit = audit_dataset(path)
    quotes = extract_quotes(data)
    baseline_fills = extract_baseline_fills(data)
    books_after: dict[str, list] = {}  # empty — unsupported path
    book_supported = bool(audit["checks"].get("market_timestamps_after_quote"))

    results: dict[str, FillModelResult] = {
        FillModelId.TRADE_THROUGH_ONLY.value: run_trade_through_baseline(baseline_fills),
        FillModelId.TOUCH_ONLY.value: run_touch_only(
            quotes, books_after, supported=book_supported
        ),
    }
    for ms in PERSISTENCE_MS_GRID:
        mid = FillModelId(f"TOUCH_PERSISTENCE_{ms}")
        results[mid.value] = run_touch_persistence(
            quotes, books_after, persistence_ms=ms, supported=book_supported
        )
    results[FillModelId.DEPTH_CONSUMPTION.value] = run_depth_consumption(
        quotes, books_after, supported=False
    )
    return results


def oos_shell(path: str, *, train_frac: float = 0.5) -> dict[str, Any]:
    """Predeclare models on development split; report each on untouched OOS.

    Without book history, experimental models remain UNSUPPORTED on both splits.
    Baseline fills are split by fill timestamp only for descriptive counts.
    """
    data = load_paper(path)
    fills = extract_baseline_fills(data)
    if not fills:
        return {"note": "no baseline fills", "dev": {}, "oos": {}}
    n = len(fills)
    cut = max(1, int(n * train_frac))
    cut = min(cut, n - 1) if n > 1 else n
    dev, oos = fills[:cut], fills[cut:]
    models = run_all_models(path)

    def _pack(subset: list[FillEvent], label: str) -> dict[str, Any]:
        return {
            "label": label,
            "n_baseline_fills": len(subset),
            "capital_lock": capital_lock_analysis(subset),
            "realized_net_joined": summarize_numeric(
                [float(f.realized_net_eur) for f in subset if f.realized_net_eur is not None]
            ),
        }

    return {
        "predeclared_models": list(models.keys()),
        "parameter_grid_persistence_ms": list(PERSISTENCE_MS_GRID),
        "note": (
            "No parameter selection on OOS. Experimental models unsupported "
            "without book recordings on both splits."
        ),
        "development": _pack(dev, "development"),
        "untouched_oos": _pack(oos, "untouched_oos"),
        "experimental_support_on_oos": {
            k: {"support": v.support, "status": v.status, "n": len(v.fills)}
            for k, v in models.items()
        },
    }


def build_study(path: str = "data/paper_25000live.json") -> dict[str, Any]:
    data = load_paper(path)
    audit = audit_dataset(path)
    fp = baseline_fingerprint(path)
    models = run_all_models(path)
    baseline = models[FillModelId.TRADE_THROUGH_ONLY.value]
    trades = list((data.get("tracker") or {}).get("trades") or [])

    # Key question: can we compare TT vs touch distributions?
    comparable = all(
        models[m].support == "SUPPORTED" and m != FillModelId.TRADE_THROUGH_ONLY.value
        for m in (
            FillModelId.TOUCH_ONLY.value,
            FillModelId.TOUCH_PERSISTENCE_250.value,
        )
    )

    if not comparable:
        toxicity_selector_answer = {
            "answer": "INSUFFICIENT_DATA",
            "detail": (
                "Cannot compare trade-through adverse distributions to touch-based "
                "eligibility: post-quote book/mid history is absent. "
                "We only observe the TRADE_THROUGH conditional distribution."
            ),
        }
        success_letter = "C"
    else:
        toxicity_selector_answer = {
            "answer": "COMPARABLE",
            "detail": "See markout tables by model.",
        }
        success_letter = "B_OR_A_SEE_EFFECT_SIZES"

    # Production recommendation options (forced set)
    if success_letter == "C":
        recommendation = "REQUIRE BETTER DATA"
        secondary = "KEEP TRADE-THROUGH BASELINE"
        abandon_note = (
            "Separately, under the conservative TT baseline economics remain deeply "
            "negative (see toxicity/edge audits). That does not authorize loosening fills."
        )
    else:
        recommendation = "KEEP TRADE-THROUGH BASELINE"
        secondary = None
        abandon_note = None

    panel = []
    for mid, res in models.items():
        panel.append(
            {
                "model": mid,
                "status": res.status,
                "support": res.support,
                "sample_count": len(res.fills),
                "fill_rate": None,
                "median_adverse": None,
                "mean_adverse": None,
                "net": None,
                "capital_lock": None,
                "notes": list(res.notes),
            }
        )
    # Enrich baseline panel from trades / markout export
    mo = markout_distribution_from_export(data)
    m5 = (mo.get("5000") or {})
    panel[0].update(
        {
            "mean_adverse_bps_5s_export": m5.get("mean"),
            "median_adverse_bps_5s_export": m5.get("median"),
            "net": fp.get("realized_net_sum"),
            "capital_lock": capital_lock_analysis(baseline.fills),
            "fill_rate": (
                len(baseline.fills) / max(1, fp["quote_count"]) if fp.get("quote_count") else None
            ),
        }
    )

    return {
        "A_data_sufficiency": audit,
        "B_fill_model_definitions": {
            "TRADE_THROUGH_ONLY": "Production conservative baseline (queue fills off).",
            "TOUCH_ONLY": "First post-quote touch of quote price (eligibility, not auto-fill).",
            "TOUCH_PERSISTENCE_*": f"Touch sustained for predeclared grid {list(PERSISTENCE_MS_GRID)} ms.",
            "DEPTH_CONSUMPTION": "UNSUPPORTED unless queue position known; never invent priority.",
            "labeling": "Experimental models = EXPERIMENTAL_COUNTERFACTUAL / OBSERVATIONAL.",
        },
        "C_eligibility_counts": {
            k: {"support": v.support, "status": v.status, "n_fills": len(v.fills)}
            for k, v in models.items()
        },
        "D_markout_distributions": {
            "by_horizon_observed_export": mo,
            "by_experimental_model": {
                k: "UNSUPPORTED — no per-model markouts without book path"
                for k, v in models.items()
                if v.status == "UNSUPPORTED"
            },
            "by_fill_type_tables": {
                "TRADE_THROUGH": {
                    horizon: mo.get(horizon)
                    for horizon in ("1000", "5000", "30000", "60000")
                },
                "TOUCH_PERSISTENCE_100": {"n": 0, "status": "UNSUPPORTED"},
                "TOUCH_PERSISTENCE_250": {"n": 0, "status": "UNSUPPORTED"},
                "TOUCH_PERSISTENCE_500": {"n": 0, "status": "UNSUPPORTED"},
                "TOUCH_PERSISTENCE_1000": {"n": 0, "status": "UNSUPPORTED"},
            },
            "baseline_fill_count": len(baseline.fills),
            "note": (
                "Per-fill-type experimental markouts require post-quote books. "
                "Observed export markouts are TT-dominated and are not attributed to touch models."
            ),
        },
        "E_capital_lock": capital_lock_analysis(baseline.fills),
        "F_causal_oos": oos_shell(path),
        "G_trade_through_toxicity_selector": toxicity_selector_answer,
        "H_production_recommendation": {
            "primary": recommendation,
            "also": secondary,
            "abandon_maker_note": abandon_note,
            "success_criterion": success_letter,
            "allowed_set": [
                "KEEP TRADE-THROUGH BASELINE",
                "REQUIRE BETTER DATA",
                "ABANDON MAKER THESIS UNDER CURRENT ECONOMICS",
            ],
            "do_not": (
                "Do not enable experimental fill models for live-equivalent PnL "
                "without real book/queue evidence."
            ),
        },
        "baseline_fingerprint": fp,
        "fill_model_lab_panel": panel,
        "breakdowns_hypothesis": breakdown(baseline.fills, trades),
        "quotes_count": len(extract_quotes(data)),
        "production_pnl_source": FillModelId.TRADE_THROUGH_ONLY.value,
        "success_letter": success_letter,
        "recommendation": recommendation,
    }
