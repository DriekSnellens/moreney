"""Register parent REJECTED records and independent child hypotheses.

Does not implement new strategy families. Does not inherit parent PnL.
"""

from __future__ import annotations

from typing import Any

from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.llm.schemas import HypothesisProposal

_PARENT_META = {
    "cross_venue_dislocation": {
        "title": "cross_venue_dislocation (tournament parent)",
        "mechanism": (
            "Cross-venue mid dislocation mean-reverts over 500–5000ms. "
            "Rejected at STABILITY_CONCENTRATION."
        ),
        "features": ["dislocation_bps", "spread"],
    },
    "short_horizon_mean_reversion": {
        "title": "short_horizon_mean_reversion (tournament parent)",
        "mechanism": (
            "Venue mid deviation from cross-venue fair mean-reverts over 500–5000ms. "
            "Rejected at STABILITY_CONCENTRATION."
        ),
        "features": ["deviation_from_cross_mid", "forward_return"],
    },
}


def _proposal(sid: str, *, title: str, mechanism: str, features: list[str], extra: str) -> HypothesisProposal:
    return HypothesisProposal(
        title=title[:200],
        mechanism=mechanism[:2000],
        why_now="Concentration forensics on a STABILITY-rejected parent.",
        not_equivalent_to=[],
        difference_from_prior_failures=extra[:2000],
        strategy_family=sid,
        required_features=features,
        required_horizons_ms=[500, 1000, 2000, 5000],
        signal_concept=mechanism[:1000],
        expected_failure_modes=["artifact_not_tradable", "OOS_FAILED", "UNSTABLE"],
        economic_mechanism=mechanism[:1000],
        execution_assumption="trade_through_conservative",
        information_value="HIGH",
        priority=2,
        what_we_learn_if_fails=(
            "The apparent concentration mechanism does not produce an independent OOS edge."
        ),
    )


def _latest_id(registry: HypothesisRegistry, sid: str, *, parent: bool) -> str | None:
    hid = None
    for row in registry.list_all():
        if row.get("event") == "annotate":
            continue
        if row.get("strategy_family") != sid:
            continue
        if row.get("source") != "concentration_forensics":
            continue
        if parent and not row.get("parent_hypothesis_id") and row.get("status") == "UNSTABLE":
            hid = row.get("hypothesis_id")
        if not parent and row.get("parent_hypothesis_id") and row.get("status") == "PROPOSED":
            hid = row.get("hypothesis_id")
    return hid


def register_forensics_hypotheses(
    analyzed: dict[str, Any],
    *,
    registry: HypothesisRegistry | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    registry = registry or HypothesisRegistry()
    created: list[str] = []
    parents: dict[str, str] = {}
    skipped: list[dict[str, Any]] = []

    for sid, block in analyzed.items():
        meta = _PARENT_META[sid]
        existing = _latest_id(registry, sid, parent=True)
        if existing:
            parents[sid] = existing
        else:
            prop = _proposal(
                sid,
                title=meta["title"],
                mechanism=meta["mechanism"],
                features=meta["features"],
                extra="Parent remains REJECTED. Forensic analysis only.",
            )
            rec = registry.register_proposal(
                prop,
                source="concentration_forensics",
                status="UNSTABLE",
                dry_run=dry_run,
            )
            if not dry_run:
                registry.append(
                    {
                        "hypothesis_id": rec["hypothesis_id"],
                        "event": "annotate",
                        "role": "parent_rejected",
                        "final_reason": "STABILITY_CONCENTRATION",
                        "status": "UNSTABLE",
                        "source": "concentration_forensics",
                    }
                )
            parents[sid] = rec["hypothesis_id"]

        cls = block.get("CONCENTRATION_CLASS")
        if cls not in {"SYMBOL_SPECIFIC", "VENUE_SPECIFIC", "REGIME_DEPENDENT"}:
            skipped.append({"strategy": sid, "reason": cls, "action": "no_child_hypothesis"})
            continue
        if _latest_id(registry, sid, parent=False):
            skipped.append({"strategy": sid, "reason": "child_already_registered"})
            continue

        child = _child_proposal(sid, block, parents[sid])
        rec = registry.register_proposal(
            child,
            source="concentration_forensics",
            status="PROPOSED",
            parent_hypothesis_id=parents[sid],
            dry_run=dry_run,
        )
        if not dry_run:
            registry.append(
                {
                    "hypothesis_id": rec["hypothesis_id"],
                    "event": "annotate",
                    "role": "forensics_child",
                    "parent_hypothesis_id": parents[sid],
                    "status": "PROPOSED",
                    "inherits_parent_pnl": False,
                    "source": "concentration_forensics",
                }
            )
        created.append(rec["hypothesis_id"])

    return {
        "parents": parents,
        "created_ids": [h for h in created if h],
        "skipped": skipped,
        "inherits_parent_pnl": False,
    }


def _child_proposal(sid: str, block: dict[str, Any], parent_id: str) -> HypothesisProposal:
    cls = block.get("CONCENTRATION_CLASS")
    source = str(block.get("CONCENTRATION_SOURCE") or "")
    top = (block.get("top_contributors") or {}).get("top_symbol") or {}
    if cls == "SYMBOL_SPECIFIC":
        title = f"{sid}_symbol_{top.get('group') or 'specific'}"
        mechanism = (
            f"Independent test of {sid} restricted to the dominating symbol group "
            f"identified by forensics ({top.get('group')}). Causal symbol identity only."
        )
        extra = f"Parent {parent_id} rejected for STABILITY. New test does not inherit PnL."
        features = (
            ["dislocation_bps", "spread"]
            if sid == "cross_venue_dislocation"
            else ["deviation_from_cross_mid", "forward_return"]
        )
    elif cls == "VENUE_SPECIFIC":
        title = f"{sid}_venue_pair_specific"
        mechanism = (
            f"Independent test of {sid} on the dominating venue pair as a stated mechanism, "
            "not as a post-hoc deletion of losers."
        )
        extra = f"Parent {parent_id} remains REJECTED."
        features = ["dislocation_bps", "route"] if sid == "cross_venue_dislocation" else ["deviation_from_cross_mid"]
    else:
        title = f"{sid}_under_pretrade_regime"
        mechanism = (
            f"Independent regime-gated test of {sid}. Gate uses only pre-trade observables "
            f"from forensics: {source}"
        )
        extra = (
            f"Parent {parent_id} remains REJECTED. Fresh DEV/OOS. "
            "No inherited profitability."
        )
        if sid == "cross_venue_dislocation":
            features = ["dislocation_bps", "spread", "quote_staleness", "event_rate"]
        else:
            features = ["deviation_from_cross_mid", "spread", "quote_staleness", "event_rate"]
    prop = _proposal(
        sid,
        title=title.replace(" ", "_")[:200],
        mechanism=mechanism,
        features=features,
        extra=extra,
    )
    # pydantic model is frozen-by-construction via extra=forbid; rebuild with not_equivalent
    return HypothesisProposal(
        **{
            **prop.model_dump(),
            "not_equivalent_to": [parent_id],
            "difference_from_prior_failures": extra,
        }
    )
