"""Research-only execution overlays on the frozen canonical waterfall.

Does not modify fill_model.py, PaperExecutor, venue fees, or strategy params.
BASELINE with fill_prob=1 and zero adders must reproduce canonical NET exactly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from bot.research.execution_realism.models import ExecutionWaterfall, FillStatus, SignalOutcome
from bot.research.final_validation.protocol import RANDOM_SEED
from bot.research.robustness.protocol import LATENCY_MS_TO_BPS
from bot.research.tournament.criteria import ADVERSE_BPS_DEFAULT, NOTIONAL_EUR_DEFAULT

_ZERO = Decimal("0")
_BPS = Decimal("10000")
_NOTIONAL = Decimal(str(NOTIONAL_EUR_DEFAULT))
# Existing breakeven.py hedge-delay conversion: 0.02 bps per ms of notional.
_HEDGE_MS_TO_BPS = Decimal("0.02")


@dataclass(slots=True)
class CanonicalLine:
    signal_id: str
    window_id: str
    symbol: str
    route: str
    canonical_net: Decimal
    gross: Decimal
    fees: Decimal
    slippage: Decimal
    adverse: Decimal
    latency: Decimal
    inventory: Decimal
    forward: float


def deterministic_fill(signal_id: str, scenario_id: str, fill_prob: float, *, seed: int = RANDOM_SEED) -> bool:
    """Deterministic Bernoulli using SHA-256. Independent of PnL. p=1 always fills."""
    if fill_prob >= 1.0:
        return True
    if fill_prob <= 0.0:
        return False
    digest = hashlib.sha256(f"{seed}:{scenario_id}:{signal_id}".encode()).digest()
    u = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return u < float(fill_prob)


def apply_overlay(line: CanonicalLine, scenario: dict) -> ExecutionWaterfall:
    """Paired scenario execution from the same canonical candidate line."""
    sid = str(scenario["scenario_id"])
    wf = ExecutionWaterfall(signal_id=line.signal_id, scenario_id=sid)
    wf.requested_notional = _NOTIONAL
    if not deterministic_fill(line.signal_id, sid, float(scenario["fill_prob"])):
        wf.fill_status = FillStatus.NO_FILL
        wf.outcome = SignalOutcome.NO_FILL
        wf.execution_net = _ZERO
        wf.filled_notional = _ZERO
        return wf

    ratio = Decimal(str(scenario["partial_ratio"]))
    if ratio < 0:
        ratio = _ZERO
    if ratio > 1:
        ratio = Decimal("1")
    fee_mult = Decimal(str(scenario["fee_mult"]))
    slip_add = Decimal(str(scenario["slip_add_bps"]))
    adv_add = Decimal(str(scenario["adverse_add_bps"]))
    lat_ms = Decimal(str(scenario["latency_add_ms"]))
    hedge_ms = Decimal(str(scenario["hedge_delay_ms"]))

    filled = _NOTIONAL * ratio
    remaining = _NOTIONAL - filled
    wf.filled_notional = filled
    wf.gross_spread = line.gross * ratio
    wf.taker_fees = line.fees * ratio * fee_mult
    wf.maker_fees = _ZERO
    wf.slippage = line.slippage * ratio + filled * slip_add / _BPS
    wf.adverse_selection = line.adverse * ratio + filled * adv_add / _BPS
    wf.latency_cost = line.latency * ratio + filled * lat_ms * Decimal(str(LATENCY_MS_TO_BPS)) / _BPS
    wf.hedge_cost = filled * hedge_ms * _HEDGE_MS_TO_BPS / _BPS
    if ratio < 1:
        wf.residual_inventory_cost = remaining * Decimal(str(ADVERSE_BPS_DEFAULT)) / _BPS
        wf.partial_fill_cost = remaining * (line.slippage / _NOTIONAL) if _NOTIONAL else _ZERO
        wf.fill_status = FillStatus.PARTIAL_FILL
        wf.outcome = SignalOutcome.PARTIAL_PROFIT
    else:
        wf.fill_status = FillStatus.FULL_FILL
        wf.outcome = SignalOutcome.SURVIVES_REALISTIC_EXECUTION

    wf.execution_net = (
        wf.gross_spread
        - wf.maker_fees
        - wf.taker_fees
        - wf.slippage
        - wf.latency_cost
        - wf.queue_cost
        - wf.partial_fill_cost
        - wf.adverse_selection
        - wf.hedge_cost
        - wf.residual_inventory_cost
    )
    return wf
