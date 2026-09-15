"""Generate docs/EXECUTION_REALISM_REPORT.md from result JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_markdown(out: dict[str, Any]) -> str:
    scenarios = out.get("scenario_results") or []
    breakeven = out.get("breakeven_surface") or {}
    alpha_loss = out.get("alpha_loss_attribution") or {}
    perf = out.get("PERFORMANCE") or {}

    scenario_rows = "\n".join(
        f"| {s.get('scenario_id','')[:50]} | {s.get('fill_model')} | {s.get('latency_scenario')} | "
        f"{s.get('hedge_scenario')} | {s.get('n_signals')} | {s.get('n_fills')} | "
        f"{s.get('execution_net_eur','0')[:12]} | {s.get('fill_rate','0'):.3f} | "
        f"{s.get('positive_windows')} | {s.get('negative_windows')} |"
        for s in scenarios[:20]
    ) or "| — | — | — | — | — | — | — | — | — | — |"

    lat_rows = "\n".join(
        f"| {r.get('latency_ms')} | {r.get('net_per_signal','')[:12]} | {'✓' if r.get('positive') else '✗'} |"
        for r in breakeven.get("latency_surface") or []
    ) or "| — | — | — |"

    loss_rows = "\n".join(
        f"| {k} | {v}% |" for k, v in alpha_loss.items()
    ) or "| — | — |"

    return f"""# Execution realism report

Research-only counterfactual validation. Does NOT claim live alpha.
Does NOT enable production execution. Does NOT loosen fills, fees, or thresholds.

**VERDICT: {out.get('VERDICT')}**

---

## A. Dataset and tape quality

| | |
|---|---|
| Signals tested | {out.get('n_signals')} |
| Independent windows | {out.get('n_windows')} |
| Scenarios evaluated | {out.get('positive_scenario_fraction')} fraction positive |
| Performance | {perf.get('execution_realism_seconds','?'):.1f}s, {perf.get('signals_per_second',0):.0f} signals/s |

---

## B. Strategies tested

Parent strategies with positive canonical replay economics only.
No new strategies created.

---

## C–F. Frozen assumptions

Latency, fill, hedge, and cancel models are predeclared in
`bot/research/execution_realism/config.py`. Not tuned after results.

---

## G. Canonical replay baseline

| | EUR |
|---|---|
| Canonical replay NET | {out.get('CANONICAL_REPLAY_NET')} |

---

## H. Realistic execution results

| | |
|---|---|
| Realistic execution NET (NORMAL/POST_ONLY_SURVIVAL/NORMAL) | {out.get('REALISTIC_EXECUTION_NET')} |
| Delta (realistic − canonical) | {out.get('DELTA')} |
| Fill survival % | {out.get('FILL_SURVIVAL_PCT')} |
| Partial fill % | {out.get('PARTIAL_FILL_PCT')} |
| No fill % | {out.get('NO_FILL_PCT')} |

---

## I. Scenario robustness

| scenario | fill_model | latency | hedge | signals | fills | exec_net | fill_rate | +w | −w |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
{scenario_rows}

Positive scenario fraction: **{out.get('positive_scenario_fraction')}**

---

## J. Break-even surface

### Latency

| latency_ms | net/signal | positive |
|---:|---:|---|
{lat_rows}

Break-even latency: **{breakeven.get('breakeven_latency_ms')}** ms

Break-even adverse add: **{breakeven.get('breakeven_adverse_add_bps')}** bps

Break-even fee multiplier: **{breakeven.get('breakeven_fee_multiplier')}**

Break-even fill rate: **{breakeven.get('breakeven_fill_rate')}**

Break-even hedge delay: **{breakeven.get('breakeven_hedge_delay_ms')}** ms

---

## K. Alpha loss attribution (WHERE THE ALPHA DISAPPEARS)

| outcome | share |
|---|---:|
{loss_rows}

---

## L. Timestamp uncertainty

Bitvavo `exchange_ts` coverage may be 0%.
Signals without exchange timestamps are flagged `TIMESTAMP_UNCERTAIN`.
Acceptance uses central latency estimate, not optimistic.

---

## M–N. Independent OOS / Performance

Windows: {out.get('n_windows')}
Execution seconds: {perf.get('execution_realism_seconds','?')}

---

## O. Limitations

- L1 depth only (no L10 orderbook)
- Cross-venue hedge uses mid+spread, not full VWAP
- Fill models are conservative by design
- Venue-specific timestamp quality propagated as uncertainty
- No queue priority modeling

---

## P. Final verdict

**{out.get('VERDICT')}**

| | |
|---|---|
| NEW_STRATEGIES_CREATED | [] |
| PRODUCTION_EXECUTION | DISABLED |
| NO_NEW_ALPHA_CLAIMED | True |
"""


def write_report(out: dict[str, Any], path: str = "docs/EXECUTION_REALISM_REPORT.md") -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(out), encoding="utf-8")
    return dest
