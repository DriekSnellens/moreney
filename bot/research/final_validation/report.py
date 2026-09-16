"""docs/CROSS_VENUE_DISLOCATION_FINAL_VALIDATION.md"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _row(results: list[dict[str, Any]], sid: str) -> dict[str, Any]:
    return next(r for r in results if r["scenario_id"] == sid)


def render_markdown(out: dict[str, Any]) -> str:
    results = out.get("scenario_results") or []
    be = out.get("break_even") or {}
    why = "\n".join(f"- {x}" for x in (out.get("WHY") or []))
    uni = out.get("UNIVERSE") or {}
    unsup = "\n".join(
        f"- `{u.get('dimension')}`: {u.get('status')} — {u.get('reason')}"
        for u in (out.get("UNSUPPORTED_BY_DATA") or [])
    )
    scen_rows = "\n".join(
        f"| {r.get('scenario_id')} | {r.get('classification')} | {r.get('candidate_count')} | "
        f"{r.get('fill_count')} | {r.get('missed_fill_count')} | {r.get('partial_fill_count')} | "
        f"{r.get('CANONICAL_REPLAY_NET')} | {r.get('EXECUTION_NET')} | "
        f"{r.get('positive_windows')}/{r.get('total_windows')} | {r.get('max_drawdown')} | "
        f"{r.get('accounting_identity_status')} |"
        for r in results
    ) or "| — | — | — | — | — | — | — | — | — | — | — |"

    def blk(sid: str) -> str:
        if not results:
            return "Not run."
        r = _row(results, sid)
        return (
            f"| | |\n|---|---|\n"
            f"| EXECUTION_NET | {r.get('EXECUTION_NET')} EUR |\n"
            f"| CANONICAL_REPLAY_NET | {r.get('CANONICAL_REPLAY_NET')} EUR |\n"
            f"| candidate_count | {r.get('candidate_count')} |\n"
            f"| fill_count | {r.get('fill_count')} |\n"
            f"| missed_fill_count | {r.get('missed_fill_count')} |\n"
            f"| partial_fill_count | {r.get('partial_fill_count')} |\n"
            f"| CANONICAL_REPLAY_NET_PER_SIGNAL | {r.get('CANONICAL_REPLAY_NET_PER_SIGNAL')} |\n"
            f"| CANONICAL_REPLAY_NET_PER_FILL | {r.get('CANONICAL_REPLAY_NET_PER_FILL')} |\n"
            f"| EXECUTION_NET_PER_SIGNAL | {r.get('EXECUTION_NET_PER_SIGNAL')} |\n"
            f"| EXECUTION_NET_PER_FILL | {r.get('EXECUTION_NET_PER_FILL')} |\n"
            f"| max_drawdown | {r.get('max_drawdown')} |\n"
            f"| windows +/− | {r.get('positive_windows')} / {r.get('negative_windows')} |\n"
            f"| median / worst / best window | {r.get('median_window_net')} / {r.get('worst_window_net')} / {r.get('best_window_net')} |\n"
            f"| top_window_share | {r.get('top_window_share')} |\n"
            f"| accounting | {r.get('accounting_identity_status')} |\n"
        )

    base = results[0] if results else {}
    man = out.get("manifest") or {}
    h7 = uni.get("H-0007") or {}
    h5 = uni.get("H-0005") or {}
    parent = uni.get("cross_venue_dislocation") or {}
    return f"""# Cross-venue dislocation — final validation

Research-only. Not live alpha. Production execution remains DISABLED.
No new strategies. No parameter tuning. No hypothesis generation.

```
FINAL_VALIDATION_VERDICT: {out.get('FINAL_VALIDATION_VERDICT')}
```

## WHY

{why}

## NEXT_ACTION

{out.get('NEXT_ACTION')}

---

## 1. Strategy identity and frozen fingerprint

| | |
|---|---|
| STRATEGY | {out.get('STRATEGY')} |
| protocol_version | {out.get('protocol_version')} |
| configuration_hash | {man.get('configuration_hash')} |
| matrix_fingerprint | {man.get('matrix_fingerprint')} |
| code_commit | {man.get('code_commit')} |
| EXECUTION | {out.get('EXECUTION')} |
| PRODUCTION_EXECUTION | {out.get('PRODUCTION_EXECUTION')} |
| NEW_STRATEGIES_CREATED | {out.get('NEW_STRATEGIES_CREATED')} |

Frozen universe:

| id | classification | reason |
|---|---|---|
| H-0007 | {h7.get('classification')} | {h7.get('reason')} |
| H-0005 | {h5.get('classification')} | {h5.get('reason')} |
| cross_venue_dislocation | {parent.get('classification')} | {parent.get('reason')} |

## 2. Dataset fingerprint

| | |
|---|---|
| DATASET | {out.get('DATASET')} |
| DATASET_FINGERPRINT | {out.get('DATASET_FINGERPRINT')} |

## 3. Time range / 4–6. Sample

| | |
|---|---|
| window_ids | {out.get('window_ids')} |
| independent complete windows | {out.get('n_windows')} |
| signals (parent candidates) | {out.get('n_signals')} |
| estimated fills (round(n × 0.55)) | {base.get('estimated_fill_count')} |
| CANONICAL_REPLAY_NET | {out.get('CANONICAL_REPLAY_NET')} EUR |

## 7. Scenario matrix

Frozen before replay. Overlay values come from the existing robustness-lab grids.

| scenario | class | candidates | fills | missed | partial | CANONICAL_REPLAY_NET | EXECUTION_NET | +w/n | max_dd | accounting |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
{scen_rows}

## 8. Baseline result

{blk('BASELINE')}

## 9. Mild realism result

{blk('MILD_REALISM')}

## 10. Moderate realism result

{blk('MODERATE_REALISM')}

## 11. Harsh realism result

{blk('HARSH_REALISM')}

## 12. Stress result

{blk('STRESS')}

## 13. Window stability

See per-scenario positive/negative/median/worst/best/top_window_share above.
A strategy is not robust if one window dominates (`top_window_share` ≥ 0.70).

## 14. Concentration

| | |
|---|---|
| ROUTE_UNIVERSE | {base.get('ROUTE_UNIVERSE')} |
| ROUTE_UNIVERSE_LIMITED | {base.get('ROUTE_UNIVERSE_LIMITED')} |
| top_symbol | {base.get('top_symbol')} |
| top_symbol_share | {base.get('top_symbol_share')} |
| concentration | {base.get('concentration')} |

## 15. Break-even sensitivities

Analytical from BASELINE totals. No interpolation.

| | |
|---|---|
| extra_adverse_required_to_zero_NET_bps | {be.get('extra_adverse_required_to_zero_NET_bps')} |
| extra_slippage_required_to_zero_NET_bps | {be.get('extra_slippage_required_to_zero_NET_bps')} |
| fee_multiplier_required_to_zero_NET | {be.get('fee_multiplier_required_to_zero_NET')} |
| fill_rate_required_to_zero_NET | {be.get('fill_rate_required_to_zero_NET')} |
| latency_degradation_required_to_zero_NET_ms | {be.get('latency_degradation_required_to_zero_NET_ms')} |
| hedge_delay_required_to_zero_NET_ms | {be.get('hedge_delay_required_to_zero_NET_ms')} |

{be.get('note')}

## 16. Unsupported assumptions

{unsup}

## 17. Accounting audit

BASELINE accounting_identity_status: **{base.get('accounting_identity_status')}**

Canonical identities remain: parent waterfall = gross − fees − slippage − adverse − latency.
Scenario EXECUTION_NET is the overlay result and is labeled separately.
FILL_RATE 0.55 is EstimatedFillCount only, never unlabeled NET/fill.

This live tape run is **not** a rewrite of the published 15-window paired figure
(ALL_PARENT 19557 signals, 10757 estimated fills, CANONICAL_REPLAY_NET 66096.91 EUR).
That published sample remains immutable. This run uses the current tape fingerprint
and 62 complete sequential windows (W0 + later complete slices).

## 17b. Performance / memory

| | |
|---|---|
| wall seconds | {(out.get('PERFORMANCE') or {}).get('seconds')} |
| replays | {(out.get('PERFORMANCE') or {}).get('replays')} |
| replays/sec | {(out.get('PERFORMANCE') or {}).get('replays_per_sec')} |
| peak RSS MB | {(out.get('PERFORMANCE') or {}).get('peak_rss_mb') or (out.get('STREAMING') or {}).get('peak_rss_mb')} |
| live waterfalls retained | 0 (overlay objects discarded per signal) |

## 17c. SHADOW_PAPER_VALIDATION (next phase only; not started)

Triggered only because the frozen rules returned ROBUST_PAPER_CANDIDATE.

| | |
|---|---|
| production execution | DISABLED |
| strategy parameters | frozen (okx→bitvavo, horizon 5000 ms, dislocation 40 bps) |
| parameter optimization | forbidden |
| LLM / new hypotheses | forbidden |
| record every candidate | required |
| compare expected vs realized | required |
| predeclared min complete windows | 20 additional unseen windows or 7 additional calendar days, whichever is later |
| enable PaperExecutor | no |

Do not start live trading.

## 18. Final verdict

```
FINAL_VALIDATION_VERDICT: {out.get('FINAL_VALIDATION_VERDICT')}
```

WHY:

{why}

NEXT_ACTION: {out.get('NEXT_ACTION')}

PRODUCTION_EXECUTION: DISABLED
NO_NEW_ALPHA_CLAIMED: True
"""


def write_report(out: dict[str, Any], path: str = "docs/CROSS_VENUE_DISLOCATION_FINAL_VALIDATION.md") -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(out), encoding="utf-8")
    return dest
