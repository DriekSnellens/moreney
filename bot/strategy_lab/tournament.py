"""Deterministic strategy tournament runner."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.core.config import Settings
from bot.research.tournament.criteria import NOTIONAL_EUR_DEFAULT
from bot.research.tournament.economics import execution_replay_net
from bot.strategy_lab.adapters import build_all_adapters
from bot.strategy_lab.adapter import decision_key
from bot.strategy_lab.capital import CapitalLedger
from bot.strategy_lab.dataset import (
    build_cycles_from_events,
    chronological_split,
    dataset_fingerprint,
    iter_baseline_opportunity_keys,
    load_research_events,
    synthetic_research_tape,
)
from bot.strategy_lab.economics import CommonEconomics
from bot.strategy_lab.scorecard import build_scorecard, merge_oos_into_leaderboard
from bot.strategy_lab.types import DecisionAction, StrategyOutcome
from bot.strategy_lab.verdict import criteria_manifest, verdict_for_scorecard

_ZERO = Decimal("0")
# Bound OBSERVED loads so multi-million-line tapes cannot OOM the lab process.
_DEFAULT_OBSERVED_MAX_EVENTS = 120_000


def _settings(**kwargs: Any) -> Settings:
    base = dict(
        paper_maker_enabled=True,
        paper_maker_fair_value=False,
        paper_maker_same_venue=True,
        paper_maker_min_profit_eur=0.01,
        paper_maker_min_net_return=0.00005,
        paper_maker_max_edge_bps=200.0,
        paper_maker_adverse_bps=0.0,
        arbitrage_opportunity_cooldown_ms=0.0,
        arbitrage_max_emits_per_cycle=8,
        arbitrage_min_liquidity_base=0.01,
        arbitrage_max_quantity=2.0,
        profitability_apply_funding=False,
        strategy_lab_enabled=True,
        strategy_lab_research_only=True,
        strategy_lab_execution_enabled=False,
        global_funding_strategy_enabled=True,
    )
    base.update(kwargs)
    return Settings(**base)


def run_tournament(
    *,
    dataset_id: str | None = None,
    research_path: Path | None = None,
    out_dir: Path | None = None,
    use_synthetic_if_thin: bool = True,
    n_synthetic_cycles: int = 80,
    development_frac: float = 0.70,
    settings: Settings | None = None,
    max_events: int | None = _DEFAULT_OBSERVED_MAX_EVENTS,
    stride: int = 1,
    outcome_mode: str = "trade_through",
) -> dict[str, Any]:
    """Run DEVELOPMENT → FREEZE → untouched OOS for all strategies + control."""
    settings = settings or _settings()
    assert getattr(settings, "strategy_lab_execution_enabled", False) is False
    if outcome_mode not in {"trade_through", "shadow"}:
        raise ValueError(f"unsupported outcome_mode: {outcome_mode}")

    research_path = research_path or Path(
        getattr(settings, "research_marketdata_recording_path", "data/research_marketdata")
    )
    events = load_research_events(
        research_path,
        max_events=max_events,
        stride=stride,
    )
    data_label = "OBSERVED"
    sample_note: str | None = None
    if len(events) < 50 and use_synthetic_if_thin:
        events = synthetic_research_tape(n_cycles=n_synthetic_cycles, seed=42)
        data_label = "SYNTHETIC"
    elif len(events) >= 50:
        sample_note = (
            f"OBSERVED streamed sample max_events={max_events} stride={stride} "
            f"(EUR × binance/bitvavo/okx); not a full-tape claim"
        )

    cycles = build_cycles_from_events(events, bucket_ms=200)
    dataset_id = dataset_id or (
        f"{data_label.lower()}_{dataset_fingerprint(cycles)[:12]}"
    )
    out = out_dir or Path("data/strategy_lab") / dataset_id
    out.mkdir(parents=True, exist_ok=True)

    # Freeze point: split BEFORE any strategy sees OOS
    development_cycles, oos_cycles = chronological_split(
        cycles, development_frac=development_frac
    )
    frozen_config = {
        "dataset_id": dataset_id,
        "data_label": data_label,
        "n_events": len(events),
        "n_cycles": len(cycles),
        "n_development": len(development_cycles),
        "n_oos": len(oos_cycles),
        "development_frac": development_frac,
        "max_events": max_events,
        "stride": stride,
        "outcome_mode": outcome_mode,
        "dataset_fingerprint": dataset_fingerprint(cycles),
        "development_fingerprint": dataset_fingerprint(development_cycles),
        "oos_fingerprint": dataset_fingerprint(oos_cycles),
        "criteria": criteria_manifest(),
        "frozen_at": datetime.now(UTC).isoformat(),
        "strategy_versions": {},
        "execution_enabled": False,
        "research_only": True,
    }

    economics = CommonEconomics(settings)
    capital = CapitalLedger.from_config(
        total_eur=float(getattr(settings, "strategy_lab_total_capital_eur", 25000) or 25000),
        mode=str(getattr(settings, "strategy_lab_capital_mode", "ISOLATED") or "ISOLATED"),
    )
    adapters = build_all_adapters(
        economics=economics, capital=capital, settings=settings
    )
    frozen_config["strategy_versions"] = {
        a.strategy_id: a.strategy_version for a in adapters
    }

    # --- DEVELOPMENT (mutable exploration allowed only here) ---
    baseline_dev = _baseline_count(development_cycles)
    for adapter in adapters:
        adapter.reset()
        for cycle in development_cycles:
            adapter.run_cycle(cycle)

    # FREEZE — no further parameter changes
    frozen_config["status"] = "FROZEN"

    # --- OOS (untouched; adapters reset decision buffers but not config) ---
    baseline_oos = _baseline_count(oos_cycles)
    oos_decisions: dict[str, list] = {}
    for adapter in adapters:
        # Keep development decisions; run OOS into a fresh list then merge scorecards
        prior = list(adapter.decisions())
        adapter._decisions.clear()  # noqa: SLF001
        for cycle in oos_cycles:
            adapter.run_cycle(cycle)
        oos_decisions[adapter.strategy_id] = list(adapter.decisions())
        # Restore combined for fingerprints of full run if needed
        adapter._decisions = prior + oos_decisions[adapter.strategy_id]  # noqa: SLF001

    scorecards: dict[str, Any] = {}
    leaderboard: list[dict[str, Any]] = []
    waterfalls: dict[str, Any] = {}

    for adapter in adapters:
        sid = adapter.strategy_id
        dev_dec = [d for d in adapter.decisions() if d.cycle_id in {c.cycle_id for c in development_cycles}]
        oos_dec = oos_decisions.get(sid, [])
        # Outcomes: trade-through conservative replay by default (fill_rate + extra adverse).
        # Shadow mode keeps expected NET for plumbing comparisons only.
        outcome_fn = (
            _trade_through_outcome if outcome_mode == "trade_through" else _shadow_outcome
        )
        dev_outcomes = [outcome_fn(d) for d in dev_dec if d.action == DecisionAction.ACCEPT]
        oos_outcomes = [outcome_fn(d) for d in oos_dec if d.action == DecisionAction.ACCEPT]

        # Funding insufficient-data shortcut
        if sid == "funding_basis" and all(
            d.action == DecisionAction.SKIP for d in (dev_dec + oos_dec)
        ):
            sc = build_scorecard(
                strategy_id=sid,
                strategy_version=adapter.strategy_version,
                phase="DEVELOPMENT",
                decisions=dev_dec,
                outcomes=[],
                baseline_opportunities=baseline_dev,
                status="INSUFFICIENT_DATA",
            )
            sc.verdict = "INSUFFICIENT_DATA"
            sc.notes.append("No funding_rate on research books")
            scorecards[sid] = {"development": sc.as_dict(), "oos": None}
            leaderboard.append(_leaderboard_row(sc, sleeve=capital.sleeve(sid).as_dict()))
            waterfalls[sid] = sc.waterfall
            continue

        sc_dev = build_scorecard(
            strategy_id=sid,
            strategy_version=adapter.strategy_version,
            phase="DEVELOPMENT",
            decisions=dev_dec,
            outcomes=dev_outcomes,
            baseline_opportunities=baseline_dev,
        )
        sc_oos = build_scorecard(
            strategy_id=sid,
            strategy_version=adapter.strategy_version,
            phase="OOS",
            decisions=oos_dec,
            outcomes=oos_outcomes,
            baseline_opportunities=baseline_oos,
        )
        verdict = verdict_for_scorecard(development=sc_dev, oos=sc_oos)
        if sid == "control_no_trade":
            verdict = "NO_EDGE" if sc_dev.accepted == 0 else "FAILED"
            sc_dev.notes.append("Control must produce zero accepts")
        # SYNTHETIC tape + shadow expected fills are not OBSERVED OOS evidence.
        if data_label == "SYNTHETIC" and verdict in {"OOS_PROMISING", "OOS_ROBUST"}:
            sc_dev.notes.append(
                "Verdict capped: SYNTHETIC dataset cannot support OOS_PROMISING/ROBUST"
            )
            verdict = "IN_SAMPLE_ONLY"
        if data_label == "SYNTHETIC":
            sc_dev.notes.append(
                "Shadow outcomes use conservative expected NET — not trade-through fills"
            )
        merge_oos_into_leaderboard(sc_dev, sc_oos, verdict=verdict)
        scorecards[sid] = {
            "development": sc_dev.as_dict(),
            "oos": sc_oos.as_dict(),
            "verdict": verdict,
        }
        leaderboard.append(_leaderboard_row(sc_dev, sleeve=capital.sleeve(sid).as_dict()))
        waterfalls[sid] = sc_dev.waterfall

    leaderboard.sort(
        key=lambda r: (
            _verdict_rank(r.get("verdict")),
            float(r.get("oos_net_per_capital_sec") or -1e18),
            float(r.get("net") or -1e18),
        ),
        reverse=True,
    )

    fingerprints = {
        "dataset": frozen_config["dataset_fingerprint"],
        "development": frozen_config["development_fingerprint"],
        "oos": frozen_config["oos_fingerprint"],
        "tournament": _tournament_fingerprint(scorecards, frozen_config),
        "criteria_version": criteria_manifest()["criteria_version"],
    }

    results = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "data_label": data_label,
        "frozen_config": frozen_config,
        "capital": capital.as_dict(),
        "baseline_opportunities_development": baseline_dev,
        "baseline_opportunities_oos": baseline_oos,
        "scorecards": scorecards,
        "leaderboard": leaderboard,
        "waterfalls": waterfalls,
        "fingerprints": fingerprints,
        "notes": [
            (
                "Outcomes use trade-through conservative execution replay "
                "(fill_rate=0.55 + extra adverse; no queue fills)."
                if outcome_mode == "trade_through"
                else "Shadow outcomes use conservative expected NET (no trade-through haircut)."
            ),
            "OOS split is chronological and frozen before strategy scoring.",
            "Do not tune parameters after inspecting OOS.",
            *( [sample_note] if sample_note else [] ),
        ],
    }

    (out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / "scorecards.json").write_text(
        json.dumps(scorecards, indent=2, default=str), encoding="utf-8"
    )
    (out / "leaderboard.json").write_text(
        json.dumps(leaderboard, indent=2, default=str), encoding="utf-8"
    )
    (out / "fingerprints.json").write_text(
        json.dumps(fingerprints, indent=2), encoding="utf-8"
    )
    (out / "frozen_config.json").write_text(
        json.dumps(frozen_config, indent=2, default=str), encoding="utf-8"
    )
    (out / "report.md").write_text(_mini_report(results), encoding="utf-8")
    return results


def _baseline_count(cycles: list) -> int:
    n = 0
    for c in cycles:
        n += sum(1 for _ in iter_baseline_opportunity_keys(c))
    return n


def _shadow_outcome(d) -> StrategyOutcome:
    return StrategyOutcome(
        decision_key=decision_key(d),
        realized_net_eur=d.costs.conservative_net_eur,
        realized_gross_eur=d.costs.gross_edge_eur,
        realized_fees_eur=d.costs.fees_eur,
        realized_slippage_eur=d.costs.slippage_eur,
        realized_adverse_eur=d.costs.adverse_latency_eur,
        filled=True,
        independent_event_id=d.cycle_id,
        metadata={"shadow": True, "trade_through_baseline": False},
    )


def _trade_through_outcome(d) -> StrategyOutcome:
    """Map accept expected NET through shared trade-through execution replay.

    Same assumptions as gated research tournament: no queue fills, fill_rate
    haircut, extra adverse bps. Does not invent fills beyond accept decisions.
    """
    expected = float(d.costs.conservative_net_eur)
    notional = (
        float(d.capital_required_eur)
        if d.capital_required_eur and d.capital_required_eur > 0
        else float(NOTIONAL_EUR_DEFAULT)
    )
    replay = execution_replay_net(expected_net=expected, notional_eur=notional)
    fill_rate = Decimal(str(replay["fill_rate"]))
    extra_adverse = Decimal(str(notional * float(replay["adverse_extra_bps"]) / 10000.0))
    realized_net = Decimal(str(replay["EXECUTION_NET"]))
    return StrategyOutcome(
        decision_key=decision_key(d),
        realized_net_eur=realized_net,
        realized_gross_eur=d.costs.gross_edge_eur * fill_rate,
        realized_fees_eur=d.costs.fees_eur * fill_rate,
        realized_slippage_eur=d.costs.slippage_eur * fill_rate,
        realized_adverse_eur=(d.costs.adverse_latency_eur + extra_adverse) * fill_rate,
        filled=True,
        independent_event_id=d.cycle_id,
        metadata={
            "shadow": False,
            "trade_through_baseline": True,
            "execution_replay": replay,
        },
    )


def _leaderboard_row(sc, *, sleeve: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": sc.strategy_id,
        "status": sc.status,
        "verdict": sc.verdict,
        "opportunities": sc.opportunities,
        "trades": sc.completed,
        "net": float(sc.realized_net_eur),
        "net_per_fill": float(sc.net_eur_per_fill),
        "net_bps": float(sc.net_bps),
        "capital": float(Decimal(str(sleeve.get("budget_eur") or 0))),
        "capital_velocity": float(sc.capital_velocity),
        "max_dd": float(sc.max_drawdown_eur),
        "oos_net": float(sc.oos_net_eur or 0),
        "oos_net_per_capital_sec": float(sc.oos_net_per_capital_second or 0),
        "participation_rate": sc.participation_rate,
        "independent_events": sc.independent_events,
        "evidence": sc.independent_events,
        "waterfall": sc.waterfall,
    }


def _verdict_rank(v: str | None) -> int:
    order = {
        "OOS_ROBUST": 70,
        "OOS_PROMISING": 60,
        "IN_SAMPLE_ONLY": 40,
        "NO_EDGE": 20,
        "OOS_UNSTABLE": 15,
        "EDGE_NEGATIVE_AFTER_COSTS": 10,
        "INSUFFICIENT_DATA": 5,
        "FAILED": 0,
        "RESEARCH": 30,
    }
    return order.get(str(v or ""), 0)


def _tournament_fingerprint(scorecards: dict, frozen: dict) -> str:
    payload = {
        "frozen": {
            "dataset": frozen.get("dataset_fingerprint"),
            "dev": frozen.get("development_fingerprint"),
            "oos": frozen.get("oos_fingerprint"),
            "criteria": frozen.get("criteria", {}).get("criteria_version"),
        },
        "scorecards": {
            k: {
                "verdict": (v.get("verdict") if isinstance(v, dict) else None)
                or (v.get("development") or {}).get("verdict"),
                "dev_net": (v.get("development") or {}).get("realized_net_eur"),
                "oos_net": ((v.get("oos") or {}) or {}).get("realized_net_eur"),
                "completed_dev": (v.get("development") or {}).get("completed"),
                "completed_oos": ((v.get("oos") or {}) or {}).get("completed"),
            }
            for k, v in sorted(scorecards.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _mini_report(results: dict[str, Any]) -> str:
    lines = [
        f"# Strategy Lab Tournament — {results['dataset_id']}",
        "",
        f"Data label: **{results['data_label']}**",
        f"Fingerprint: `{results['fingerprints']['tournament']}`",
        "",
        "## Leaderboard",
        "",
        "| Strategy | Verdict | NET | OOS NET | Velocity | Participation |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in results["leaderboard"]:
        lines.append(
            f"| {row['strategy']} | {row['verdict']} | {row['net']:.4f} | "
            f"{row['oos_net']:.4f} | {row['capital_velocity']:.6g} | "
            f"{row['participation_rate']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            *[f"- {n}" for n in results.get("notes") or []],
            "",
            "See docs/STRATEGY_LAB_REPORT.md for the full research report.",
        ]
    )
    return "\n".join(lines) + "\n"
