"""Causal walk-forward toxicity evaluation + age ablation + forensics."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bot.opportunity.calibration import EvCalibrator
from bot.opportunity.toxicity.dataset import build_labeled_events
from bot.opportunity.toxicity.shadow import shadow_admit
from bot.opportunity.toxicity.shrinkage import HierarchicalToxicityModel
from bot.opportunity.toxicity.types import LabeledEvent, PreTradeFeatures

_ZERO = Decimal("0")


@dataclass
class _WFResult:
    name: str
    quoted: int = 0
    rejected: int = 0
    fills: int = 0
    completed: int = 0
    realized_net: Decimal = _ZERO
    adverse_sum: Decimal = _ZERO
    fees_sum: Decimal = _ZERO
    reject_reasons: dict[str, int] | None = None
    events: list[dict[str, Any]] | None = None

    def summary(self) -> dict[str, Any]:
        n = max(1, self.completed)
        return {
            "name": self.name,
            "quoted": self.quoted,
            "rejected": self.rejected,
            "fills": self.fills,
            "completed_trades": self.completed,
            "realized_net": str(self.realized_net),
            "net_per_fill": str(self.realized_net / Decimal(n)),  # OBSERVED: mapped to ObservedRealizedRoundtripNetEUR / fills; not canonical replay
            "adverse_per_fill": str(self.adverse_sum / Decimal(n)),
            "fees_per_fill": str(self.fees_sum / Decimal(n)),
            "fill_rate": None,  # all labeled events are fills in this dataset
            "reject_reasons": dict(self.reject_reasons or {}),
        }


def _early_stop_blocks(calibrator: EvCalibrator, route: str) -> bool:
    st = calibrator.route_state(route)
    state = str(st.get("state") or "").lower()
    return state in {"early_stopped", "hard_stopped", "stopped"}


def walk_forward_toxicity(
    events: list[LabeledEvent],
    *,
    policy: str,
    model_name: str = "C_HIERARCHICAL",
    prior_strength: int = 8,
    uncertainty_weight: Decimal = Decimal("0.5"),
    use_early_stop: bool = False,
    max_age_ms: float | None = None,
    label_horizon: str = "5s",
) -> dict[str, Any]:
    """Strict causal order: predict → decide → reveal label → observe.

    Policies:
      baseline — take all (historical fills as taken)
      conditional_ev — reject if expected_net - max(0, hist_adv_eur - buffer) <= 0
      toxicity — shadow toxicity admission
      toxicity_plus_early_stop — toxicity + calibrator early stop
    """
    model = HierarchicalToxicityModel(prior_strength=prior_strength, model=model_name)
    calibrator = EvCalibrator(prior_strength=40, min_samples=20, early_stop_samples=8)
    # Track historical adverse EUR by route for conditional-EV shadow (past only)
    route_adv: dict[str, list[Decimal]] = {}
    out = _WFResult(name=policy, reject_reasons={}, events=[])

    for ev in events:
        feats = ev.features
        # Age ablation: reject if book_age exceeds threshold (shadow only)
        if max_age_ms is not None and float(feats.book_age_ms) > max_age_ms:
            out.rejected += 1
            out.reject_reasons["max_age"] = out.reject_reasons.get("max_age", 0) + 1
            out.events.append(
                {
                    "opportunity_id": feats.opportunity_id,
                    "decision": "reject",
                    "reason": "max_age",
                    "realized_net": None,
                }
            )
            continue

        pred = model.predict(feats)
        decision = "take"
        reason = "baseline_take"

        if policy == "baseline":
            decision, reason = "take", "baseline_take"
        elif policy == "conditional_ev":
            hist = route_adv.get(feats.route) or []
            if len(hist) >= 3:
                mean_adv = sum(hist, _ZERO) / Decimal(len(hist))
                extra = max(_ZERO, mean_adv - feats.expected_buffer_eur)
                if feats.expected_net_eur - extra <= 0:
                    decision, reason = "reject", "conditional_ev_non_positive"
            if decision == "take":
                reason = "conditional_ev_accept"
        elif policy in {"toxicity", "toxicity_plus_early_stop"}:
            shadow = shadow_admit(
                feats, pred, uncertainty_weight=uncertainty_weight
            )
            if not shadow.accept:
                decision, reason = "reject", shadow.reason
            else:
                decision, reason = "take", shadow.reason
            if (
                decision == "take"
                and use_early_stop
                and _early_stop_blocks(calibrator, feats.route)
            ):
                decision, reason = "reject", "early_stop_historical"
        else:
            raise ValueError(f"unknown policy {policy}")

        if decision == "reject":
            out.rejected += 1
            out.reject_reasons[reason] = out.reject_reasons.get(reason, 0) + 1
            out.events.append(
                {
                    "opportunity_id": feats.opportunity_id,
                    "decision": "reject",
                    "reason": reason,
                    "predicted_adverse_bps": str(pred.expected_adverse_bps),
                    "sample_count": pred.sample_count,
                    "shrinkage_source": pred.shrinkage_source,
                    "realized_net": None,
                }
            )
            # Rejects do NOT update toxicity beliefs or adverse labels.
            continue

        # Taken: count fill + learn AFTER decision
        out.quoted += 1
        out.fills += 1
        out.completed += 1
        out.realized_net += ev.realized_net_eur
        out.adverse_sum += ev.realized_adverse_eur
        out.fees_sum += feats.expected_fees_eur
        out.events.append(
            {
                "opportunity_id": feats.opportunity_id,
                "decision": "take",
                "reason": reason,
                "predicted_adverse_bps": str(pred.expected_adverse_bps),
                "observed_adverse_bps": str(ev.label_bps(label_horizon)),
                "prediction_error_bps": str(
                    ev.label_bps(label_horizon) - pred.expected_adverse_bps
                ),
                "sample_count": pred.sample_count,
                "uncertainty_bps": str(pred.uncertainty_bps),
                "shrinkage_source": pred.shrinkage_source,
                "realized_net": str(ev.realized_net_eur),
            }
        )
        # Observe for future predictions only
        model.observe(feats, ev.label_bps(label_horizon))
        route_adv.setdefault(feats.route, []).append(ev.realized_adverse_eur)
        key = (
            f"{feats.strategy}|{feats.symbol}|{feats.route}|{feats.side}"
        )
        calibrator.observe(
            key=key,
            route=feats.route,
            strategy=feats.strategy,
            expected_net=feats.expected_net_eur,
            realized_net=ev.realized_net_eur,
        )

    # Calibration: predicted vs observed for taken events
    taken = [e for e in (out.events or []) if e.get("decision") == "take"]
    if taken:
        errs = [Decimal(str(e["prediction_error_bps"])) for e in taken if "prediction_error_bps" in e]
        mean_err = sum(errs, _ZERO) / Decimal(len(errs)) if errs else _ZERO
    else:
        mean_err = None
    summary = out.summary()
    summary["mean_prediction_error_bps"] = str(mean_err) if mean_err is not None else None
    summary["events"] = out.events
    return summary


def compare_policies(events: list[LabeledEvent]) -> dict[str, Any]:
    policies = [
        ("A_BASELINE", dict(policy="baseline")),
        ("B_CONDITIONAL_EV", dict(policy="conditional_ev")),
        ("C_TOXICITY", dict(policy="toxicity", model_name="C_HIERARCHICAL")),
        (
            "D_TOXICITY_PLUS_EARLY_STOP",
            dict(
                policy="toxicity_plus_early_stop",
                model_name="C_HIERARCHICAL",
                use_early_stop=True,
            ),
        ),
    ]
    # Also compare model families under toxicity policy
    model_cmp = {}
    for mname in ("A_GLOBAL", "B_ROUTE", "C_HIERARCHICAL", "D_BUCKETED"):
        model_cmp[mname] = walk_forward_toxicity(
            events, policy="toxicity", model_name=mname
        )
        # strip heavy events for summary table
        model_cmp[mname] = {
            k: v for k, v in model_cmp[mname].items() if k != "events"
        }

    results = {}
    for name, kwargs in policies:
        full = walk_forward_toxicity(events, **kwargs)
        results[name] = {k: v for k, v in full.items() if k != "events"}
        results[name]["_events"] = full.get("events")
    return {"policies": results, "models_under_toxicity_policy": model_cmp}


def split_events(
    events: list[LabeledEvent], *, train_frac: float = 0.4, val_frac: float = 0.3
) -> tuple[list[LabeledEvent], list[LabeledEvent], list[LabeledEvent]]:
    n = len(events)
    if n == 0:
        return [], [], []
    i = max(1, int(n * train_frac))
    j = max(i + 1, int(n * (train_frac + val_frac)))
    j = min(j, n - 1) if n > 2 else n
    return events[:i], events[i:j], events[j:]


def untouched_oos_eval(events: list[LabeledEvent]) -> dict[str, Any]:
    """Train beliefs on train+val chronologically, freeze, then score untouched test.

    Actually for toxicity we use pure walk-forward on each segment separately:
    - in-sample causal: full series walk-forward
    - untouched OOS: walk-forward on test only after warming model on train+val
      without using test labels during warm-up decisions... Warm-up observes
      train+val outcomes; then walk-forward on test.
    """
    train, val, test = split_events(events)
    warm_events = train + val

    def _warm(model: HierarchicalToxicityModel, rows: list[LabeledEvent]) -> None:
        for ev in rows:
            model.observe(ev.features, ev.label_bps("5s"))

    oos: dict[str, Any] = {}
    for mname in ("A_GLOBAL", "B_ROUTE", "C_HIERARCHICAL", "D_BUCKETED"):
        # Warm on past only
        model = HierarchicalToxicityModel(model=mname)
        _warm(model, warm_events)
        # Walk-forward on test starting from warmed model
        # Reimplement mini loop to preserve warmed state
        from bot.opportunity.toxicity.shadow import shadow_admit

        quoted = rejected = completed = 0
        net = adv = fees = _ZERO
        for ev in test:
            pred = model.predict(ev.features)
            shadow = shadow_admit(ev.features, pred)
            if not shadow.accept:
                rejected += 1
                continue
            quoted += 1
            completed += 1
            net += ev.realized_net_eur
            adv += ev.realized_adverse_eur
            fees += ev.features.expected_fees_eur
            model.observe(ev.features, ev.label_bps("5s"))
        n = max(1, completed)
        oos[mname] = {
            "train": len(train),
            "val": len(val),
            "test": len(test),
            "quoted": quoted,
            "rejected": rejected,
            "completed_trades": completed,
            "realized_net": str(net),
            "net_per_fill": str(net / Decimal(n)),  # OBSERVED: mapped to ObservedRealizedRoundtripNetEUR / fills; not canonical replay
            "adverse_per_fill": str(adv / Decimal(n)),
            "fees_per_fill": str(fees / Decimal(n)),
        }
    # Baseline on test: take all
    base_net = sum((e.realized_net_eur for e in test), _ZERO)
    oos["BASELINE_TAKE_ALL"] = {
        "test": len(test),
        "completed_trades": len(test),
        "realized_net": str(base_net),
        "net_per_fill": str(base_net / Decimal(max(1, len(test)))),  # OBSERVED: mapped to ObservedRealizedRoundtripNetEUR / fills; not canonical replay
    }
    return oos


def age_ablation(events: list[LabeledEvent]) -> dict[str, Any]:
    thresholds = [60000, 30000, 10000, 4000, 2000]
    baseline = walk_forward_toxicity(events, policy="baseline")
    base_net = Decimal(str(baseline["realized_net"]))
    rows = []
    for ms in thresholds:
        # Count how many have known age; if all unknown (0), ablation is inconclusive
        known = sum(1 for e in events if float(e.features.book_age_ms) > 0)
        res = walk_forward_toxicity(events, policy="baseline", max_age_ms=float(ms))
        net = Decimal(str(res["realized_net"]))
        # profits/losses relative to baseline takes
        avoided_loss = _ZERO
        lost_profit = _ZERO
        fills_lost = 0
        for ev in events:
            if float(ev.features.book_age_ms) > ms:
                fills_lost += 1
                if ev.realized_net_eur < 0:
                    avoided_loss += -ev.realized_net_eur
                else:
                    lost_profit += ev.realized_net_eur
        rows.append(
            {
                "max_age_ms": ms,
                "events_with_known_age": known,
                "fills_lost": fills_lost,
                "realized_net": str(net),
                "net_difference_vs_baseline": str(net - base_net),
                "realized_losses_avoided": str(avoided_loss),
                "realized_profits_lost": str(lost_profit),
                "note": (
                    "inconclusive_missing_book_age"
                    if known == 0
                    else "causal_shadow_age_gate"
                ),
            }
        )
    return {"baseline_net": str(base_net), "thresholds": rows}


def loss_forensics(events: list[LabeledEvent]) -> list[dict[str, Any]]:
    """Table for every completed losing round trip + toxicity prediction at t."""
    model = HierarchicalToxicityModel(model="C_HIERARCHICAL")
    rows = []
    for ev in events:
        pred = model.predict(ev.features)
        if ev.realized_net_eur < 0:
            rows.append(
                {
                    "opportunity_id": ev.features.opportunity_id,
                    "timestamp": ev.features.timestamp,
                    "symbol": ev.features.symbol,
                    "route": ev.features.route,
                    "side": ev.features.side,
                    "fill_type": ev.fill_type_observed,
                    "quote_age_bucket": ev.features.quote_age_bucket,
                    "book_age_ms": str(ev.features.book_age_ms),
                    "spread_bps": str(ev.features.spread_bps),
                    "spread_bucket": ev.features.spread_bucket,
                    "regime": ev.features.regime,
                    "fair_value_deviation_bps": str(ev.features.fair_value_deviation_bps),
                    "expected_net": str(ev.features.expected_net_eur),
                    "realized_net": str(ev.realized_net_eur),
                    "realized_adverse_eur": str(ev.realized_adverse_eur),
                    "adverse_bps_proxy": str(ev.adverse_bps_proxy),
                    "markout_5s_bps": str(ev.markout_5s_bps),
                    "toxicity_predicted_bps": str(pred.expected_adverse_bps),
                    "toxicity_sample_count": pred.sample_count,
                    "toxicity_uncertainty_bps": str(pred.uncertainty_bps),
                    "shrinkage_source": pred.shrinkage_source,
                    "hypothesis": _hypothesis(ev, pred),
                }
            )
        # Observe after prediction (causal)
        model.observe(ev.features, ev.label_bps("5s"))
    return rows


def _hypothesis(ev: LabeledEvent, pred) -> str:
    bits = []
    if "bitvavo" in ev.features.route:
        bits.append("bitvavo_route")
    if ev.fill_type_observed == "trade_through":
        bits.append("trade_through_fill")
    if ev.adverse_bps_proxy > Decimal("20"):
        bits.append("high_adverse_bps")
    if pred.sample_count < 3:
        bits.append("sparse_pretrade_evidence")
    return ",".join(bits) or "unspecified"


def calibration_by_decile(events: list[LabeledEvent]) -> dict[str, Any]:
    """Walk-forward predictions vs outcomes — can we separate high vs low adverse?"""
    model = HierarchicalToxicityModel(model="C_HIERARCHICAL")
    pairs: list[tuple[Decimal, Decimal]] = []
    for ev in events:
        pred = model.predict(ev.features)
        obs = ev.label_bps("5s")
        pairs.append((pred.expected_adverse_bps, obs))
        model.observe(ev.features, obs)
    if len(pairs) < 4:
        return {"n": len(pairs), "note": "too_few_for_deciles", "pairs": [
            {"predicted": str(p), "observed": str(o)} for p, o in pairs
        ]}
    ordered = sorted(pairs, key=lambda x: x[0])
    mid = len(ordered) // 2
    low = ordered[:mid]
    high = ordered[mid:]
    def _avg(xs: list[tuple[Decimal, Decimal]], idx: int) -> Decimal:
        return sum((x[idx] for x in xs), _ZERO) / Decimal(len(xs))

    return {
        "n": len(pairs),
        "low_pred_mean_predicted": str(_avg(low, 0)),
        "low_pred_mean_observed": str(_avg(low, 1)),
        "high_pred_mean_predicted": str(_avg(high, 0)),
        "high_pred_mean_observed": str(_avg(high, 1)),
        "separates_tail": _avg(high, 1) > _avg(low, 1),
        "note": (
            "Separates if high-predicted half has higher observed adverse. "
            "Tiny-n — hypothesis only."
        ),
    }


def trade_through_analysis(events: list[LabeledEvent]) -> dict[str, Any]:
    tt = [e for e in events if e.fill_type_observed == "trade_through"]
    other = [e for e in events if e.fill_type_observed != "trade_through"]
    def _stats(rows: list[LabeledEvent]) -> dict[str, Any]:
        if not rows:
            return {"n": 0}
        adv = [e.adverse_bps_proxy for e in rows]
        net = [e.realized_net_eur for e in rows]
        return {
            "n": len(rows),
            "mean_adverse_bps": str(sum(adv, _ZERO) / Decimal(len(adv))),
            "mean_realized_net": str(sum(net, _ZERO) / Decimal(len(net))),
            "total_realized_net": str(sum(net, _ZERO)),
        }
    return {
        "statement": (
            "All observed maker fills in this paper dump are trade-through "
            "under the simulator (queue fills off). The toxicity model estimates "
            "E(adverse | TRADE_THROUGH, state) — not neutral resting-maker fills "
            "on a live exchange."
        ),
        "trade_through": _stats(tt),
        "other_or_unknown": _stats(other),
        "fraction_trade_through": (
            float(len(tt) / len(events)) if events else None
        ),
    }
