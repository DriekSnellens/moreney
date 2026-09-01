"""Canonical research accounting — one economic replay for every strategy."""

from __future__ import annotations

from bot.research.accounting.protocol import (
    PACKAGE_LABEL,
    PROTOCOL_VERSION,
    REPLAY_VERSION,
    SCHEMA_VERSION,
)
from bot.research.accounting.schema import CrossWorldError, EconomicWorld, UnlabeledMetricError
from bot.research.accounting.waterfall import (
    CanonicalEconomics,
    assert_waterfall_identity,
    assemble_canonical,
    from_attached_events,
)

__all__ = [
    "PACKAGE_LABEL",
    "PROTOCOL_VERSION",
    "REPLAY_VERSION",
    "SCHEMA_VERSION",
    "CanonicalEconomics",
    "CrossWorldError",
    "EconomicWorld",
    "UnlabeledMetricError",
    "assert_waterfall_identity",
    "assemble_canonical",
    "from_attached_events",
]
