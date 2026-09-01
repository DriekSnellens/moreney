"""Markdown report for live execution diagnosis."""

from __future__ import annotations

from typing import Any


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


def write_report(data: dict[str, Any]) -> str:
    err = data.get("exchange_errors") or {}
    gap = data.get("buy_fill_gap") or {}
    lines: list[str] = [
        "# Live Execution Diagnosis — ExchangeError & Buy Fill Gap",
        "",
        f"**Generated:** {data.get('generated_at', 'unknown')}",
        "",
        "Research-only analysis of `data/live_audit.jsonl`. No live trading logic changed.",
        "",
    ]

    # Executive summary
    top_err = (err.get("buckets") or [{}])[0] if err.get("buckets") else {}
    bullets = [
        f"**{err.get('total_exceptions', 0)}** `micro_order_exception` events in audit.",
        f"Top error class: **{top_err.get('category', 'N/A')}** ({top_err.get('pct', 0)}%).",
        f"Buy fills in audit: **{gap.get('buy_filled', 0)}** vs sell fills: **{gap.get('sell_filled', 0)}**.",
        f"Bitvavo buys stuck at submitted: **{gap.get('buy_submitted', 0)}** (resting maker quotes).",
    ]
    lines.append(_section("1. Executive Summary", "\n".join(f"- {b}" for b in bullets)))

    # Exchange errors
    err_rows = [
        "| Category | Count | % | Sample |",
        "|----------|------:|--:|--------|",
    ]
    for b in err.get("buckets") or []:
        sample = str(b.get("sample_message") or "")[:80].replace("|", "/")
        err_rows.append(
            f"| {b.get('category')} | {b.get('count')} | {b.get('pct')} | {sample} |"
        )
    spike_lines = ["| Hour | Exceptions | Submits | Ratio |", "|------|----------:|--------:|------:|"]
    for s in err.get("hourly_spikes") or []:
        spike_lines.append(
            f"| {s.get('hour')} | {s.get('exceptions')} | {s.get('order_submits')} | {s.get('submit_exc_ratio')} |"
        )
    err_body = "\n".join(err_rows) + "\n\n### Hourly spikes\n\n" + "\n".join(spike_lines)
    if err.get("okx_clord_samples"):
        err_body += "\n\n**OKX clOrdId samples from rejections:** `" + "`, `".join(
            err["okx_clord_samples"][:5]
        ) + "`"
    for note in err.get("notes") or []:
        err_body += f"\n\n> {note}"
    lines.append(_section("2. ExchangeError Breakdown", err_body))

    # Buy fill gap
    vss_rows = [
        "| Venue | Side | Status | Count |",
        "|-------|------|--------|------:|",
    ]
    for row in gap.get("by_venue_side_status") or []:
        vss_rows.append(
            f"| {row.get('venue')} | {row.get('side')} | {row.get('status')} | {row.get('count')} |"
        )
    gap_body = "\n".join(
        [
            f"- Buy: submitted={gap.get('buy_submitted')}, filled={gap.get('buy_filled')}, "
            f"pending={gap.get('buy_pending')}, cancelled={gap.get('buy_cancelled')}",
            f"- Sell: submitted={gap.get('sell_submitted')}, filled={gap.get('sell_filled')}, "
            f"pending={gap.get('sell_pending')}, cancelled={gap.get('sell_cancelled')}",
            f"- Filled sell notional (audit): €{gap.get('filled_sell_notional_eur')}",
            f"- Submitted buy notional (audit): €{gap.get('submitted_buy_notional_eur')}",
            f"- Bridge live_fill_count: {gap.get('bridge_live_fill_count')}",
            f"- Bridge backfill_mirrored_count: {gap.get('bridge_backfill_count')}",
            f"- live_maker: {gap.get('live_maker')}",
            "",
            "### Venue × side × status",
            "",
            "\n".join(vss_rows),
        ]
    )
    if gap.get("filled_symbols"):
        gap_body += "\n\n### Filled sell symbols\n\n"
        gap_body += "\n".join(f"- {sym}: {n}" for sym, n in gap["filled_symbols"])
    if gap.get("order_blocked_top"):
        gap_body += "\n\n### order_blocked (top)\n\n"
        gap_body += "\n".join(f"- {r}: {n}" for r, n in gap["order_blocked_top"])
    if gap.get("bridge_skips_top"):
        gap_body += "\n\n### Bridge skip counters (top)\n\n"
        gap_body += "\n".join(f"- {k}: {n}" for k, n in gap["bridge_skips_top"])
    rc_lines = []
    for rc in gap.get("root_causes") or []:
        rc_lines.append(f"- **[{rc.get('severity')}] {rc.get('id')}:** {rc.get('summary')}")
    if rc_lines:
        gap_body += "\n\n### Root causes\n\n" + "\n".join(rc_lines)
    for note in gap.get("notes") or []:
        gap_body += f"\n\n> {note}"
    lines.append(_section("3. Buy Fill Gap Analysis", gap_body))

    lines.append(
        _section(
            "4. Interpretation",
            "\n".join(
                [
                    "1. **OKX was effectively down** for this session window: ~99% of exceptions are "
                    "`OKX_CLORDID_REJECTED`, concentrated in SOLEUR submit bursts (Aug 22).",
                    "2. **Buys and sells use different execution economics:** with `live_maker=true`, "
                    "buys rest on the book (`submitted`); sells cross when profitable vs break-even.",
                    "3. **Zero buy fills in audit does not prove zero buy fills on exchange** — "
                    "async resting fills are mirrored in bridge state without a second `micro_order_result`.",
                    "4. **Session PnL is driven by sell-down of existing inventory** (backfill-mirrored "
                    "cost basis), not a balanced round-trip like research CVD.",
                    "5. **Paper vs live divergence** is amplified by strategy mismatch (CVD vs maker_inventory) "
                    "and accounting split (bridge FIFO vs paper portfolio).",
                ]
            ),
        )
    )
    return "\n".join(lines) + "\n"
