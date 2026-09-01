"""Canonical metric schema: worlds, metadata, labeled quantities.

Naked floats must not cross this module's public boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from bot.research.accounting.protocol import (
    ADVERSE_EXTRA_BPS,
    ADVERSE_MODEL,
    FEE_MODEL,
    FILL_MODEL,
    FILL_RATE,
    MEAN_EDGE_REPLAY_VERSION,
    NOTIONAL_EUR,
    REPLAY_VERSION,
    SCHEMA_VERSION,
)

Aggregation = Literal["aggregate", "per_signal", "per_fill", "count", "ratio", "flag"]
ExpectationKind = Literal["expected", "realized", "observed"]


class EconomicWorld(str, Enum):
    SIGNAL_EXPECTATION = "SIGNAL_EXPECTATION"
    EXECUTION_REPLAY = "EXECUTION_REPLAY"
    OBSERVED = "OBSERVED"


class CrossWorldError(TypeError):
    """Raised when two incompatible economic worlds are combined unlabeled."""


class UnlabeledMetricError(ValueError):
    """Raised when a metric is emitted without the required metadata."""


class AccountingIdentityError(AssertionError):
    """Raised when the canonical waterfall identity fails."""


@dataclass(frozen=True, slots=True)
class MetricMetadata:
    numerator: str
    denominator: str | None
    unit: str
    notional_basis: str
    fill_model: str
    adverse_model: str
    fee_model: str
    replay_version: str
    economic_world: EconomicWorld
    expected_or_realized: ExpectationKind
    aggregation: Aggregation
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "unit": self.unit,
            "notional_basis": self.notional_basis,
            "fill_model": self.fill_model,
            "adverse_model": self.adverse_model,
            "fee_model": self.fee_model,
            "replay_version": self.replay_version,
            "economic_world": self.economic_world.value,
            "expected_or_realized": self.expected_or_realized,
            "aggregation": self.aggregation,
            "schema_version": self.schema_version,
            "notes": self.notes,
        }


def _dec(value: Decimal | int | str | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class LabeledQuantity:
    """A single economic number that cannot be emitted without metadata."""

    quantity: str
    value: Decimal
    metadata: MetricMetadata

    def __post_init__(self) -> None:
        if not self.quantity:
            raise UnlabeledMetricError("quantity name is required")
        if self.metadata is None:
            raise UnlabeledMetricError(f"{self.quantity} missing metadata")
        if not self.metadata.unit:
            raise UnlabeledMetricError(f"{self.quantity} missing unit")
        if not self.metadata.replay_version:
            raise UnlabeledMetricError(f"{self.quantity} missing replay_version")
        if not self.metadata.economic_world:
            raise UnlabeledMetricError(f"{self.quantity} missing economic_world")

    def as_float(self) -> float:
        """Explicit conversion only — not used for cross-module unlabeled math."""
        return float(self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "value": str(self.value),
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class LabeledCount:
    quantity: str
    value: int
    metadata: MetricMetadata

    def __post_init__(self) -> None:
        if self.metadata is None:
            raise UnlabeledMetricError(f"{self.quantity} missing metadata")
        if int(self.value) < 0:
            raise UnlabeledMetricError(f"{self.quantity} count must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "value": int(self.value),
            "metadata": self.metadata.to_dict(),
        }


def require_world(*quantities: LabeledQuantity | LabeledCount, world: EconomicWorld) -> None:
    for q in quantities:
        if q.metadata.economic_world != world:
            raise CrossWorldError(
                f"{q.quantity} is {q.metadata.economic_world.value}, expected {world.value}"
            )


def forbid_cross_world(a: LabeledQuantity, b: LabeledQuantity) -> None:
    if a.metadata.economic_world != b.metadata.economic_world:
        raise CrossWorldError(
            f"Cannot combine {a.quantity} ({a.metadata.economic_world.value}) "
            f"with {b.quantity} ({b.metadata.economic_world.value}) without an "
            "explicit CrossWorldComparison."
        )


def labeled_ratio(
    *,
    quantity: str,
    numerator: LabeledQuantity,
    denominator: LabeledQuantity | LabeledCount,
    unit: str,
    aggregation: Aggregation,
    notes: str = "",
) -> LabeledQuantity:
    forbid_cross_world(numerator, denominator) if isinstance(denominator, LabeledQuantity) else None
    if isinstance(denominator, LabeledCount):
        if denominator.metadata.economic_world != numerator.metadata.economic_world:
            raise CrossWorldError(
                f"Cannot divide {numerator.quantity} by {denominator.quantity} "
                "across economic worlds."
            )
        den_val = Decimal(denominator.value)
        den_name = denominator.quantity
    else:
        den_val = denominator.value
        den_name = denominator.quantity
    if den_val == 0:
        raise AccountingIdentityError(f"{quantity}: denominator {den_name} is zero")
    meta = MetricMetadata(
        numerator=numerator.quantity,
        denominator=den_name,
        unit=unit,
        notional_basis=numerator.metadata.notional_basis,
        fill_model=numerator.metadata.fill_model,
        adverse_model=numerator.metadata.adverse_model,
        fee_model=numerator.metadata.fee_model,
        replay_version=numerator.metadata.replay_version,
        economic_world=numerator.metadata.economic_world,
        expected_or_realized=numerator.metadata.expected_or_realized,
        aggregation=aggregation,
        notes=notes,
    )
    return LabeledQuantity(quantity=quantity, value=numerator.value / den_val, metadata=meta)


def replay_money_meta(
    *,
    numerator: str,
    denominator: str | None,
    aggregation: Aggregation,
    notes: str = "",
) -> MetricMetadata:
    return MetricMetadata(
        numerator=numerator,
        denominator=denominator,
        unit="EUR",
        notional_basis=f"{NOTIONAL_EUR} EUR per admitted signal",
        fill_model=FILL_MODEL,
        adverse_model=ADVERSE_MODEL,
        fee_model=FEE_MODEL,
        replay_version=REPLAY_VERSION,
        economic_world=EconomicWorld.EXECUTION_REPLAY,
        expected_or_realized="realized",
        aggregation=aggregation,
        notes=notes,
    )


def expected_money_meta(
    *,
    numerator: str,
    denominator: str | None,
    aggregation: Aggregation,
    notes: str = "",
) -> MetricMetadata:
    return MetricMetadata(
        numerator=numerator,
        denominator=denominator,
        unit="EUR" if aggregation == "aggregate" else "EUR_per_signal",
        notional_basis=f"{NOTIONAL_EUR} EUR per signal (mean-edge waterfall)",
        fill_model="none_expectation_not_a_fill",
        adverse_model=ADVERSE_MODEL,
        fee_model=FEE_MODEL,
        replay_version="signal_expectation_mean_edge_v1",
        economic_world=EconomicWorld.SIGNAL_EXPECTATION,
        expected_or_realized="expected",
        aggregation=aggregation,
        notes=notes,
    )


def count_meta(quantity: str, *, notes: str = "") -> MetricMetadata:
    return MetricMetadata(
        numerator=quantity,
        denominator=None,
        unit="count",
        notional_basis="not_applicable",
        fill_model=FILL_MODEL,
        adverse_model=ADVERSE_MODEL,
        fee_model=FEE_MODEL,
        replay_version=REPLAY_VERSION,
        economic_world=EconomicWorld.EXECUTION_REPLAY,
        expected_or_realized="realized",
        aggregation="count",
        notes=notes,
    )


def mean_edge_meta(
    *,
    numerator: str,
    denominator: str | None,
    aggregation: Aggregation,
    unit: str,
    notes: str = "",
) -> MetricMetadata:
    return MetricMetadata(
        numerator=numerator,
        denominator=denominator,
        unit=unit,
        notional_basis=f"{NOTIONAL_EUR} EUR per signal",
        fill_model=f"{FILL_MODEL}; overlay fill_rate={FILL_RATE}",
        adverse_model=f"{ADVERSE_MODEL}; extra_adverse_bps={ADVERSE_EXTRA_BPS}",
        fee_model=FEE_MODEL,
        replay_version=MEAN_EDGE_REPLAY_VERSION,
        economic_world=EconomicWorld.EXECUTION_REPLAY,
        expected_or_realized="realized",
        aggregation=aggregation,
        notes=notes or (
            "SIDECAR replay: fill_rate * (expected_net_per_signal - extra_adverse). "
            "Not the canonical per-signal waterfall sum. Must never occupy generic NET/fill."
        ),
    )


def observed_meta(
    *,
    numerator: str,
    denominator: str | None,
    aggregation: Aggregation,
    unit: str,
    notes: str = "",
) -> MetricMetadata:
    return MetricMetadata(
        numerator=numerator,
        denominator=denominator,
        unit=unit,
        notional_basis="observed_paper_or_live_notional",
        fill_model="observed_fills",
        adverse_model="observed_markout",
        fee_model="observed_fees",
        replay_version="observed_v1",
        economic_world=EconomicWorld.OBSERVED,
        expected_or_realized="observed",
        aggregation=aggregation,
        notes=notes or "Must never silently substitute for execution replay.",
    )


@dataclass(frozen=True, slots=True)
class CrossWorldComparison:
    """Explicit, recorded comparison across economic worlds."""

    comparison_id: str
    numerator: LabeledQuantity
    denominator: LabeledQuantity
    value: Decimal
    notes: str = ""
    metadata: MetricMetadata = field(init=False)

    def __post_init__(self) -> None:
        if self.denominator.value == 0:
            raise AccountingIdentityError(f"{self.comparison_id}: denominator is zero")
        object.__setattr__(
            self,
            "metadata",
            MetricMetadata(
                numerator=self.numerator.quantity,
                denominator=self.denominator.quantity,
                unit="dimensionless_ratio",
                notional_basis=(
                    f"num:{self.numerator.metadata.notional_basis}; "
                    f"den:{self.denominator.metadata.notional_basis}"
                ),
                fill_model=(
                    f"num:{self.numerator.metadata.fill_model}; "
                    f"den:{self.denominator.metadata.fill_model}"
                ),
                adverse_model=(
                    f"num:{self.numerator.metadata.adverse_model}; "
                    f"den:{self.denominator.metadata.adverse_model}"
                ),
                fee_model=(
                    f"num:{self.numerator.metadata.fee_model}; "
                    f"den:{self.denominator.metadata.fee_model}"
                ),
                replay_version="cross_world_comparison_v1",
                economic_world=self.numerator.metadata.economic_world,
                expected_or_realized="realized",
                aggregation="ratio",
                notes=(
                    f"{self.notes} worlds="
                    f"{self.numerator.metadata.economic_world.value}/"
                    f"{self.denominator.metadata.economic_world.value}"
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "value": str(self.value),
            "numerator": self.numerator.to_dict(),
            "denominator": self.denominator.to_dict(),
            "metadata": self.metadata.to_dict(),
            "notes": self.notes,
        }


def ev_capture(
    *,
    observed_realized_net: LabeledQuantity,
    predicted_expected_net: LabeledQuantity,
) -> CrossWorldComparison:
    if observed_realized_net.metadata.economic_world != EconomicWorld.OBSERVED:
        raise CrossWorldError("EV_CAPTURE numerator must be OBSERVED")
    if predicted_expected_net.metadata.economic_world != EconomicWorld.SIGNAL_EXPECTATION:
        raise CrossWorldError("EV_CAPTURE denominator must be SIGNAL_EXPECTATION")
    return CrossWorldComparison(
        comparison_id="EV_CAPTURE",
        numerator=observed_realized_net,
        denominator=predicted_expected_net,
        value=observed_realized_net.value / predicted_expected_net.value,
        notes="observed_realized_net / predicted_expected_net; both definitions recorded.",
    )
