# Autonomous Local LLM Research

## Why the LLM is not the judge

The deterministic Strategy Research Tournament remains the only authority for
verdicts (`DATA_UNSUPPORTED` … `PAPER_CANDIDATE`). The local LLM is a
**research scientist**: it reads evidence, proposes hypotheses, and writes
non-authoritative notes. It cannot approve strategies, change fills/fees/PnL,
enable execution, or inspect untouched OOS before freeze.

## Runtime

- Provider: **Ollama only** (no OpenAI/Anthropic/Google/paid APIs)
- Default model: `qwen3:4b-instruct` (configurable)
- Env: `RESEARCH_LLM_*` and budget knobs (see `.env.example`)
- Default: `RESEARCH_LLM_AUTONOMOUS_ENABLED=false`

## CLI

```bash
python -m bot.research.autonomous.runner --dry-run --fake-llm
python -m bot.research.autonomous.runner --analyze-existing
python -m bot.research.autonomous.runner  # proposals; tournament only if autonomous=true
```

## Safety

- Structured JSON schemas (`extra=forbid`)
- StrategySpecValidator (registered families/features/horizons only)
- Duplicate detection + explicit differentiation required to revisit failures
- Experiment budgets
- OOS-blind context builder
- No shell / Redis write / arbitrary filesystem tools
- Not on market-data or trading hot path

## Architecture

```
LOCAL MARKET DATA → RESEARCH TAPE → DETERMINISTIC TOURNAMENT
                                         ↑ experiments
                              AUTONOMOUS RESEARCH DIRECTOR
                                         ↑ evidence
                              LOCAL QWEN3 VIA OLLAMA
```
