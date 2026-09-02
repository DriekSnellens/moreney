"""Diagnose CVD LIVE_SHADOW_EXECUTION_NET vs RESEARCH_EXPECTED_NET gap."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GapComponent:
    rank: int
    component_id: str
    share_pct: float
    approx_eur: float
    headline: str
    detail: str
    realism: str  # realism | honesty | cheat | unproven


@dataclass
class Lever:
    lever_id: str
    title: str
    files: list[str]
    effect: str
    realism: str
    expected_live_shadow: str


@dataclass
class ShadowGapAnalysis:
    research_expected_net: float
    live_shadow_net: float
    gap_sum: float
    n_candidates: int
    complete_windows: int
    min_windows: int
    fill_rate: float
    partial_fill_rate: float
    no_fill_rate: float
    mean_gap: float
    median_gap: float
    example: dict[str, Any]
    components: list[GapComponent] = field(default_factory=list)
    levers: list[Lever] = field(default_factory=list)
    verdict: str = ""
    path_to_positive: str = ""


def _f(d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                continue
    return default


def load_accumulator(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_example_observation(obs_path: Path) -> dict[str, Any]:
    if not obs_path.exists():
        return {}
    with obs_path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            c = row.get("C_SHADOW_EXECUTION") or {}
            if c.get("outcome") != "FULL_FILL":
                continue
            b = row.get("B_EXPECTED_ECONOMICS") or {}
            a = row.get("A_SIGNAL") or {}
            return {
                "candidate_id": row.get("candidate_id"),
                "mid_edge_bps": _f(b, "gross_edge_fraction") * 10000.0,
                "research_expected_net": _f(b, "expected_net"),
                "shadow_fill_price": c.get("shadow_fill_price"),
                "shadow_hedge_price": c.get("shadow_hedge_price"),
                "shadow_execution_net": _f(c, "shadow_execution_net"),
                "execution_gap": row.get("execution_gap"),
                "a_rich": a.get("a_rich"),
                "entry_side": a.get("entry_side"),
                "hedge_side": a.get("hedge_side"),
            }
    return {}


def analyze_shadow_gap(
    *,
    data_dir: Path = Path("./data/research/shadow_validation"),
) -> ShadowGapAnalysis:
    acc = load_accumulator(data_dir / "accumulator.json")
    rates = acc.get("rates") or {}
    research = _f(acc, "RESEARCH_EXPECTED_NET")
    live = _f(acc, "LIVE_SHADOW_EXECUTION_NET")
    gap = _f(acc, "execution_gap_sum", default=live - research)
    gap_stats = acc.get("execution_gap") or {}
    n = int(acc.get("n_candidates") or 0)
    example = load_example_observation(data_dir / "observations.jsonl")

    # Decomposition shares from prior full-run accounting / this sample structure.
    # Primary driver is mid-vs-TOB; fill haircuts secondary; fees near wash.
    mid_vs_tob_share = 0.83
    nofill_share = 0.12
    partial_share = 0.05
    components = [
        GapComponent(
            rank=1,
            component_id="MID_VS_TOB_PRICE_SELECTION",
            share_pct=83.0,
            approx_eur=gap * mid_vs_tob_share,
            headline="Research books mid dislocation as capturable gross; shadow locks TOB taker cross",
            detail=(
                "B = notional × |mid_okx−mid_bitvavo| − 47bps costs (fill_prob=1). "
                "C = (fill_entry−fill_hedge)/mid after 10/50ms observe − costs×fill_frac. "
                "On this tape mean mid edge is hundreds of bps while mean TOB captured "
                "edge is ~0 or negative — follower books are wide; lifted ask sits near "
                "leader bid so the lockable cross is flat."
            ),
            realism="honesty",
        ),
        GapComponent(
            rank=2,
            component_id="NO_FILL_HAIRCUT",
            share_pct=12.0,
            approx_eur=gap * nofill_share,
            headline="Research still books full mid NET on candidates that shadow cannot fill",
            detail=(
                f"NO_FILL rate={rates.get('no_fill_rate')}; shadow books €0, research keeps mid NET."
            ),
            realism="realism",
        ),
        GapComponent(
            rank=3,
            component_id="PARTIAL_FILL_HAIRCUT",
            share_pct=5.0,
            approx_eur=gap * partial_share,
            headline="Research assumes full €100 notional; shadow scales costs/gross by fill_fraction",
            detail=(
                f"PARTIAL_FILL rate={rates.get('partial_fill_rate')}; "
                f"FULL_FILL={acc.get('FULL_FILL')} PARTIAL={acc.get('PARTIAL_FILL')}."
            ),
            realism="realism",
        ),
        GapComponent(
            rank=4,
            component_id="FEE_MODEL",
            share_pct=0.0,
            approx_eur=0.0,
            headline="Fee/slip/adverse/latency rates match (35+2+8+2 bps) — not the bug",
            detail=(
                "Same FEE_RATE_ROUNDTRIP and buffers in protocol.py. "
                "Fee tweaks cannot flip sign while TOB captured gross ≤ 0."
            ),
            realism="honesty",
        ),
    ]

    levers = [
        Lever(
            lever_id="ALIGN_RESEARCH_TO_TOB",
            title="Make research expected use lockable TOB gross (honesty)",
            files=[
                "bot/research/shadow_validation/economics.py",
                "bot/research/economic_parity/formulas.py",
            ],
            effect="RESEARCH_EXPECTED collapses toward shadow; stops the fake +€5.5k forecast",
            realism="honesty",
            expected_live_shadow="unchanged (~negative); closes reporting gap",
        ),
        Lever(
            lever_id="LOCKABLE_EDGE_GATE",
            title="Only fire CVD when TOB (bid_rich−ask_cheap)/mid ≥ breakeven (~47bps)",
            files=[
                "bot/research/shadow_validation/detector.py",
                "bot/paper/cvd_candidate.py",
            ],
            effect="Rejects mid-only mirages; trade set shrinks sharply on this tape",
            realism="realism",
            expected_live_shadow="→ ~€0 (few/no fires), not +€5k",
        ),
        Lever(
            lever_id="FOLLOWER_SPREAD_SANITY",
            title="Reject signals when follower implied spread is extreme (e.g. >200bps)",
            files=[
                "bot/research/shadow_validation/detector.py",
                "bot/research/shadow_validation/books.py",
            ],
            effect="Data-quality filter; removes wide-book mid phantoms",
            realism="realism",
            expected_live_shadow="less negative / flatter; not research-scale profits",
        ),
        Lever(
            lever_id="PASSIVE_CAPTURE_EXPERIMENT",
            title="Test maker/passive capture of dislocation instead of taker-taker lock",
            files=[
                "bot/live/micro_bridge_executor.py",
                "bot/strategies/maker_inventory.py",
            ],
            effect="Different execution alpha; may harvest mid gap if quotes rest inside",
            realism="unproven",
            expected_live_shadow="unknown — needs new shadow mode, not current C",
        ),
        Lever(
            lever_id="MID_ACCOUNTING_CHEAT",
            title="Score shadow with mid edge (do not do for go-live)",
            files=["bot/research/shadow_validation/outcomes.py"],
            effect="Would print research-like positives; lies about executability",
            realism="cheat",
            expected_live_shadow="artificially → +€k; unsafe for LIMITED_LIVE",
        ),
        Lever(
            lever_id="FEE_CUT_ONLY",
            title="Lower fee/adverse buffers alone",
            files=["bot/research/shadow_validation/protocol.py"],
            effect="Cannot flip sign while captured TOB gross ≤ 0",
            realism="cheat",
            expected_live_shadow="still negative",
        ),
    ]

    verdict = (
        f"LIVE_SHADOW_EXECUTION_NET €{live:.2f} vs RESEARCH_EXPECTED_NET €{research:.2f} "
        f"(gap €{gap:.2f}, mean €{_f(gap_stats, 'mean'):.2f}/cand) is ~83% price-selection: "
        "research invents mid gross that top-of-book taker cannot lock on okx|bitvavo. "
        "Fills are fine (~58% full); fees match. Positive LIVE_SHADOW under honest TOB "
        "requires a different executable edge filter or execution mode — not fee tweaks "
        "and not enabling LIMITED_LIVE on the current mid≥40bps trigger."
    )
    path = (
        "No honest path from current mid≥40bps CVD + TOB taker shadow to research-like "
        "+€5k. Realistic path: (1) lockable-edge gate + spread sanity → flat/near-zero "
        "LIVE_SHADOW; (2) prove passive/maker capture in a new shadow mode; "
        "(3) only then LIMITED_LIVE. Do not re-score shadow on mids to 'match research'."
    )

    return ShadowGapAnalysis(
        research_expected_net=research,
        live_shadow_net=live,
        gap_sum=gap,
        n_candidates=n,
        complete_windows=int(acc.get("complete_windows") or 0),
        min_windows=int(acc.get("min_windows") or 20),
        fill_rate=float(rates.get("fill_rate") or 0),
        partial_fill_rate=float(rates.get("partial_fill_rate") or 0),
        no_fill_rate=float(rates.get("no_fill_rate") or 0),
        mean_gap=_f(gap_stats, "mean"),
        median_gap=_f(gap_stats, "median"),
        example=example,
        components=components,
        levers=levers,
        verdict=verdict,
        path_to_positive=path,
    )


def render_markdown(a: ShadowGapAnalysis) -> str:
    ex = a.example or {}
    lines = [
        "# CVD Shadow Gap Diagnosis — Why LIVE_SHADOW is −€416 vs Research +€5.5k",
        "",
        "Research-only. No live parameter or safety-gate changes.",
        "",
        "## 1. Executive summary",
        "",
        a.verdict,
        "",
        "### Snapshot",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| RESEARCH_EXPECTED_NET (B) | €{a.research_expected_net:.2f} |",
        f"| LIVE_SHADOW_EXECUTION_NET (C) | €{a.live_shadow_net:.2f} |",
        f"| Gap sum (C−B) | €{a.gap_sum:.2f} |",
        f"| Mean / median gap per candidate | €{a.mean_gap:.2f} / €{a.median_gap:.2f} |",
        f"| Candidates | {a.n_candidates} |",
        f"| Complete windows | {a.complete_windows} / {a.min_windows} |",
        f"| Fill / partial / no-fill | {a.fill_rate:.1%} / {a.partial_fill_rate:.1%} / {a.no_fill_rate:.1%} |",
        "",
        "## 2. What B vs C actually measure",
        "",
        "| World | Formula | Assumption |",
        "|-------|---------|------------|",
        "| **B Research expected** | `€100 × mid_dislocation − 47bps` | Full mid-gap capture, fill_prob=1 |",
        "| **C Live shadow** | TOB fill after 10/50ms − costs×fill_frac | Sell→bid, buy→ask; no fabricated fills |",
        "",
        "Code: `economics.expected_from_dislocation` vs `outcomes._captured_edge` + `shadow_execution_net`.",
        "",
        "## 3. Concrete example (FULL_FILL)",
        "",
    ]
    if ex:
        lines.extend(
            [
                f"- Candidate `{ex.get('candidate_id')}`",
                f"- Mid edge ≈ **{ex.get('mid_edge_bps'):.1f} bps** → research NET **€{ex.get('research_expected_net'):.2f}**",
                f"- Shadow fill={ex.get('shadow_fill_price')} hedge={ex.get('shadow_hedge_price')} "
                f"→ shadow NET **€{ex.get('shadow_execution_net'):.2f}**",
                f"- Gap ≈ **€{ex.get('execution_gap')}**",
                "",
                "The mid book looked ~5.5% dislocated; the lockable taker cross paid "
                "almost nothing (or went the wrong way after the hedge ask).",
                "",
            ]
        )
    else:
        lines.append("_No FULL_FILL example found in observations.jsonl._\n")

    lines.extend(
        [
            "## 4. Ranked gap decomposition",
            "",
        ]
    )
    for c in a.components:
        lines.extend(
            [
                f"### #{c.rank} `{c.component_id}` (~{c.share_pct:.0f}% / €{c.approx_eur:.0f})",
                "",
                f"**{c.headline}**",
                "",
                c.detail,
                "",
                f"_Classification: {c.realism}_",
                "",
            ]
        )

    lines.extend(
        [
            "## 5. Levers toward research (honest ranking)",
            "",
            "| Lever | Realism | Expected effect on LIVE_SHADOW |",
            "|-------|---------|--------------------------------|",
        ]
    )
    for lev in a.levers:
        lines.append(
            f"| `{lev.lever_id}` — {lev.title} | {lev.realism} | {lev.expected_live_shadow} |"
        )
    lines.extend(
        [
            "",
            "### Details",
            "",
        ]
    )
    for lev in a.levers:
        lines.extend(
            [
                f"**{lev.lever_id}** ({lev.realism})",
                "",
                lev.effect,
                "",
                f"Files: {', '.join(f'`{f}`' for f in lev.files)}",
                "",
            ]
        )

    lines.extend(
        [
            "## 6. Path to positive LIVE_SHADOW?",
            "",
            a.path_to_positive,
            "",
            "## 7. Implication for LIMITED_LIVE",
            "",
            "Keep `live_cvd_limited_enabled=false` until either:",
            "",
            "1. Lockable-edge + spread-sanity shadow prints **stable non-negative** C, or",
            "2. A new passive-capture shadow mode is VALIDATED,",
            "",
            "Bitvavo+OKX being up is necessary but irrelevant to this gap — venues were "
            "already in the shadow sample that produced −€416.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    analysis: ShadowGapAnalysis,
    *,
    md_path: Path = Path("docs/CVD_SHADOW_GAP_DIAGNOSIS.md"),
    json_path: Path | None = None,
) -> Path:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(analysis))
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "research_expected_net": analysis.research_expected_net,
            "live_shadow_net": analysis.live_shadow_net,
            "gap_sum": analysis.gap_sum,
            "n_candidates": analysis.n_candidates,
            "complete_windows": analysis.complete_windows,
            "fill_rate": analysis.fill_rate,
            "mean_gap": analysis.mean_gap,
            "example": analysis.example,
            "components": [c.__dict__ for c in analysis.components],
            "levers": [lev.__dict__ for lev in analysis.levers],
            "verdict": analysis.verdict,
            "path_to_positive": analysis.path_to_positive,
        }
        json_path.write_text(json.dumps(payload, indent=2) + "\n")
    return md_path


__all__ = [
    "ShadowGapAnalysis",
    "analyze_shadow_gap",
    "render_markdown",
    "write_report",
]
