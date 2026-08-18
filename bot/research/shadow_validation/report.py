"""Final shadow-validation report. Generated only when the frozen sample completes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.research.shadow_validation.protocol import (
    DEFAULT_RUN_DIR,
    FINAL_RESULTS_FILENAME,
    HISTORICAL_FINAL_VALIDATION,
    PROPOSAL_PATH,
    REPORT_PATH,
    STRATEGY_ID,
)
from bot.research.shadow_validation.proposal import maybe_write_proposal
from bot.research.shadow_validation.scorecard import build_scorecard


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC).isoformat()


def build_final_payload(
    *,
    identity: dict[str, Any],
    snapshot: dict[str, Any],
    decision: dict[str, Any],
    run_start_ms: float,
    end_ms: float,
) -> dict[str, Any]:
    rates = snapshot.get("rates") or {}
    gap = snapshot.get("execution_gap") or {}
    return {
        "1_frozen_strategy_fingerprint": identity.get("strategy_fingerprint"),
        "2_code_commit": identity.get("git_commit"),
        "3_start_end_timestamps": {"start": _iso(run_start_ms), "end": _iso(end_ms)},
        "4_sample_duration": {
            "calendar_days": snapshot.get("calendar_days"),
            "complete_windows": snapshot.get("complete_windows"),
        },
        "5_independent_windows": snapshot.get("complete_windows"),
        "6_total_candidates": snapshot.get("n_candidates"),
        "7_valid_observations": snapshot.get("valid_observations"),
        "8_invalid_observations": snapshot.get("invalid_observations"),
        "9_full_fills": snapshot.get("FULL_FILL"),
        "10_partial_fills": snapshot.get("PARTIAL_FILL"),
        "11_no_fills": snapshot.get("NO_FILL"),
        "12_expected_economics": {
            "label": "B_EXPECTED_ECONOMICS",
            "RESEARCH_EXPECTED_NET": snapshot.get("RESEARCH_EXPECTED_NET"),
            "historical": HISTORICAL_FINAL_VALIDATION,
        },
        "13_observed_shadow_economics": {
            "label": "C_SHADOW_EXECUTION",
            "LIVE_SHADOW_EXECUTION_NET": snapshot.get("LIVE_SHADOW_EXECUTION_NET"),
        },
        "14_execution_gap": gap,
        "15_fill_behavior": {
            "fill_rate": rates.get("fill_rate"),
            "partial_fill_rate": rates.get("partial_fill_rate"),
            "no_fill_rate": rates.get("no_fill_rate"),
            "quote_survival_rate": rates.get("quote_survival_rate"),
        },
        "16_hedge_behavior": {
            "follower_availability_rate": rates.get("follower_availability_rate"),
            "hedge_failure_rate": rates.get("hedge_failure_rate"),
            "mean_hedge_deterioration_bps": rates.get("mean_hedge_deterioration_bps"),
            "FOLLOWER_UNAVAILABLE": snapshot.get("FOLLOWER_UNAVAILABLE"),
            "HEDGE_WORSENED": snapshot.get("HEDGE_WORSENED"),
        },
        "17_adverse_selection": {
            "mean_adverse_selection_bps": rates.get("mean_adverse_selection_bps"),
        },
        "18_concentration": {"top_window_share": snapshot.get("top_window_share")},
        "19_accounting_audit": {
            "accounting_fail": snapshot.get("accounting_fail"),
            "status": "PASS" if int(snapshot.get("accounting_fail") or 0) == 0 else "FAIL",
        },
        "20_final_verdict": decision.get("SHADOW_VALIDATION_VERDICT"),
        "21_exactly_one_next_action": decision.get("NEXT_ACTION"),
        "WHY": decision.get("WHY"),
        "strategy_id": STRATEGY_ID,
        "run_fingerprint": identity.get("run_fingerprint"),
        "parameter_hash": identity.get("parameter_hash"),
        "config_hash": identity.get("config_hash"),
        "production_execution": "DISABLED",
        "retuning_allowed": False,
        "D_REALIZED_MARKET_OUTCOME_is_not_shadow_net": True,
        "A_SIGNAL_is_not_a_fill": True,
        "scorecard": build_scorecard(
            snapshot,
            decision,
            integrity=str(identity.get("VALIDATION_INTEGRITY") or "VALID"),
            identity=identity,
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    hist = HISTORICAL_FINAL_VALIDATION
    why = "\n".join(f"- {x}" for x in (payload.get("WHY") or []))
    gap = payload.get("14_execution_gap") or {}
    fill = payload.get("15_fill_behavior") or {}
    hedge = payload.get("16_hedge_behavior") or {}
    ts = payload.get("3_start_end_timestamps") or {}
    dur = payload.get("4_sample_duration") or {}
    exp = payload.get("12_expected_economics") or {}
    sh = payload.get("13_observed_shadow_economics") or {}
    adv = payload.get("17_adverse_selection") or {}
    conc = payload.get("18_concentration") or {}
    acc = payload.get("19_accounting_audit") or {}
    return f"""# Cross-venue dislocation — SHADOW PAPER VALIDATION

Research-only. Production execution remains **DISABLED**.
No parameter tuning. No LLM strategy generation. No fabricated fills.

## 1. Frozen strategy fingerprint

`{payload.get("1_frozen_strategy_fingerprint")}`

## 2. Code commit

`{payload.get("2_code_commit")}`

Run fingerprint: `{payload.get("run_fingerprint")}`
Parameter hash: `{payload.get("parameter_hash")}`
Config hash: `{payload.get("config_hash")}`

## 3. Start / end timestamps

- start: {ts.get("start")}
- end: {ts.get("end")}

## 4. Sample duration

- calendar days: {dur.get("calendar_days")}
- complete independent windows: {dur.get("complete_windows")}

## 5. Independent windows

{payload.get("5_independent_windows")}

## 6. Total candidates

{payload.get("6_total_candidates")}

## 7. Valid observations

{payload.get("7_valid_observations")}

## 8. Invalid observations

{payload.get("8_invalid_observations")}

## 9. Full fills

{payload.get("9_full_fills")}

## 10. Partial fills

{payload.get("10_partial_fills")}

## 11. No fills

{payload.get("11_no_fills")}

## 12. Expected economics (B — not a fill, not live NET)

RESEARCH EXPECTATION (historical final validation):

- fill assumption: `{hist.get("expected_fill_assumption")}`
- fill model: `{hist.get("fill_model")}`
- BASELINE EXECUTION_NET: {hist.get("BASELINE_EXECUTION_NET_EUR")} EUR
- candidates / canonical fills: {hist.get("n_candidates")} / {hist.get("n_canonical_fills")}

Live predicted expected NET (sum of B_EXPECTED_ECONOMICS.expected_net):
{exp.get("RESEARCH_EXPECTED_NET")} EUR

## 13. Observed shadow economics (C — simulated execution)

LIVE_SHADOW_EXECUTION_NET: {sh.get("LIVE_SHADOW_EXECUTION_NET")} EUR

This is **not** B_EXPECTED_ECONOMICS and **not** D_REALIZED_MARKET_OUTCOME.

## 14. Execution gap

`execution_gap = realized_shadow_execution_net − predicted_expected_net`

{json.dumps(gap, indent=2, sort_keys=True)}

## 15. Fill behavior

{json.dumps(fill, indent=2, sort_keys=True)}

Historical canonical replay assumed a fill for every candidate (67443/67443).
Live fill_rate is the quantity that tests that assumption.

## 16. Hedge behavior

{json.dumps(hedge, indent=2, sort_keys=True)}

## 17. Adverse selection

{json.dumps(adv, indent=2, sort_keys=True)}

## 18. Concentration

{json.dumps(conc, indent=2, sort_keys=True)}

## 19. Accounting audit

{json.dumps(acc, indent=2, sort_keys=True)}

## 20. Final verdict

**{payload.get("20_final_verdict")}**

{why}

## 21. Exactly one next action

**{payload.get("21_exactly_one_next_action")}**

Production execution remains DISABLED until explicit approval of a separate
LIMITED_PAPER_EXECUTION proposal. Negative shadow results do not trigger
retuning, new regimes, or LLM hypotheses.

H-0005 remains REJECT_AS_INCREMENTAL_FILTER.
H-0007 remains REJECT / GATE_INACTIVE.
"""


def maybe_write_final(
    *,
    identity: dict[str, Any],
    snapshot: dict[str, Any],
    decision: dict[str, Any],
    run_start_ms: float,
    end_ms: float,
    run_dir: str | Path | None = None,
    report_path: str | Path | None = None,
    write_docs: bool = True,
    proposal_path: str | Path | None = None,
) -> dict[str, Any]:
    if not snapshot.get("sample_complete"):
        return {"written": False, "reason": "sample_incomplete"}
    payload = build_final_payload(
        identity=identity,
        snapshot=snapshot,
        decision=decision,
        run_start_ms=run_start_ms,
        end_ms=end_ms,
    )
    root = Path(run_dir or DEFAULT_RUN_DIR)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / FINAL_RESULTS_FILENAME
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if write_docs:
        md_path = Path(report_path or REPORT_PATH)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        maybe_write_proposal(decision, path=proposal_path or PROPOSAL_PATH)
    return {"written": True, "payload": payload, "json_path": str(json_path)}
