"""Explicit canonical quantity constructors. No unlabeled aliases."""

from __future__ import annotations

from decimal import Decimal

from bot.research.accounting.protocol import FILL_RATE, NOTIONAL_EUR, REPLAY_VERSION
from bot.research.accounting.schema import (
    LabeledCount,
    LabeledQuantity,
    count_meta,
    expected_money_meta,
    mean_edge_meta,
    observed_meta,
    replay_money_meta,
)


def CanonicalNotionalEUR(value: Decimal | str | int | float | None = None) -> LabeledQuantity:
    v = NOTIONAL_EUR if value is None else (
        value if isinstance(value, Decimal) else Decimal(str(value))
    )
    return LabeledQuantity(
        quantity="CanonicalNotionalEUR",
        value=v,
        metadata=replay_money_meta(
            numerator="research_notional_per_signal",
            denominator=None,
            aggregation="per_signal",
            notes="Frozen research notional. Not a PnL figure.",
        ),
    )


def SignalCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="SignalCount",
        value=int(n),
        metadata=count_meta("SignalCount", notes="Admitted gated signals in the replay universe."),
    )


def CandidateCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="CandidateCount",
        value=int(n),
        metadata=count_meta("CandidateCount", notes="Parent-universe candidates before the child gate."),
    )


def AdmittedCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="AdmittedCount",
        value=int(n),
        metadata=count_meta("AdmittedCount", notes="Gate-admitted signals."),
    )


def RejectedCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="RejectedCount",
        value=int(n),
        metadata=count_meta("RejectedCount", notes="Gate-rejected (not labels)."),
    )


def EstimatedFillCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="EstimatedFillCount",
        value=int(n),
        metadata=count_meta(
            "EstimatedFillCount",
            notes=f"round(SignalCount * fill_rate={FILL_RATE}); estimated, not observed fills.",
        ),
    )


def ExecutionCount(n: int) -> LabeledCount:
    return LabeledCount(
        quantity="ExecutionCount",
        value=int(n),
        metadata=count_meta("ExecutionCount", notes="Observed executions (OBSERVED world uses observed_meta)."),
    )


def GrossEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="GrossEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(notional * forward_return)",
            denominator=None,
            aggregation="aggregate",
        ),
    )


def FeesEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="FeesEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(notional * round_trip_taker_fee_rate)",
            denominator=None,
            aggregation="aggregate",
        ),
    )


def SlippageEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="SlippageEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(notional * slippage_bps / 10000)",
            denominator=None,
            aggregation="aggregate",
        ),
    )


def AdverseEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="AdverseEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(notional * adverse_bps / 10000)",
            denominator=None,
            aggregation="aggregate",
        ),
    )


def FundingEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="FundingEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(funding)",
            denominator=None,
            aggregation="aggregate",
            notes="Research replay currently records 0.",
        ),
    )


def TransferEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="TransferEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(transfer_fx)",
            denominator=None,
            aggregation="aggregate",
            notes="Research replay currently records 0.",
        ),
    )


def OtherCostsEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="OtherCostsEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(latency_penalty + other_explicit_costs)",
            denominator=None,
            aggregation="aggregate",
            notes="Latency penalty is an explicit other cost in the frozen research model.",
        ),
    )


def ExpectedNetEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="ExpectedNetEUR",
        value=value,
        metadata=expected_money_meta(
            numerator="expected_net_per_signal_eur * SignalCount",
            denominator=None,
            aggregation="aggregate",
            notes="Product of mean-edge expected net per signal and signal count. Not the replay sum.",
        ),
    )


def ExpectedNetPerSignalEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="ExpectedNetPerSignalEUR",
        value=value,
        metadata=expected_money_meta(
            numerator="mean_edge_waterfall(abs(mean_forward))",
            denominator="one_signal",
            aggregation="per_signal",
        ),
    )


def RealizedReplayNetEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="RealizedReplayNetEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="sum(signal realized_replay_net)",
            denominator=None,
            aggregation="aggregate",
            notes="Canonical execution-replay aggregate. Primary strategy evaluation output.",
        ),
    )


def RealizedReplayNetPerFillEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="RealizedReplayNetPerFillEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="RealizedReplayNetEUR",
            denominator="EstimatedFillCount",
            aggregation="per_fill",
            notes="RealizedReplayNetEUR / EstimatedFillCount. The only legitimate generic replay NET/fill.",
        ),
    )


def RealizedReplayNetPerSignalEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="RealizedReplayNetPerSignalEUR",
        value=value,
        metadata=replay_money_meta(
            numerator="RealizedReplayNetEUR",
            denominator="SignalCount",
            aggregation="per_signal",
        ),
    )


def MeanEdgeExecutionReplayNetPerSignalEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="MeanEdgeExecutionReplayNetPerSignalEUR",
        value=value,
        metadata=mean_edge_meta(
            numerator=f"fill_rate={FILL_RATE} * (ExpectedNetPerSignalEUR - extra_adverse_eur)",
            denominator="one_signal",
            aggregation="per_signal",
            unit="EUR_per_signal",
        ),
    )


def MeanEdgeExecutionReplayNetPerFillEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="MeanEdgeExecutionReplayNetPerFillEUR",
        value=value,
        metadata=mean_edge_meta(
            numerator="MeanEdgeExecutionReplayNetPerSignalEUR",
            denominator="EstimatedFillCount",
            aggregation="per_fill",
            unit="EUR_per_estimated_fill_of_mean_edge_replay",
        ),
    )


def ObservedRealizedRoundtripNetEUR(value: Decimal) -> LabeledQuantity:
    return LabeledQuantity(
        quantity="ObservedRealizedRoundtripNetEUR",
        value=value,
        metadata=observed_meta(
            numerator="sum(observed paper/live round-trip net)",
            denominator=None,
            aggregation="aggregate",
            unit="EUR",
        ),
    )


def replay_version() -> str:
    return REPLAY_VERSION
