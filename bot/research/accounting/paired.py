"""Paired parent vs child vs no-trade on the exact same candidate universe."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any, Sequence

from bot.research.accounting.schema import AccountingIdentityError, EconomicWorld
from bot.research.accounting.waterfall import (
    CanonicalEconomics,
    empty_canonical,
    from_attached_events,
)

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PairedPartition:
    parent_events: tuple[dict[str, Any], ...]
    child_events: tuple[dict[str, Any], ...]
    excluded_events: tuple[dict[str, Any], ...]
    unsupported_events: tuple[dict[str, Any], ...]
    candidates: int
    admitted: int
    rejected: int
    unsupported: int


@dataclass(frozen=True, slots=True)
class PairedWindowResult:
    window_id: str
    complete: bool
    start_ts_ns: int
    end_ts_ns_inclusive: int
    parent: CanonicalEconomics
    child: CanonicalEconomics
    no_trade: CanonicalEconomics
    excluded: CanonicalEconomics
    unsupported: CanonicalEconomics
    child_only_signals: int
    parent_only_signals: int
    shared_signals: int
    delta_replay_net_eur: Decimal
    delta_replay_net_per_signal_eur: Decimal | None
    delta_replay_net_per_fill_eur: Decimal | None
    shared_signal_net: Decimal
    excluded_signal_net: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window_id,
            "complete": self.complete,
            "start_ts_ns": self.start_ts_ns,
            "end_ts_ns_inclusive": self.end_ts_ns_inclusive,
            "parent_replay_net": str(self.parent.replay_net.value),
            "child_replay_net": str(self.child.replay_net.value),
            "delta": str(self.delta_replay_net_eur),
            "parent_signals": self.parent.signals.value,
            "child_signals": self.child.signals.value,
            "parent_fills": self.parent.fills.value,
            "child_fills": self.child.fills.value,
            "shared_signal_net": str(self.shared_signal_net),
            "excluded_signal_net": str(self.excluded_signal_net),
            "child_only_signals": self.child_only_signals,
            "parent_only_signals": self.parent_only_signals,
            "shared_signals": self.shared_signals,
            "parent": self.parent.report_block(),
            "child": self.child.report_block(),
            "no_trade": self.no_trade.report_block(),
            "world": EconomicWorld.EXECUTION_REPLAY.value,
        }


def _ids(events: Sequence[dict[str, Any]]) -> set[tuple[Any, Any, Any]]:
    out: set[tuple[Any, Any, Any]] = set()
    for e in events:
        out.add((e.get("ts_ns"), e.get("symbol"), e.get("route") or e.get("venue")))
    return out


def pair_window(
    *,
    window_id: str,
    complete: bool,
    start_ts_ns: int,
    end_ts_ns_inclusive: int,
    partition: PairedPartition,
    venue: str,
    venue_exit: str | None,
    mean_forward_parent: float | None,
    mean_forward_child: float | None,
    mean_forward_excluded: float | None = None,
) -> PairedWindowResult:
    parent = from_attached_events(
        partition.parent_events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_forward_parent,
        audit={
            "candidates": partition.candidates,
            "admitted": partition.candidates,
            "rejected": 0,
        },
    )
    child = from_attached_events(
        partition.child_events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_forward_child,
        audit={
            "candidates": partition.candidates,
            "admitted": partition.admitted,
            "rejected": partition.rejected,
        },
    )
    excluded = from_attached_events(
        partition.excluded_events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=mean_forward_excluded,
        audit={
            "candidates": partition.candidates,
            "admitted": partition.rejected,
            "rejected": 0,
        },
    )
    unsupported = from_attached_events(
        partition.unsupported_events,
        venue=venue,
        venue_exit=venue_exit,
        mean_forward=0.0,
        audit={
            "candidates": partition.unsupported,
            "admitted": 0,
            "rejected": 0,
        },
    )
    no_trade = empty_canonical(venue=venue, venue_exit=venue_exit)

    parent_ids = _ids(partition.parent_events)
    child_ids = _ids(partition.child_events)
    shared = parent_ids & child_ids
    child_only = child_ids - parent_ids
    parent_only = parent_ids - child_ids

    summed = child.replay_net.value + excluded.replay_net.value + unsupported.replay_net.value
    if abs(summed - parent.replay_net.value) > Decimal("0.0001"):
        raise AccountingIdentityError(
            f"paired identity failed: child+excluded+unsupported={summed} "
            f"parent={parent.replay_net.value}"
        )

    delta = child.replay_net.value - parent.replay_net.value
    d_ps = None
    if child.signals.value and parent.signals.value:
        d_ps = child.replay_net_per_signal.value - parent.replay_net_per_signal.value
    d_pf = None
    if child.replay_net_per_fill is not None and parent.replay_net_per_fill is not None:
        d_pf = child.replay_net_per_fill.value - parent.replay_net_per_fill.value

    return PairedWindowResult(
        window_id=window_id,
        complete=complete,
        start_ts_ns=int(start_ts_ns),
        end_ts_ns_inclusive=int(end_ts_ns_inclusive),
        parent=parent,
        child=child,
        no_trade=no_trade,
        excluded=excluded,
        unsupported=unsupported,
        child_only_signals=len(child_only),
        parent_only_signals=len(parent_only),
        shared_signals=len(shared),
        delta_replay_net_eur=delta,
        delta_replay_net_per_signal_eur=d_ps,
        delta_replay_net_per_fill_eur=d_pf,
        shared_signal_net=child.replay_net.value,
        excluded_signal_net=excluded.replay_net.value,
    )


def pair_from_stored_nets(
    *,
    window_id: str,
    complete: bool,
    start_ts_ns: int,
    end_ts_ns_inclusive: int,
    parent: CanonicalEconomics,
    child: CanonicalEconomics,
    excluded_net: Decimal | None = None,
) -> dict[str, Any]:
    """Paired table row when only aggregates exist.

    excluded_net defaults to parent_net - child_net (unsupported=0).
    """
    excl = parent.replay_net.value - child.replay_net.value if excluded_net is None else excluded_net
    return {
        "window": window_id,
        "complete": complete,
        "start_ts_ns": start_ts_ns,
        "end_ts_ns_inclusive": end_ts_ns_inclusive,
        "parent_replay_net": str(parent.replay_net.value),
        "child_replay_net": str(child.replay_net.value),
        "delta": str(child.replay_net.value - parent.replay_net.value),
        "parent_signals": parent.signals.value,
        "child_signals": child.signals.value,
        "parent_fills": parent.fills.value,
        "child_fills": child.fills.value,
        "shared_signal_net": str(child.replay_net.value),
        "excluded_signal_net": str(excl),
        "child_only_signals": 0,
        "parent_only_signals": parent.signals.value - child.signals.value,
        "shared_signals": child.signals.value,
        "world": EconomicWorld.EXECUTION_REPLAY.value,
        "paired_universe": True,
        "identity": "parent_replay_net = child_replay_net + excluded_signal_net",
    }


def aggregate_paired(rows: Sequence[PairedWindowResult] | Sequence[dict[str, Any]]) -> dict[str, Any]:
    deltas: list[Decimal] = []
    complete_rows: list[Any] = []
    for row in rows:
        if isinstance(row, PairedWindowResult):
            if not row.complete:
                continue
            complete_rows.append(row)
            deltas.append(row.delta_replay_net_eur)
        else:
            if not row.get("complete", True):
                continue
            complete_rows.append(row)
            deltas.append(Decimal(str(row["delta"])))
    if not deltas:
        return {
            "n_complete_windows": 0,
            "mean_delta": None,
            "median_delta": None,
            "positive_window_fraction": None,
            "worst_window": None,
            "best_window": None,
            "aggregate_delta": str(_ZERO),
        }

    mean_d = sum(deltas, _ZERO) / Decimal(len(deltas))
    med_d = Decimal(str(median(deltas)))
    n_pos = sum(1 for d in deltas if d > 0)
    worst_i = min(range(len(deltas)), key=lambda i: deltas[i])
    best_i = max(range(len(deltas)), key=lambda i: deltas[i])

    def _id(row: Any) -> str:
        return row.window_id if isinstance(row, PairedWindowResult) else str(row.get("window"))

    return {
        "n_complete_windows": len(deltas),
        "mean_delta": str(mean_d),
        "median_delta": str(med_d),
        "positive_window_fraction": n_pos / len(deltas),
        "worst_window": {"window": _id(complete_rows[worst_i]), "delta": str(deltas[worst_i])},
        "best_window": {"window": _id(complete_rows[best_i]), "delta": str(deltas[best_i])},
        "aggregate_delta": str(sum(deltas, _ZERO)),
        "aggregate_delta_positive": sum(deltas, _ZERO) > 0,
        "world": EconomicWorld.EXECUTION_REPLAY.value,
        "quantity": "delta_replay_net_eur",
        "definition": (
            "child RealizedReplayNetEUR - parent RealizedReplayNetEUR on the same universe"
        ),
        "p_values": "not_computed_assumptions_not_justified",
    }
