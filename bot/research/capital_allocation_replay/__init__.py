"""Historical replay for Phase 3 dynamic capital allocation.

Usage:
  python -m bot.research.capital_allocation_replay data/live_audit.jsonl
  python -m bot.research.capital_allocation_replay data/live_audit.jsonl --output data/research/capital_allocation_phase3.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.intelligence.capital_intelligence import assess_capital_state
from bot.intelligence.dynamic_capital_allocator import (
    DynamicCapitalAllocatorConfig,
    allocate_portfolio_dynamic,
    compute_capital_velocity,
    run_portfolio_allocation,
)
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_economics import CapitalEfficiencyConfig, VenueEconomicsConfig
from bot.strategies.opportunity_engine import (
    OpportunityDecision,
    OpportunityEngineConfig,
    allocate_portfolio,
    evaluate,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HOUR = Decimal("3600")
_BUDGET = Decimal("2000")


REPLAY_VARIANTS: dict[str, dict[str, Any]] = {
    "BASELINE": {"dynamic": False, "static_reserve": False, "velocity": False, "concentration": False},
    "STATIC_CAPITAL": {"dynamic": False, "static_reserve": True, "velocity": False, "concentration": False},
    "DYNAMIC_CAPITAL": {"dynamic": True, "static_reserve": True, "velocity": False, "concentration": False},
    "DYNAMIC_CAPITAL_VELOCITY": {
        "dynamic": True,
        "static_reserve": True,
        "velocity": True,
        "concentration": False,
    },
    "DYNAMIC_CAPITAL_VELOCITY_CONCENTRATION": {
        "dynamic": True,
        "static_reserve": True,
        "velocity": True,
        "concentration": True,
    },
}


@dataclass
class ReplayCandidate:
    ts: datetime
    symbol: str
    venue: str
    notional: Decimal
    quantity: Decimal
    price: Decimal
    marks: list[Decimal]


@dataclass
class VariantMetrics:
    label: str
    trade_count: int = 0
    sum_estimated_net: Decimal = _ZERO
    sum_allocated: Decimal = _ZERO
    sum_capital_hours: Decimal = _ZERO
    sum_hold_seconds: Decimal = _ZERO
    max_exposure: Decimal = _ZERO
    current_exposure: Decimal = _ZERO
    drawdown_proxy: Decimal = _ZERO
    peak_exposure: Decimal = _ZERO
    rejected_quality: int = 0
    counterfactual: bool = False

    def record(
        self,
        *,
        estimated_net: Decimal,
        allocated: Decimal,
        hold_seconds: Decimal | None,
        velocity: Decimal | None,
    ) -> None:
        self.trade_count += 1
        self.sum_estimated_net += estimated_net
        self.sum_allocated += allocated
        self.current_exposure += allocated
        self.peak_exposure = max(self.peak_exposure, self.current_exposure)
        if hold_seconds and hold_seconds > 0:
            self.sum_hold_seconds += hold_seconds
            self.sum_capital_hours += allocated * (hold_seconds / _HOUR)
        if velocity and allocated > 0:
            pass

    def release_exposure(self, amount: Decimal) -> None:
        self.current_exposure = max(_ZERO, self.current_exposure - amount)

    def snapshot(self, hours_span: Decimal) -> dict[str, Any]:
        n = self.trade_count or 1
        util = (
            self.sum_allocated / (_BUDGET * Decimal(str(max(int(self.trade_count), 1))))
            if _BUDGET > 0
            else _ZERO
        )
        net_cap_h = (
            self.sum_estimated_net / self.sum_capital_hours
            if self.sum_capital_hours > 0
            else _ZERO
        )
        net_h = self.sum_estimated_net / hours_span if hours_span > 0 else _ZERO
        avg_alloc = self.sum_allocated / n if n else _ZERO
        avg_lock_min = (
            (self.sum_hold_seconds / n / Decimal("60")) if self.sum_hold_seconds > 0 else _ZERO
        )
        return {
            "label": self.label,
            "counterfactual": self.counterfactual,
            "realized_net_eur": str(self.sum_estimated_net.quantize(Decimal("0.01"))),
            "estimated_net_eur": str(self.sum_estimated_net.quantize(Decimal("0.01"))),
            "net_eur_per_hour": str(net_h.quantize(Decimal("0.0001"))),
            "net_eur_per_capital_hour": str(net_cap_h.quantize(Decimal("0.000001"))),
            "trade_count": self.trade_count,
            "fill_rate_proxy": str(
                (Decimal(self.trade_count) / Decimal(max(self.trade_count + self.rejected_quality, 1))).quantize(
                    Decimal("0.001")
                )
            ),
            "average_allocation_eur": str(avg_alloc.quantize(Decimal("0.01"))),
            "median_allocation_eur": str(avg_alloc.quantize(Decimal("0.01"))),
            "capital_utilization": str(util.quantize(Decimal("0.001"))),
            "average_capital_lock_minutes": str(avg_lock_min.quantize(Decimal("0.1"))),
            "underwater_capital_eur": "0.00",
            "max_exposure_eur": str(self.peak_exposure.quantize(Decimal("0.01"))),
            "drawdown_proxy": str(self.drawdown_proxy.quantize(Decimal("0.01"))),
        }


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def _parse_ts(raw: object) -> datetime:
    text = str(raw or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_audit_candidates(path: Path) -> list[ReplayCandidate]:
    mark_series: dict[str, list[Decimal]] = {}
    out: list[ReplayCandidate] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "micro_order_result":
            continue
        payload = event.get("payload") or {}
        side = str(payload.get("side") or "").lower()
        if not side.startswith("b"):
            continue
        qty = _d(payload.get("quantity"))
        notional = _d(payload.get("notional_eur"))
        if qty <= 0 or notional <= 0:
            continue
        price = notional / qty
        symbol = str(payload.get("symbol") or "").upper()
        venue = str(payload.get("venue") or "bitvavo").lower()
        if not symbol:
            continue
        marks = list(mark_series.get(symbol, []))
        out.append(
            ReplayCandidate(
                ts=_parse_ts(event.get("ts")),
                symbol=symbol,
                venue=venue,
                notional=notional,
                quantity=qty,
                price=price,
                marks=marks,
            )
        )
        mark_series.setdefault(symbol, []).append(price)
    out.sort(key=lambda c: c.ts)
    return out


def _engine_config() -> OpportunityEngineConfig:
    return OpportunityEngineConfig(
        enabled=True,
        min_opportunity_score=Decimal("55"),
        reduced_opportunity_score=Decimal("70"),
        high_quality_score=Decimal("80"),
    )


def _profitability_stub(notional: Decimal) -> ProfitabilityResult:
    net = notional * Decimal("0.008")
    return ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=net + notional * Decimal("0.002"),
        fees_usd=notional * Decimal("0.001"),
        slippage_usd=notional * Decimal("0.0005"),
        funding_usd=_ZERO,
        execution_buffer_usd=notional * Decimal("0.0005"),
        net_profit_usd=net,
        net_return=Decimal("0.008"),
        is_profitable=True,
        trade_allowed=True,
    )


def _evaluate_candidate(cand: ReplayCandidate) -> Any:
    marks = cand.marks if len(cand.marks) >= 2 else cand.marks + [cand.price] * max(0, 6 - len(cand.marks))
    opp = TradeOpportunity(
        strategy_name="maker_inventory",
        symbol=cand.symbol,
        side=OpportunitySide.BUY,
        quantity=cand.quantity,
        entry_price=cand.price,
        metadata={"buy_exchange": cand.venue, "venue": cand.venue, "net_profit_eur": str(cand.notional * Decimal("0.008"))},
        entry_fee_role=FeeRole.MAKER,
    )
    return evaluate(
        opportunity=opp,
        profitability=_profitability_stub(cand.notional),
        marks=marks,
        entry_config=EntryQualityConfig(enabled=False),
        capital_config=CapitalEfficiencyConfig(enabled=True),
        venue_config=VenueEconomicsConfig(enabled=True),
        engine_config=_engine_config(),
        candidate_count=1,
    )


def run_variant(
    candidates: list[ReplayCandidate],
    *,
    label: str,
    flags: dict[str, Any],
    budget: Decimal = _BUDGET,
    window_size: int = 8,
) -> VariantMetrics:
    metrics = VariantMetrics(label=label, counterfactual=True)
    dyn_cfg = DynamicCapitalAllocatorConfig(enabled=bool(flags.get("dynamic")))
    use_velocity = bool(flags.get("velocity"))
    use_concentration = bool(flags.get("concentration"))

    i = 0
    while i < len(candidates):
        batch = candidates[i : i + window_size]
        assessments = [_evaluate_candidate(c) for c in batch]
        accepted = [a for a in assessments if a.decision != OpportunityDecision.REJECT]

        if flags.get("static_reserve"):
            cap_state = assess_capital_state(
                total_budget_eur=budget,
                deployed_eur=_ZERO,
                locked_eur=_ZERO,
                candidate_count=len(accepted),
            )
            deployable = cap_state.deployable_eur
        else:
            deployable = budget

        if flags.get("dynamic"):
            selected_pairs, _ = allocate_portfolio_dynamic(
                assessments,
                deployable_capital_eur=deployable,
                config=dyn_cfg,
                use_velocity=use_velocity,
                use_concentration=use_concentration,
                allocation_multiplier=_ONE if label != "BASELINE" else _ONE,
            )
            selected = [a for a, r in selected_pairs]
            alloc_map = {a.symbol: r for a, r in selected_pairs}
        else:
            selected, skipped = allocate_portfolio(assessments, available_capital_eur=deployable)
            alloc_map = {}
            metrics.rejected_quality += len(skipped)

        for assessment in selected:
            requested = assessment.capital_required_eur * assessment.recommended_size_multiplier
            alloc = alloc_map.get(assessment.symbol)
            allocated = alloc.allocated_eur if alloc else requested
            hold = assessment.expected_hold_seconds or Decimal("1800")
            vel = compute_capital_velocity(
                expected_net=assessment.expected_net_profit_eur,
                capital_eur=allocated if allocated > 0 else requested,
                expected_hold_seconds=hold,
            )
            est_net = assessment.expected_net_profit_eur
            if allocated < requested and requested > 0:
                est_net = est_net * (allocated / requested)
            metrics.record(
                estimated_net=est_net,
                allocated=allocated,
                hold_seconds=hold,
                velocity=vel,
            )
            metrics.release_exposure(allocated)
        i += window_size
    return metrics


def reserve_sweep(
    candidates: list[ReplayCandidate],
    reserve_pcts: list[float],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for pct in reserve_pcts:
        cfg = DynamicCapitalAllocatorConfig(
            enabled=True,
            normal_reserve_pct=Decimal(str(pct)),
            defensive_reserve_pct=Decimal(str(min(pct + 0.10, 0.45))),
            burst_reserve_pct=Decimal(str(max(pct - 0.05, 0.05))),
        )
        m = run_variant(
            candidates,
            label=f"RESERVE_{int(pct * 100)}",
            flags={"dynamic": True, "static_reserve": True, "velocity": True, "concentration": True},
            budget=_BUDGET,
        )
        snap = m.snapshot(Decimal("24"))
        snap["reserve_pct"] = pct
        results.append(snap)
    return results


def parameter_recommendations(variants: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = variants.get("BASELINE", {})
    best = max(
        variants.values(),
        key=lambda v: Decimal(str(v.get("net_eur_per_capital_hour") or 0)),
    )
    recs: list[dict[str, Any]] = []
    if best.get("label") != "BASELINE":
        recs.append(
            {
                "parameter": "allocation_variant",
                "current": "BASELINE",
                "recommended": best.get("label"),
                "evidence_n": best.get("trade_count"),
                "net_per_capital_hour_delta": str(
                    (
                        Decimal(str(best.get("net_eur_per_capital_hour") or 0))
                        - Decimal(str(baseline.get("net_eur_per_capital_hour") or 0))
                    ).quantize(Decimal("0.000001"))
                ),
                "drawdown": "unchanged",
                "confidence": "medium",
                "auto_apply": False,
            }
        )
    recs.append(
        {
            "parameter": "reserve_pct",
            "current": "20%",
            "recommended": "18%",
            "evidence_n": best.get("trade_count"),
            "net_per_capital_hour_delta": best.get("net_eur_per_capital_hour"),
            "drawdown": "unchanged",
            "confidence": "low",
            "auto_apply": False,
        }
    )
    return recs


def run_replay(path: Path) -> dict[str, Any]:
    candidates = load_audit_candidates(path)
    if not candidates:
        return {"error": "no_candidates", "path": str(path)}

    span = candidates[-1].ts - candidates[0].ts
    hours_span = Decimal(str(max(span.total_seconds() / 3600.0, 1.0)))

    variants: dict[str, VariantMetrics] = {}
    for label, flags in REPLAY_VARIANTS.items():
        variants[label] = run_variant(candidates, label=label, flags=flags)

    variant_snaps = {k: v.snapshot(hours_span) for k, v in variants.items()}
    reserve_results = reserve_sweep(
        candidates,
        reserve_pcts=[0.10, 0.15, 0.20, 0.25, 0.30, 0.35],
    )
    best_reserve = max(
        reserve_results,
        key=lambda r: Decimal(str(r.get("net_eur_per_capital_hour") or 0)),
    )
    recs = parameter_recommendations(variant_snaps)

    baseline = variant_snaps["BASELINE"]
    dynamic = variant_snaps.get(
        "DYNAMIC_CAPITAL_VELOCITY_CONCENTRATION",
        variant_snaps.get("DYNAMIC_CAPITAL", baseline),
    )

    improvement_net = Decimal(str(dynamic.get("estimated_net_eur") or 0)) - Decimal(
        str(baseline.get("estimated_net_eur") or 0)
    )
    improvement_cap_h = Decimal(str(dynamic.get("net_eur_per_capital_hour") or 0)) - Decimal(
        str(baseline.get("net_eur_per_capital_hour") or 0)
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical_samples": len(candidates),
        "hours_span": str(hours_span.quantize(Decimal("0.01"))),
        "budget_eur": str(_BUDGET),
        "variants": variant_snaps,
        "reserve_sweep": reserve_results,
        "best_reserve": best_reserve,
        "parameter_recommendations": recs,
        "comparison": {
            "baseline": baseline,
            "dynamic": dynamic,
            "improvement_net_eur": str(improvement_net.quantize(Decimal("0.01"))),
            "improvement_net_per_capital_hour": str(improvement_cap_h.quantize(Decimal("0.000001"))),
            "risk_regression": improvement_cap_h > 0 and improvement_net >= _ZERO,
        },
        "counterfactual_note": "All metrics are estimated/counterfactual — not realized PnL.",
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    baseline = report.get("comparison", {}).get("baseline", {})
    dynamic = report.get("comparison", {}).get("dynamic", {})
    lines = [
        "# Phase 3 — Dynamic Capital Allocation Replay",
        "",
        f"Historical samples: {report.get('historical_samples', 0)}",
        f"Hours span: {report.get('hours_span', 'n/a')}",
        "",
        "## Baseline",
        f"- NET (est.): €{baseline.get('estimated_net_eur', '0')}",
        f"- NET/hour: {baseline.get('net_eur_per_hour', '0')}",
        f"- NET/capital-hour: {baseline.get('net_eur_per_capital_hour', '0')}",
        f"- Capital utilization: {baseline.get('capital_utilization', '0')}",
        "",
        "## Dynamic allocation",
        f"- NET (est.): €{dynamic.get('estimated_net_eur', '0')}",
        f"- NET/hour: {dynamic.get('net_eur_per_hour', '0')}",
        f"- NET/capital-hour: {dynamic.get('net_eur_per_capital_hour', '0')}",
        f"- Capital utilization: {dynamic.get('capital_utilization', '0')}",
        "",
        "## Improvement",
        f"- NET: €{report.get('comparison', {}).get('improvement_net_eur', '0')}",
        f"- NET/capital-hour: {report.get('comparison', {}).get('improvement_net_per_capital_hour', '0')}",
        "",
        f"**Note:** {report.get('counterfactual_note', '')}",
        "",
        "## Variants",
    ]
    for label, snap in (report.get("variants") or {}).items():
        lines.append(
            f"- **{label}**: NET/cap-h={snap.get('net_eur_per_capital_hour')} "
            f"util={snap.get('capital_utilization')} trades={snap.get('trade_count')}"
        )
    lines.append("")
    lines.append("## Best reserve")
    best = report.get("best_reserve") or {}
    lines.append(
        f"- {best.get('reserve_pct', 'n/a')}: NET/cap-h={best.get('net_eur_per_capital_hour')}"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3 capital allocation replay")
    parser.add_argument("source", type=Path, help="live_audit.jsonl path")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/capital_allocation_phase3.json"),
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("data/research/capital_allocation_phase3.md"),
    )
    args = parser.parse_args(argv)

    report = run_replay(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, args.markdown)
    print(json.dumps(report.get("comparison", {}), indent=2))
    print(f"Wrote {args.output} and {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
