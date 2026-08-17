"""Markdown + compact dashboard payload for concentration forensics."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _fmt(x: Any, digits: int = 4) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def _pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{100.0 * float(x):.1f}%"
    except (TypeError, ValueError):
        return "—"


def compact_dashboard(out: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for sid in out.get("STRATEGIES_ANALYZED") or []:
        block = (out.get("strategies") or {}).get(sid) or {}
        top = block.get("top_contributors") or {}
        cls = block.get("CONCENTRATION_CLASS")
        rows.append(
            {
                "STRATEGY": sid,
                "TOTAL_RESULT": (block.get("forensic_totals") or {}).get("NET"),
                "TOP_SYMBOL": (top.get("top_symbol") or {}).get("group"),
                "TOP_SYMBOL_CONTRIBUTION": (top.get("top_symbol") or {}).get("NET"),
                "TOP_SYMBOL_SHARE": (top.get("top_symbol") or {}).get("share"),
                "TOP_VENUE": (top.get("top_venue_pair") or {}).get("group"),
                "TOP_VENUE_CONTRIBUTION": (top.get("top_venue_pair") or {}).get("NET"),
                "TOP_TIME_BLOCK": (top.get("top_chrono_block") or {}).get("group"),
                "TOP_TIME_BLOCK_CONTRIBUTION": (top.get("top_chrono_block") or {}).get("NET"),
                "POSITIVE_BLOCKS": (block.get("chrono_blocks") or {}).get("positive_blocks"),
                "NEGATIVE_BLOCKS": (block.get("chrono_blocks") or {}).get("negative_blocks"),
                "CONCENTRATION_VERDICT": cls,
                "STRUCTURAL_EXPLANATION": block.get("CONCENTRATION_SOURCE"),
                "RECOMMENDED_NEXT_HYPOTHESIS": _next_hyp(out, sid),
            }
        )
    return {
        "label": "CONCENTRATION_FORENSICS",
        "STATUS": out.get("STATUS"),
        "DATASET": out.get("DATASET"),
        "STRATEGIES_ANALYZED": out.get("STRATEGIES_ANALYZED"),
        "rows": rows,
        "NEW_HYPOTHESES_CREATED": out.get("NEW_HYPOTHESES_CREATED"),
        "LLM_USED": out.get("LLM_USED"),
        "PRODUCTION_TRADING_CHANGED": False,
        "NEXT_RESEARCH_ACTION": out.get("NEXT_RESEARCH_ACTION"),
        "disclaimer": "Descriptive forensics. Parents remain REJECTED. Not alpha.",
    }


def _next_hyp(out: dict[str, Any], sid: str) -> str:
    recs = out.get("hypothesis_records") or {}
    parents = recs.get("parents") or {}
    created = recs.get("created_ids") or []
    cls = ((out.get("strategies") or {}).get(sid) or {}).get("CONCENTRATION_CLASS")
    if cls in {"SYMBOL_SPECIFIC", "VENUE_SPECIFIC", "REGIME_DEPENDENT"} and created:
        return f"independent child of {parents.get(sid)} — {created}"
    if cls == "TIME_SPECIFIC":
        return "none (inspect conditions; no time filter)"
    return "none"


def write_markdown(out: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cvd = out.get("CROSS_VENUE_DISLOCATION") or {}
    mr = out.get("SHORT_HORIZON_MEAN_REVERSION") or {}
    lines = [
        "# Concentration Forensics Report",
        "",
        "**Package:** CONCENTRATION_FORENSICS  ",
        f"**Criteria:** `{out.get('criteria_version')}`  ",
        "**Execution:** OFF  ",
        "**Parents:** remain REJECTED  ",
        "**Claim:** none (descriptive analysis only)",
        "",
        "This analysis does not retune parameters, loosen gates, or change fees, fills, PnL, OOS, or execution.",
        "",
        "## Frozen tournament context",
        "",
        f"- DATASET: `{out.get('DATASET')}`",
        f"- Tape duration: {_fmt(out.get('DATA_DURATION'), 1)} s",
        f"- Stride: {out.get('stride')} (matches the observed-tape tournament rerun)",
        f"- Frozen params source: `{out.get('frozen_params_source')}`",
        f"- OOS window: `{out.get('OOS_WINDOW')}`",
        "",
        "Per-event NET uses the same shared waterfall rates as the tournament. "
        "The **sum** of per-event NET is forensic accounting, not tournament EXPECTED_NET "
        "(which is mean-edge × notional − costs once).",
        "",
    ]
    for sid, card in (
        ("cross_venue_dislocation", cvd),
        ("short_horizon_mean_reversion", mr),
    ):
        block = (out.get("strategies") or {}).get(sid) or {}
        lines.extend(_strategy_md(sid, card, block))

    lines.extend(
        [
            "## Hypotheses",
            "",
            f"- NEW_HYPOTHESES_CREATED: {out.get('NEW_HYPOTHESES_CREATED')}",
            f"- LLM_USED: {out.get('LLM_USED')}",
            f"- PRODUCTION_TRADING_CHANGED: NO",
            f"- NEXT_RESEARCH_ACTION: {out.get('NEXT_RESEARCH_ACTION')}",
            "",
            "A new hypothesis ID does **not** implement a new strategy. Parents were not modified.",
            "",
            "## LLM advisory",
            "",
        ]
    )
    llm = out.get("llm_advisory") or {}
    if llm.get("used") == "YES":
        adv = llm.get("advisory") or {}
        lines.append(f"- Pattern: {adv.get('structurally_interesting_pattern')}")
        lines.append(f"- LLM explanation (advisory): {adv.get('most_likely_explanation')}")
        lines.append(f"- Notes: {adv.get('notes')}")
        lines.append("")
        lines.append("Deterministic classes above are authoritative.")
    else:
        lines.append(f"LLM not used ({llm.get('status')}).")
    lines.extend(
        [
            "",
            "## Final output",
            "",
            "```",
            f"DATASET: {out.get('DATASET')}",
            "",
            f"STRATEGIES_ANALYZED: {out.get('STRATEGIES_ANALYZED')}",
            "",
            "CROSS_VENUE_DISLOCATION:",
            "",
            f"CONCENTRATION_SOURCE: {cvd.get('CONCENTRATION_SOURCE')}",
            "",
            f"CONCENTRATION_CLASS: {cvd.get('CONCENTRATION_CLASS')}",
            "",
            f"STRUCTURAL_FEATURE_FOUND: {cvd.get('STRUCTURAL_FEATURE_FOUND')}",
            "",
            f"RECOMMENDED_ACTION: {cvd.get('RECOMMENDED_ACTION')}",
            "",
            "",
            "SHORT_HORIZON_MEAN_REVERSION:",
            "",
            f"CONCENTRATION_SOURCE: {mr.get('CONCENTRATION_SOURCE')}",
            "",
            f"CONCENTRATION_CLASS: {mr.get('CONCENTRATION_CLASS')}",
            "",
            f"STRUCTURAL_FEATURE_FOUND: {mr.get('STRUCTURAL_FEATURE_FOUND')}",
            "",
            f"RECOMMENDED_ACTION: {mr.get('RECOMMENDED_ACTION')}",
            "",
            "",
            f"NEW_HYPOTHESES_CREATED: {out.get('NEW_HYPOTHESES_CREATED')}",
            "",
            f"LLM_USED: {out.get('LLM_USED')}",
            "",
            "PRODUCTION_TRADING_CHANGED:",
            "NO",
            "",
            f"NEXT_RESEARCH_ACTION: {out.get('NEXT_RESEARCH_ACTION')}",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _strategy_md(sid: str, card: dict[str, Any], block: dict[str, Any]) -> list[str]:
    top = block.get("top_contributors") or {}
    blocks = block.get("chrono_blocks") or {}
    ts = top.get("top_symbol") or {}
    tv = top.get("top_venue_pair") or {}
    tb = top.get("top_chrono_block") or {}
    tot = block.get("forensic_totals") or {}
    lines = [
        f"## {sid}",
        "",
        f"- Parent verdict: `{block.get('parent_verdict')}` / `{block.get('parent_failed_gate')}`",
        f"- Frozen params: `{block.get('frozen_params')}`",
        f"- Tournament EXPECTED_NET: {_fmt(block.get('tournament_expected_net'))} (unchanged)",
        f"- Forensic sum NET: {_fmt(tot.get('NET'))} over {tot.get('signals')} OOS events",
        f"- Route tautology: {top.get('route_share_tautology')}",
        "",
        "### Top contributors (descriptive)",
        "",
        f"- Top 1 symbols NET: {_fmt((top.get('top_1') or {}).get('NET'))} ({_pct((top.get('top_1') or {}).get('share_of_total_net'))})",
        f"- Top 5 symbols NET: {_fmt((top.get('top_5') or {}).get('NET'))} ({_pct((top.get('top_5') or {}).get('share_of_total_net'))})",
        f"- Top 10 symbols NET: {_fmt((top.get('top_10') or {}).get('NET'))} ({_pct((top.get('top_10') or {}).get('share_of_total_net'))})",
        f"- HHI abs-forward (symbols): {_fmt(top.get('herfindahl_symbol_abs_forward'))}",
        f"- Top symbol: `{ts.get('group')}` NET={_fmt(ts.get('NET'))} ({_pct(ts.get('share'))}); rest={_fmt(ts.get('rest_NET'))}",
        f"- Top venue pair: `{tv.get('group')}` NET={_fmt(tv.get('NET'))} ({_pct(tv.get('share'))})",
        f"- Top hour: `{(top.get('top_hour') or {}).get('group')}` NET={_fmt((top.get('top_hour') or {}).get('NET'))} ({_pct((top.get('top_hour') or {}).get('share'))})",
        f"- Top chrono block: `{tb.get('group')}` NET={_fmt(tb.get('NET'))} ({_pct(tb.get('share'))})",
        f"- Top 10 events share: {_pct(top.get('top_10_trades_share'))}",
        "",
        "### Chronological blocks (equal width on frozen OOS; not chosen by PnL)",
        "",
        f"- Positive blocks: {blocks.get('positive_blocks')}",
        f"- Negative blocks: {blocks.get('negative_blocks')}",
        f"- Median block PnL: {_fmt(blocks.get('median_block_PnL'))}",
        f"- Mean block PnL: {_fmt(blocks.get('mean_block_PnL'))}",
        f"- Best: {(blocks.get('best_block') or {}).get('group')} NET={_fmt((blocks.get('best_block') or {}).get('NET'))}",
        f"- Worst: {(blocks.get('worst_block') or {}).get('group')} NET={_fmt((blocks.get('worst_block') or {}).get('NET'))}",
        "",
        "| Block | signals | gross | fees | slippage | adverse | NET | NET/trade |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in blocks.get("blocks") or []:
        lines.append(
            f"| {row.get('group')} | {row.get('signals')} | {_fmt(row.get('gross'))} | "
            f"{_fmt(row.get('fees'))} | {_fmt(row.get('slippage'))} | {_fmt(row.get('adverse'))} | "
            f"{_fmt(row.get('NET'))} | {_fmt(row.get('NET_per_trade'))} |"
        )
    lines.extend(
        [
            "",
            "### Classification",
            "",
            f"- CONCENTRATION_SOURCE: {card.get('CONCENTRATION_SOURCE')}",
            f"- CONCENTRATION_CLASS: `{card.get('CONCENTRATION_CLASS')}`",
            f"- STRUCTURAL_FEATURE_FOUND: {card.get('STRUCTURAL_FEATURE_FOUND')}",
            f"- RECOMMENDED_ACTION: {card.get('RECOMMENDED_ACTION')}",
            "",
        ]
    )
    notes = ((block.get("classification") or {}).get("notes")) or []
    for n in notes:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("### Leave-one-group-out (forensic; not used to drop losers)")
    lines.append("")
    loo = block.get("leave_one_out") or {}
    for name in ("symbol", "venue_pair", "chrono_block"):
        pack = loo.get(name) or {}
        lines.append(f"**{name}** FULL={_fmt(pack.get('FULL_RESULT'))}")
        for row in (pack.get("rows") or [])[:8]:
            lines.append(
                f"- WITHOUT `{row.get('left_out')}`: {_fmt(row.get('WITHOUT'))} "
                f"(group NET={_fmt(row.get('group_NET'))}"
                f"{'; SIGN_FLIP' if row.get('sign_flip') else ''})"
            )
        lines.append("")
    lines.append("### Regime contrast (pre-trade features only)")
    lines.append("")
    for name, reg in (block.get("regime_explanation") or {}).items():
        lines.append(
            f"- {name}: focus=`{reg.get('focus_group')}` share={_pct(reg.get('share_of_total_net'))} "
            f"structural={reg.get('structural')} features={reg.get('structural_features')}"
        )
    lines.append("")
    nulls = block.get("null_checks") or {}
    lines.extend(
        [
            "### Null checks (fixed seed; not an alpha claim)",
            "",
            f"- seed={nulls.get('seed')} N={nulls.get('n_permutations')}",
            f"- top symbol abs-forward share: {_pct(nulls.get('observed_top_symbol_abs_forward_share'))} "
            f"p_signal={_fmt(nulls.get('p_permute_signal_top_symbol'))}",
            f"- top block abs-net share: {_pct(nulls.get('observed_top_block_net_share'))} "
            f"p_rotate={_fmt(nulls.get('p_rotate_chrono_top_block'))}",
            "",
        ]
    )
    return lines
