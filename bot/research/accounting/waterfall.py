"""Exactly one canonical execution-replay waterfall.

gross - fees - slippage - adverse - funding - transfer - other_costs
= realized_replay_net

Consumers must not recompute this identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence

from bot.research.accounting.protocol import (
    FILL_RATE,
    NOTIONAL_EUR,
    REPLAY_VERSION,
    WATERFALL_TOLERANCE,
)
from bot.research.accounting.quantities import (
    AdmittedCount,
    AdverseEUR,
    CandidateCount,
    CanonicalNotionalEUR,
    EstimatedFillCount,
    ExpectedNetEUR,
    ExpectedNetPerSignalEUR,
    FeesEUR,
    FundingEUR,
    GrossEUR,
    MeanEdgeExecutionReplayNetPerFillEUR,
    MeanEdgeExecutionReplayNetPerSignalEUR,
    OtherCostsEUR,
    RealizedReplayNetEUR,
    RealizedReplayNetPerFillEUR,
    RealizedReplayNetPerSignalEUR,
    RejectedCount,
    SignalCount,
    SlippageEUR,
    TransferEUR,
)
from bot.research.accounting.schema import (
    AccountingIdentityError,
    EconomicWorld,
    LabeledQuantity,
    labeled_ratio,
)
from bot.research.tournament.criteria import (
    ADVERSE_BPS_DEFAULT,
    LATENCY_PENALTY_BPS,
    SLIPPAGE_BPS_DEFAULT,
)
from bot.research.tournament.economics import execution_replay_net, net_waterfall_from_edge, round_trip_fee_rate

_BPS = Decimal("10000")
_ZERO = Decimal("0")


def estimated_fill_count(signal_count: int) -> int:
    n = int(signal_count or 0)
    if n <= 0:
        return 0
    return max(1, int(round(n * FILL_RATE)))


def unit_costs_eur(
    *,
    venue: str,
    venue_exit: str | None,
    notional: Decimal = NOTIONAL_EUR,
) -> dict[str, Decimal]:
    fee_rate = Decimal(str(round_trip_fee_rate(venue, venue_exit)))
    fees = notional * fee_rate
    slip = notional * (Decimal(str(SLIPPAGE_BPS_DEFAULT)) / _BPS)
    adverse = notional * (Decimal(str(ADVERSE_BPS_DEFAULT)) / _BPS)
    other = notional * (Decimal(str(LATENCY_PENALTY_BPS)) / _BPS)
    return {
        "fees": fees,
        "slippage": slip,
        "adverse": adverse,
        "funding": _ZERO,
        "transfer": _ZERO,
        "other_costs": other,
        "fee_rate": fee_rate,
        "notional": notional,
    }


@dataclass(frozen=True, slots=True)
class SignalReplayLine:
    """One admitted-signal canonical waterfall. Immutable."""

    ts_ns: int | None
    symbol: str | None
    route: str | None
    forward: Decimal
    notional_eur: Decimal
    gross: Decimal
    fees: Decimal
    slippage: Decimal
    adverse: Decimal
    funding: Decimal
    transfer: Decimal
    other_costs: Decimal
    realized_replay_net: Decimal

    def residual(self) -> Decimal:
        return (
            self.gross
            - self.fees
            - self.slippage
            - self.adverse
            - self.funding
            - self.transfer
            - self.other_costs
            - self.realized_replay_net
        )


def line_from_forward(
    *,
    forward: float | Decimal,
    venue: str,
    venue_exit: str | None,
    ts_ns: int | None = None,
    symbol: str | None = None,
    route: str | None = None,
    notional: Decimal = NOTIONAL_EUR,
) -> SignalReplayLine:
    costs = unit_costs_eur(venue=venue, venue_exit=venue_exit, notional=notional)
    fwd = forward if isinstance(forward, Decimal) else Decimal(str(forward))
    gross = notional * fwd
    net = (
        gross
        - costs["fees"]
        - costs["slippage"]
        - costs["adverse"]
        - costs["funding"]
        - costs["transfer"]
        - costs["other_costs"]
    )
    return SignalReplayLine(
        ts_ns=None if ts_ns is None else int(ts_ns),
        symbol=symbol,
        route=route,
        forward=fwd,
        notional_eur=notional,
        gross=gross,
        fees=costs["fees"],
        slippage=costs["slippage"],
        adverse=costs["adverse"],
        funding=costs["funding"],
        transfer=costs["transfer"],
        other_costs=costs["other_costs"],
        realized_replay_net=net,
    )


def line_from_attached_event(
    event: dict[str, Any],
    *,
    venue: str,
    venue_exit: str | None,
) -> SignalReplayLine:
    """Map existing attach_event_economics rows onto the canonical waterfall.

    latency is OtherCostsEUR. Does not change the numeric cost model.
    """
    notional = NOTIONAL_EUR
    if event.get("gross") is not None and event.get("forward") is not None:
        gross = Decimal(str(event["gross"]))
        fwd = Decimal(str(event["forward"]))
    elif event.get("forward") is not None:
        return line_from_forward(
            forward=event["forward"],
            venue=venue,
            venue_exit=venue_exit,
            ts_ns=event.get("ts_ns"),
            symbol=event.get("symbol"),
            route=event.get("route") or event.get("venue"),
        )
    else:
        gross = Decimal(str(event.get("gross") or 0))
        fwd = _ZERO
    fees = Decimal(str(event.get("fees") or 0))
    slip = Decimal(str(event.get("slippage") or 0))
    adverse = Decimal(str(event.get("adverse") or 0))
    other = Decimal(str(event.get("latency") or event.get("other_costs") or 0))
    funding = Decimal(str(event.get("funding") or 0))
    transfer = Decimal(str(event.get("transfer") or 0))
    stored_net = event.get("net")
    computed = gross - fees - slip - adverse - funding - transfer - other
    if stored_net is not None:
        net = Decimal(str(stored_net))
    else:
        net = computed
    return SignalReplayLine(
        ts_ns=None if event.get("ts_ns") is None else int(event["ts_ns"]),
        symbol=None if event.get("symbol") is None else str(event.get("symbol")),
        route=None if event.get("route") is None else str(event.get("route")),
        forward=fwd,
        notional_eur=notional,
        gross=gross,
        fees=fees,
        slippage=slip,
        adverse=adverse,
        funding=funding,
        transfer=transfer,
        other_costs=other,
        realized_replay_net=net,
    )


def assert_waterfall_identity(
    lines: Sequence[SignalReplayLine],
    *,
    tolerance: Decimal = WATERFALL_TOLERANCE,
) -> None:
    for i, line in enumerate(lines):
        resid = line.residual()
        if abs(resid) > tolerance:
            raise AccountingIdentityError(
                f"signal[{i}] waterfall residual {resid} exceeds {tolerance}"
            )


def assert_aggregate_identity(
    lines: Sequence[SignalReplayLine],
    aggregate_net: Decimal,
    *,
    tolerance: Decimal = WATERFALL_TOLERANCE,
) -> None:
    summed = sum((ln.realized_replay_net for ln in lines), _ZERO)
    if abs(summed - aggregate_net) > tolerance:
        raise AccountingIdentityError(
            f"sum(signal realized_replay_net)={summed} != aggregate {aggregate_net}"
        )


@dataclass(frozen=True, slots=True)
class CanonicalEconomics:
    """The only object consumers may use for strategy evaluation economics."""

    world: EconomicWorld
    replay_version: str
    notional: LabeledQuantity
    signals: LabeledCount
    candidates: LabeledCount
    admitted: LabeledCount
    rejected: LabeledCount
    fills: LabeledCount
    gross: LabeledQuantity
    fees: LabeledQuantity
    slippage: LabeledQuantity
    adverse: LabeledQuantity
    funding: LabeledQuantity
    transfer: LabeledQuantity
    other_costs: LabeledQuantity
    replay_net: LabeledQuantity
    replay_net_per_signal: LabeledQuantity
    replay_net_per_fill: LabeledQuantity | None
    expected_net_total: LabeledQuantity
    expected_net_per_signal: LabeledQuantity
    mean_edge_execution_replay_net_per_signal: LabeledQuantity
    mean_edge_execution_replay_net_per_fill: LabeledQuantity | None
    venue: str
    venue_exit: str | None
    lines: tuple[SignalReplayLine, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "world": self.world.value,
            "replay_version": self.replay_version,
            "venue": self.venue,
            "venue_exit": self.venue_exit,
            "notional": self.notional.to_dict(),
            "signals": self.signals.to_dict(),
            "candidates": self.candidates.to_dict(),
            "admitted": self.admitted.to_dict(),
            "rejected": self.rejected.to_dict(),
            "fills": self.fills.to_dict(),
            "replay_gross_eur": self.gross.to_dict(),
            "replay_fees_eur": self.fees.to_dict(),
            "replay_slippage_eur": self.slippage.to_dict(),
            "replay_adverse_eur": self.adverse.to_dict(),
            "replay_funding_eur": self.funding.to_dict(),
            "replay_transfer_eur": self.transfer.to_dict(),
            "replay_other_costs_eur": self.other_costs.to_dict(),
            "replay_net_eur": self.replay_net.to_dict(),
            "replay_net_per_signal_eur": self.replay_net_per_signal.to_dict(),
            "replay_net_per_fill_eur": (
                None if self.replay_net_per_fill is None else self.replay_net_per_fill.to_dict()
            ),
            "expected_net_total_eur": self.expected_net_total.to_dict(),
            "expected_net_per_signal_eur": self.expected_net_per_signal.to_dict(),
            "mean_edge_execution_replay_net_per_signal_eur": (
                self.mean_edge_execution_replay_net_per_signal.to_dict()
            ),
            "mean_edge_execution_replay_net_per_fill_eur": (
                None
                if self.mean_edge_execution_replay_net_per_fill is None
                else self.mean_edge_execution_replay_net_per_fill.to_dict()
            ),
            "n_lines": len(self.lines),
        }

    def report_block(self) -> dict[str, Any]:
        """Labeled three-world block for dashboards and JSON reports."""
        fills_n = self.fills.value
        return {
            "SIGNALS": self.signals.value,
            "CANDIDATES": self.candidates.value,
            "ADMITTED": self.admitted.value,
            "REJECTED": self.rejected.value,
            "FILLS": fills_n,
            "FILLS_definition": self.fills.metadata.to_dict(),
            "EXPECTED_WORLD": {
                "world": EconomicWorld.SIGNAL_EXPECTATION.value,
                "expected_net_total_eur": self.expected_net_total.to_dict(),
                "expected_net_per_signal_eur": self.expected_net_per_signal.to_dict(),
            },
            "EXECUTION_REPLAY_WORLD": {
                "world": EconomicWorld.EXECUTION_REPLAY.value,
                "replay_version": self.replay_version,
                "replay_gross_eur": self.gross.to_dict(),
                "replay_fees_eur": self.fees.to_dict(),
                "replay_slippage_eur": self.slippage.to_dict(),
                "replay_adverse_eur": self.adverse.to_dict(),
                "replay_funding_eur": self.funding.to_dict(),
                "replay_transfer_eur": self.transfer.to_dict(),
                "replay_other_costs_eur": self.other_costs.to_dict(),
                "replay_net_eur": self.replay_net.to_dict(),
                "replay_net_per_signal_eur": self.replay_net_per_signal.to_dict(),
                "replay_net_per_fill_eur": (
                    None if self.replay_net_per_fill is None else self.replay_net_per_fill.to_dict()
                ),
            },
            "MEAN_EDGE_EXECUTION_REPLAY_SIDECAR": {
                "world": EconomicWorld.EXECUTION_REPLAY.value,
                "replay_version": self.mean_edge_execution_replay_net_per_signal.metadata.replay_version,
                "mean_edge_execution_replay_net_per_signal_eur": (
                    self.mean_edge_execution_replay_net_per_signal.to_dict()
                ),
                "mean_edge_execution_replay_net_per_fill_eur": (
                    None
                    if self.mean_edge_execution_replay_net_per_fill is None
                    else self.mean_edge_execution_replay_net_per_fill.to_dict()
                ),
                "warning": (
                    "This sidecar is NOT canonical replay NET/fill. "
                    "Historically published as unlabeled NET/fill (e.g. 0.00503)."
                ),
            },
            "OBSERVED_WORLD": {
                "world": EconomicWorld.OBSERVED.value,
                "status": "NOT_RUN",
                "note": "Paper/live observations must never silently replace replay values.",
            },
        }


def assemble_canonical(
    lines: Sequence[SignalReplayLine],
    *,
    venue: str,
    venue_exit: str | None,
    candidates: int,
    admitted: int,
    rejected: int,
    mean_forward: float | None,
    expected_net_per_signal: Decimal | None = None,
) -> CanonicalEconomics:
    assert_waterfall_identity(lines)
    n = len(lines)
    gross = sum((ln.gross for ln in lines), _ZERO)
    fees = sum((ln.fees for ln in lines), _ZERO)
    slip = sum((ln.slippage for ln in lines), _ZERO)
    adverse = sum((ln.adverse for ln in lines), _ZERO)
    funding = sum((ln.funding for ln in lines), _ZERO)
    transfer = sum((ln.transfer for ln in lines), _ZERO)
    other = sum((ln.other_costs for ln in lines), _ZERO)
    net = sum((ln.realized_replay_net for ln in lines), _ZERO)
    assert_aggregate_identity(lines, net)

    fills_n = estimated_fill_count(n)
    replay_net = RealizedReplayNetEUR(net)
    signals = SignalCount(n)
    fills = EstimatedFillCount(fills_n)
    per_signal = (
        labeled_ratio(
            quantity="RealizedReplayNetPerSignalEUR",
            numerator=replay_net,
            denominator=signals,
            unit="EUR",
            aggregation="per_signal",
        )
        if n
        else RealizedReplayNetPerSignalEUR(_ZERO)
    )
    per_fill = (
        labeled_ratio(
            quantity="RealizedReplayNetPerFillEUR",
            numerator=replay_net,
            denominator=fills,
            unit="EUR",
            aggregation="per_fill",
            notes="RealizedReplayNetEUR / EstimatedFillCount",
        )
        if fills_n
        else None
    )

    if expected_net_per_signal is None:
        edge = abs(float(mean_forward or 0.0))
        wf = net_waterfall_from_edge(
            gross_edge_fraction=edge, venue=venue, venue_exit=venue_exit
        )
        expected_ps = Decimal(str(wf["EXPECTED_NET"]))
    else:
        expected_ps = expected_net_per_signal
    expected_ps_q = ExpectedNetPerSignalEUR(expected_ps)
    expected_total = ExpectedNetEUR(expected_ps * Decimal(n))

    sidecar = execution_replay_net(expected_net=float(expected_ps))
    mean_edge_ps = MeanEdgeExecutionReplayNetPerSignalEUR(
        Decimal(str(sidecar["EXECUTION_NET"]))
    )
    mean_edge_pf = None
    if fills_n:
        mean_edge_pf = MeanEdgeExecutionReplayNetPerFillEUR(
            mean_edge_ps.value / Decimal(fills_n)
        )

    return CanonicalEconomics(
        world=EconomicWorld.EXECUTION_REPLAY,
        replay_version=REPLAY_VERSION,
        notional=CanonicalNotionalEUR(),
        signals=signals,
        candidates=CandidateCount(int(candidates)),
        admitted=AdmittedCount(int(admitted)),
        rejected=RejectedCount(int(rejected)),
        fills=fills,
        gross=GrossEUR(gross),
        fees=FeesEUR(fees),
        slippage=SlippageEUR(slip),
        adverse=AdverseEUR(adverse),
        funding=FundingEUR(funding),
        transfer=TransferEUR(transfer),
        other_costs=OtherCostsEUR(other),
        replay_net=replay_net,
        replay_net_per_signal=per_signal
        if n
        else RealizedReplayNetPerSignalEUR(_ZERO),
        replay_net_per_fill=per_fill,
        expected_net_total=expected_total,
        expected_net_per_signal=expected_ps_q,
        mean_edge_execution_replay_net_per_signal=mean_edge_ps,
        mean_edge_execution_replay_net_per_fill=mean_edge_pf,
        venue=venue,
        venue_exit=venue_exit,
        lines=tuple(lines),
    )


def from_attached_events(
    events: Iterable[dict[str, Any]],
    *,
    venue: str,
    venue_exit: str | None,
    mean_forward: float | None,
    audit: dict[str, Any] | None = None,
) -> CanonicalEconomics:
    rows = list(events)
    lines = [line_from_attached_event(e, venue=venue, venue_exit=venue_exit) for e in rows]
    audit = audit or {}
    n = len(lines)
    return assemble_canonical(
        lines,
        venue=venue,
        venue_exit=venue_exit,
        candidates=int(audit.get("candidates") or n),
        admitted=int(audit.get("admitted") or n),
        rejected=int(audit.get("rejected") or 0),
        mean_forward=mean_forward,
    )


def from_component_sums(
    *,
    venue: str,
    venue_exit: str | None,
    signals: int,
    candidates: int,
    admitted: int,
    rejected: int,
    fills: int | None,
    gross: float | Decimal,
    fees: float | Decimal,
    slippage: float | Decimal,
    adverse: float | Decimal,
    net: float | Decimal,
    mean_forward: float | None,
    funding: float | Decimal = 0,
    transfer: float | Decimal = 0,
    other_costs: float | Decimal | None = None,
    expected_net_per_signal: float | Decimal | None = None,
) -> CanonicalEconomics:
    """Rebuild canonical economics from stored aggregates (no event list).

    other_costs defaults to the waterfall residual so identity holds.
    """
    g = Decimal(str(gross))
    f = Decimal(str(fees))
    s = Decimal(str(slippage))
    a = Decimal(str(adverse))
    n = Decimal(str(net))
    fund = Decimal(str(funding))
    xfer = Decimal(str(transfer))
    if other_costs is None:
        other = g - f - s - a - fund - xfer - n
    else:
        other = Decimal(str(other_costs))
    dummy = SignalReplayLine(
        ts_ns=None,
        symbol=None,
        route=None,
        forward=_ZERO,
        notional_eur=NOTIONAL_EUR,
        gross=g,
        fees=f,
        slippage=s,
        adverse=a,
        funding=fund,
        transfer=xfer,
        other_costs=other,
        realized_replay_net=n,
    )
    # Aggregate-only reconstruction: one synthetic line holding the sums.
    # Per-signal identity is the aggregate identity. Do not treat dummy as a signal.
    assert_waterfall_identity([dummy])
    fills_n = int(fills) if fills is not None else estimated_fill_count(int(signals))
    replay_net = RealizedReplayNetEUR(n)
    sig = SignalCount(int(signals))
    fill_q = EstimatedFillCount(fills_n)
    per_signal = (
        labeled_ratio(
            quantity="RealizedReplayNetPerSignalEUR",
            numerator=replay_net,
            denominator=sig,
            unit="EUR",
            aggregation="per_signal",
        )
        if int(signals)
        else RealizedReplayNetPerSignalEUR(_ZERO)
    )
    per_fill = (
        labeled_ratio(
            quantity="RealizedReplayNetPerFillEUR",
            numerator=replay_net,
            denominator=fill_q,
            unit="EUR",
            aggregation="per_fill",
        )
        if fills_n
        else None
    )
    if expected_net_per_signal is None:
        edge = abs(float(mean_forward or 0.0))
        wf = net_waterfall_from_edge(
            gross_edge_fraction=edge, venue=venue, venue_exit=venue_exit
        )
        expected_ps = Decimal(str(wf["EXPECTED_NET"]))
    else:
        expected_ps = Decimal(str(expected_net_per_signal))
    sidecar = execution_replay_net(expected_net=float(expected_ps))
    mean_edge_ps = MeanEdgeExecutionReplayNetPerSignalEUR(
        Decimal(str(sidecar["EXECUTION_NET"]))
    )
    mean_edge_pf = (
        MeanEdgeExecutionReplayNetPerFillEUR(mean_edge_ps.value / Decimal(fills_n))
        if fills_n
        else None
    )
    return CanonicalEconomics(
        world=EconomicWorld.EXECUTION_REPLAY,
        replay_version=REPLAY_VERSION,
        notional=CanonicalNotionalEUR(),
        signals=sig,
        candidates=CandidateCount(int(candidates)),
        admitted=AdmittedCount(int(admitted)),
        rejected=RejectedCount(int(rejected)),
        fills=fill_q,
        gross=GrossEUR(g),
        fees=FeesEUR(f),
        slippage=SlippageEUR(s),
        adverse=AdverseEUR(a),
        funding=FundingEUR(fund),
        transfer=TransferEUR(xfer),
        other_costs=OtherCostsEUR(other),
        replay_net=replay_net,
        replay_net_per_signal=per_signal,
        replay_net_per_fill=per_fill,
        expected_net_total=ExpectedNetEUR(expected_ps * Decimal(int(signals))),
        expected_net_per_signal=ExpectedNetPerSignalEUR(expected_ps),
        mean_edge_execution_replay_net_per_signal=mean_edge_ps,
        mean_edge_execution_replay_net_per_fill=mean_edge_pf,
        venue=venue,
        venue_exit=venue_exit,
        lines=(dummy,),
    )


def empty_canonical(*, venue: str, venue_exit: str | None) -> CanonicalEconomics:
    return from_component_sums(
        venue=venue,
        venue_exit=venue_exit,
        signals=0,
        candidates=0,
        admitted=0,
        rejected=0,
        fills=0,
        gross=0,
        fees=0,
        slippage=0,
        adverse=0,
        net=0,
        mean_forward=0.0,
        expected_net_per_signal=0,
        other_costs=0,
    )
