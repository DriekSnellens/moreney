"""Canonical replay economics for parent / retained / excluded groups."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from bot.research.accounting.audit import audit_canonical
from bot.research.accounting.protocol import WATERFALL_TOLERANCE
from bot.research.accounting.waterfall import from_attached_events

_ZERO = Decimal("0")


def group_economics(
    events: Sequence[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    label: str,
    parent_signals: int | None = None,
    parent_net: Decimal | None = None,
) -> dict[str, Any]:
    n = len(events)
    mean_fwd = (
        sum(float(e.get("forward") or 0.0) for e in events) / n if n else None
    )
    econ = from_attached_events(
        events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_fwd,
        audit={"candidates": n, "admitted": n, "rejected": 0},
    )
    acc = audit_canonical(econ)
    parent_n = parent_signals if parent_signals is not None else n
    pnet = parent_net if parent_net is not None else econ.replay_net.value
    share_sig = (n / parent_n) if parent_n else None
    share_net = (econ.replay_net.value / pnet) if pnet else None
    return {
        "GROUP": label,
        "signal_count": econ.signals.value,
        "estimated_fills": econ.fills.value,
        "gross_eur": str(econ.gross.value),
        "fees_eur": str(econ.fees.value),
        "slippage_eur": str(econ.slippage.value),
        "adverse_eur": str(econ.adverse.value),
        "other_costs_eur": str(econ.other_costs.value),
        "replay_net_eur": str(econ.replay_net.value),
        "replay_net_per_signal": (
            None if n == 0 else str(econ.replay_net_per_signal.value)
        ),
        "replay_net_per_fill": (
            None if econ.replay_net_per_fill is None else str(econ.replay_net_per_fill.value)
        ),
        "share_of_parent_signals": None if share_sig is None else float(share_sig),
        "share_of_parent_net": None if share_net is None else str(share_net),
        "world": "EXECUTION_REPLAY",
        "ACCOUNTING_AUDIT": acc["ACCOUNTING_AUDIT"],
        "canonical": econ.report_block(),
        "excluded_economically_positive": (
            econ.replay_net.value > 0 if label == "EXCLUDED_BY_CHILD" else None
        ),
    }


def assert_parent_identity(
    parent: dict[str, Any],
    retained: dict[str, Any],
    excluded: dict[str, Any],
    *,
    unsupported_net: Decimal = _ZERO,
    unsupported_n: int = 0,
) -> list[str]:
    issues: list[str] = []
    p_n = int(parent["signal_count"])
    r_n = int(retained["signal_count"])
    e_n = int(excluded["signal_count"])
    if p_n != r_n + e_n + unsupported_n:
        issues.append(
            f"parent signals {p_n} != retained {r_n} + excluded {e_n} + unsupported {unsupported_n}"
        )
    p_net = Decimal(str(parent["replay_net_eur"]))
    r_net = Decimal(str(retained["replay_net_eur"]))
    e_net = Decimal(str(excluded["replay_net_eur"]))
    if abs(p_net - (r_net + e_net + unsupported_net)) > WATERFALL_TOLERANCE:
        issues.append(
            f"parent net {p_net} != retained {r_net} + excluded {e_net} + unsupported {unsupported_net}"
        )
    return issues
