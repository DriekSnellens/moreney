"""Offline economic edge audit over persisted paper JSON.

Usage:
  .venv/bin/python -m bot.opportunity.audit_report data/paper_25000live.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.opportunity.calibration import EvCalibrator
from bot.opportunity.cost_ownership import ownership_table
from bot.opportunity.edge_decomposition import edge_decomposition
from bot.opportunity.toxicity import classify_markout_bps, toxicity_report
from bot.opportunity.waterfall import decompose_trade_row
from bot.paper.markout import MarkoutTracker

_ZERO = Decimal("0")


def load_paper(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def bitvavo_timeline(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cum_e = _ZERO
    cum_r = _ZERO
    prevented_if_stop_at: dict[str, Decimal] = {}
    cal = EvCalibrator(
        prior_strength=40,
        min_samples=20,
        early_stop_samples=8,
        early_stop_capture=Decimal("-0.25"),
        early_stop_min_loss_eur=Decimal("5"),
    )
    early_fired_at: int | None = None
    shrunk_negative_at: int | None = None
    loss_after_early = _ZERO
    loss_after_classic = _ZERO

    bv = [
        t
        for t in sorted(trades, key=lambda x: x.get("timestamp") or "")
        if f"{t.get('buy_exchange')}->{t.get('sell_exchange')}" == "bitvavo->bitvavo"
    ]
    for i, t in enumerate(bv, start=1):
        exp = Decimal(str(t.get("expected_net_profit") or 0))
        real = Decimal(str(t.get("realized_net_profit") or 0))
        cum_e += exp
        cum_r += real
        cal.observe(
            key=f"maker_inventory|{t.get('symbol')}|bitvavo->bitvavo|buy",
            route="bitvavo->bitvavo",
            strategy=str(t.get("strategy") or "maker_inventory"),
            expected_net=exp,
            realized_net=real,
        )
        route = cal.snapshot()["routes"].get("bitvavo->bitvavo") or {}
        raw_cap = route.get("raw_capture")
        early = bool(route.get("early_stop"))
        # Classic shrunk gate approx: n>=20 and shrunk<=0
        n = int(route.get("n") or 0)
        alpha = Decimal(n) / Decimal(n + 40) if n else _ZERO
        raw = Decimal(str(raw_cap)) if raw_cap is not None else _ZERO
        shrunk = alpha * raw + (Decimal("1") - alpha) * Decimal("1")
        if early and early_fired_at is None:
            early_fired_at = i
        if n >= 20 and shrunk <= 0 and shrunk_negative_at is None:
            shrunk_negative_at = i
        if early_fired_at is not None and i > early_fired_at:
            loss_after_early += real
        if shrunk_negative_at is not None and i > shrunk_negative_at:
            loss_after_classic += real
        rows.append(
            {
                "n": i,
                "timestamp": t.get("timestamp"),
                "symbol": t.get("symbol"),
                "expected": str(exp),
                "realized": str(real),
                "cum_expected": str(cum_e),
                "cum_realized": str(cum_r),
                "raw_capture": raw_cap,
                "shrunk_capture": str(shrunk),
                "early_stop": early,
            }
        )

    # Counterfactual: if stopped after first early_stop trigger, remaining loss avoided.
    avoided = _ZERO
    good_blocked = _ZERO
    if early_fired_at is not None:
        for j, t in enumerate(bv, start=1):
            if j <= early_fired_at:
                continue
            real = Decimal(str(t.get("realized_net_profit") or 0))
            if real < 0:
                avoided += abs(real)
            else:
                good_blocked += real

    return {
        "route": "bitvavo->bitvavo",
        "trades": len(bv),
        "cum_expected": str(cum_e),
        "cum_realized": str(cum_r),
        "early_stop_would_fire_at_n": early_fired_at,
        "classic_shrunk_gate_at_n": shrunk_negative_at,
        "loss_after_early_stop_eur": str(loss_after_early),
        "loss_avoided_if_early_stop_eur": str(avoided),
        "good_trades_blocked_if_early_stop_eur": str(good_blocked),
        "timeline": rows,
        "kind": "counterfactual",
    }


def markout_samples_from_state(markout_state: dict[str, Any]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    by_bucket = markout_state.get("by_bucket") or {}
    for key, vals in by_bucket.items():
        parts = str(key).split("|")
        venue = parts[0] if len(parts) > 0 else ""
        symbol = parts[1] if len(parts) > 1 else ""
        side = parts[2] if len(parts) > 2 else ""
        for v in vals or []:
            bps = Decimal(str(v))
            samples.append(
                {
                    "venue": venue,
                    "symbol": symbol,
                    "side": side,
                    "strategy": "maker_inventory",
                    "p_fill": "1.0",  # fleet TRADE_THROUGH=1.0, queue=0
                    "markout_bps_5s": str(bps),
                    "toxicity": classify_markout_bps(bps),
                }
            )
    return samples


def run_audit(path: Path) -> dict[str, Any]:
    data = load_paper(path)
    trades = list((data.get("tracker") or {}).get("trades") or [])
    markout_state = data.get("markout") or {}
    mo_samples = markout_samples_from_state(markout_state)
    # Attach realized where we can match loosely by symbol (observational only).
    tox = toxicity_report(mo_samples)
    edge = edge_decomposition(trades)
    waterfalls = [decompose_trade_row(t) for t in trades]
    identity_ok = sum(
        1
        for w in waterfalls
        if "identity_ok" in (w.get("realized") or {}).get("notes", [])
    )

    tracker = MarkoutTracker()
    tracker.import_state(markout_state)
    snap = tracker.snapshot()
    suggested_old_ceil = tracker.suggested_adverse_bps(
        floor=Decimal("2"), ceiling=Decimal("15")
    )
    suggested_new_ceil = tracker.suggested_adverse_bps(
        floor=Decimal("2"), ceiling=Decimal("40")
    )

    return {
        "source": str(path),
        "runtime_seconds": data.get("runtime_seconds"),
        "realized_pnl": (data.get("tracker") or {}).get("realized_pnl"),
        "trade_count": len(trades),
        "waterfall": {
            "trades_decomposed": len(waterfalls),
            "identity_ok": identity_ok,
            "samples": waterfalls[:5],
            "double_count_issues": [
                i for w in waterfalls for i in (w.get("double_count_issues") or [])
            ],
        },
        "cost_ownership": ownership_table(),
        "edge_decomposition": edge,
        "toxicity": tox,
        "bitvavo_timeline": bitvavo_timeline(trades),
        "markout": {
            "snapshot": snap,
            "suggested_adverse_bps_ceiling_15": str(suggested_old_ceil),
            "suggested_adverse_bps_ceiling_40": str(suggested_new_ceil),
            "ceiling_was_binding": suggested_old_ceil >= Decimal("15")
            and suggested_new_ceil > suggested_old_ceil,
        },
        "calibration_persisted": data.get("calibration"),
        "hypotheses_unproven": [
            "Fair-value alternatives (microprice, depth-weighted) need OOS book tape",
            "Quote-width vs P(fill)×E(NET|fill) curve needs more regime-tagged fills",
            "Actual capital lock duration vs quote_max_age_ms proxy",
            "Missed-opportunity counterfactual fill rates without look-ahead",
            "Buy vs sell asymmetric required edge — Bitvavo buy markouts look worse but n small",
        ],
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "data/paper_25000live.json")
    report = run_audit(path)
    out = Path("data/economic_edge_audit.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: report[k] for k in (
        "source", "runtime_seconds", "realized_pnl", "trade_count",
        "bitvavo_timeline", "markout",
    )}, indent=2, default=str))
    print(f"\nFull report written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
