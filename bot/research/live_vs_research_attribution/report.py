"""Markdown report generation."""

from __future__ import annotations

from typing import Any


def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


def write_report(report: dict[str, Any]) -> str:
    lines: list[str] = ["# Live vs Research Attribution Report", ""]

    # 1 Executive Summary
    es = report.get("executive_summary") or {}
    lines.append(_section(
        "1. Executive Summary",
        "\n".join([
            f"**Generated:** {report.get('generated_at', 'unknown')}",
            "",
            es.get("headline", ""),
            "",
            "**Key findings:**",
            *[f"- {b}" for b in es.get("bullets", [])],
            "",
            f"**Primary root cause:** {es.get('primary_root_cause', 'UNKNOWN')}",
            f"**Confidence:** {es.get('confidence', 'UNKNOWN')}",
        ]),
    ))

    # 2 What Research Actually Trades
    rs = report.get("strategy_mismatch") or {}
    lines.append(_section(
        "2. What Research Actually Trades",
        "\n".join([
            f"- **Strategy:** `{rs.get('research_strategy', 'cross_venue_dislocation')}`",
            f"- **Canonical replay NET:** €{_fmt((report.get('research_realism') or {}).get('canonical_replay_net_eur'))}",
            f"- **Signals (final validation):** {_fmt((report.get('research_realism') or {}).get('signal_count'))}",
            f"- **Route:** okx|bitvavo (frozen CVD candidate)",
            "- **Execution model:** Instant round-trip taker arb at depth-VWAP with canonical replay assumptions",
        ]),
    ))

    # 3 What Live Actually Trades
    lines.append(_section(
        "3. What Live Actually Trades",
        "\n".join([
            f"- **Strategy:** `{rs.get('live_strategy', 'maker_inventory alt-beta')}`",
            f"- **Executor:** `{rs.get('live_executor', 'MicroBudgetLiveExecutor')}`",
            f"- **Session realized PnL:** €{_fmt((report.get('sample') or {}).get('live_realized_pnl_eur'))}",
            f"- **Live fills (audit):** {_fmt((report.get('sample') or {}).get('live_fill_count'))}",
            f"- **Execute venues:** Bitvavo + OKX",
            "- **Execution model:** Maker paper + live taker; trail/exit engine; inventory FIFO",
        ]),
    ))

    # 4 Strategy Match / Mismatch
    table = rs.get("comparison_table") or []
    tbl_lines = ["| Component | Research | Live | Same? |", "|---|---|---|---|"]
    for row in table:
        tbl_lines.append(
            f"| {row['component']} | {row['research'][:80]}… | {row['live'][:80]}… | {row['same']} |"
            if len(row.get("research", "")) > 80
            else f"| {row['component']} | {row['research']} | {row['live']} | {row['same']} |"
        )
    lines.append(_section("4. Strategy Match / Mismatch", "\n".join(tbl_lines + ["", rs.get("summary", "")])))

    # 5 Opportunity Funnel
    funnel = report.get("funnel") or {}
    fl = ["| Stage | Count | Note |", "|---|---:|---|"]
    for st in funnel.get("stages") or []:
        fl.append(f"| {st['stage']} | {_fmt(st.get('count'))} | {st.get('note', '')} |")
    lines.append(_section("5. Opportunity Funnel", "\n".join(fl)))

    # 6 Skip Attribution
    skips = report.get("skip_attribution") or {}
    sl = [
        f"**Total skip events:** {skips.get('total_skip_events', 0)}",
        "",
        "| Reason | Count | % of skips | Expected NET |",
        "|---|---:|---:|---|",
    ]
    for reason, info in sorted(
        (skips.get("by_reason") or {}).items(),
        key=lambda x: -x[1].get("count", 0),
    )[:15]:
        sl.append(
            f"| `{reason}` | {info['count']} | {info['pct_of_skip_events']}% | "
            f"{_fmt(info.get('expected_net_total_eur'))} |"
        )
    ins = skips.get("insufficient_data") or []
    if ins:
        sl.extend(["", "**INSUFFICIENT_DATA:**", *[f"- {x}" for x in ins]])
    lines.append(_section("6. Skip Attribution", "\n".join(sl)))

    # 7-9 Profitability, GOE, Risk
    lines.append(_section(
        "7. Profitability Attribution",
        report.get("sections", {}).get("profitability", "INSUFFICIENT_DATA — per-opportunity profitability rejections not logged live."),
    ))
    goe = report.get("goe_attribution") or {}
    lines.append(_section(
        "8. GOE Attribution",
        "\n".join([
            f"- Historical audit replay candidates: {_fmt(goe.get('candidates'))}",
            f"- Rejected (GOE replay): {_fmt(goe.get('rejected'))} ({_fmt(goe.get('reject_rate'))})",
            f"- Estimated NET (accepted, replay): €{_fmt(goe.get('estimated_net_eur'))}",
            "- Live GOE enabled: **False** (default)",
            "- Note: Replay applies GOE to submitted buys only — selection bias.",
        ]),
    ))
    lines.append(_section(
        "9. Risk Attribution",
        report.get("sections", {}).get("risk", "INSUFFICIENT_DATA — per-opportunity risk decisions not exported to audit."),
    ))

    # 10 Execution
    exe = report.get("execution_attribution") or {}
    el = [
        f"- Filled orders: {exe.get('filled_count', 0)}",
        f"- Buy/Sell: {exe.get('buy_fills', 0)} / {exe.get('sell_fills', 0)}",
        f"- Total notional: €{_fmt(exe.get('total_notional_eur'))}",
        f"- Realized trade PnL (bridge): €{_fmt(exe.get('realized_trade_pnl_eur'))}",
        "",
        "**Degradation categories:**",
    ]
    for cat, n in (exe.get("degradation_category_counts") or {}).items():
        el.append(f"- {cat}: {n}")
    ins = exe.get("insufficient_data") or []
    if ins:
        el.extend(["", "**INSUFFICIENT_DATA:**", *[f"- {x}" for x in ins]])
    lines.append(_section("10. Execution Attribution", "\n".join(el)))

    # 11 Adverse Selection
    adv = report.get("adverse_selection") or {}
    lines.append(_section(
        "11. Adverse Selection",
        "\n".join([
            f"- Live attribution records: {_fmt(adv.get('live_attribution_records'))}",
            f"- Observation mode: {_fmt(adv.get('observation_mode'))}",
            f"- Phase21 toxic proxy (historical): {_fmt(adv.get('phase21_toxic_proxy_count'))}",
            f"- Phase21 avg adverse score: {_fmt(adv.get('phase21_avg_adverse_score'))}",
            *[f"- {x}" for x in (adv.get("insufficient_data") or [])],
        ]),
    ))

    # 12 Inventory
    inv = report.get("inventory_attribution") or {}
    lines.append(_section(
        "12. Position / Inventory Effects",
        "\n".join([
            f"- Inventory-related skips: {inv.get('total_inventory_skips', 0)}",
            f"- Locked notional: €{_fmt(inv.get('locked_notional_eur'))}",
            f"- Blocked sells (session): {_fmt(inv.get('blocked_sells_session'))}",
            f"- Open lots: {inv.get('session_lots_count', 0)}",
            "",
            "**Top inventory skips:**",
            *[f"- `{k}`: {v}" for k, v in sorted(
                (inv.get("inventory_related_skips") or {}).items(),
                key=lambda x: -x[1],
            )[:8]],
        ]),
    ))

    # 13 Exit
    lines.append(_section(
        "13. Exit Attribution",
        report.get("sections", {}).get(
            "exit",
            "Live-only exit engine (trail, soft-arm, time_stop_below_be, momentum exits). "
            "Research uses immediate round-trip close — not comparable.",
        ),
    ))

    # 14 Capital Efficiency
    cap = report.get("capital_efficiency") or {}
    lines.append(_section(
        "14. Capital Efficiency",
        "\n".join([
            f"- Locked notional: €{_fmt(cap.get('locked_notional_eur'))}",
            f"- Free quote: €{_fmt(cap.get('free_quote_eur'))}",
            f"- Portfolio value: €{_fmt(cap.get('portfolio_value_eur'))}",
            f"- Research NET/capital-hour: {_fmt(cap.get('research_net_per_capital_hour'))}",
            f"- Live realized NET/capital-hour: {_fmt(cap.get('live_net_per_capital_hour'))}",
            cap.get("note", ""),
        ]),
    ))

    # 15 Regime
    reg = report.get("regime_attribution") or {}
    lines.append(_section(
        "15. Regime Analysis",
        reg.get("summary", "INSUFFICIENT_DATA — regime not tagged on live fills in audit."),
    ))

    # 16 Venue
    ven = report.get("venue_attribution") or {}
    vl = ["| Venue | Fills | Notional EUR |", "|---|---:|---:|"]
    for v, info in (ven.get("by_venue") or {}).items():
        vl.append(f"| {v} | {info.get('fills', 0)} | {info.get('notional_eur', 'NULL')} |")
    lines.append(_section("16. Venue Analysis", "\n".join(vl)))

    # 17 Canonical vs realism
    rr = report.get("research_realism") or {}
    lines.append(_section(
        "17. Canonical vs Mild vs Moderate vs Live",
        "\n".join([
            "| Level | NET EUR | Notes |",
            "|---|---:|---|",
            f"| Canonical replay | €{_fmt(rr.get('canonical_replay_net_eur'))} | cross_venue_dislocation, 62 windows |",
            f"| Mild realism | €{_fmt(rr.get('mild_realism_net_eur'))} | +fee/slip/adverse/latency band |",
            f"| Moderate realism | €{_fmt(rr.get('moderate_realism_net_eur'))} | stronger degradation |",
            f"| Live realized (session) | €{_fmt(rr.get('live_realized_net_eur'))} | alt-beta maker book |",
            f"| Matched live sample | {_fmt(rr.get('matched_live_sample_net_eur'))} | research↔live match |",
            "",
            "**Interpretation:** Research and live are different strategies; direct NET comparison is diagnostic only.",
        ]),
    ))

    # 18 Data Quality
    dq = report.get("data_quality") or {}
    lines.append(_section(
        "18. Data Quality / Accounting Audit",
        "\n".join([
            f"- Fill event IDs unique: {(dq.get('fill_accounting') or {}).get('fill_event_id_unique')}",
            f"- Exchange order IDs unique: {(dq.get('fill_accounting') or {}).get('exchange_order_id_unique')}",
            f"- Timestamps monotonic: {(dq.get('timestamp_consistency') or {}).get('monotonic')}",
            f"- Attribution store empty: {dq.get('attribution_store_empty')}",
            f"- Missing sources: {dq.get('missing_sources') or []}",
        ]),
    ))

    # 19 Root Cause Ranking
    rcr = report.get("root_causes") or []
    rl = ["| Rank | Cause | Confidence | Evidence |", "|---:|---|---|---|"]
    for i, rc in enumerate(rcr, 1):
        rl.append(f"| {i} | {rc.get('cause')} | {rc.get('confidence')} | {rc.get('evidence', '')[:120]} |")
    lines.append(_section("19. Root Cause Ranking", "\n".join(rl)))

    # 20 Experiments
    exps = report.get("recommended_experiments") or []
    elines = []
    for ex in exps:
        elines.extend([
            f"### {ex.get('hypothesis', 'Experiment')}",
            f"- **Change:** {ex.get('change')}",
            f"- **Control:** {ex.get('control')}",
            f"- **Metric:** {ex.get('metric')}",
            f"- **Min sample:** {ex.get('minimum_sample_size')}",
            f"- **Success:** {ex.get('success_criterion')}",
            f"- **Rollback:** {ex.get('rollback')}",
            f"- **OOS protection:** {ex.get('oos_leakage_protection')}",
            "",
        ])
    lines.append(_section("20. Recommended Next Experiments", "\n".join(elines)))

    # Final conclusions
    fc = report.get("final_conclusions") or []
    wnt = report.get("what_not_to_change_yet") or []
    lines.extend([
        "---",
        "",
        "## Final Conclusions",
        "",
        *[f"{i}. {c}" for i, c in enumerate(fc, 1)],
        "",
        "## What NOT to Change Yet",
        "",
        *[f"- {x}" for x in wnt],
    ])

    return "\n".join(lines)
