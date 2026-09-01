"""Historical replay ablation for execution intelligence (Phase 2).

Usage:
  python -m bot.research.execution_intelligence_ablation data/live_audit.jsonl
  python -m bot.research.execution_intelligence_ablation data/live_audit.jsonl --output data/research/execution_intelligence_ablation.json
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
from bot.intelligence.adverse_selection import assess_adverse_selection, post_fill_adverse_pct
from bot.intelligence.resting_order_intelligence import RestingOrderAction, assess_resting_order
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_economics import CapitalEfficiencyConfig, VenueEconomicsConfig
from bot.strategies.opportunity_engine import (
    OpportunityDecision,
    OpportunityEngineConfig,
    evaluate,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


ABLATION_CONFIGS: dict[str, dict[str, bool]] = {
    "BASELINE": {
        "regime_engine_enabled": False,
        "adverse_selection_enabled": False,
        "outcome_learning_enabled": False,
        "execution_quality_enabled": False,
    },
    "PLUS_REGIME": {
        "regime_engine_enabled": True,
        "adverse_selection_enabled": False,
        "outcome_learning_enabled": False,
        "execution_quality_enabled": False,
    },
    "PLUS_ADVERSE": {
        "regime_engine_enabled": False,
        "adverse_selection_enabled": True,
        "outcome_learning_enabled": False,
        "execution_quality_enabled": False,
    },
    "PLUS_EXECUTION": {
        "regime_engine_enabled": False,
        "adverse_selection_enabled": False,
        "outcome_learning_enabled": False,
        "execution_quality_enabled": True,
    },
    "PLUS_LEARNING": {
        "regime_engine_enabled": False,
        "adverse_selection_enabled": False,
        "outcome_learning_enabled": True,
        "execution_quality_enabled": False,
    },
    "PHASE2_FULL": {
        "regime_engine_enabled": True,
        "adverse_selection_enabled": True,
        "outcome_learning_enabled": True,
        "execution_quality_enabled": True,
    },
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


@dataclass
class ReplayCandidate:
    ts: datetime
    symbol: str
    venue: str
    side: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    marks: list[Decimal]


@dataclass
class AblationMetrics:
    label: str
    candidates: int = 0
    high_quality: int = 0
    reduced: int = 0
    rejected: int = 0
    sum_score: Decimal = _ZERO
    sum_size_mult: Decimal = _ZERO
    sum_estimated_net: Decimal = _ZERO
    sum_adverse: Decimal = _ZERO
    adverse_samples: int = 0
    toxic_proxy: int = 0
    resting_would_cancel: int = 0
    resting_hold: int = 0

    def record(
        self,
        *,
        decision: OpportunityDecision,
        score: Decimal,
        size_mult: Decimal,
        estimated_net: Decimal,
        adverse: Decimal | None = None,
        resting_action: RestingOrderAction | None = None,
    ) -> None:
        self.candidates += 1
        if decision == OpportunityDecision.HIGH_QUALITY:
            self.high_quality += 1
        elif decision == OpportunityDecision.REDUCED:
            self.reduced += 1
        else:
            self.rejected += 1
        self.sum_score += score
        self.sum_size_mult += size_mult
        if decision != OpportunityDecision.REJECT:
            self.sum_estimated_net += estimated_net * size_mult
        if adverse is not None:
            self.adverse_samples += 1
            self.sum_adverse += adverse
            if adverse >= Decimal("0.65"):
                self.toxic_proxy += 1
        if resting_action is not None:
            if resting_action in {RestingOrderAction.CANCEL, RestingOrderAction.EXPIRE}:
                self.resting_would_cancel += 1
            else:
                self.resting_hold += 1

    def snapshot(self) -> dict[str, Any]:
        n = self.candidates or 1
        accepted = self.high_quality + self.reduced
        return {
            "label": self.label,
            "candidates": self.candidates,
            "accepted": accepted,
            "high_quality": self.high_quality,
            "reduced": self.reduced,
            "rejected": self.rejected,
            "accept_rate": str((Decimal(accepted) / Decimal(n)).quantize(Decimal("0.001"))),
            "reject_rate": str((Decimal(self.rejected) / Decimal(n)).quantize(Decimal("0.001"))),
            "avg_score": str((self.sum_score / n).quantize(Decimal("0.1"))),
            "avg_size_multiplier": str((self.sum_size_mult / n).quantize(Decimal("0.001"))),
            "estimated_net_eur": str(self.sum_estimated_net.quantize(Decimal("0.01"))),
            "avg_adverse_score": str(
                (self.sum_adverse / self.adverse_samples).quantize(Decimal("0.001"))
            )
            if self.adverse_samples
            else None,
            "toxic_proxy_count": self.toxic_proxy,
            "resting_would_cancel": self.resting_would_cancel,
            "resting_hold": self.resting_hold,
        }


def load_audit_candidates(path: Path) -> list[ReplayCandidate]:
    """Extract chronological buy candidates from live audit — no look-ahead marks."""
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
                side=side,
                quantity=qty,
                price=price,
                notional=notional,
                marks=marks,
            )
        )
        mark_series.setdefault(symbol, []).append(price)
    out.sort(key=lambda c: c.ts)
    return out


def _engine_config(flags: dict[str, bool]) -> OpportunityEngineConfig:
    return OpportunityEngineConfig(
        enabled=True,
        min_opportunity_score=Decimal("55"),
        reduced_opportunity_score=Decimal("70"),
        high_quality_score=Decimal("80"),
        **flags,
    )


def _profitability_stub(notional: Decimal, net_pct: Decimal = Decimal("0.008")) -> ProfitabilityResult:
    net = notional * net_pct
    return ProfitabilityResult(
        opportunity_id=uuid4(),
        gross_profit_usd=net + notional * Decimal("0.002"),
        fees_usd=notional * Decimal("0.001"),
        slippage_usd=notional * Decimal("0.0005"),
        funding_usd=_ZERO,
        execution_buffer_usd=notional * Decimal("0.0005"),
        net_profit_usd=net,
        net_return=net_pct,
        is_profitable=True,
        trade_allowed=True,
    )


def run_ablation(candidates: Iterable[ReplayCandidate]) -> dict[str, AblationMetrics]:
    metrics = {label: AblationMetrics(label=label) for label in ABLATION_CONFIGS}
    entry_cfg = EntryQualityConfig(enabled=False)
    cap_cfg = CapitalEfficiencyConfig(enabled=True)
    ven_cfg = VenueEconomicsConfig(enabled=True)

    candidate_list = list(candidates)
    for cand in candidate_list:
        opp = TradeOpportunity(
            strategy_name="maker_inventory",
            symbol=cand.symbol,
            side=OpportunitySide.BUY,
            quantity=cand.quantity,
            entry_price=cand.price,
            metadata={"buy_exchange": cand.venue, "venue": cand.venue, "net_profit_eur": str(cand.notional * Decimal("0.008"))},
            entry_fee_role=FeeRole.MAKER,
        )
        prof = _profitability_stub(cand.notional)
        marks = cand.marks if len(cand.marks) >= 2 else cand.marks + [cand.price] * max(0, 6 - len(cand.marks))

        adv = assess_adverse_selection(
            marks=marks,
            side=OpportunitySide.BUY,
            order_price=cand.price,
        )
        resting = assess_resting_order(
            side="buy",
            order_price=cand.price,
            age_sec=20.0,
            marks=marks,
            adverse=adv,
            expected_net_eur=prof.net_profit_usd,
            opportunity_score=Decimal("70"),
            observation_mode=False,
        )

        for label, flags in ABLATION_CONFIGS.items():
            cfg = _engine_config(flags)
            assessment = evaluate(
                opportunity=opp,
                profitability=prof,
                marks=marks,
                entry_config=entry_cfg,
                capital_config=cap_cfg,
                venue_config=ven_cfg,
                engine_config=cfg,
                candidate_count=len(candidate_list),
            )
            metrics[label].record(
                decision=assessment.decision,
                score=assessment.opportunity_score,
                size_mult=assessment.recommended_size_multiplier,
                estimated_net=assessment.expected_net_profit_eur,
                adverse=assessment.adverse_selection_score or adv.adverse_selection_score,
                resting_action=resting.action if flags.get("adverse_selection_enabled") else None,
            )
    return metrics


def compare_verdict(results: dict[str, AblationMetrics]) -> dict[str, Any]:
    baseline = results["BASELINE"].snapshot()
    full = results["PHASE2_FULL"].snapshot()
    b_net = _d(baseline.get("estimated_net_eur"))
    f_net = _d(full.get("estimated_net_eur"))
    b_reject = _d(baseline.get("reject_rate"))
    f_reject = _d(full.get("reject_rate"))
    b_toxic = int(baseline.get("toxic_proxy_count") or 0)
    f_toxic = int(full.get("toxic_proxy_count") or 0)
    net_improved = f_net >= b_net * Decimal("0.95")
    toxic_reduced = f_toxic <= b_toxic
    reject_not_excessive = f_reject <= b_reject + Decimal("0.15")
    activate = net_improved and toxic_reduced and reject_not_excessive
    return {
        "baseline_net_eur": str(b_net),
        "phase2_net_eur": str(f_net),
        "net_delta_eur": str((f_net - b_net).quantize(Decimal("0.01"))),
        "baseline_reject_rate": str(b_reject),
        "phase2_reject_rate": str(f_reject),
        "baseline_toxic_proxy": b_toxic,
        "phase2_toxic_proxy": f_toxic,
        "net_improved": net_improved,
        "toxic_reduced": toxic_reduced,
        "reject_not_excessive": reject_not_excessive,
        "recommend_activate_execution": activate,
        "reason": (
            "Phase2 improves or preserves NET while reducing toxic proxy"
            if activate
            else "Phase2 does not meet activation criteria — keep observation mode"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execution intelligence ablation replay")
    parser.add_argument("source", type=Path, help="live_audit.jsonl path")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/execution_intelligence_ablation.json"),
    )
    parser.add_argument("--limit", type=int, default=0, help="Max candidates (0=all)")
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"Source not found: {args.source}", file=sys.stderr)
        return 1

    candidates = load_audit_candidates(args.source)
    if args.limit > 0:
        candidates = candidates[: args.limit]
    if not candidates:
        print("No candidates found", file=sys.stderr)
        return 1

    results = run_ablation(candidates)
    report = {
        "source": str(args.source),
        "candidate_count": len(candidates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configs": {label: m.snapshot() for label, m in results.items()},
        "verdict": compare_verdict(results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["verdict"], indent=2))
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
