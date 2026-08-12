"""Idempotent fill application into the paper portfolio."""

from __future__ import annotations

import logging
from uuid import UUID

from bot.portfolio.models import AccountingResult, Fill, Order
from bot.portfolio.portfolio import PaperPortfolio

logger = logging.getLogger(__name__)


class FillTracker:
    """Records fills and applies them to the portfolio at most once."""

    def __init__(self, portfolio: PaperPortfolio) -> None:
        self._portfolio = portfolio
        self._fills: dict[UUID, Fill] = {}
        self._results: list[AccountingResult] = []

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills.values())

    @property
    def results(self) -> list[AccountingResult]:
        return list(self._results)

    def apply(self, order: Order, fill: Fill) -> AccountingResult:
        result = self._portfolio.apply_fill(order, fill)
        if result.duplicate:
            logger.info(
                "FILL_DUPLICATE ignored fill_id=%s order_id=%s",
                fill.id,
                order.id,
            )
            return result
        self._fills[fill.id] = fill
        self._results.append(result)
        logger.info(
            "FILL_APPLIED fill_id=%s order_id=%s symbol=%s qty=%s price=%s fee=%s "
            "realized_pnl=%s",
            fill.id,
            order.id,
            fill.symbol,
            fill.quantity,
            fill.price,
            fill.fee,
            result.realized_pnl,
        )
        return result

    def has_fill(self, fill_id: UUID) -> bool:
        return fill_id in self._fills or str(fill_id) in self._portfolio.accounting.processed_fill_ids
