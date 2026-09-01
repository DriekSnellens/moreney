"""Autonomous local LLM research — fake provider, no Ollama required."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.core.config import Settings
from bot.research.autonomous.director import ResearchDirector
from bot.research.llm.budget import ExperimentBudget, budget_from_settings
from bot.research.llm.context import build_research_context, list_supported_catalog
from bot.research.llm.experiment_planner import StrategySpecValidator
from bot.research.llm.hypothesis_memory import HypothesisRegistry
from bot.research.llm.provider import FakeResearchLLMProvider, ProviderError
from bot.research.llm.result_analyst import analyze_results
from bot.research.llm.safety import (
    analysis_cannot_override_verdict,
    assert_no_shell_tools,
    context_is_oos_blind,
)
from bot.research.llm.schemas import HypothesisBatch, HypothesisProposal, ResultAnalysisBatch


def _hyp(**kwargs):
    base = {
        "title": "Test hypothesis slow imbalance",
        "mechanism": "Depth imbalance predicts short horizon mid move after costs",
        "why_now": "Prior imbalance cost-negative; test longer horizon only",
        "not_equivalent_to": [],
        "difference_from_prior_failures": "Uses only 2000ms supported horizon",
        "strategy_family": "order_book_imbalance",
        "required_features": ["depth_imbalance", "spread"],
        "required_horizons_ms": [1000, 2000],
        "signal_concept": "Signed imbalance with spread filter",
        "expected_failure_modes": ["cost_negative"],
        "economic_mechanism": "Edge must exceed retail taker roundtrip",
        "execution_assumption": "trade_through_conservative",
        "information_value": "HIGH",
        "priority": 1,
        "what_we_learn_if_fails": "Even slow imbalance cannot clear shared costs",
    }
    base.update(kwargs)
    return HypothesisProposal.model_validate(base)


def test_ollama_unavailable_fake_health() -> None:
    p = FakeResearchLLMProvider(health_status="UNAVAILABLE")
    h = p.health()
    assert h.available is False
    assert h.status == "UNAVAILABLE"


def test_model_unavailable_status() -> None:
    p = FakeResearchLLMProvider(health_status="MODEL_UNAVAILABLE")
    assert p.health().status == "MODEL_UNAVAILABLE"
    with pytest.raises(ProviderError):
        p.generate_structured(
            system_prompt="x",
            context={},
            schema_model=HypothesisBatch,
        )


def test_malformed_structured_output() -> None:
    p = FakeResearchLLMProvider(responses=[{"nope": True}])
    with pytest.raises(ProviderError):
        p.generate_structured(
            system_prompt="x", context={}, schema_model=HypothesisBatch
        )


def test_unknown_schema_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisProposal.model_validate(
            {
                **_hyp().model_dump(),
                "python_code": "print('x')",
            }
        )


def test_invalid_feature_and_horizon() -> None:
    with pytest.raises(ValidationError):
        _hyp(required_features=["not_a_real_feature"])
    with pytest.raises(ValidationError):
        _hyp(required_horizons_ms=[17])


def test_unknown_family() -> None:
    with pytest.raises(ValidationError):
        _hyp(strategy_family="maker_inventory")


def test_validator_budget_and_forbidden_tokens() -> None:
    budget = ExperimentBudget(max_features_per_strategy=4, max_new_hypotheses_per_run=3)
    v = StrategySpecValidator()
    bad = _hyp(execution_assumption="enable execution with queue fill")
    # construction allowed; validator catches tokens in blob
    r = v.validate(bad, budget=budget, supported_horizons={1000, 2000})
    assert r.ok is False
    assert any("forbidden_token" in x or "queue" in x for x in r.reasons)


def test_duplicate_and_differentiated(tmp_path: Path) -> None:
    reg = HypothesisRegistry(tmp_path / "reg.jsonl")
    h1 = _hyp()
    reg.register_proposal(h1, source="llm", status="OOS_FAILED")
    dups = reg.find_duplicates(_hyp())
    assert dups
    # differentiated
    h2 = _hyp(
        title="Differentiated",
        not_equivalent_to=[dups[0]["hypothesis_id"]],
        difference_from_prior_failures="New regime filter and longer horizon only",
        mechanism="Depth imbalance with regime filter not previously tested on this tape",
    )
    # still similar — but researcher allows with differentiation
    assert h2.difference_from_prior_failures


def test_budget_enforcement() -> None:
    b = ExperimentBudget(max_new_hypotheses_per_run=1, max_parameter_combinations=2)
    ok, _ = b.can_accept_hypothesis(n_features=2, n_params=1)
    assert ok
    b.record_hypothesis(n_params=1)
    ok2, why = b.can_accept_hypothesis(n_features=2, n_params=1)
    assert ok2 is False
    assert why == "max_new_hypotheses_per_run"


def test_oos_blind_context() -> None:
    ctx = build_research_context(
        dataset_id="d",
        data_duration=100.0,
        venues=["binance"],
        symbols=["BTCEUR"],
        readiness={"LEAD_LAG_1000MS": "READY_WITH_CAUTION", "LEAD_LAG_50MS": "NOT_READY"},
        hypotheses=[],
        tournament_summary={"scoreboard": []},
        budget={"remaining_this_run": 3},
        include_oos_result_summary=False,
    )
    assert ctx["oos_blind"] is True
    assert context_is_oos_blind(ctx) is True
    assert "untouched_oos_raw" not in ctx


def test_analysis_cannot_modify_verdict() -> None:
    canonical = {"lead_lag": "OOS_FAILED"}
    out = analysis_cannot_override_verdict({"verdict": "PAPER_CANDIDATE"}, canonical)
    assert out == canonical


def test_result_analyst_with_fake() -> None:
    p = FakeResearchLLMProvider(
        responses=[
            {
                "label": "NON_AUTHORITATIVE_ANALYSIS",
                "items": [
                    {
                        "strategy_family": "lead_lag",
                        "learned": "OOS reversed",
                        "failure_gate": "OOS",
                        "another_experiment_justified": False,
                    }
                ],
                "shared_lessons": ["costs dominate"],
            }
        ]
    )
    analysis, canonical, status = analyze_results(
        p,
        result_summary={
            "scoreboard": [{"STRATEGY": "lead_lag", "VERDICT": "OOS_FAILED"}]
        },
        budget_calls_remaining=2,
    )
    assert status == "OK"
    assert analysis is not None
    assert canonical["lead_lag"] == "OOS_FAILED"


def test_no_shell_tools() -> None:
    assert_no_shell_tools({})
    with pytest.raises(RuntimeError):
        assert_no_shell_tools({"shell": True})


def test_registry_append_only(tmp_path: Path) -> None:
    reg = HypothesisRegistry(tmp_path / "r.jsonl")
    a = reg.register_proposal(_hyp(), status="PROPOSED")
    reg.update_status_append(a["hypothesis_id"], status="OOS_FAILED", final_reason="oos")
    rows = reg.list_all()
    assert len(rows) == 2
    assert rows[0]["status"] == "PROPOSED"
    assert rows[1]["status"] == "OOS_FAILED"


def test_dry_run_does_not_mutate_registry(tmp_path: Path) -> None:
    settings = Settings(
        research_llm_enabled=True,
        research_llm_autonomous_enabled=False,
    )
    reg = HypothesisRegistry(tmp_path / "r.jsonl")
    provider = FakeResearchLLMProvider(
        responses=[
            {
                "analysis": {"observations": []},
                "hypotheses": [_hyp().model_dump()],
            }
        ]
    )
    director = ResearchDirector(settings=settings, provider=provider, registry=reg)
    report = director.run(dry_run=True, research_path=tmp_path / "empty_tape")
    assert report["dry_run"] is True
    assert reg.list_all() == []
    assert report["experiments_completed"] == 0


def test_loop_terminates_one_round(tmp_path: Path) -> None:
    settings = Settings(
        research_llm_enabled=True,
        research_llm_autonomous_enabled=False,
        research_llm_max_rounds=1,
    )
    provider = FakeResearchLLMProvider(
        responses=[
            {
                "analysis": {
                    "observations": [
                        {
                            "evidence_id": "e1",
                            "observation": "costs dominate",
                            "confidence": "medium",
                        }
                    ]
                },
                "hypotheses": [_hyp().model_dump()],
            }
        ]
    )
    reg = HypothesisRegistry(tmp_path / "r.jsonl")
    director = ResearchDirector(settings=settings, provider=provider, registry=reg)
    report = director.run(dry_run=True)
    assert report["rounds_executed"] <= 1


def test_same_fake_response_same_spec(tmp_path: Path) -> None:
    payload = {
        "analysis": {"observations": []},
        "hypotheses": [_hyp().model_dump()],
    }
    settings = Settings(research_llm_autonomous_enabled=False)
    results = []
    for _ in range(2):
        provider = FakeResearchLLMProvider(responses=[json.loads(json.dumps(payload))])
        reg = HypothesisRegistry(tmp_path / f"r{_}.jsonl")
        director = ResearchDirector(settings=settings, provider=provider, registry=reg)
        results.append(director.run(dry_run=True)["proposal"]["specs"])
    assert results[0] == results[1]


def test_llm_failure_does_not_break_when_disabled(tmp_path: Path) -> None:
    settings = Settings(research_llm_enabled=True)
    provider = FakeResearchLLMProvider(health_status="UNAVAILABLE")
    director = ResearchDirector(
        settings=settings,
        provider=provider,
        registry=HypothesisRegistry(tmp_path / "r.jsonl"),
    )
    report = director.run(dry_run=True, llm_disabled=False)
    assert report["LLM_STATUS"] == "UNAVAILABLE"
    # deterministic path still returns a report
    assert "multiple_testing_exposure" in report


def test_production_and_tournament_untouched() -> None:
    import bot.execution.paper_executor as paper_ex
    import bot.research.tournament.engine as eng
    import bot.strategies.maker_inventory as maker

    for mod in (paper_ex, maker):
        src = inspect.getsource(mod)
        assert "ollama" not in src.lower()
        assert "ResearchDirector" not in src
    # tournament engine must not import ollama
    src = inspect.getsource(eng)
    assert "ollama" not in src.lower()
    assert "ResearchDirector" not in src


def test_catalog_lists_families() -> None:
    cat = list_supported_catalog()
    assert "lead_lag" in cat["strategy_families"]
    assert 1000 in cat["horizons_ms"]


def test_budget_from_settings() -> None:
    s = Settings(research_max_new_hypotheses_per_run=2)
    b = budget_from_settings(s)
    assert b.max_new_hypotheses_per_run == 2


def test_unsupported_horizon_all_rejected() -> None:
    v = StrategySpecValidator()
    b = ExperimentBudget()
    h = _hyp(required_horizons_ms=[50, 100])
    r = v.validate(h, budget=b, supported_horizons={1000, 2000, 5000})
    assert r.ok is False
    assert "all_requested_horizons_unsupported" in r.reasons
