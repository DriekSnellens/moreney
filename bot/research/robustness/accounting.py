"""Accounting consistency audit. Attach units; fail if NET/fill is ambiguous."""

from __future__ import annotations

from typing import Any

from bot.research.robustness.protocol import (
    ADVERSE_EXTRA_BPS,
    FILL_RATE_BASELINE,
    NOTIONAL_EUR,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    SLIPPAGE_BPS_DEFAULT,
)

REL_TOL = 0.01


def tagged(value: Any, unit: str, definition: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "definition": definition}


def _rel_close(a: float | None, b: float | None, tol: float = REL_TOL) -> bool:
    if a is None or b is None:
        return False
    scale = max(abs(float(a)), abs(float(b)), 1e-12)
    return abs(float(a) - float(b)) / scale <= tol


def canonical_units(
    *,
    net_sum: float | None,
    expected_net: float | None,
    execution_net: float | None,
    signals: int,
    fills: int,
    reported_net_per_fill: float | None,
    gross_sum: float | None = None,
    fees_sum: float | None = None,
    slip_sum: float | None = None,
    adverse_sum: float | None = None,
) -> dict[str, Any]:
    n = max(int(signals or 0), 0)
    f = max(int(fills or 0), 0)
    net_per_signal = (float(net_sum) / n) if n and net_sum is not None else None
    net_per_fill_from_sum = (float(net_sum) / f) if f and net_sum is not None else None
    net_per_fill_from_replay = (
        (float(execution_net) / f) if f and execution_net is not None else None
    )
    net_per_notional = (
        (float(net_per_signal) / NOTIONAL_EUR) if net_per_signal is not None else None
    )
    return {
        "notional": tagged(NOTIONAL_EUR, "EUR", "Per-signal research notional (unchanged)."),
        "NET": tagged(
            net_sum,
            "EUR",
            "Absolute currency: sum of per-signal nets at the research notional.",
        ),
        "EXPECTED_NET": tagged(
            expected_net,
            "EUR_per_signal",
            "Mean-edge waterfall NET per signal (tournament economic model).",
        ),
        "EXECUTION_NET": tagged(
            execution_net,
            "EUR_per_signal_replay",
            (
                f"fill_rate={FILL_RATE_BASELINE} * (EXPECTED_NET - extra_adverse "
                f"{ADVERSE_EXTRA_BPS} bps * notional). Not a sum."
            ),
        ),
        "NET_per_signal": tagged(
            net_per_signal,
            "EUR_per_signal",
            "NET / signal_count. Should match EXPECTED_NET when forwards are signed consistently.",
        ),
        "NET_per_fill_from_sum": tagged(
            net_per_fill_from_sum,
            "EUR_per_estimated_fill",
            "NET / completed_round_trips. Uses sum NET, not the mean-edge replay.",
        ),
        "NET_per_fill_from_replay": tagged(
            net_per_fill_from_replay,
            "EUR_per_estimated_fill_of_mean_edge_replay",
            "EXECUTION_NET / completed_round_trips. This is what the first lab reported as NET/fill.",
        ),
        "NET_per_fill_reported": tagged(
            reported_net_per_fill,
            "EUR_per_estimated_fill_of_mean_edge_replay",
            "Value published by the first lab as NET/fill.",
        ),
        "NET_per_notional": tagged(
            net_per_notional,
            "fraction_of_notional",
            "NET_per_signal / notional. Multiply by 10000 for bps of notional.",
        ),
        "gross_sum": tagged(gross_sum, "EUR", "Sum of per-signal gross (notional * forward)."),
        "fees_sum": tagged(fees_sum, "EUR", "Sum of per-signal round-trip fees."),
        "slippage_sum": tagged(slip_sum, "EUR", "Sum of per-signal slippage."),
        "adverse_sum": tagged(adverse_sum, "EUR", "Sum of per-signal adverse."),
        "signals": tagged(n, "count", "Admitted gated signals."),
        "fills": tagged(
            f,
            "count",
            f"round(signals * fill_rate={FILL_RATE_BASELINE}); estimated, not observed fills.",
        ),
        "baseline_cost_bps": {
            "adverse_bps": tagged(ADVERSE_BPS_DEFAULT, "bps", "Production research adverse."),
            "slippage_bps": tagged(SLIPPAGE_BPS_DEFAULT, "bps", "Production research slippage."),
            "latency_bps": tagged(LATENCY_PENALTY_BPS, "bps", "Production research latency penalty."),
        },
    }


def audit_card(card: dict[str, Any]) -> dict[str, Any]:
    oos = card.get("OOS_RESULT") or {}
    metrics = card.get("metrics_oos") or {}
    net = oos.get("NET") if oos.get("NET") is not None else metrics.get("NET")
    expected = oos.get("EXPECTED_NET") if oos.get("EXPECTED_NET") is not None else metrics.get("EXPECTED_NET")
    execution = (
        oos.get("EXECUTION_NET")
        or metrics.get("EXECUTION_NET")
        or (oos.get("execution_replay") or {}).get("EXECUTION_NET")
        or (metrics.get("execution_replay") or {}).get("EXECUTION_NET")
    )
    signals = int(oos.get("signals") or metrics.get("signals") or card.get("SAMPLE_COUNT") or 0)
    fills = int(oos.get("completed_round_trips") or metrics.get("completed_round_trips") or 0)
    reported = card.get("NET/fill")
    if reported is None:
        reported = oos.get("NET/fill") or oos.get("NET_per_fill")
    units = canonical_units(
        net_sum=None if net is None else float(net),
        expected_net=None if expected is None else float(expected),
        execution_net=None if execution is None else float(execution),
        signals=signals,
        fills=fills,
        reported_net_per_fill=None if reported is None else float(reported),
        gross_sum=oos.get("gross"),
        fees_sum=oos.get("fees"),
        slip_sum=oos.get("slippage"),
        adverse_sum=oos.get("adverse"),
    )
    issues: list[str] = []
    if net is None or reported is None or fills <= 0:
        issues.append("missing_net_or_fill")
    sum_per_fill = units["NET_per_fill_from_sum"]["value"]
    replay_per_fill = units["NET_per_fill_from_replay"]["value"]
    published_matches_sum = _rel_close(
        None if reported is None else float(reported), sum_per_fill
    )
    published_matches_replay = _rel_close(
        None if reported is None else float(reported), replay_per_fill
    )
    if not published_matches_sum and not published_matches_replay:
        issues.append("reported_net_per_fill_matches_neither_definition")
    if published_matches_replay and not published_matches_sum:
        issues.append(
            "reported_NET_per_fill_is_replay_not_sum_NET_over_fills"
        )
    mean_matches = _rel_close(
        units["NET_per_signal"]["value"],
        None if expected is None else float(expected),
        tol=0.02,
    )
    if expected is not None and units["NET_per_signal"]["value"] is not None and not mean_matches:
        issues.append("EXPECTED_NET_does_not_match_NET_over_signals")

    # Ambiguous if the published NET/fill is not the sum-NET definition and is
    # unlabeled, which is the first-lab discrepancy (2218/363 vs 0.005).
    ambiguous = (not published_matches_sum) or ("missing_net_or_fill" in issues)
    verdict = "FAIL" if ambiguous else "PASS"
    return {
        "ACCOUNTING_AUDIT": verdict,
        "ambiguous_net_per_fill": ambiguous,
        "published_matches_sum_NET_over_fills": published_matches_sum,
        "published_matches_replay": published_matches_replay,
        "EXPECTED_NET_matches_mean_signal_NET": mean_matches,
        "issues": issues,
        "units": units,
        "note": (
            "NET is absolute EUR (sum). First-lab NET/fill was EXECUTION_NET/fills "
            "(EUR per estimated fill of the mean-edge replay), not NET/fills."
        ),
    }
