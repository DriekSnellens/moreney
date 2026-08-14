"""Walk-forward validation for strategy changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backtesting.engine import BacktestEngine, BacktestResult


@dataclass
class WalkForwardResult:
    """Aggregated in-sample / out-of-sample metrics."""

    windows: int = 0
    in_sample_approved: int = 0
    out_of_sample_approved: int = 0
    in_sample_net: Decimal = Decimal("0")
    out_of_sample_net: Decimal = Decimal("0")
    details: list[dict[str, str | int]] = field(default_factory=list)


class WalkForwardValidator:
    """Simple walk-forward split over snapshot timeline."""

    def __init__(self, engine: BacktestEngine, *, train_ratio: float = 0.7) -> None:
        self._engine = engine
        self._train_ratio = train_ratio

    async def run(self, snapshots: list) -> WalkForwardResult:
        if not snapshots:
            return WalkForwardResult()
        split = max(1, int(len(snapshots) * self._train_ratio))
        train = snapshots[:split]
        test = snapshots[split:]
        result = WalkForwardResult(windows=2 if test else 1)
        if train:
            is_res = await self._engine.run(train)
            result.in_sample_approved = is_res.approved_count
            result.in_sample_net = is_res.total_expected_net_profit_usd
            result.details.append(
                {
                    "window": "in_sample",
                    "snapshots": len(train),
                    "approved": is_res.approved_count,
                    "net": str(is_res.total_expected_net_profit_usd),
                }
            )
        if test:
            oos_res = await self._engine.run(test)
            result.out_of_sample_approved = oos_res.approved_count
            result.out_of_sample_net = oos_res.total_expected_net_profit_usd
            result.details.append(
                {
                    "window": "out_of_sample",
                    "snapshots": len(test),
                    "approved": oos_res.approved_count,
                    "net": str(oos_res.total_expected_net_profit_usd),
                }
            )
        return result
