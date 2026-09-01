"""Analyze strategy mismatch and PnL gap components."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bot.research.strategy_pnl_gap.loaders import LoadedGapData, StrategyPnL, _ZERO

_NOTIONAL_RESEARCH = Decimal("100")


@dataclass
class GapComponent:
    component_id: str
    severity: str
    headline: str
    detail: str
    quantified_eur: Decimal | None = None
    quantified_label: str | None = None


@dataclass
class StrategyComparisonRow:
    world: str
    strategy_id: str
    realized_or_expected_eur: Decimal | None
    trade_or_signal_count: int | None
    per_unit_eur: Decimal | None
    execution_model: str
    period_note: str


@dataclass
class GapAnalysis:
    comparison_table: list[StrategyComparisonRow] = field(default_factory=list)
    paper_cvd_aggregate: dict[str, Any] = field(default_factory=dict)
    components: list[GapComponent] = field(default_factory=list)
    verdict: str = ""
    fair_comparison_notes: list[str] = field(default_factory=list)


def _aggregate_cvd(papers: LoadedGapData) -> dict[str, Any]:
    trades = executions = opps = 0
    pnl = fees = _ZERO
    for row in papers.paper_instances:
        if row.name == "lab_strategy":
            continue
        for s in row.strategies:
            if s.strategy != "cross_venue_dislocation":
                continue
            trades += s.trades
            executions += s.executions
            opps += s.opportunities
            pnl += s.net_pnl_eur
            fees += s.fees_eur
    per_trade = (pnl / trades) if trades else None
    per_exec = (pnl / executions) if executions else None
    per_signal_research_scale = (
        papers.research_canonical_net_eur / papers.research_signal_count
        if papers.research_canonical_net_eur is not None
        and papers.research_signal_count
        else None
    )
    return {
        "net_pnl_eur": str(pnl),
        "trades": trades,
        "executions": executions,
        "opportunities_seen": opps,
        "execution_rate": round(executions / opps, 4) if opps else None,
        "pnl_per_trade_eur": str(per_trade) if per_trade is not None else None,
        "pnl_per_execution_eur": str(per_exec) if per_exec is not None else None,
        "research_pnl_per_signal_eur": str(per_signal_research_scale)
        if per_signal_research_scale is not None
        else None,
        "fees_eur": str(fees),
    }


def analyze_gap(data: LoadedGapData) -> GapAnalysis:
    analysis = GapAnalysis()
    cvd_agg = _aggregate_cvd(data)
    analysis.paper_cvd_aggregate = cvd_agg

    fleet_realized = sum(
        (row.tracker_realized_eur for row in data.paper_instances if row.name != "lab_strategy"),
        _ZERO,
    )
    lab = next((r for r in data.paper_instances if r.name == "lab_strategy"), None)
    lab_maker_pnl = _ZERO
    if lab:
        for s in lab.strategies:
            if s.strategy == "maker_inventory":
                lab_maker_pnl = s.net_pnl_eur

    shadow_n = int(data.shadow.get("n_candidates") or 0)
    shadow_res = Decimal(str(data.shadow.get("RESEARCH_EXPECTED_NET") or 0))
    shadow_live = Decimal(str(data.shadow.get("LIVE_SHADOW_EXECUTION_NET") or 0))

    analysis.comparison_table = [
        StrategyComparisonRow(
            world="Research (canonical replay)",
            strategy_id="cross_venue_dislocation",
            realized_or_expected_eur=data.research_canonical_net_eur,
            trade_or_signal_count=data.research_signal_count,
            per_unit_eur=(
                data.research_canonical_net_eur / data.research_signal_count
                if data.research_canonical_net_eur is not None and data.research_signal_count
                else None
            ),
            execution_model="Taker round-trip; mid dislocation; fill_prob=1.0 (baseline)",
            period_note="62×30min OOS windows on frozen mdresearch tape",
        ),
        StrategyComparisonRow(
            world="Research (moderate realism)",
            strategy_id="cross_venue_dislocation",
            realized_or_expected_eur=data.research_moderate_net_eur,
            trade_or_signal_count=data.research_signal_count,
            per_unit_eur=(
                data.research_moderate_net_eur / data.research_signal_count
                if data.research_moderate_net_eur is not None and data.research_signal_count
                else None
            ),
            execution_model="50% miss + partial fills + fee/slip overlays",
            period_note="Same tape as canonical",
        ),
        StrategyComparisonRow(
            world="Paper fleet (5 twins, Aug 20–27)",
            strategy_id="cross_venue_dislocation (CVD inject)",
            realized_or_expected_eur=Decimal(str(cvd_agg.get("net_pnl_eur") or 0)),
            trade_or_signal_count=int(cvd_agg.get("trades") or 0),
            per_unit_eur=Decimal(str(cvd_agg["pnl_per_trade_eur"]))
            if cvd_agg.get("pnl_per_trade_eur")
            else None,
            execution_model="PaperExecutor + frozen CVD inject; NOT maker_inventory",
            period_note="~168h live shared tape; paper_maker_enabled=false",
        ),
        StrategyComparisonRow(
            world="Paper lab (8021, maker sandbox)",
            strategy_id="maker_inventory",
            realized_or_expected_eur=lab_maker_pnl if lab else None,
            trade_or_signal_count=lab.strategies[0].trades if lab and lab.strategies else 0,
            per_unit_eur=None,
            execution_model="Post-only maker composite + triangle + funding",
            period_note="Active sandbox; essentially flat",
        ),
        StrategyComparisonRow(
            world="Live micro (8020)",
            strategy_id="maker_inventory",
            realized_or_expected_eur=data.live_realized_eur,
            trade_or_signal_count=None,
            per_unit_eur=None,
            execution_model="Live maker buys + trail/taker exits; never-loss",
            period_note="Bridge FIFO; mostly backfill sells",
        ),
        StrategyComparisonRow(
            world="Shadow paper (incomplete sample)",
            strategy_id="cross_venue_dislocation",
            realized_or_expected_eur=shadow_res,
            trade_or_signal_count=shadow_n or None,
            per_unit_eur=(shadow_res / shadow_n) if shadow_n else None,
            execution_model="Observe-only taker sim on live L1",
            period_note=f"{data.shadow.get('complete_windows', 0)}/20 windows complete",
        ),
        StrategyComparisonRow(
            world="Shadow taker sim (same candidates)",
            strategy_id="cross_venue_dislocation",
            realized_or_expected_eur=shadow_live,
            trade_or_signal_count=shadow_n or None,
            per_unit_eur=(shadow_live / shadow_n) if shadow_n else None,
            execution_model="Top-of-book taker prices (not mid dislocation)",
            period_note="Explains parity mismatch magnitude",
        ),
    ]

    parity_total = int(data.parity.get("total_candidates") or 0)
    parity_rplf = int(data.parity.get("research_pass_live_fail") or 0)

    analysis.components = [
        GapComponent(
            component_id="STRATEGY_MISMATCH",
            severity="HIGH",
            headline="Production live/paper-hot path runs maker_inventory, research validates cross_venue_dislocation",
            detail=(
                "Paper fleet PnL (+€3.4k CVD) measures frozen CVD inject on a taker-style paper path. "
                "Live micro (-€9.6) measures maker spread capture with inventory/trail/never-loss. "
                "These are different alphas, not comparable without explicit relabeling."
            ),
            quantified_eur=Decimal(str(cvd_agg.get("net_pnl_eur") or 0)) - (data.live_realized_eur or _ZERO),
            quantified_label="Paper CVD aggregate minus live maker realized",
        ),
        GapComponent(
            component_id="PRICING_MISMATCH",
            severity="HIGH",
            headline="Research gross uses mid dislocation; live gate uses ask-minus-ask",
            detail=(
                f"Economic parity: {parity_rplf}/{parity_total} candidates pass frozen research "
                f"but fail live NetProfitCalculator. Root cause: {data.parity.get('root_cause', 'DIFFERENT_PRICE_SELECTION')}. "
                "Breakeven under frozen costs ≈47 bps vs 40 bps signal threshold."
            ),
        ),
        GapComponent(
            component_id="EXECUTION_MODEL",
            severity="HIGH",
            headline="Canonical replay assumes fill_prob=1.0; paper/live face gates and resting fills",
            detail=(
                f"Paper CVD: {cvd_agg.get('execution_rate', 'INSUFFICIENT_DATA')} exec/opportunity rate. "
                f"€{cvd_agg.get('pnl_per_trade_eur', '?')}/trade vs research €{cvd_agg.get('research_pnl_per_signal_eur', '?')}/signal. "
                "Shadow taker sim: mean gap ≈€5.66/candidate vs frozen expected NET."
            ),
            quantified_eur=shadow_live - shadow_res if shadow_n else None,
            quantified_label="Shadow taker sim minus research expected (same 1096 cands)",
        ),
        GapComponent(
            component_id="SCALE_AND_PERIOD",
            severity="MEDIUM",
            headline="Research totals are full-tape OOS; paper is one week live tape at fleet scale",
            detail=(
                f"Research canonical €{data.research_canonical_net_eur or 'INSUFFICIENT_DATA'} on "
                f"{data.research_signal_count or '?'} signals @ €{_NOTIONAL_RESEARCH} notional. "
                f"Paper fleet CVD €{cvd_agg.get('net_pnl_eur')} on {cvd_agg.get('trades')} trades over ~168h. "
                "Extrapolation requires matched notional, window, and route — not done here."
            ),
        ),
        GapComponent(
            component_id="INVENTORY_AND_EXIT",
            severity="MEDIUM",
            headline="Live maker never realizes losses; paper CVD counts round-trip wins only",
            detail=(
                f"Live dominant skips: time_stop_below_be={data.live_skips.get('time_stop_below_be', 0)}, "
                f"trail_no_trusted_cost={data.live_skips.get('trail_no_trusted_cost', 0)}. "
                "CVD paper tracker shows 0 losing trades on cross_venue_dislocation — optimistic vs live bag-holding."
            ),
        ),
    ]

    analysis.verdict = (
        "The Paper vs Research PnL gap is primarily a **strategy and economics mismatch**, not a "
        "single calibration bug. Positive paper fleet results (+€3.4k) come from **CVD inject** on "
        "paper taker semantics; negative live results (-€9.6) come from **maker_inventory** with "
        "never-loss exits. Research canonical (+€212k) is a third world: historical mid-convergence "
        "at fill_prob=1.0. Shadow/parity prove that even the **same CVD signals** flip negative "
        "under taker top-of-book pricing (~99% live gate reject)."
    )

    analysis.fair_comparison_notes = [
        "Compare paper_lab (maker_inventory) vs live micro on same env knobs — not paper_200live vs research.",
        "Compare shadow RESEARCH_EXPECTED_NET vs LIVE_SHADOW_EXECUTION_NET on completed windows only (need 20 windows).",
        "Re-run final_validation slice on Aug 20–27 live tape fingerprint for apples-to-apples period match.",
        "Do not extrapolate research €212k to fleet €200 starting capital without notional and signal rate conversion.",
    ]

    return analysis
