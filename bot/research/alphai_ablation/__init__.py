"""Shadow ablation for AlphaI feature components.

Usage:
  python -m bot.research.alphai_ablation data/live_audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from bot.core.enums import FeeRole, OpportunitySide
from bot.core.models import ProfitabilityResult, TradeOpportunity
from bot.integrations.alphai.features import AlphaIFeatureConfig, compute_alphai_feature
from bot.integrations.alphai.signals import AlphaITradingSignals, build_trading_signals
from bot.integrations.alphai.parse import AlphaIRegimeState
from bot.strategies.entry_quality import EntryQualityConfig
from bot.strategies.opportunity_economics import CapitalEfficiencyConfig, VenueEconomicsConfig
from bot.strategies.opportunity_engine import (
    OpportunityDecision,
    OpportunityEngineConfig,
    evaluate,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")

ABLATIONS: dict[str, dict[str, bool]] = {
    "BASELINE": {
        "alphai_feature_enabled": False,
        "block_only": False,
        "size_boost": False,
        "exit_urgency": False,
        "adverse_timing": False,
    },
    "PLUS_BLOCK_ONLY": {
        "alphai_feature_enabled": True,
        "block_only": True,
        "size_boost": False,
        "exit_urgency": False,
        "adverse_timing": False,
    },
    "PLUS_SIZE_BOOST": {
        "alphai_feature_enabled": True,
        "block_only": False,
        "size_boost": True,
        "exit_urgency": False,
        "adverse_timing": False,
    },
    "PLUS_EXIT_URGENCY": {
        "alphai_feature_enabled": True,
        "block_only": False,
        "size_boost": False,
        "exit_urgency": True,
        "adverse_timing": False,
    },
    "PLUS_ADVERSE_TIMING": {
        "alphai_feature_enabled": True,
        "block_only": False,
        "size_boost": False,
        "exit_urgency": False,
        "adverse_timing": True,
    },
    "ALPHAI_FULL": {
        "alphai_feature_enabled": True,
        "block_only": True,
        "size_boost": True,
        "exit_urgency": True,
        "adverse_timing": True,
    },
}


@dataclass
class Metrics:
    label: str
    candidates: int = 0
    accepted: int = 0
    rejected: int = 0
    sum_est_net: Decimal = _ZERO
    sum_size: Decimal = _ZERO
    waits: int = 0
    blocks: int = 0

    def snapshot(self) -> dict[str, Any]:
        n = self.candidates or 1
        return {
            "label": self.label,
            "candidates": self.candidates,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "accept_rate": str((Decimal(self.accepted) / Decimal(n)).quantize(Decimal("0.001"))),
            "estimated_net_eur": str(self.sum_est_net.quantize(Decimal("0.01"))),
            "avg_size_mult": str((self.sum_size / n).quantize(Decimal("0.001"))),
            "adverse_waits": self.waits,
            "blocks": self.blocks,
            "counterfactual": True,
        }


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


def load_candidates(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mark_series: dict[str, list[Decimal]] = {}
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
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            continue
        price = notional / qty
        marks = list(mark_series.get(symbol, []))
        out.append(
            {
                "symbol": symbol,
                "venue": str(payload.get("venue") or "bitvavo").lower(),
                "qty": qty,
                "price": price,
                "notional": notional,
                "marks": marks,
            }
        )
        mark_series.setdefault(symbol, []).append(price)
    return out


def _base(symbol: str) -> str:
    s = symbol.upper()
    for q in ("EUR", "USDT", "USDC", "USD"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def _demo_signals(candidates: list[dict[str, Any]]) -> AlphaITradingSignals:
    """Build a synthetic signal set from symbols present — decision-time only."""
    bases = sorted({_base(c["symbol"]) for c in candidates})
    picks = bases[: min(5, len(bases))]
    avoid = bases[-2:] if len(bases) > 6 else []
    bullish = picks[:2]
    scores = {b: float(5.0 - i * 0.4) for i, b in enumerate(picks)}
    return build_trading_signals(
        AlphaIRegimeState(
            enabled=True,
            bullish_bases=frozenset(bullish),
            blocked_bases=frozenset(),
            macro_reduce_only=False,
        ),
        {
            "picks": [{"base": b, "score": scores[b]} for b in picks],
            "avoid": [{"base": b, "score": -2.0} for b in avoid],
            "watch": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def run_ablation(path: Path) -> dict[str, Any]:
    candidates = load_candidates(path)
    if not candidates:
        return {"error": "no_candidates", "path": str(path)}
    signals = _demo_signals(candidates)
    results: dict[str, Any] = {}

    for label, flags in ABLATIONS.items():
        m = Metrics(label=label)
        cfg = OpportunityEngineConfig(
            enabled=True,
            alphai_feature_enabled=bool(flags["alphai_feature_enabled"]),
            weight_alphai=Decimal("0.06"),
            adverse_selection_enabled=bool(flags["adverse_timing"]),
        )
        feat_cfg = AlphaIFeatureConfig(
            enabled=True,
            shadow_only=not flags.get("adverse_timing"),
            auto_apply=False,
        )
        for cand in candidates:
            base = _base(cand["symbol"])
            feat = compute_alphai_feature(
                base,
                signals,
                adverse_score=Decimal("0.50") if flags["adverse_timing"] else Decimal("0.20"),
                signal_age_hours_value=Decimal("2"),
                config=feat_cfg,
            )
            if flags["block_only"] and (
                base in signals.avoid_bases or base in signals.blocked_bases
            ):
                m.candidates += 1
                m.rejected += 1
                m.blocks += 1
                continue

            opp = TradeOpportunity(
                strategy_name="maker_inventory",
                symbol=cand["symbol"],
                side=OpportunitySide.BUY,
                quantity=cand["qty"],
                entry_price=cand["price"],
                metadata={
                    "buy_exchange": cand["venue"],
                    "net_profit_eur": str(cand["notional"] * Decimal("0.008")),
                },
                entry_fee_role=FeeRole.MAKER,
            )
            net = cand["notional"] * Decimal("0.008")
            prof = ProfitabilityResult(
                opportunity_id=uuid4(),
                gross_profit_usd=net + cand["notional"] * Decimal("0.002"),
                fees_usd=cand["notional"] * Decimal("0.001"),
                slippage_usd=cand["notional"] * Decimal("0.0005"),
                funding_usd=_ZERO,
                execution_buffer_usd=cand["notional"] * Decimal("0.0005"),
                net_profit_usd=net,
                net_return=Decimal("0.008"),
                is_profitable=True,
                trade_allowed=True,
            )
            marks = cand["marks"] or [cand["price"]] * 6
            assessment = evaluate(
                opportunity=opp,
                profitability=prof,
                marks=marks,
                entry_config=EntryQualityConfig(enabled=False),
                capital_config=CapitalEfficiencyConfig(enabled=True),
                venue_config=VenueEconomicsConfig(enabled=True),
                engine_config=cfg,
                alphai_signals=signals if flags["alphai_feature_enabled"] else None,
                alphai_feature_config=feat_cfg,
                alphai_signal_age_hours=Decimal("2"),
            )
            m.candidates += 1
            size = assessment.recommended_size_multiplier
            if flags["size_boost"] and signals.is_top_pick(base):
                size = min(_ONE, size * Decimal("1.10"))
            if flags["adverse_timing"] and feat.entry_timing == "WAIT":
                m.waits += 1
                m.rejected += 1
                continue
            if assessment.decision == OpportunityDecision.REJECT:
                m.rejected += 1
            else:
                m.accepted += 1
                m.sum_est_net += assessment.expected_net_profit_eur * size
            m.sum_size += size
            if flags["exit_urgency"] and feat.exit_urgency:
                # Proxy: earlier harvest slightly reduces hold capital lock → keep NET
                pass
        results[label] = m.snapshot()

    baseline = results.get("BASELINE", {})
    full = results.get("ALPHAI_FULL", baseline)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "historical_samples": len(candidates),
        "variants": results,
        "comparison": {
            "baseline_est_net": baseline.get("estimated_net_eur"),
            "full_est_net": full.get("estimated_net_eur"),
            "note": "Counterfactual / estimated only — not realized PnL. auto_apply=false.",
        },
        "recommendation": {
            "live_status": "SHADOW",
            "auto_apply": False,
            "next_step": "Collect live attribution samples before enabling adverse WAIT rejects.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AlphaI feature ablation")
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/alphai_ablation.json"),
    )
    args = parser.parse_args(argv)
    report = run_ablation(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report.get("comparison", {}), indent=2))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
