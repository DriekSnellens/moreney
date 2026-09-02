"""Analyze why live maker_inventory misses €20–100/day targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bot.research.underperformance_diagnosis.loaders import (
    LoadedUnderperformance,
    _ZERO,
    _dec,
)

# Documented live dashboard band (maker velocity aspiration).
TARGET_LOW = Decimal("20")
TARGET_HIGH = Decimal("50")
# User-stated band (often conflated with alt-beta 2–5% of €2k).
USER_TARGET_LOW = Decimal("50")
USER_TARGET_HIGH = Decimal("100")
CLIP_EUR = Decimal("55")
SOFT_ARM = Decimal("0.012")
RING_EUR = Decimal("1000")
BUDGET_EUR = Decimal("2000")


@dataclass
class RootCause:
    rank: int
    cause_id: str
    severity: str  # CRITICAL | HIGH | MEDIUM
    category: str  # architecture | parameter | strategy | execution
    headline: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    levers: list[str] = field(default_factory=list)


@dataclass
class ThroughputModel:
    soft_exit_eur: Decimal
    soft_partial_eur: Decimal
    exits_for_target: dict[str, int]
    ring_turns_for_target: dict[str, Decimal]
    note: str


@dataclass
class UnderperformanceAnalysis:
    loaded_at: str
    elapsed_hours: Decimal
    budget_eur: Decimal
    free_eur: Decimal
    portfolio_eur: Decimal
    bridge_realized_eur: Decimal
    session_realized_eur: Decimal
    capital_deployed_eur: Decimal
    capital_locked_eur: Decimal
    active_ring: dict[str, str]
    strategy: str
    daily_history: list[dict[str, Any]]
    throughput: ThroughputModel
    root_causes: list[RootCause]
    expectation_note: str
    verdict: str
    recommended_routes: list[str]


def _throughput() -> ThroughputModel:
    soft = CLIP_EUR * SOFT_ARM
    partial = soft * Decimal("0.15")
    exits: dict[str, int] = {}
    turns: dict[str, Decimal] = {}
    for label, target in (
        ("doc_20", TARGET_LOW),
        ("doc_50", TARGET_HIGH),
        ("user_50", USER_TARGET_LOW),
        ("user_100", USER_TARGET_HIGH),
    ):
        exits[label] = int((target / soft).to_integral_value(rounding="ROUND_UP"))
        turns[label] = (target / (RING_EUR * SOFT_ARM)).quantize(Decimal("0.1"))
    return ThroughputModel(
        soft_exit_eur=soft,
        soft_partial_eur=partial,
        exits_for_target=exits,
        ring_turns_for_target=turns,
        note=(
            f"Idealized fee-free: full exit of €{CLIP_EUR} clip at +{SOFT_ARM*100}% "
            f"= €{soft}/trade. Soft partial 15% ≈ €{partial}/trade."
        ),
    )


def _holding_rows(bridge: dict[str, Any]) -> list[dict[str, Any]]:
    states = ((bridge.get("trail_take_profit") or {}).get("states")) or {}
    rows: list[dict[str, Any]] = []
    for st in states.values():
        if not isinstance(st, dict):
            continue
        rows.append(
            {
                "venue": st.get("venue"),
                "base": str(st.get("base") or "").upper(),
                "notional_eur": str(st.get("notional_eur") or "0"),
                "unrealized_eur": str(st.get("unrealized_eur") or "0"),
                "below_be": bool(st.get("below_be")),
                "gain_pct": st.get("gain_pct"),
            }
        )
    return rows


def analyze_underperformance(data: LoadedUnderperformance) -> UnderperformanceAnalysis:
    status = data.status
    bridge = data.bridge
    diag = bridge.get("diagnostics") or {}
    skips = bridge.get("skips") or {}
    trail = bridge.get("trail_take_profit") or {}
    underwater = trail.get("underwater_blocked_bases") or {}
    why_idle = list(diag.get("why_idle") or [])
    scan = (status.get("last_cycle") or {}).get("scan") or {}
    elapsed_h = _dec(status.get("elapsed_seconds")) / Decimal("3600")
    free = _dec(bridge.get("free_quote_eur"))
    deployed = _dec(diag.get("capital_deployed_eur"))
    locked = _dec(bridge.get("micro_locked_notional_eur") or diag.get("capital_locked_eur"))
    realized = _dec(bridge.get("netto_winst_eur") or bridge.get("realized_trade_pnl_eur"))
    session_realized = _dec(diag.get("realized_net_eur_session") or "0")
    portfolio = _dec(bridge.get("portfolio_value_eur") or status.get("portfolio_value_eur"))
    budget = _dec(status.get("budget_eur") or BUDGET_EUR)
    strategy = str(status.get("strategy") or "maker_inventory")

    daily = [
        {
            "day": d.day,
            "n_points": d.n_points,
            "realized_end": str(d.realized_end),
            "session_end": str(d.session_end),
            "session_peak": str(d.session_peak),
            "free_end": str(d.free_end),
            "portfolio_end": str(d.portfolio_end),
        }
        for d in data.days
    ]

    holdings = _holding_rows(bridge)
    underwater_notional = sum(
        (_dec(h["notional_eur"]) for h in holdings if h.get("below_be")),
        _ZERO,
    )

    causes: list[RootCause] = [
        RootCause(
            rank=1,
            cause_id="CAPITAL_DEADLOCK",
            severity="CRITICAL",
            category="architecture",
            headline=(
                "Active ring stays €0 despite ~€3.8k free cash — underwater bags "
                "block Util-B deploy path"
            ),
            detail=(
                "ACTIVE_RING counts only focus inventory above break-even. "
                "Never-loss forbids selling underwater bags. "
                "live_micro_ring_soft_block_underwater_eur=25 disables soft-momentum "
                "and focus-relax while underwater book ≥ €25 — which is true whenever "
                "a single €55 clip is stuck. Free EUR cannot refill the ring."
            ),
            evidence=[
                f"free_eur={free}",
                f"capital_deployed_eur={deployed}",
                f"micro_locked_notional_eur={locked}",
                f"underwater_blocked_bases={underwater}",
                f"sell_below_break_even skips={skips.get('sell_below_break_even', 0)}",
                f"time_stop_below_be skips={skips.get('time_stop_below_be', 0)}",
                *[w for w in why_idle if "ACTIVE_RING" in w or "UNDERWATER" in w or "SELLS" in w],
            ],
            levers=[
                "Raise/zero live_micro_ring_soft_block_underwater_eur (unblock Util-B without forced loss)",
                "Differentiate live_micro_ring_momentum_min_return below paper_buy_momentum_min_return",
                "Product decision only: re-enable cut_loss_below_be to recycle stuck bags",
                "Natural unlock: wait until ATOM/BNB/SOL ≥ fee-aware BE",
            ],
        ),
        RootCause(
            rank=2,
            cause_id="STRATEGY_EXPECTATION_MISMATCH",
            severity="CRITICAL",
            category="strategy",
            headline=(
                "€50–100/day expectation is not evidenced by live maker_inventory; "
                "paper fleet profits came from CVD inject"
            ),
            detail=(
                "Live hot path runs maker_inventory with research hooks disabled. "
                "Dashboard documents €20–50/day as aspirational maker velocity. "
                "User €50–100 maps closest to alt-beta 2–5% of €2000 (odds.py), "
                "not maker recycle math. Paper fleet +€3.4k is CVD inject, not maker. "
                f"Paper lab maker realized ≈ €{data.paper_lab_realized}."
            ),
            evidence=[
                f"live strategy={strategy}",
                f"paper_lab_realized={data.paper_lab_realized}",
                f"bridge_realized={realized}",
                "live_disable_research_hooks=true (CVD off hot path)",
                "docs/PAPER_VS_RESEARCH_PNL_GAP_REPORT.md STRATEGY_MISMATCH",
            ],
            levers=[
                "Treat €20–50 as aspirational until ring velocity is proven live",
                "Do not use CVD paper/research totals as live maker forecast",
                "Only consider CVD live after shadow VALIDATED (currently NO-GO)",
            ],
        ),
        RootCause(
            rank=3,
            cause_id="ZERO_VELOCITY",
            severity="HIGH",
            category="architecture",
            headline="€50/day needs ~76 full soft recycles; current session has 0 new live trades",
            detail=(
                "Idealized model: €55 clip × 1.2% soft arm ≈ €0.66/trade → "
                "76 full exits for €50/day (~4.2× ring turns). "
                "With capital_deployed=0 and session fills=backfill-only, "
                "realized harvest is structurally impossible regardless of scan volume."
            ),
            evidence=[
                f"live_trades_executed={status.get('live_trades_executed')}",
                f"approved_opportunities={status.get('approved_opportunities')}",
                f"session_live_fill_count={bridge.get('session_live_fill_count')}",
                f"backfill_mirrored_count={bridge.get('backfill_mirrored_count')}",
                f"scan opportunities_emitted={scan.get('opportunities_emitted')}",
                f"cross_venue opportunities_emitted={(scan.get('cross_venue') or {}).get('opportunities_emitted')}",
            ],
            levers=[
                "Unlock capital deadlock (cause #1) before tuning harvest partials",
                "Measure fills/hour after deploy resumes — not scan emits",
            ],
        ),
        RootCause(
            rank=4,
            cause_id="ENTRY_GATE_STACK",
            severity="HIGH",
            category="parameter",
            headline=(
                "Even if soft-block lifts, entry/profit stack still starves new BE+ bags"
            ),
            detail=(
                "Session sets paper_buy_momentum_min_return=0.0015 AND "
                "live_micro_ring_momentum_min_return=0.0015 (soft floor is a no-op). "
                "Plus focus-only, rising-mark requirements, corr-sector block, "
                "buy_quality_pause, and NET floors €0.03 / 4 bps. "
                "why_not_trade profitability rejects dominate with €0 estimated missed profit."
            ),
            evidence=[
                f"focus_base_required={skips.get('focus_base_required', 0)}",
                f"momentum_block={skips.get('momentum_block', 0)}",
                f"buy_quality_pause={skips.get('buy_quality_pause', 0)}",
                f"corr_sector_momentum_block={skips.get('corr_sector_momentum_block', 0)}",
                f"why_not_trade={(status.get('why_not_trade') or {}).get('top_rejection_reasons')}",
            ],
            levers=[
                "Actually lower ring momentum floor while NEED (today equal to full floor)",
                "While ring NEED: modestly ease paper_maker_min_net_return / min_profit_eur",
                "Keep never-loss; do not confuse entry easing with cut-loss",
            ],
        ),
        RootCause(
            rank=5,
            cause_id="MAKER_EDGE_THIN",
            severity="HIGH",
            category="strategy",
            headline="maker_inventory shows near-zero edge after costs in paper lab and live",
            detail=(
                "Paper lab maker sandbox ~flat. Live GOE profitability gate theoretical "
                "sum is negative. Scan rejects dominated by stale_edge + fees_eat_edge. "
                "Unlocking deploy alone does not magically create €50–100/day without "
                "proven maker NET after fees and fills."
            ),
            evidence=[
                f"paper_lab_realized={data.paper_lab_realized}",
                f"paper_lab_equity={data.paper_lab_equity}",
                f"scan reject_counts top={sorted((scan.get('reject_counts') or {}).items(), key=lambda kv: -kv[1])[:5]}",
                f"underwater_holdings_notional≈{underwater_notional}",
            ],
            levers=[
                "Ablate maker floors vs observed fill/markout on live tape",
                "Separate 'can deploy' from 'has positive expectancy' experiments",
            ],
        ),
        RootCause(
            rank=6,
            cause_id="EXECUTION_HISTORY",
            severity="MEDIUM",
            category="execution",
            headline="Historical buy-fill gap / exchange errors reduced realized harvest",
            detail=(
                "Prior live_audit showed thousands of OKX clOrdId rejects and Bitvavo "
                "buy submits without fills. Fixes landed; current session still shows "
                "0 new live trades — deadlock dominates now, but execution debt remains "
                "in cumulative realized (−€9.62)."
            ),
            evidence=[
                f"bridge_realized={realized}",
                "See docs/LIVE_EXECUTION_DIAGNOSIS_REPORT.md / live_execution_fixes PR",
            ],
            levers=[
                "Keep monitoring micro_order_exception rate after restart",
                "Do not attribute current €0/day mainly to clOrdId anymore",
            ],
        ),
    ]

    expectation_note = (
        "In-repo live dashboard target is €20–50/day netto on the €2k maker micro path "
        "(weekly 140–350). €50–100/day is not a documented maker target; it aligns with "
        "paper odds alt-beta 2–5% of €2000 (coin move, not bid/ask harvest). "
        "Commit lineage tuned toward €20–50/day maker velocity without a second strategy."
    )

    verdict = (
        "Live underperformance vs €50–100/day (and even vs €20–50) is primarily an "
        "architectural capital deadlock plus a strategy/expectation mismatch: the bot "
        "that is supposed to harvest maker velocity cannot deploy (€0 active ring) while "
        "the numbers that look like €50+/day come from CVD paper/research or alt-beta "
        "MTM — neither of which is the live hot path today. Parameter easing alone "
        "cannot hit the target while never-loss + underwater soft-block keep cash idle."
    )

    routes = [
        "Route B-lite (recommended first): unblock Util-B soft deploy without cut-loss "
        "(raise soft_block_underwater_eur; make ring momentum floor actually softer; "
        "measure fills/hour and NET/hour for 24–48h).",
        "Route B-product: explicit cut-loss / bag-clear product decision to recycle "
        "ATOM/BNB/SOL underwater — frames realized loss vs continued idle cash.",
        "Route A (CVD): only after shadow VALIDATED; do not treat paper CVD €3.4k as "
        "current live forecast (shadow currently NO-GO).",
        "Reset expectation: until ring velocity >0 and paper/lab maker shows positive "
        "NET/hour, treat €50–100/day as unsupported for maker_inventory.",
    ]

    return UnderperformanceAnalysis(
        loaded_at=data.loaded_at,
        elapsed_hours=elapsed_h,
        budget_eur=budget,
        free_eur=free,
        portfolio_eur=portfolio,
        bridge_realized_eur=realized,
        session_realized_eur=session_realized,
        capital_deployed_eur=deployed,
        capital_locked_eur=locked,
        active_ring={
            "why_idle": "; ".join(w for w in why_idle if "ACTIVE_RING" in w) or "unknown",
            "underwater_blocked_bases": str(underwater),
        },
        strategy=strategy,
        daily_history=daily,
        throughput=_throughput(),
        root_causes=causes,
        expectation_note=expectation_note,
        verdict=verdict,
        recommended_routes=routes,
    )


__all__ = [
    "UnderperformanceAnalysis",
    "RootCause",
    "ThroughputModel",
    "analyze_underperformance",
    "TARGET_LOW",
    "TARGET_HIGH",
    "USER_TARGET_LOW",
    "USER_TARGET_HIGH",
]
