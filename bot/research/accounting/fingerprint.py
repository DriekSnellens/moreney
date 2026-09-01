"""Deterministic replay fingerprint (research path only)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bot.research.accounting.protocol import REPLAY_VERSION, SCHEMA_VERSION
from bot.research.accounting.waterfall import CanonicalEconomics


def replay_fingerprint(econ: CanonicalEconomics, *, extra: dict[str, Any] | None = None) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "replay_version": REPLAY_VERSION,
        "venue": econ.venue,
        "venue_exit": econ.venue_exit,
        "signals": econ.signals.value,
        "fills": econ.fills.value,
        "gross": str(econ.gross.value),
        "fees": str(econ.fees.value),
        "slippage": str(econ.slippage.value),
        "adverse": str(econ.adverse.value),
        "funding": str(econ.funding.value),
        "transfer": str(econ.transfer.value),
        "other_costs": str(econ.other_costs.value),
        "replay_net": str(econ.replay_net.value),
        "lines": [
            {
                "ts_ns": ln.ts_ns,
                "symbol": ln.symbol,
                "route": ln.route,
                "forward": str(ln.forward),
                "net": str(ln.realized_replay_net),
            }
            for ln in econ.lines
        ],
        "extra": extra or {},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
