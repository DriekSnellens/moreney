"""Frozen-config experiment runner over persisted paper trade rows.

Compares economic decision rules on the *same* completed-trade dataset.
Does not re-simulate fills more easily — it only re-applies gates/EV rules
causally (no look-ahead) to observed outcomes.

Usage:
  .venv/bin/python -m bot.opportunity.experiment_runner data/paper_25000live.json
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.opportunity.calibration import EvCalibrator

_ZERO = Decimal("0")


EXPERIMENT_CONFIGS: dict[str, dict[str, Any]] = {
    "baseline_shrink_only": {
        "early_stop": False,
        "conditional_ev": False,
        "description": "Shrinkage hard gate only (min_samples=20); no early stop",
    },
    "conditional_ev": {
        "early_stop": False,
        "conditional_ev": True,
        "description": "Reject when causal rolling adverse implies NET_IF_FILL ≤ 0",
    },
    "early_stop": {
        "early_stop": True,
        "conditional_ev": False,
        "description": "Early raw stop n>=8 capture<=-0.25 loss>=5",
    },
    "conditional_ev_plus_early_stop": {
        "early_stop": True,
        "conditional_ev": True,
        "description": "Both causal conditional EV filter and early route stop",
    },
}


def _d(v: object) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return _ZERO


def run_experiment(trades: list[dict[str, Any]], *, config: dict[str, Any]) -> dict[str, Any]:
    """Walk trades chronologically; apply frozen gate rules; report metrics.

    Kind: counterfactual on observed fills (not a new fill simulation).
    Conditional EV uses only *prior* trades' realized adverse (causal).
    """
    cal = EvCalibrator(
        prior_strength=40,
        min_samples=20,
        early_stop_samples=8,
        early_stop_capture=Decimal("-0.25"),
        early_stop_min_loss_eur=Decimal("5"),
    )
    taken: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    stopped_routes: set[str] = set()
    # Causal adverse memory: route → list of prior realized_adverse
    prior_adverse: dict[str, list[Decimal]] = {}

    for trade in sorted(trades, key=lambda t: t.get("timestamp") or ""):
        route = f"{trade.get('buy_exchange')}->{trade.get('sell_exchange')}"
        exp = _d(trade.get("expected_net_profit"))
        real = _d(trade.get("realized_net_profit"))
        predicted_adverse = _d(trade.get("expected_adverse"))
        realized_adverse = _d(trade.get("realized_adverse"))

        net_if_fill = exp
        if config.get("conditional_ev"):
            hist = prior_adverse.get(route) or []
            if len(hist) >= 3:
                # Median of prior adverses only (no look-ahead).
                ordered = sorted(hist)
                causal_adv = ordered[len(ordered) // 2]
                extra = max(_ZERO, causal_adv - predicted_adverse)
                net_if_fill = exp - extra

        reject_reason = None
        if config.get("early_stop") and cal.hard_gate_negative_route(route):
            reject_reason = "early_stop_or_hard_gate"
            stopped_routes.add(route)
        elif config.get("conditional_ev") and len(prior_adverse.get(route) or []) >= 3 and net_if_fill <= 0:
            reject_reason = "conditional_ev_non_positive"

        if reject_reason:
            rejected.append({**trade, "reject_reason": reject_reason})
        else:
            taken.append(trade)
            cal.observe(
                key=f"{trade.get('strategy')}|{trade.get('symbol')}|{route}|buy",
                route=route,
                strategy=str(trade.get("strategy") or ""),
                expected_net=exp,
                realized_net=real,
            )

        # Update causal memory *after* the decision (walk-forward).
        prior_adverse.setdefault(route, []).append(realized_adverse)

    n_taken = len(taken)
    sum_real = sum((_d(t.get("realized_net_profit")) for t in taken), _ZERO)
    sum_exp = sum((_d(t.get("expected_net_profit")) for t in taken), _ZERO)
    return {
        "config": config,
        "kind": "counterfactual_causal",
        "opportunities_scanned": len(trades),
        "quotes_taken": n_taken,
        "quotes_rejected": len(rejected),
        "completed_round_trips": n_taken,
        "fill_rate": None,
        "total_realized_net": str(sum_real),
        "net_per_fill": str(sum_real / n_taken) if n_taken else "0",
        "sum_expected_net": str(sum_exp),
        "ev_capture": str(sum_real / sum_exp) if abs(sum_exp) > Decimal("0.01") else None,
        "stopped_routes": sorted(stopped_routes),
        "reject_reason_counts": _count([r.get("reject_reason") for r in rejected]),
    }


def _count(items: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        out[key] = out.get(key, 0) + 1
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "data/paper_25000live.json")
    data = json.loads(path.read_text())
    trades = list((data.get("tracker") or {}).get("trades") or [])
    results = {
        name: run_experiment(trades, config=cfg)
        for name, cfg in EXPERIMENT_CONFIGS.items()
    }
    out = {
        "source": str(path),
        "trade_count": len(trades),
        "split": "in_sample_counterfactual_causal",
        "note": (
            "Same observed fills; gates applied chronologically with no look-ahead. "
            "Not untouched OOS. Simulator fill assumptions unchanged."
        ),
        "experiments": results,
    }
    dest = Path("data/experiment_comparison.json")
    dest.write_text(json.dumps(out, indent=2, default=str))
    summary = {
        name: {
            "taken": r["quotes_taken"],
            "rejected": r["quotes_rejected"],
            "realized_net": r["total_realized_net"],
            "net_per_fill": r["net_per_fill"],
            "stopped_routes": r["stopped_routes"],
        }
        for name, r in results.items()
    }
    print(json.dumps({"source": str(path), "summary": summary}, indent=2))
    print(f"\nWrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
