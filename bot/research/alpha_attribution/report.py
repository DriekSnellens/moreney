"""Render ALPHA_ATTRIBUTION_REPORT.md from canonical result objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_markdown(out: dict[str, Any]) -> str:
    audit = out.get("stored_paired_audit") or out.get("live_paired_audit") or {}
    live_audit = out.get("live_paired_audit") or {}
    groups = out.get("groups") or {}
    contexts = out.get("contexts") or []
    loo = out.get("leave_one_context_out") or {}
    obs = out.get("NEW_RESEARCH_OBSERVATIONS") or []
    features = out.get("ranked_gate_forensics") or out.get("feature_attribution") or []
    windows = (audit.get("windows") or [])[:15]
    win_rows = "\n".join(
        f"| {w.get('window_id')} | {w.get('parent_signal_count')} | {w.get('child_signal_count')} | "
        f"{w.get('parent_fill_count')} | {w.get('child_fill_count')} | {w.get('parent_replay_net_eur')} | "
        f"{w.get('child_replay_net_eur')} | {w.get('paired_delta_eur')} | {w.get('retained_signal_net_eur')} | "
        f"{w.get('excluded_signal_net_eur')} |"
        for w in windows
    ) or "| — | — | — | — | — | — | — | — | — | — |"
    group_rows = []
    for key in ("ALL_PARENT", "RETAINED_BY_CHILD", "EXCLUDED_BY_CHILD"):
        g = groups.get(key) or {}
        group_rows.append(
            f"| {key} | {g.get('signal_count')} | {g.get('estimated_fills')} | "
            f"{g.get('gross_eur')} | {g.get('fees_eur')} | {g.get('slippage_eur')} | "
            f"{g.get('adverse_eur')} | {g.get('other_costs_eur')} | {g.get('replay_net_eur')} | "
            f"{g.get('replay_net_per_signal')} | {g.get('replay_net_per_fill')} | "
            f"{g.get('share_of_parent_signals')} | {g.get('share_of_parent_net')} | "
            f"{g.get('positive_windows')} |"
        )
    ctx_rows = "\n".join(
        f"| {c.get('context')} | {c.get('signal_count')} | {c.get('replay_net_eur')} | "
        f"{c.get('replay_net_per_signal')} | {c.get('stability')} | {c.get('contribution_share')} | "
        f"{c.get('positive_windows')} | {c.get('negative_windows')} | DESCRIPTIVE_ONLY |"
        for c in contexts
    ) or "| — | — | — | — | — | — | — | — | — |"
    loo_rows = "\n".join(
        f"| {r.get('context')} | {((r.get('ONLY_context') or {}).get('net'))} | "
        f"{((r.get('WITHOUT_context') or {}).get('net'))} | {r.get('context_contribution')} | "
        f"{((r.get('ONLY_context') or {}).get('positive_windows'))}/"
        f"{((r.get('ONLY_context') or {}).get('negative_windows'))} | "
        f"{r.get('CONTEXT_DEPENDENT_FLAG')} |"
        for r in (loo.get("rows") or [])
    ) or "| — | — | — | — | — | — |"
    feat_rows = "\n".join(
        f"| {f.get('feature')} | {f.get('bucket')} | "
        f"n={(f.get('retained') or {}).get('signal_count')} net={(f.get('retained') or {}).get('replay_net_eur')} | "
        f"n={(f.get('excluded') or {}).get('signal_count')} net={(f.get('excluded') or {}).get('replay_net_eur')} | "
        f"{(f.get('difference') or {}).get('excluded_minus_retained_replay_net_eur')} | "
        f"{f.get('economic_contribution')} | {f.get('window_stability')} | "
        f"{f.get('pre_trade_available')} | {f.get('usefulness')} |"
        for f in features[:15]
    ) or "| — | — | — | — | — | — | — | — | — |"
    obs_rows = "\n".join(
        f"- **{o.get('title')}** ({o.get('type')}): {o.get('finding')} "
        f"[usefulness={o.get('candidate_hypothesis_usefulness')}; auto_strategy={o.get('auto_strategy')}]"
        for o in obs
    ) or "- none"
    excl = groups.get("EXCLUDED_BY_CHILD") or {}
    excl_pos = excl.get("excluded_economically_positive")
    group_table = "\n".join(group_rows)
    return f"""# Alpha attribution report

Research-only forensic attribution of the H-0005 freshness gate versus its parent
on the **same paired signal universe**. Canonical execution replay is the only
evaluation NET. Expected metrics are not substitutes.

**NO NEW ALPHA CLAIMED.**

This document does not enable trading, does not retune `quote_age_ms`, does not
modify H-0005, does not resurrect H-0007, and does not create a production
strategy. Bins are existing forensic/tournament constants. Contexts are
`DESCRIPTIVE_ONLY`.

Artifact: `data/research/alpha_attribution_results.json`

---

## 1. Paired delta plausibility audit

Sign convention (explicit):

```
paired_delta_eur = child_replay_net_eur - parent_replay_net_eur
```

Negative delta means the child underperformed the parent on canonical replay.

| Field | Value |
|---|---|
| Stored audit | {audit.get('PAIRED_DELTA_ACCOUNTING_AUDIT')} |
| Live audit | {live_audit.get('PAIRED_DELTA_ACCOUNTING_AUDIT')} |
| Combined `PAIRED_DELTA_ACCOUNTING_AUDIT` | {out.get('PAIRED_DELTA_ACCOUNTING_AUDIT')} |
| LIVE_VS_PUBLISHED (current tape vs frozen number) | {out.get('LIVE_VS_PUBLISHED')} |
| Reported aggregate delta | {audit.get('reported_aggregate_delta_eur')} |
| SUM(window paired deltas) | {audit.get('sum_window_paired_deltas_eur')} |
| SUM(parent replay NET) | {audit.get('sum_parent_replay_net_eur')} |
| SUM(child replay NET) | {audit.get('sum_child_replay_net_eur')} |
| SUM(excluded signal NET) | {audit.get('sum_excluded_signal_net_eur')} |
| Stored issues | {audit.get('issues') or []} |
| Live vs published issues | {live_audit.get('issues') or []} |

Identities checked per complete window:

- `parent_replay_net = shared_signal_net + excluded_signal_net`
- `child_replay_net = shared_signal_net` (pure filter: `child_only_signals = 0`)
- `paired_delta = child - parent`
- `SUM(window deltas) = reported aggregate` — **not rewritten on mismatch**

### Complete windows

| window_id | parent_signals | child_signals | parent_fills | child_fills | parent_replay_net | child_replay_net | paired_delta | retained_net | excluded_net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{win_rows}

---

## 2. Parent vs child decomposition

| | EUR |
|---|---|
| PARENT_REPLAY_NET | {out.get('PARENT_REPLAY_NET')} |
| H-0005 / RETAINED_SIGNAL_NET | {out.get('RETAINED_SIGNAL_NET')} |
| EXCLUDED_SIGNAL_NET | {out.get('EXCLUDED_SIGNAL_NET')} |
| First-lab OOS H-0005 canonical NET (different split; headline only) | {out.get('first_lab_canonical_h0005_net')} |

Walk-forward complete windows are the paired universe that produced the published
`-51461.29` delta. First-lab OOS `2218.37` is a **different sample** and is not
substituted for paired-window economics.

---

## 3. Retained vs excluded economics

Excluded economically positive on canonical replay: **{excl_pos}**

Do not infer this from EXPECTED_NET.

| GROUP | signals | fills | gross | fees | slippage | adverse | other_costs | replay_net | net/signal | net/fill | share_signals | share_net | +windows |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{group_table}

---

## 4. Feature attribution (pre-trade only)

Outcomes (`forward`, replay NET) are never admission features.
Unavailable pre-trade: inventory_state, predicted adverse as a state,
fill probability as a state (research uses frozen model constants).

Ranked by excluded canonical replay |NET| (forensic; not a threshold search):

| feature | bucket | retained | excluded | difference | economic contribution | stability | pre-trade | usefulness |
|---|---|---|---|---|---|---|---|---|
{feat_rows}

---

## 5. Window stability

Existing floors: ≥30 signals, ≥3 windows, 70% symbol/route caps.
`STABLE` also requires no negative windows. Not an alpha claim.

See context table. Feature-bucket stability is in the ranked forensics rows.

---

## 6. Parent signal quality map (`DESCRIPTIVE_ONLY`)

Named contexts use existing quote-age / strength / depth buckets. Not grid-searched.

| context | signals | replay_net | net/signal | stability | contribution_share | +windows | −windows | label |
|---|---:|---:|---:|---|---:|---:|---:|---|
{ctx_rows}

---

## 7. Leave-one-context-out / context dependency

`CONTEXT_DEPENDENCY` = {out.get('CONTEXT_DEPENDENCY')}

Flagged: {loo.get('flagged_contexts')}

Existing LOO share floor = 0.50, or WITHOUT-context net ≤ 0. Not an automatic
reject or promote.

| context | ONLY net | WITHOUT net | contribution | +/− windows (ONLY) | CONTEXT_DEPENDENT_FLAG |
|---|---:|---:|---:|---|---|
{loo_rows}

---

## 8. Ranked research observations

{obs_rows}

Future hypothesis requirements (none created here):

1. pre-trade feature
2. causal availability
3. sufficient economic contribution
4. stability evidence
5. DEV-only threshold definition
6. fresh unseen OOS data

---

## 9. Explicit non-claims

| | |
|---|---|
| NO_NEW_ALPHA_CLAIMED | {out.get('NO_NEW_ALPHA_CLAIMED')} |
| NEW_STRATEGIES_CREATED | {out.get('NEW_STRATEGIES_CREATED')} |
| PRODUCTION_EXECUTION | {out.get('PRODUCTION_EXECUTION')} |
| DESCRIPTIVE_ONLY | {out.get('DESCRIPTIVE_ONLY')} |
| h0005_modified | {out.get('h0005_modified')} |
| h0007_optimized | {out.get('h0007_optimized')} |
| oos_thresholds_created | {out.get('oos_thresholds_created')} |

---

## 10. WHY H-0005 underperformed

{out.get('WHY_H0005_UNDERPERFORMED')}
"""


def write_report(out: dict[str, Any], path: Path | str = "docs/ALPHA_ATTRIBUTION_REPORT.md") -> Path:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(out), encoding="utf-8")
    return dest
