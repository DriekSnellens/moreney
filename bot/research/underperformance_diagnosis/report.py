"""Render underperformance diagnosis markdown + JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.underperformance_diagnosis.analyze import UnderperformanceAnalysis


def analysis_to_dict(analysis: UnderperformanceAnalysis) -> dict[str, Any]:
    return {
        "loaded_at": analysis.loaded_at,
        "elapsed_hours": str(analysis.elapsed_hours),
        "budget_eur": str(analysis.budget_eur),
        "free_eur": str(analysis.free_eur),
        "portfolio_eur": str(analysis.portfolio_eur),
        "bridge_realized_eur": str(analysis.bridge_realized_eur),
        "session_realized_eur": str(analysis.session_realized_eur),
        "capital_deployed_eur": str(analysis.capital_deployed_eur),
        "capital_locked_eur": str(analysis.capital_locked_eur),
        "active_ring": analysis.active_ring,
        "strategy": analysis.strategy,
        "daily_history": analysis.daily_history,
        "throughput": {
            "soft_exit_eur": str(analysis.throughput.soft_exit_eur),
            "soft_partial_eur": str(analysis.throughput.soft_partial_eur),
            "exits_for_target": analysis.throughput.exits_for_target,
            "ring_turns_for_target": {
                k: str(v) for k, v in analysis.throughput.ring_turns_for_target.items()
            },
            "note": analysis.throughput.note,
        },
        "root_causes": [
            {
                "rank": c.rank,
                "cause_id": c.cause_id,
                "severity": c.severity,
                "category": c.category,
                "headline": c.headline,
                "detail": c.detail,
                "evidence": c.evidence,
                "levers": c.levers,
            }
            for c in analysis.root_causes
        ],
        "expectation_note": analysis.expectation_note,
        "verdict": analysis.verdict,
        "recommended_routes": analysis.recommended_routes,
    }


def render_markdown(analysis: UnderperformanceAnalysis) -> str:
    t = analysis.throughput
    lines: list[str] = [
        "# Live Underperformance Diagnosis — vs €20–100/day Expectation",
        "",
        f"**Generated:** {analysis.loaded_at}",
        "",
        "Research-only. No live logic, parameters, or safety gates changed.",
        "",
        "## 1. Executive Summary",
        "",
        analysis.verdict,
        "",
        "### Snapshot",
        "",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Strategy | `{analysis.strategy}` |",
        f"| Budget / free EUR | €{analysis.budget_eur} / €{analysis.free_eur} |",
        f"| Portfolio | €{analysis.portfolio_eur} |",
        f"| Bridge realized (cum) | €{analysis.bridge_realized_eur} |",
        f"| Session realized NET | €{analysis.session_realized_eur} |",
        f"| Capital deployed / locked | €{analysis.capital_deployed_eur} / €{analysis.capital_locked_eur} |",
        f"| Session elapsed | {analysis.elapsed_hours} h |",
        "",
        f"**Active ring:** {analysis.active_ring.get('why_idle')}",
        "",
        f"**Underwater blocks:** `{analysis.active_ring.get('underwater_blocked_bases')}`",
        "",
        "## 2. Where does €50–100/day come from?",
        "",
        analysis.expectation_note,
        "",
        "## 3. Throughput math (what €50/day actually requires)",
        "",
        t.note,
        "",
        "| Target | Full soft exits / day | Ring turns @ 1.2% on €1k |",
        "|--------|----------------------:|-------------------------:|",
        f"| Doc €20 | {t.exits_for_target['doc_20']} | {t.ring_turns_for_target['doc_20']}× |",
        f"| Doc €50 | {t.exits_for_target['doc_50']} | {t.ring_turns_for_target['doc_50']}× |",
        f"| User €50 | {t.exits_for_target['user_50']} | {t.ring_turns_for_target['user_50']}× |",
        f"| User €100 | {t.exits_for_target['user_100']} | {t.ring_turns_for_target['user_100']}× |",
        "",
        "Current session: **0** new live trades → **0** recycles → target unreachable.",
        "",
        "## 4. Recent daily history (dashboard)",
        "",
        "| Day | Points | Realized end | Session end | Session peak | Free end |",
        "|-----|-------:|-------------:|------------:|-------------:|---------:|",
    ]
    for d in analysis.daily_history:
        lines.append(
            f"| {d['day']} | {d['n_points']} | {d['realized_end']} | "
            f"{d['session_end']} | {d['session_peak']} | {d['free_end']} |"
        )
    lines.extend(
        [
            "",
            "## 5. Ranked root causes",
            "",
        ]
    )
    for c in analysis.root_causes:
        lines.extend(
            [
                f"### #{c.rank} [{c.severity}] `{c.cause_id}` ({c.category})",
                "",
                f"**{c.headline}**",
                "",
                c.detail,
                "",
                "Evidence:",
                "",
            ]
        )
        for e in c.evidence:
            lines.append(f"- `{e}`")
        lines.extend(["", "Levers:", ""])
        for lever in c.levers:
            lines.append(f"- {lever}")
        lines.append("")
    lines.extend(
        [
            "## 6. Recommended routes",
            "",
        ]
    )
    for i, route in enumerate(analysis.recommended_routes, 1):
        lines.append(f"{i}. {route}")
    lines.extend(
        [
            "",
            "## 7. What this is *not*",
            "",
            "- Not primarily a missing-coin / focus-list issue (see prior focus what-if).",
            "- Not primarily current OKX clOrdId failures (historical; fixes landed).",
            "- Not evidence that CVD research €212k extrapolates to this €2k pocket.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    analysis: UnderperformanceAnalysis,
    *,
    md_path: Path = Path("docs/LIVE_UNDERPERFORMANCE_DIAGNOSIS.md"),
    json_path: Path = Path("data/research/live_underperformance_diagnosis.json"),
) -> tuple[Path, Path]:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = analysis_to_dict(analysis)
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(analysis))
    return md_path, json_path


__all__ = ["analysis_to_dict", "render_markdown", "write_report"]
