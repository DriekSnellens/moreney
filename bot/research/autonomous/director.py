"""Autonomous research director — LLM proposes; tournament judges."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.core.config import Settings, get_settings
from bot.research.llm.budget import budget_from_settings
from bot.research.llm.context import build_research_context, load_tournament_summary
from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.llm.ollama import build_provider_from_settings
from bot.research.llm.provider import ResearchLLMProvider
from bot.research.llm.researcher import propose_and_filter
from bot.research.llm.result_analyst import analyze_results
from bot.research.llm.safety import assert_no_shell_tools, context_is_oos_blind
from bot.research.tournament.engine import run_tournament


class ResearchDirector:
    """Bounded autonomous loop: propose → validate → tournament → analyze → stop."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider: ResearchLLMProvider | None = None,
        registry: HypothesisRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or build_provider_from_settings(self.settings)
        self.registry = registry or HypothesisRegistry()
        assert_no_shell_tools({})  # no tool surface by default

    def run(
        self,
        *,
        dataset_id: str | None = None,
        research_path: Path | str = "data/research_marketdata",
        readiness_report: Path | str = "data/market_data_research_report.json",
        dry_run: bool = False,
        llm_disabled: bool = False,
        analyze_existing: bool = False,
        max_hypotheses: int | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        settings = self.settings
        rounds = int(getattr(settings, "research_llm_max_rounds", 1) or 1)
        rounds = max(1, min(rounds, 3))

        prior = load_tournament_summary()
        used_exps = len(prior.get("scoreboard") or [])
        budget = budget_from_settings(settings, used_dataset_experiments=used_exps)
        if max_hypotheses is not None:
            budget.max_new_hypotheses_per_run = max(1, int(max_hypotheses))

        health = self.provider.health()
        llm_status = health.status
        autonomous = bool(getattr(settings, "research_llm_autonomous_enabled", False))
        llm_enabled = bool(getattr(settings, "research_llm_enabled", True)) and not llm_disabled

        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "LLM_STATUS": llm_status,
            "provider": health.provider,
            "model": health.model,
            "autonomous_mode": autonomous,
            "research_only": True,
            "dry_run": dry_run,
            "rounds_configured": rounds,
            "rounds_executed": 0,
            "hypotheses_proposed": 0,
            "rejected_duplicate": 0,
            "rejected_validator": 0,
            "accepted_for_experiment": 0,
            "experiments_completed": 0,
            "tournament": None,
            "llm_analysis": None,
            "analysis_label": "NON_AUTHORITATIVE_ANALYSIS",
            "multiple_testing_exposure": {},
            "performance": {},
            "PRODUCTION_TRADING_CHANGED": False,
            "execution_enabled": False,
        }

        # Build OOS-blind context (no untouched OOS raw)
        readiness = {}
        rp = Path(readiness_report)
        if rp.exists():
            try:
                readiness = (
                    json.loads(rp.read_text(encoding="utf-8")).get("J_horizon_readiness") or {}
                ).get("horizon_scores") or {}
            except json.JSONDecodeError:
                readiness = {}

        supported_h = set()
        for k, v in readiness.items():
            if v in {"READY", "READY_WITH_CAUTION"}:
                try:
                    supported_h.add(int(str(k).replace("LEAD_LAG_", "").replace("MS", "")))
                except ValueError:
                    pass

        context = build_research_context(
            dataset_id=dataset_id or prior.get("DATASET_ID"),
            data_duration=prior.get("DATA_DURATION"),
            venues=prior.get("VENUES") or (prior.get("CURRENT_DATASET") or {}).get("venues"),
            symbols=prior.get("SYMBOLS"),
            readiness=readiness,
            hypotheses=self.registry.list_all(),
            tournament_summary=prior,
            budget=budget.as_dict(),
            include_oos_result_summary=False,
        )
        if not context_is_oos_blind(context):
            raise RuntimeError("OOS blindness violated before proposal")

        proposal_info: dict[str, Any] = {
            "llm_status": "SKIPPED",
            "proposed": [],
            "rejected_duplicate": [],
            "rejected_validator": [],
            "accepted": [],
            "specs": [],
        }

        if analyze_existing and prior:
            # Skip proposal; analyze existing tournament only
            analysis, canonical, astatus = analyze_results(
                self.provider,
                result_summary=prior,
                budget_calls_remaining=budget.remaining_llm_calls(),
            )
            if analysis is not None:
                budget.record_llm_call()
            report["llm_analysis"] = analysis.model_dump() if analysis else None
            report["analysis_status"] = astatus
            report["canonical_verdicts_unchanged"] = canonical
            report["tournament"] = {
                "DATASET_ID": prior.get("DATASET_ID"),
                "scoreboard": prior.get("scoreboard"),
                "PAPER_CANDIDATES": prior.get("PAPER_CANDIDATES"),
            }
            report["rounds_executed"] = 1
            report["performance"] = {"seconds": time.perf_counter() - t0}
            self._persist(report)
            return report

        shadow_locked = False
        try:
            from bot.research.shadow_validation.protocol import (
                HYPOTHESIS_GENERATOR_ENABLED as _SHADOW_HYP_GEN,
                SHADOW_PAPER_VALIDATION_ACTIVE as _SHADOW_ACTIVE,
            )

            shadow_locked = bool(_SHADOW_ACTIVE) and not bool(_SHADOW_HYP_GEN)
        except Exception:
            shadow_locked = False

        if shadow_locked:
            proposal_info["llm_status"] = "LOCKED_SHADOW_PAPER_VALIDATION"
            report["SHADOW_PAPER_VALIDATION"] = "LOCKED"
            report["hypotheses_proposed"] = 0
        elif llm_enabled and health.available and (autonomous or dry_run or True):
            # Proposal allowed for research planning even when autonomous flag is false,
            # but tournament mutation only when autonomous or explicit non-dry research run.
            proposal_info = propose_and_filter(
                self.provider,
                context=context,
                registry=self.registry,
                budget=budget,
                supported_horizons=supported_h or None,
                dry_run=dry_run,
            )
            report["rounds_executed"] = 1
        elif not health.available:
            report["LLM_STATUS"] = health.status
            proposal_info["llm_status"] = health.status

        report["hypotheses_proposed"] = len(proposal_info.get("proposed") or [])
        report["rejected_duplicate"] = len(proposal_info.get("rejected_duplicate") or [])
        report["rejected_validator"] = len(proposal_info.get("rejected_validator") or [])
        report["accepted_for_experiment"] = len(proposal_info.get("accepted") or [])
        report["proposal"] = {
            "llm_status": proposal_info.get("llm_status"),
            "accepted_ids": [a.get("hypothesis_id") for a in proposal_info.get("accepted") or []],
            "rejected_duplicate": proposal_info.get("rejected_duplicate"),
            "rejected_validator": proposal_info.get("rejected_validator"),
            "specs": proposal_info.get("specs"),
            "observations": proposal_info.get("analysis_observations"),
        }

        tournament_result = None
        run_experiments = (
            not dry_run
            and autonomous
            and bool(getattr(settings, "research_llm_enabled", True))
            and not llm_disabled
        )
        # Also allow explicit research invocation to run tournament for accepted families
        # when autonomous is on. If autonomous is off, still may run deterministic
        # tournament alone without LLM — but here we only run when autonomous and accepted.
        if dry_run:
            report["experiments_completed"] = 0
            report["note"] = "dry-run: no tournament mutation"
        elif run_experiments or (
            not dry_run
            and proposal_info.get("accepted")
            and autonomous
        ):
            for rec in proposal_info.get("accepted") or []:
                if not dry_run:
                    self.registry.update_status_append(
                        rec["hypothesis_id"], status="RUNNING"
                    )
            tournament_result = run_tournament(
                research_path=research_path,
                readiness_report=readiness_report,
            )
            report["experiments_completed"] = len(tournament_result.get("scoreboard") or [])
            report["tournament"] = {
                "DATASET_ID": tournament_result.get("DATASET_ID"),
                "STATUS": tournament_result.get("STATUS"),
                "scoreboard": tournament_result.get("scoreboard"),
                "PAPER_CANDIDATES": tournament_result.get("PAPER_CANDIDATES"),
                "ALL_STRATEGIES_REJECTED": tournament_result.get("ALL_STRATEGIES_REJECTED"),
            }
            # Map family verdicts back to accepted hypotheses
            by_family = {
                str(r.get("STRATEGY")): r for r in (tournament_result.get("scoreboard") or [])
            }
            for rec in proposal_info.get("accepted") or []:
                fam = rec.get("strategy_family")
                row = by_family.get(str(fam)) or {}
                verdict = str(row.get("VERDICT") or "REJECTED")
                gate = row.get("FAILED_GATE")
                self.registry.update_status_append(
                    rec["hypothesis_id"],
                    status=verdict if verdict in {
                        "DATA_UNSUPPORTED",
                        "NO_SIGNAL",
                        "INSUFFICIENT_SAMPLE",
                        "IN_SAMPLE_ONLY",
                        "OOS_FAILED",
                        "COST_NEGATIVE",
                        "EXECUTION_NEGATIVE",
                        "UNSTABLE",
                        "PAPER_CANDIDATE",
                    } else "REJECTED",
                    evidence_summary=f"dev={row.get('DEV_SIGNALS')} oos={row.get('OOS_SIGNALS')}",
                    final_reason=f"gate={gate}",
                    related_experiment=tournament_result.get("DATASET_ID"),
                )

            # Post-freeze: LLM may see OOS RESULT SUMMARY only
            oos_summary = {
                "scoreboard": tournament_result.get("scoreboard"),
                "PAPER_CANDIDATES": tournament_result.get("PAPER_CANDIDATES"),
            }
            analysis, canonical, astatus = analyze_results(
                self.provider,
                result_summary={**tournament_result, **oos_summary},
                budget_calls_remaining=budget.remaining_llm_calls(),
            )
            if analysis is not None:
                budget.record_llm_call()
            report["llm_analysis"] = analysis.model_dump() if analysis else None
            report["analysis_status"] = astatus
            report["canonical_verdicts_unchanged"] = canonical
        elif not autonomous and not dry_run:
            # LLM may propose research notes but cannot create production strategies;
            # still allow reading existing tournament without mutation.
            report["note"] = (
                "RESEARCH_LLM_AUTONOMOUS_ENABLED=false — proposals recorded; "
                "tournament not auto-invoked"
            )
            if prior:
                report["tournament"] = {
                    "DATASET_ID": prior.get("DATASET_ID"),
                    "scoreboard": prior.get("scoreboard"),
                    "PAPER_CANDIDATES": prior.get("PAPER_CANDIDATES"),
                }

        # Multiple testing exposure
        all_h = [h for h in self.registry.list_all() if h.get("hypothesis_id") and not h.get("event")]
        report["multiple_testing_exposure"] = {
            "hypotheses_attempted": len(all_h),
            "parameter_combinations_evaluated": budget.used_parameter_combinations + used_exps,
            "oos_survivors": sum(
                1
                for r in ((report.get("tournament") or {}).get("scoreboard") or [])
                if r.get("VERDICT")
                in {"COST_NEGATIVE", "EXECUTION_NEGATIVE", "UNSTABLE", "PAPER_CANDIDATE"}
            ),
            "PAPER_CANDIDATES": len(
                ((report.get("tournament") or {}).get("PAPER_CANDIDATES") or [])
            ),
        }
        report["budget"] = budget.as_dict()
        report["performance"] = {
            "seconds": time.perf_counter() - t0,
            "llm_calls": budget.used_llm_calls,
            "context_bytes": len(json.dumps(context, default=str).encode("utf-8")),
        }
        # Hard stop: one round by default; never infinite loop
        assert report["rounds_executed"] <= rounds
        self._persist(report)
        return report

    def _persist(self, report: dict[str, Any]) -> None:
        out = Path("data/autonomous_research")
        out.mkdir(parents=True, exist_ok=True)
        path = out / "last_run.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        Path("data/autonomous_research_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
        )
