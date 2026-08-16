"""Scorecard aggregation from decisions + outcomes."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Sequence

from bot.strategy_lab.capital import net_eur_per_capital_second
from bot.strategy_lab.types import (
    DecisionAction,
    Scorecard,
    StrategyDecision,
    StrategyOutcome,
)

_ZERO = Decimal("0")


def _pct_loss(vals: list[Decimal], p: float) -> Decimal:
    losses = sorted(v for v in vals if v < 0)
    if not losses:
        return _ZERO
    idx = min(len(losses) - 1, max(0, int(round((p / 100.0) * (len(losses) - 1)))))
    return losses[idx]


def _max_drawdown(nets: list[Decimal]) -> Decimal:
    peak = _ZERO
    equity = _ZERO
    max_dd = _ZERO
    for n in nets:
        equity += n
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def build_scorecard(
    *,
    strategy_id: str,
    strategy_version: str,
    phase: str,
    decisions: Sequence[StrategyDecision],
    outcomes: Sequence[StrategyOutcome],
    baseline_opportunities: int,
    status: str = "RESEARCH",
) -> Scorecard:
    accepted = [d for d in decisions if d.action == DecisionAction.ACCEPT]
    rejected = [d for d in decisions if d.action == DecisionAction.REJECT]
    outcome_by_key = {o.decision_key: o for o in outcomes}
    completed_nets: list[Decimal] = []
    gross = fees = slip = adv = _ZERO
    realized = _ZERO
    expected = _ZERO
    capital_sum = _ZERO
    lock_sum = 0.0
    independent: set[str] = set()
    waterfall_acc = {
        "gross_opportunity": _ZERO,
        "buy_fees": _ZERO,
        "sell_fees": _ZERO,
        "slippage": _ZERO,
        "adverse_selection": _ZERO,
        "funding": _ZERO,
        "transfer_fx": _ZERO,
        "net": _ZERO,
    }

    for d in accepted:
        expected += d.costs.conservative_net_eur
        capital_sum += d.capital_required_eur
        lock_sum += d.estimated_capital_lock_ms
        waterfall_acc["gross_opportunity"] += d.costs.gross_edge_eur
        waterfall_acc["buy_fees"] += d.costs.fees_eur / 2
        waterfall_acc["sell_fees"] += d.costs.fees_eur / 2
        waterfall_acc["slippage"] += d.costs.slippage_eur
        waterfall_acc["adverse_selection"] += d.costs.adverse_latency_eur
        waterfall_acc["funding"] += d.costs.funding_eur
        waterfall_acc["transfer_fx"] += d.costs.hedge_other_eur
        key = (
            f"{d.strategy_id}|{d.cycle_id}|{d.symbol}|{d.route}|"
            f"{d.action.value}|{d.ts_ns}"
        )
        # Also try outcome keys produced by tournament
        o = outcome_by_key.get(key)
        if o is None:
            # fuzzy: match by cycle+symbol+route
            for ok, ov in outcome_by_key.items():
                if d.cycle_id in ok and d.symbol in ok and d.route in ok:
                    o = ov
                    break
        if o is not None and o.filled:
            completed_nets.append(o.realized_net_eur)
            realized += o.realized_net_eur
            gross += o.realized_gross_eur
            fees += o.realized_fees_eur
            slip += o.realized_slippage_eur
            adv += o.realized_adverse_eur
            independent.add(o.independent_event_id)
            waterfall_acc["net"] += o.realized_net_eur
        else:
            # Shadow: use conservative expected as stand-in when no fill model
            completed_nets.append(d.costs.conservative_net_eur)
            realized += d.costs.conservative_net_eur
            gross += d.costs.gross_edge_eur
            fees += d.costs.fees_eur
            slip += d.costs.slippage_eur
            adv += d.costs.adverse_latency_eur
            independent.add(d.cycle_id)
            waterfall_acc["net"] += d.costs.conservative_net_eur

    completed = len(completed_nets)
    winning = sum(1 for n in completed_nets if n > 0)
    losing = sum(1 for n in completed_nets if n < 0)
    win_rate = (winning / completed) if completed else 0.0
    n_opp = len(decisions)
    n_acc = len(accepted)
    participation = (n_acc / baseline_opportunities) if baseline_opportunities else 0.0
    avg_lock = (lock_sum / n_acc) if n_acc else 0.0
    avg_cap = (capital_sum / n_acc) if n_acc else _ZERO
    velocity = (
        net_eur_per_capital_second(realized, avg_cap, avg_lock)
        if n_acc and avg_cap > 0
        else _ZERO
    )
    notional = avg_cap if avg_cap > 0 else Decimal("1")
    net_bps = (realized / notional * Decimal("10000")) if completed else _ZERO
    ev_capture = None
    if expected != 0:
        ev_capture = float(realized / expected)

    # Sharpe-like diagnostic (mean/std of per-trade NET) — not for live sizing
    sharpe = None
    if len(completed_nets) >= 5:
        mean = sum(completed_nets) / len(completed_nets)
        var = sum((x - mean) ** 2 for x in completed_nets) / len(completed_nets)
        std = var.sqrt() if var > 0 else _ZERO
        if std > 0:
            sharpe = float(mean / std)

    return Scorecard(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        status=status,
        phase=phase,
        opportunities=n_opp,
        accepted=n_acc,
        rejected=len(rejected),
        completed=completed,
        winning=winning,
        losing=losing,
        win_rate=win_rate,
        gross_pnl_eur=gross,
        fees_eur=fees,
        slippage_eur=slip,
        adverse_eur=adv,
        net_pnl_eur=realized,
        net_eur_per_fill=(realized / completed) if completed else _ZERO,
        net_bps=net_bps,
        expected_net_eur=expected,
        realized_net_eur=realized,
        ev_capture=ev_capture,
        capital_used_eur=avg_cap,
        average_capital_lock_ms=avg_lock,
        capital_velocity=velocity,
        max_drawdown_eur=_max_drawdown(completed_nets),
        worst_loss_eur=min(completed_nets) if completed_nets else _ZERO,
        p95_loss_eur=_pct_loss(completed_nets, 95),
        opportunity_frequency=float(n_opp),
        rejection_rate=(len(rejected) / n_opp) if n_opp else 0.0,
        participation_rate=participation,
        baseline_opportunities=baseline_opportunities,
        independent_events=len(independent),
        sharpe_like=sharpe,
        waterfall={k: str(v) for k, v in waterfall_acc.items()},
    )


def merge_oos_into_leaderboard(
    development: Scorecard,
    oos: Scorecard,
    *,
    verdict: str,
) -> Scorecard:
    development.oos_net_eur = oos.realized_net_eur
    development.oos_net_per_capital_second = oos.capital_velocity
    development.oos_drawdown_eur = oos.max_drawdown_eur
    development.verdict = verdict
    development.status = verdict
    return development
