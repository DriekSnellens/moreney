"""Load paper, research, live, parity, and shadow artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

_ZERO = Decimal("0")
_REPO = Path(__file__).resolve().parents[3]

_PAPER_STARTING: dict[str, Decimal] = {
    "200live": Decimal("200"),
    "500live": Decimal("500"),
    "1000live": Decimal("1000"),
    "5000live": Decimal("5000"),
    "25000live": Decimal("25000"),
    "lab_strategy": Decimal("2000"),
}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v if v is not None else 0))
    except Exception:
        return _ZERO


@dataclass
class StrategyPnL:
    strategy: str
    net_pnl_eur: Decimal = _ZERO
    trades: int = 0
    executions: int = 0
    opportunities: int = 0
    fees_eur: Decimal = _ZERO


@dataclass
class PaperInstanceRow:
    name: str
    starting_eur: Decimal
    equity_eur: Decimal
    tracker_realized_eur: Decimal
    portfolio_realized_eur: Decimal
    fees_eur: Decimal
    volume_eur: Decimal
    runtime_hours: float
    real_orders_placed: int
    primary_strategy: str
    strategies: list[StrategyPnL] = field(default_factory=list)


@dataclass
class LoadedGapData:
    paper_instances: list[PaperInstanceRow] = field(default_factory=list)
    research_canonical_net_eur: Decimal | None = None
    research_signal_count: int | None = None
    research_moderate_net_eur: Decimal | None = None
    parity: dict[str, Any] = field(default_factory=dict)
    shadow: dict[str, Any] = field(default_factory=dict)
    live_realized_eur: Decimal | None = None
    live_portfolio_eur: Decimal | None = None
    live_strategy: str = "maker_inventory"
    live_skips: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _parse_paper(name: str, data: dict[str, Any]) -> PaperInstanceRow:
    tr = data.get("tracker") or {}
    pf = data.get("portfolio") or {}
    st = pf.get("stats") or {}
    starting = _PAPER_STARTING.get(name, _ZERO)
    strategies: list[StrategyPnL] = []
    for sk, sv in (tr.get("strategies") or {}).items():
        if not isinstance(sv, dict):
            continue
        strategies.append(
            StrategyPnL(
                strategy=str(sk),
                net_pnl_eur=_d(sv.get("net_pnl")),
                trades=int(sv.get("trades") or 0),
                executions=int(sv.get("executions") or 0),
                opportunities=int(sv.get("opportunities") or 0),
                fees_eur=_d(sv.get("fees")),
            )
        )
    strategies.sort(key=lambda s: s.net_pnl_eur, reverse=True)
    primary = strategies[0].strategy if strategies else "unknown"
    maker_flag = bool((data.get("settings") or {}).get("paper_maker_enabled"))
    if name == "lab_strategy" or maker_flag:
        primary = "maker_inventory"
    elif any(s.strategy == "cross_venue_dislocation" for s in strategies):
        primary = "cross_venue_dislocation (inject)"
    return PaperInstanceRow(
        name=name,
        starting_eur=starting,
        equity_eur=_d(pf.get("equity")),
        tracker_realized_eur=_d(tr.get("realized_pnl")),
        portfolio_realized_eur=_d(st.get("realized_pnl")),
        fees_eur=_d(tr.get("fees") or st.get("fees_paid")),
        volume_eur=_d(tr.get("trading_volume") or st.get("total_trading_volume")),
        runtime_hours=float(data.get("runtime_seconds") or 0) / 3600.0,
        real_orders_placed=int(data.get("real_orders_placed") or 0),
        primary_strategy=primary,
        strategies=strategies,
    )


def load_gap_data(
    *,
    repo: Path | None = None,
) -> LoadedGapData:
    root = repo or _REPO
    out = LoadedGapData()

    for name in ["200live", "500live", "1000live", "5000live", "25000live", "lab_strategy"]:
        data = _load_json(root / "data" / f"paper_{name}.json")
        if data:
            out.paper_instances.append(_parse_paper(name, data))

    fv = _load_json(root / "data/research/final_validation/results.json") or {}
    out.research_canonical_net_eur = _d(fv.get("CANONICAL_REPLAY_NET")) if fv else None
    baseline = fv.get("BASELINE_RESULT") or {}
    out.research_signal_count = int(baseline.get("signal_count") or baseline.get("candidate_count") or 0) or None
    for sc in fv.get("scenario_results") or []:
        if sc.get("scenario_id") == "MODERATE_REALISM":
            out.research_moderate_net_eur = _d(sc.get("execution_net_eur"))

    out.parity = _load_json(root / "data/research/economic_parity/phase1_report.json") or {}
    out.shadow = _load_json(root / "data/research/shadow_validation/accumulator.json") or {}

    live = _load_json(root / "data/live_micro_session_status.json") or {}
    bridge = live.get("bridge") or {}
    out.live_realized_eur = _d(bridge.get("realized_trade_pnl_eur"))
    out.live_portfolio_eur = _d(bridge.get("portfolio_value_eur"))
    skips = bridge.get("skips") or {}
    out.live_skips = {str(k): int(v) for k, v in skips.items() if v is not None}

    if not out.paper_instances:
        out.notes.append("No paper_*.json state files found.")
    if out.research_canonical_net_eur is None:
        out.notes.append("Missing data/research/final_validation/results.json")
    return out
