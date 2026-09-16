# Autonomous Local LLM Research — Report

## A. Existing architecture reused

Deterministic tournament (`bot.research.tournament`), hypothesis-free scoreboard,
chrono split, shared fees/waterfall, market-data readiness report, paper dashboard.

## B. Why the LLM is not the judge

LLM proposes and analyzes. Tournament verdicts remain canonical. Analysis is labeled
`NON_AUTHORITATIVE_ANALYSIS` and cannot mutate verdicts/PnL/OOS/economics.

## C. Ollama integration

`bot/research/llm/ollama.py` — isolated HTTP client to `127.0.0.1:11434`.

## D. Default model

`qwen3:4b-instruct`

## E. Model configuration

`RESEARCH_LLM_MODEL` (e.g. `qwen3:8b`) — never auto-download; never GPU-required.

## F. Structured output schemas

Pydantic models with `extra=forbid`: `HypothesisBatch`, `ResultAnalysisBatch`.

## G. Hypothesis registry

Append-only JSONL at `data/research_hypotheses/registry.jsonl`.

## H. Duplicate prevention

Mechanism fingerprint + token Jaccard + feature overlap; revisits require explicit differentiation.

## I. Research context

Bounded deterministic builder; summaries only; max bytes/experiments/hypotheses.

## J. Experiment budget

Max hypotheses/run, params, features, dataset experiments, LLM calls.

## K. OOS blindness

Pre-freeze context excludes untouched OOS raw keys; enforced in `context_is_oos_blind`.

## L. Strategy compiler safety

`StrategySpecValidator` — registered DSL only; rejects fee/fill/execution/OOS overrides.

## M. Autonomous research lifecycle

`ResearchDirector`: readiness → registry → context → propose → validate → budget →
(optional) tournament → result summary analysis → stop (max rounds=1 default).

## N. Failure handling

Ollama/model unavailable → `LLM_STATUS=UNAVAILABLE|MODEL_UNAVAILABLE`; tournament still works.

## O. Performance

LLM not on hot path; only explicit CLI/batch. Context size and call counts reported.

## P. Tests

`tests/test_autonomous_llm_research.py` — fake provider; Ollama not required.

## Q. Production regression

Paper executor / maker / tournament engine do not import Ollama or ResearchDirector.
Execution remains disabled; autonomous default false.
