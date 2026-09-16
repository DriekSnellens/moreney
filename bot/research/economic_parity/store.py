"""Persist candidate-level parity forensics."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.models import TradeOpportunity
from bot.research.economic_parity.evaluator import (
    evaluate_frozen_research_economics,
    evaluate_live_profitability_economics,
)
from bot.research.economic_parity.formulas import (
    LIVE_PROFITABILITY_FORMULA,
    RESEARCH_PROFITABILITY_FORMULA,
    breakeven_dislocation_bps,
)

_DEFAULT_ROOT = Path("data/research/economic_parity")


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {k: None for k in ("p0", "p10", "p25", "p50", "p75", "p90", "p100")}
    ordered = sorted(values)

    def _pct(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        idx = (len(ordered) - 1) * p
        lo = int(idx)
        hi = min(lo + 1, len(ordered) - 1)
        frac = idx - lo
        return ordered[lo] * (1 - frac) + ordered[hi] * frac

    return {
        "p0": ordered[0],
        "p10": _pct(0.10),
        "p25": _pct(0.25),
        "p50": _pct(0.50),
        "p75": _pct(0.75),
        "p90": _pct(0.90),
        "p100": ordered[-1],
    }


@dataclass
class EconomicParityStore:
    """Accumulates per-candidate dual-world records."""

    root: Path = field(default_factory=lambda: _DEFAULT_ROOT)
    records: list[dict[str, Any]] = field(default_factory=list)
    _seen_ids: set[str] = field(default_factory=set, repr=False)

    def record(
        self,
        opportunity: TradeOpportunity,
        *,
        research: Any,
        live: Any,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        oid = str(opportunity.id)
        if oid in self._seen_ids:
            return self.records[-1]
        self._seen_ids.add(oid)
        parity_mismatch = research.profitable != live.profitable
        row = {
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
            "opportunity_id": oid,
            "symbol": opportunity.symbol,
            "route": research.route,
            "strategy_fingerprint": research.strategy_fingerprint,
            "dislocation_bps": research.dislocation_bps,
            "notional_eur": research.notional_eur,
            "leader_bid": research.leader_bid,
            "leader_ask": research.leader_ask,
            "follower_bid": research.follower_bid,
            "follower_ask": research.follower_ask,
            "research_expected_net": research.expected_net_eur,
            "live_expected_net": live.expected_net_eur,
            "delta_expected_net": live.expected_net_eur - research.expected_net_eur,
            "research_profitable": research.profitable,
            "live_profitable": live.profitable,
            "parity_mismatch": parity_mismatch,
            "breakeven_dislocation_bps": research.breakeven_dislocation_bps,
            "research_rejection_reason": research.rejection_reason,
            "live_rejection_reason": live.rejection_reason,
            "research": research.as_dict(),
            "live": live.as_dict(),
        }
        self.records.append(row)
        self._append_jsonl(row)
        return row

    def _append_jsonl(self, row: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "candidates.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def summary(self) -> dict[str, Any]:
        n = len(self.records)
        research_profitable = sum(1 for r in self.records if r["research_profitable"])
        live_profitable = sum(1 for r in self.records if r["live_profitable"])
        mismatches = sum(1 for r in self.records if r["parity_mismatch"])
        dis = [float(r["dislocation_bps"]) for r in self.records]
        nets = [float(r["research_expected_net"]) for r in self.records]
        be = [float(r["breakeven_dislocation_bps"]) for r in self.records]
        gross = [float(r["research"]["gross_eur"]) for r in self.records]
        fees = [float(r["research"]["fees_eur"]) for r in self.records]
        slip = [float(r["research"]["slippage_eur"]) for r in self.records]
        adverse = [float(r["research"]["adverse_eur"]) for r in self.records]

        rejection_counts: dict[str, int] = {}
        for r in self.records:
            key = str(r["research_rejection_reason"])
            rejection_counts[key] = rejection_counts.get(key, 0) + 1
        top_rejection = max(rejection_counts, key=rejection_counts.get) if rejection_counts else None

        buckets = _diagnostic_buckets(self.records)

        verdict = "ECONOMIC_PARITY_PASS"
        divergence: str | None = None
        if mismatches > 0:
            verdict = "ECONOMIC_PARITY_FAIL"
            divergence = _classify_divergence(self.records)

        return {
            "ECONOMIC_PARITY": verdict,
            "ROOT_CAUSE": divergence,
            "RESEARCH_PROFITABILITY_FORMULA": RESEARCH_PROFITABILITY_FORMULA,
            "LIVE_PROFITABILITY_FORMULA": LIVE_PROFITABILITY_FORMULA,
            "LIVE_CANDIDATES_ANALYZED": n,
            "RESEARCH_PROFITABLE": research_profitable,
            "LIVE_PROFITABLE": live_profitable,
            "PARITY_MISMATCHES": mismatches,
            "MEDIAN_DISLOCATION_BPS": statistics.median(dis) if dis else None,
            "MEDIAN_BREAKEVEN_BPS": breakeven_dislocation_bps(),
            "MEDIAN_EXPECTED_NET": statistics.median(nets) if nets else None,
            "TOP_REJECTION_REASON": top_rejection,
            "rejection_counts": rejection_counts,
            "percentiles": {
                "dislocation_bps": _percentiles(dis),
                "expected_net_eur": _percentiles(nets),
                "breakeven_dislocation_bps": _percentiles(be),
                "gross_eur": _percentiles(gross),
                "fees_eur": _percentiles(fees),
                "slippage_eur": _percentiles(slip),
                "adverse_eur": _percentiles(adverse),
            },
            "diagnostic_buckets": buckets,
            "candidates_created": n,
            "research_economics_profitable": research_profitable,
            "live_economics_profitable": live_profitable,
            "parity_mismatches": mismatches,
            "median_dislocation_bps": statistics.median(dis) if dis else None,
            "median_breakeven_bps": breakeven_dislocation_bps(),
            "median_expected_net": statistics.median(nets) if nets else None,
            "top_rejection_causes": rejection_counts,
        }

    def flush_report(self) -> Path:
        summary = self.summary()
        self.root.mkdir(parents=True, exist_ok=True)
        out = self.root / "report.json"
        out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return out


def _diagnostic_buckets(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges = [
        (40.0, 50.0),
        (50.0, 75.0),
        (75.0, 100.0),
        (100.0, float("inf")),
    ]
    out: list[dict[str, Any]] = []
    for lo, hi in edges:
        bucket = [r for r in records if lo <= float(r["dislocation_bps"]) < hi]
        if not bucket:
            continue
        nets = [float(r["research_expected_net"]) for r in bucket]
        label = f"{lo:.0f}–{hi:.0f} bps" if hi != float("inf") else f"{lo:.0f}+ bps"
        out.append(
            {
                "bucket": label,
                "count": len(bucket),
                "median_expected_net": statistics.median(nets),
            }
        )
    return out


def _classify_divergence(records: list[dict[str, Any]]) -> str:
    """Heuristic divergence tag from mismatch patterns."""
    mism = [r for r in records if r["parity_mismatch"]]
    if not mism:
        return "NONE"
    research_pos_live_neg = sum(
        1 for r in mism if r["research_profitable"] and not r["live_profitable"]
    )
    if research_pos_live_neg == len(mism):
        live_gross_neg = sum(1 for r in mism if float(r["live"]["gross_eur"]) <= 0)
        if live_gross_neg > len(mism) * 0.8:
            return "DIFFERENT_PRICE_SELECTION"
        return "OTHER"
    return "OTHER"
