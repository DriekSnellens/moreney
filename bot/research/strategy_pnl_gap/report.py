"""Markdown report for paper vs research PnL gap."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _fmt(v: object) -> str:
    if v is None:
        return "INSUFFICIENT_DATA"
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def write_report(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    data = payload.get("data") or {}
    lines: list[str] = [
        "# Paper vs Research PnL Gap — Strategy Mismatch (CVD vs maker_inventory)",
        "",
        f"**Generated:** {payload.get('generated_at', 'unknown')}",
        "",
        "Research-only. No live logic, parameters, or safety gates changed.",
        "",
        "## 1. Executive Summary",
        "",
        analysis.get("verdict", ""),
        "",
    ]

    lines.append("### Comparison at a glance")
    lines.append("")
    lines.append("| World | Strategy | PnL / expected | Unit count | €/unit | Execution model |")
    lines.append("|-------|----------|---------------:|-----------:|-------:|-----------------|")
    for row in analysis.get("comparison_table") or []:
        lines.append(
            f"| {row.get('world')} | `{row.get('strategy_id')}` | "
            f"€{_fmt(row.get('realized_or_expected_eur'))} | "
            f"{row.get('trade_or_signal_count') or '—'} | "
            f"{_fmt(row.get('per_unit_eur')) if row.get('per_unit_eur') is not None else '—'} | "
            f"{row.get('execution_model', '')[:60]} |"
        )
    lines.append("")

    cvd = analysis.get("paper_cvd_aggregate") or {}
    lines.extend(
        [
            "## 2. Paper Fleet — waar komt de +€3.4k vandaan?",
            "",
            "De vijf capital twins (`200live`–`25000live`) draaien `_build_strategy()` met "
            "`paper_maker_enabled=false` → `cross_exchange_arbitrage` + funding. "
            "**Maar ~99% van realized PnL komt uit CVD inject** (`cross_venue_dislocation`), "
            "niet uit de geselecteerde arb-strategie.",
            "",
            f"- **Aggregate CVD realized:** €{cvd.get('net_pnl_eur', 'INSUFFICIENT_DATA')}",
            f"- **Trades:** {cvd.get('trades', '—')} | **Executions:** {cvd.get('executions', '—')} | "
            f"**Opportunities seen:** {cvd.get('opportunities_seen', '—')}",
            f"- **Execution rate (exec/opp):** {cvd.get('execution_rate', '—')}",
            f"- **€/trade (paper CVD):** {cvd.get('pnl_per_trade_eur', '—')}",
            f"- **€/signal (research canonical @€100):** {cvd.get('research_pnl_per_signal_eur', '—')}",
            "",
            "Per instance (tracker.realized_pnl):",
            "",
        ]
    )
    for inst in data.get("paper_instances") or []:
        if inst.get("name") == "lab_strategy":
            continue
        lines.append(
            f"- **{inst.get('name')}**: start €{inst.get('starting_eur')} → equity €{_fmt(inst.get('equity_eur'))} | "
            f"realized €{_fmt(inst.get('tracker_realized_eur'))} | "
            f"primary `{inst.get('primary_strategy')}`"
        )
    lines.append("")

    parity = data.get("parity") or {}
    shadow = data.get("shadow") or {}
    lines.extend(
        [
            "## 3. Research CVD — wat meet canonical replay?",
            "",
            f"- **CANONICAL_REPLAY_NET:** €{_fmt(data.get('research_canonical_net_eur'))}",
            f"- **Signals:** {data.get('research_signal_count', 'INSUFFICIENT_DATA')}",
            f"- **MODERATE_REALISM NET:** €{_fmt(data.get('research_moderate_net_eur'))}",
            "",
            "Hypothese: okx|bitvavo **mid** dislocatie ≥40 bps convergeert over 5s. "
            "Kosten: taker/taker round-trip (~35 bps) + slip/adverse/latency. "
            "Baseline: **elke signal = fill** (fill_prob=1.0).",
            "",
            "## 4. Waarom research ≠ paper ≠ live",
            "",
        ]
    )
    for comp in analysis.get("components") or []:
        lines.append(f"### [{comp.get('severity')}] {comp.get('component_id')}")
        lines.append("")
        lines.append(f"**{comp.get('headline')}**")
        lines.append("")
        lines.append(comp.get("detail", ""))
        if comp.get("quantified_eur") is not None:
            lines.append("")
            lines.append(
                f"*Quantified:* {comp.get('quantified_label', 'delta')} = "
                f"€{_fmt(comp.get('quantified_eur'))}"
            )
        lines.append("")

    lines.extend(
        [
            "## 5. Economic parity & shadow (zelfde CVD, andere wereld)",
            "",
            f"- Parity candidates: **{parity.get('total_candidates', '—')}**",
            f"- Pass research / fail live: **{parity.get('research_pass_live_fail', '—')}** "
            f"({parity.get('root_cause', 'DIFFERENT_PRICE_SELECTION')})",
            f"- Shadow candidates: **{shadow.get('n_candidates', '—')}** "
            f"(windows {shadow.get('complete_windows', 0)}/{shadow.get('min_windows', 20)} complete)",
            f"- Shadow RESEARCH_EXPECTED_NET: **€{_fmt(shadow.get('RESEARCH_EXPECTED_NET'))}**",
            f"- Shadow LIVE_SHADOW_EXECUTION_NET: **€{_fmt(shadow.get('LIVE_SHADOW_EXECUTION_NET'))}**",
            f"- Mean execution gap: **€{_fmt((shadow.get('execution_gap') or {}).get('mean'))}**/observation",
            "",
            "Voorbeeld (parity sample): BTCEUR 161 bps dislocation → research +€1.14, live NET −€1.77 "
            "omdat leader_ask > follower_ask terwijl mids divergeren.",
            "",
            "## 6. Live maker_inventory",
            "",
            f"- **Bridge realized PnL:** €{_fmt(data.get('live_realized_eur'))}",
            f"- **Portfolio MTM:** €{_fmt(data.get('live_portfolio_eur'))}",
            "- Economie: post-only maker quotes, sequential buy→trail sell, **never sell below break-even**",
            "- Dominant skips: `time_stop_below_be`, `trail_dust`, `focus_base_required`, `buy_quality_pause`",
            "",
            "## 7. Eerlijke vergelijking (aanbevolen)",
            "",
        ]
    )
    for note in analysis.get("fair_comparison_notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 8. Conclusie")
    lines.append("")
    lines.append(
        "De gap is **verwacht** zolang paper-fleet success op **CVD inject** wordt afgelezen "
        "terwijl live op **maker_inventory** draait. Research +€212k is geen voorspelling voor "
        "live maker PnL; het is een upper bound op een **ander product** (mid-dislocation taker arb). "
        "Shadow/parity tonen dat zelfs binnen CVD de live pricing formule ~100% reject geeft. "
        "Volgende research-stap: voltooi shadow validation (20 windows) en vergelijk "
        "`paper_lab` (maker) met live micro op identieke config — niet paper_200live vs canonical."
    )
    lines.append("")
    return "\n".join(lines)
