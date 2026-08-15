"""Reproduceable PnL waterfall for expected and realized round-trips.

Identity (cash, realized):

    gross_opportunity
    - buy_fees
    - sell_fees
    - slippage
    - adverse_selection   (price gap vs expected exit, after known costs)
    - funding
    - transfer_fx
    - inventory_effect
    = realized_net

``execution_buffer`` is an *expected* haircut only. It must never appear as a
separate cash line in realized PnL (that would double-count adverse).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

_ZERO = Decimal("0")
_TOL = Decimal("0.0001")


class PnlWaterfall(BaseModel):
    """One economic decomposition. All fields are euro (quote) amounts."""

    gross_opportunity: Decimal = _ZERO
    buy_fees: Decimal = _ZERO
    sell_fees: Decimal = _ZERO
    slippage: Decimal = _ZERO
    adverse_selection: Decimal = _ZERO
    execution_buffer: Decimal = _ZERO
    funding: Decimal = _ZERO
    transfer_fx: Decimal = _ZERO
    inventory_effect: Decimal = _ZERO
    net: Decimal = _ZERO
    kind: str = "expected"  # expected | realized
    notes: list[str] = Field(default_factory=list)

    def recomputed_net(self, *, include_buffer: bool) -> Decimal:
        costs = (
            self.buy_fees
            + self.sell_fees
            + self.slippage
            + self.adverse_selection
            + self.funding
            + self.transfer_fx
            + self.inventory_effect
        )
        if include_buffer:
            costs = costs + self.execution_buffer
        return self.gross_opportunity - costs

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "gross_opportunity": str(self.gross_opportunity),
            "buy_fees": str(self.buy_fees),
            "sell_fees": str(self.sell_fees),
            "slippage": str(self.slippage),
            "adverse_selection": str(self.adverse_selection),
            "execution_buffer": str(self.execution_buffer),
            "funding": str(self.funding),
            "transfer_fx": str(self.transfer_fx),
            "inventory_effect": str(self.inventory_effect),
            "net": str(self.net),
            "notes": list(self.notes),
        }


def expected_waterfall(
    *,
    gross: Decimal,
    buy_fee: Decimal,
    sell_fee: Decimal,
    slippage: Decimal,
    funding: Decimal = _ZERO,
    execution_buffer: Decimal = _ZERO,
    extra_adverse: Decimal = _ZERO,
    transfer_fx: Decimal = _ZERO,
    inventory_relief: Decimal = _ZERO,
    net: Decimal | None = None,
) -> PnlWaterfall:
    """Build expected waterfall. Buffer is charged; inventory_relief reduces cost."""
    inventory_effect = -inventory_relief  # negative cost = benefit
    wf = PnlWaterfall(
        kind="expected",
        gross_opportunity=gross,
        buy_fees=buy_fee,
        sell_fees=sell_fee,
        slippage=slippage,
        adverse_selection=extra_adverse,
        execution_buffer=execution_buffer,
        funding=funding,
        transfer_fx=transfer_fx,
        inventory_effect=inventory_effect,
    )
    recomputed = wf.recomputed_net(include_buffer=True)
    wf.net = net if net is not None else recomputed
    if abs(wf.net - recomputed) > _TOL:
        wf.notes.append(
            f"expected net {wf.net} diverges from recomputed {recomputed}"
        )
    return wf


def realized_waterfall(
    *,
    gross: Decimal,
    buy_fee: Decimal,
    sell_fee: Decimal,
    slippage: Decimal,
    funding: Decimal = _ZERO,
    transfer_fx: Decimal = _ZERO,
    inventory_effect: Decimal = _ZERO,
    realized_net: Decimal,
) -> PnlWaterfall:
    """Build realized waterfall. Adverse is the residual that closes the identity.

    execution_buffer is always 0 on realized (haircut, not cash).
    """
    known = (
        buy_fee
        + sell_fee
        + slippage
        + funding
        + transfer_fx
        + inventory_effect
    )
    # gross - known - adverse = realized_net  ⇒  adverse = gross - known - realized_net
    adverse = gross - known - realized_net
    wf = PnlWaterfall(
        kind="realized",
        gross_opportunity=gross,
        buy_fees=buy_fee,
        sell_fees=sell_fee,
        slippage=slippage,
        adverse_selection=adverse,
        execution_buffer=_ZERO,
        funding=funding,
        transfer_fx=transfer_fx,
        inventory_effect=inventory_effect,
        net=realized_net,
    )
    recomputed = wf.recomputed_net(include_buffer=False)
    if abs(recomputed - realized_net) > _TOL:
        wf.notes.append(
            f"realized identity broken: recomputed {recomputed} != {realized_net}"
        )
    else:
        wf.notes.append("identity_ok")
    return wf


def assert_no_double_count(expected: PnlWaterfall, realized: PnlWaterfall) -> list[str]:
    """Return issues if buffer is treated as realized cash or adverse is double-booked."""
    issues: list[str] = []
    if realized.execution_buffer != 0:
        issues.append(
            "realized waterfall charges execution_buffer (expected-only haircut)"
        )
    # If expected already folded adverse into buffer, extra_adverse should not
    # restate the same euro amount as buffer.
    if (
        expected.execution_buffer > 0
        and expected.adverse_selection > 0
        and abs(expected.adverse_selection - expected.execution_buffer) < _TOL
    ):
        issues.append(
            "expected adverse_selection equals execution_buffer — likely double count"
        )
    if realized.kind != "realized" or expected.kind != "expected":
        issues.append("waterfall kind mismatch")
    return issues


def decompose_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decompose a persisted tracker trade row into expected + realized waterfalls."""

    def _d(key: str, default: str = "0") -> Decimal:
        raw = row.get(key)
        if raw is None or raw == "":
            return Decimal(default)
        return Decimal(str(raw))

    fees_e = _d("fees")
    # Expected fee split unknown in legacy rows → put all on buy_fees for identity.
    expected = expected_waterfall(
        gross=_d("expected_gross", str(_d("gross_profit"))),
        buy_fee=fees_e,
        sell_fee=_ZERO,
        slippage=_d("slippage"),
        execution_buffer=_d("expected_adverse", str(_d("execution_buffer"))),
        inventory_relief=_d("expected_inventory"),
        net=_d("expected_net_profit"),
    )
    fees_r = _d("realized_fees", str(fees_e))
    slip_r = _d("realized_slippage", str(_d("slippage")))
    realized_net = _d("realized_net_profit")
    # Reconstruct gross from cash identity when not stored:
    # realized = gross - fees - slip - adverse - fx - inventory
    # We do not know gross_r separately; approximate via expected gross then residual.
    gross_r = realized_net + fees_r + slip_r
    # If expected gross was positive and this "gross_r" is far below, adverse will
    # absorb the gap when we rebuild from expected gross as the opportunity.
    # Prefer opportunity gross (what the model claimed) so adverse = model error.
    opp_gross = expected.gross_opportunity
    realized = realized_waterfall(
        gross=opp_gross if opp_gross != 0 else gross_r,
        buy_fee=fees_r,
        sell_fee=_ZERO,
        slippage=slip_r,
        realized_net=realized_net,
    )
    return {
        "expected": expected.as_dict(),
        "realized": realized.as_dict(),
        "double_count_issues": assert_no_double_count(expected, realized),
        "ev_gap": str(realized_net - expected.net),
        "gross_evaporated": str(opp_gross - (realized_net + fees_r + slip_r)),
    }
