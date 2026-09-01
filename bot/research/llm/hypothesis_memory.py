"""Append-only hypothesis registry — failed hypotheses are permanent evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.llm.schemas import HypothesisProposal

HYPOTHESIS_STATUSES = (
    "PROPOSED",
    "ACCEPTED_FOR_RESEARCH",
    "RUNNING",
    "REJECTED",
    "DATA_UNSUPPORTED",
    "NO_SIGNAL",
    "INSUFFICIENT_SAMPLE",
    "OOS_FAILED",
    "COST_NEGATIVE",
    "EXECUTION_NEGATIVE",
    "UNSTABLE",
    "PAPER_CANDIDATE",
    "DUPLICATE",
    "INVALID",
    "CANDIDATE",
)


def _normalize_text(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t


def mechanism_fingerprint(mechanism: str, family: str, features: list[str]) -> str:
    payload = {
        "mechanism": _normalize_text(mechanism),
        "family": family,
        "features": sorted(features),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def token_jaccard(a: str, b: str) -> float:
    ta = set(_normalize_text(a).split())
    tb = set(_normalize_text(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class HypothesisRegistry:
    """Append-only JSONL registry. Never overwrite prior failures."""

    def __init__(self, path: Path | str = "data/research_hypotheses/registry.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def list_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {
            **record,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row

    def next_id(self) -> str:
        n = len(self.list_all()) + 1
        return f"H-{n:04d}"

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.list_all():
            st = str(row.get("status") or "")
            counts[st] = counts.get(st, 0) + 1
        return counts

    def find_duplicates(
        self,
        proposal: HypothesisProposal,
        *,
        similarity_threshold: float = 0.82,
    ) -> list[dict[str, Any]]:
        fp = mechanism_fingerprint(
            proposal.mechanism, proposal.strategy_family, list(proposal.required_features)
        )
        hits: list[dict[str, Any]] = []
        for row in self.list_all():
            if row.get("mechanism_fingerprint") == fp:
                hits.append({**row, "match": "fingerprint"})
                continue
            if row.get("strategy_family") != proposal.strategy_family:
                continue
            sim = token_jaccard(str(row.get("mechanism") or ""), proposal.mechanism)
            feats_a = set(row.get("required_features") or [])
            feats_b = set(proposal.required_features)
            feat_overlap = (
                len(feats_a & feats_b) / len(feats_a | feats_b) if (feats_a or feats_b) else 0.0
            )
            if sim >= similarity_threshold and feat_overlap >= 0.5:
                hits.append({**row, "match": "similarity", "similarity": sim})
        return hits

    def register_proposal(
        self,
        proposal: HypothesisProposal,
        *,
        source: str = "llm",
        status: str = "PROPOSED",
        parent_hypothesis_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        hid = self.next_id()
        record = {
            "hypothesis_id": hid,
            "parent_hypothesis_id": parent_hypothesis_id,
            "title": proposal.title,
            "mechanism": proposal.mechanism,
            "strategy_family": proposal.strategy_family,
            "required_features": list(proposal.required_features),
            "required_horizons_ms": list(proposal.required_horizons_ms),
            "signal_concept": proposal.signal_concept,
            "economic_mechanism": proposal.economic_mechanism,
            "execution_assumption": proposal.execution_assumption,
            "information_value": proposal.information_value,
            "what_we_learn_if_fails": proposal.what_we_learn_if_fails,
            "difference_from_prior_failures": proposal.difference_from_prior_failures,
            "not_equivalent_to": list(proposal.not_equivalent_to),
            "priority": proposal.priority,
            "created_at": datetime.now(UTC).isoformat(),
            "source": source,
            "status": status,
            "related_experiments": [],
            "prior_hypotheses_considered": list(proposal.not_equivalent_to),
            "difference_from_prior_hypotheses": proposal.difference_from_prior_failures,
            "evidence_summary": "",
            "final_reason": "",
            "mechanism_fingerprint": mechanism_fingerprint(
                proposal.mechanism,
                proposal.strategy_family,
                list(proposal.required_features),
            ),
            "dry_run": dry_run,
        }
        if dry_run:
            return record
        return self.append(record)

    def update_status_append(
        self,
        hypothesis_id: str,
        *,
        status: str,
        evidence_summary: str = "",
        final_reason: str = "",
        related_experiment: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Append a status-change record (append-only; never mutate prior lines)."""
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"invalid status={status}")
        row = {
            "hypothesis_id": hypothesis_id,
            "event": "status_update",
            "status": status,
            "evidence_summary": evidence_summary,
            "final_reason": final_reason,
            "related_experiment": related_experiment,
            "created_at": datetime.now(UTC).isoformat(),
            "source": "system",
        }
        if dry_run:
            return row
        return self.append(row)
